#include "keyboard-internal.h"
#include "io.h"
#include "irq.h"
#include "ksched.h"
#include "serial.h"
#include "heap.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[KBD] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void field(const char *s, cpu_u64 n)
{
    char b[21]; unsigned int i=20; b[i]=0;
    do { b[--i]=(char)('0'+n%10); n/=10; } while(n);
    text(s); text(b+i);
}
static void ring_tests(void)
{
    struct kbd_ring q = {0};
    struct kbd_sample sample = {77, 99};
    require(kbd_ring_get(&q, &sample)==0 && sample.scan==99, "empty_unchanged");
    for (unsigned int cycle=0; cycle<16; ++cycle) {
        for (unsigned int size=1; size<=KBD_RING_CAPACITY; ++size) {
            cpu_u64 rec=q.received, drops=q.dropped, epoch=q.epoch;
            for (unsigned int i=0; i<size; ++i)
                require(kbd_ring_put(&q,(cpu_u8)(i+cycle))==1, "ring_fill");
            require(q.received-rec==size && q.dropped==drops, "ring_capacity");
            if (size==KBD_RING_CAPACITY) {
                for (unsigned int n=0;n<8;++n) require(kbd_ring_put(&q,0xff)==0,"ring_full");
                require(q.dropped-drops==8 && q.epoch-epoch==8,"ring_drops");
            }
            for (unsigned int i=0;i<size;++i)
                require(kbd_ring_get(&q,&sample)==1 && sample.scan==(cpu_u8)(i+cycle) &&
                        sample.epoch==epoch, "ring_fifo");
            require(kbd_ring_get(&q,&sample)==0,"ring_empty");
            require(kbd_ring_put(&q,0x42)==1 && kbd_ring_get(&q,&sample)==1 &&
                    sample.scan==0x42 && sample.epoch==q.epoch,"ring_after_overflow");
        }
    }
    q.received=~0ULL;
    require(kbd_ring_put(&q,1)==-1,"ring_counter_wrap");
    q.received=0; q.head=KBD_STORAGE;
    require(kbd_ring_put(&q,1)==-1 && kbd_ring_get(&q,&sample)==-1,"ring_bad_index");
    q=(struct kbd_ring){0};
    struct kbd_decoder decoder={0}; struct kbd_event event;
    cpu_u64 seen=0;
    for (unsigned int i=0;i<KBD_RING_CAPACITY-1;++i)
        require(kbd_ring_put(&q,0x1e)==1,"loss_fill");
    require(kbd_ring_put(&q,0xe0)==1 && kbd_ring_put(&q,0x1c)==0,"loss_drop");
    for (unsigned int i=0;i<KBD_RING_CAPACITY;++i)
        require(kbd_stream_next(&q,&decoder,&seen,&event)==KBD_EVENT,"loss_retained");
    require(decoder.extended && kbd_ring_put(&q,0x30)==1,"loss_pending_prefix");
    require(kbd_stream_next(&q,&decoder,&seen,&event)==KBD_LOST && !decoder.extended &&
            kbd_stream_next(&q,&decoder,&seen,&event)==KBD_EVENT && event.key==0x30 &&
            event.type==KBD_EVENT_PRESS &&
            kbd_stream_next(&q,&decoder,&seen,&event)==KBD_EMPTY,"loss_boundary");
    ++q.epoch;
    require(kbd_stream_next(&q,&decoder,&seen,&event)==KBD_LOST &&
            kbd_stream_next(&q,&decoder,&seen,&event)==KBD_EMPTY,"loss_empty");
    text("[KBD] queue FIFO, capacity, wrap and loss verified (synthetic)\r\n");
}
static void decode_tests(void)
{
    struct kbd_decoder d={0}; struct kbd_event e;
    const cpu_u8 keys[]={0x1e,0x30,0x2e,0x20,0x39,0x1c,0x2a,0x36};
    for (unsigned int i=0;i<sizeof(keys);++i) {
        for (unsigned int repeat=0;repeat<3;++repeat)
            require(kbd_decode(&d,keys[i],&e) && e.key==keys[i] &&
                    e.type==KBD_EVENT_PRESS,"decode_make_repeat");
        require(kbd_decode(&d,keys[i]|0x80,&e) && e.key==keys[i] &&
                e.type==KBD_EVENT_RELEASE,"decode_break");
    }
    const cpu_u8 extended[]={0xe0,0x1c,0xe0,0x9c,0xe0,0x2a,0xe0,0x37,
                            0xe1,0x1d,0x45,0xe1,0x9d,0xc5,0xe1,0x1e,0xe0,0xe0,0x1c};
    for (unsigned int i=0;i<sizeof(extended);++i)
        require(!kbd_decode(&d,extended[i],&e) && e.type==KBD_EVENT_UNKNOWN &&
                !e.key && e.scan==extended[i],"decode_prefix");
    require(kbd_decode(&d,0x1e,&e) && e.type==KBD_EVENT_PRESS,"decode_resync");
    require(!kbd_decode(&d,0x2d,&e) && !kbd_decode(&d,0xfa,&e) && !e.key,"decode_unknown");
    text("[KBD] Set-1 subset and prefix isolation verified (synthetic)\r\n");
}
static volatile cpu_u64 timer_ticks, worker_runs;
static volatile int worker_stop;
static void input_timer(void)
{
    struct kbd_event e;
    require(kbd_poll(&e)==KBD_BAD_CONTEXT && !kbd_initialize(),"irq_api_context");
    ++timer_ticks;
}
static void input_worker(void *arg)
{
    (void)arg;
    while(!worker_stop) { ++worker_runs; __asm__ volatile ("pause" ::: "memory"); }
}
static void event(unsigned int index)
{
    struct kbd_event e;
    enum kbd_result result;
    while ((result=kbd_poll(&e))==KBD_EMPTY)
        __asm__ volatile ("sti; hlt; cli" ::: "memory");
    require(result==KBD_EVENT,"input_loss");
    field("[KBD] event=",index); field(" scan=",e.scan); field(" key=",e.key);
    field(" type=",e.type); text("\r\n");
}
void keyboard_self_test(void)
{
    require(cpu_interrupts_disabled(),"if0");
    struct pmm_statistics before, after; struct heap_statistics hb, ha;
    struct sched_statistics sb, sa;
    cpu_u64 tables=vm_kernel_space()->table_pages;
    require(pmm_statistics(&before)==PMM_OK && heap_statistics(&hb)==HEAP_OK &&
            scheduler_statistics(&sb),"statistics");
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage8 hardware input\r\n"
         "[KBD] self-test started\r\n");
    struct kbd_event e;
    require(kbd_poll(&e)==KBD_NOT_READY && kbd_poll(0)==KBD_BAD_CONTEXT,"api_preinit");
    ring_tests(); decode_tests();
    if (!kbd_initialize()) {
        require((io_in8(0x21)&2) && !kbd_initialize(),"init_failure_mask");
        text("[KBD] initialization failed phase="); text(kbd_init_error()); text("\r\n");
        require(0,"kbd_init");
    }
    require(!kbd_initialize() && kbd_poll(&e)==KBD_EMPTY,"init_once");
    irq_restore(0x200);
    struct kbd_statistics initial;
    require(kbd_poll(&e)==KBD_EMPTY && !cpu_interrupts_disabled() &&
            kbd_statistics(&initial) && !cpu_interrupts_disabled(),"api_preserves_if1");
    irq_restore(0);
    require(!kbd_statistics(0) && kbd_poll(0)==KBD_BAD_CONTEXT && cpu_interrupts_disabled(),
            "api_invalid_preserves_if0");
    text("[KBD] i8042 configured, Set-2 translated to Set-1, irq1 enabled\r\n");
    thread_id worker;
    require(thread_create(&worker,input_worker,0) && irq_set_handler(0,input_timer) &&
            irq_set_enabled(0,1),"timer_worker_start");
    /* No expected keys in the guest: the host chooses input after this build.
       Each sendkey produces two ordinary bytes. Unknown keys are reported too. */
    for (unsigned int i=0;i<8;++i) {
        field("[KBD] waiting for input=",i); text("\r\n");
        require(serial_flush(),"flush_wait");
        event(i*2); event(i*2+1);
    }
    require(irq_set_enabled(1,0) && irq_set_enabled(0,0),"mask");
    worker_stop=1;
    while(thread_ready_count()>1) require(thread_yield(),"worker_finish");
    require(thread_join(worker) && timer_ticks && worker_runs && scheduler_statistics(&sa) &&
            sa.ticks-sb.ticks==timer_ticks && sa.switches>sb.switches,"timer_coexistence");
    struct kbd_statistics s;
    require(kbd_statistics(&s) && s.received==16 && !s.dropped && s.reads==16 &&
            s.irqs==s.reads+s.empty_irqs && !s.errors && !s.auxiliary && !s.queued &&
            kbd_poll(&e)==KBD_EMPTY,"hardware_counts");
    field("[KBD] irqs=",s.irqs); field(" reads=",s.reads); field(" received=",s.received);
    field(" dropped=",s.dropped); field(" errors=",s.errors); field(" auxiliary=",s.auxiliary);
    field(" empty=",s.empty_irqs); text("\r\n");
    field("[KBD] concurrent timer_ticks=",timer_ticks); field(" worker_runs=",worker_runs); text("\r\n");
    require(pic_in_service()==0 && io_in8(0x21)==0xff && io_in8(0xa1)==0xff &&
            cpu_interrupts_disabled() && pmm_statistics(&after)==PMM_OK &&
            heap_statistics(&ha)==HEAP_OK && after.allocated_bytes==before.allocated_bytes &&
            after.free_bytes==before.free_bytes && vm_kernel_space()->table_pages==tables &&
            hb.used_bytes==ha.used_bytes && hb.free_blocks==ha.free_blocks &&
            pmm_check() && vm_check(vm_kernel_space()) && heap_check() && scheduler_check(),"resource_balance");
    field("[KBD] final allocated_bytes=",after.allocated_bytes); field(" free_bytes=",after.free_bytes);
    field(" table_pages=",tables); text("\r\n[TEST] keyboard input verified\r\n");
    require(serial_flush(),"flush_final");
}
