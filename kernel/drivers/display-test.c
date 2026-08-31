#include "display.h"
#include "display-internal.h"
#include "boot_memory.h"
#include "serial.h"
#include "io.h"
#include "vm.h"
#include "pmm.h"
#include "heap.h"
#include "ksched.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[FB] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void field(const char *s, cpu_u64 n)
{
    char b[21]; unsigned int i=20; b[i]=0;
    do { b[--i]=(char)('0'+n%10); n/=10; } while(n);
    text(s); text(b+i);
}
static void pattern0(void)
{
    cpu_u32 w=display_width(), h=display_height();
    require(w && h, "geometry");
    require(display_fill_rect(0,0,w,h,0,0,0),"pattern_clear");
    require(display_fill_rect(0,0,w,16,0,0,255),"border_top");
    require(display_fill_rect(0,h-16,w,16,0,0,255),"border_bottom");
    require(display_fill_rect(0,16,16,h-32,0,0,255),"border_left");
    require(display_fill_rect(w-16,16,16,h-32,0,0,255),"border_right");
    require(display_fill_rect(w/2-32,h/2-32,64,64,255,0,0),"square");
    require(display_fill_rect(16,32,8,8,0,255,0),"marker");
    require(display_draw_text(40,32,"RYNOROS FRAME BUFFER",224,224,224),"text1");
    require(display_draw_text(40,48,"BGA 1024X768 STAGE 9",224,224,224),"text2");
    require(display_draw_text(40,64,"ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 .-:/?",224,224,224),"font_atlas");
    require(display_draw_text(40,80,"A\nB\rC",224,224,224),"text_controls");
    require(display_draw_text(w-3,h-3,"AZ",224,224,224),"text_clip");
}
static void verify_pattern0(void)
{
    cpu_u8 r,g,b;
    cpu_u32 w=display_width(), h=display_height();
    require(display_read_pixel(0,0,&r,&g,&b) && r==0 && g==0 && b==255,"read_border_tl");
    require(display_read_pixel(w-1,h-1,&r,&g,&b) && r==0 && g==0 && b==255,"read_border_br");
    require(display_read_pixel(w/2,h/2,&r,&g,&b) && r==255 && g==0 && b==0,"read_square");
    require(display_read_pixel(20,36,&r,&g,&b) && r==0 && g==255 && b==0,"read_marker");
    require(display_read_pixel(300,100,&r,&g,&b) && r==0 && g==0 && b==0,"read_clear");
}
static void metadata_tests(void)
{
    const struct boot_fb_info good=*(const struct boot_fb_info *)__fb_info_start;
    struct boot_fb_info h;
    require(!display_validate_info(&good),"metadata_valid");
#define BAD(member,value) do { h=good; h.member=(value); require(display_validate_info(&h)!=0,"metadata_" #member); } while(0)
    BAD(magic,0); BAD(version,0); BAD(status,0); BAD(bpp,24); BAD(memory_model,0);
    BAD(red_mask,0); BAD(green_mask,0); BAD(blue_mask,0);
    BAD(width,0); BAD(height,0); BAD(width,4097); BAD(height,4097);
    BAD(width,~0u); BAD(height,~0u); BAD(pitch,0); BAD(pitch,4092); BAD(pitch,4097); BAD(pitch,0xfffffffcU);
    BAD(lfb_phys,0); BAD(lfb_phys,4095); BAD(lfb_phys,~0u); BAD(lfb_phys,0xfffff000u);
    BAD(aperture_bytes,0); BAD(aperture_bytes,4096); BAD(aperture_bytes,0xffffffffu); BAD(pci_id,0); BAD(mode,0);
#undef BAD
}

static void mmio_tests(void)
{
    struct vm_space *s=vm_kernel_space();
    const struct boot_fb_info *h=(const void *)__fb_info_start;
    cpu_u64 va=VM_MMIO_BASE+0x10000000, pa;
    struct pmm_statistics before, after;
    struct vm_mapping m;
    cpu_u64 tables=s->table_pages;
    require(pmm_statistics(&before)==PMM_OK,"mmio_stats");
    enum pmm_state state;
    require(vm_query(s,(cpu_u64)__fb_info_start,&m)==VM_OK &&
            m.physical==(cpu_u64)__fb_info_start && !m.permissions && !m.uncached &&
            pmm_query((cpu_u64)__fb_info_start,&state)==PMM_OK && state==PMM_STATE_RESERVED,
            "handoff_reserved_readonly_nx");
    require(vm_map_device(s,va+1,h->lfb_phys,1,VM_WRITE)==VM_ALIGNMENT &&
            vm_map_device(s,va,h->lfb_phys+1,1,VM_WRITE)==VM_ALIGNMENT &&
            vm_map_device(s,va,h->lfb_phys,0,VM_WRITE)==VM_INVALID &&
            vm_map_device(s,VM_MMIO_END-VM_PAGE_SIZE,h->lfb_phys,2,VM_WRITE)==VM_PERMISSION &&
            vm_map_device(s,0x800000000000ULL,h->lfb_phys,1,VM_WRITE)==VM_NONCANONICAL &&
            vm_map_device(s,va,h->lfb_phys,~0ULL,VM_WRITE)==VM_OVERFLOW &&
            vm_map_device(s,va,~0ULL-4095,1,VM_WRITE)==VM_PHYSICAL,"mmio_invalid_ranges");
    require(pmm_allocate(&pa)==PMM_OK,"mmio_allocate");
    require(vm_map_device(s,va,pa,1,VM_WRITE)==VM_PHYSICAL &&
            vm_map_device(s,va,s->root,1,VM_WRITE)==VM_PHYSICAL &&
            vm_map_device(s,va,0x5000,1,VM_WRITE)==VM_PHYSICAL,"mmio_ram_rejected");
    require(vm_map(s,va,pa,VM_WRITE)==VM_PERMISSION,"mmio_slot_exclusive");
    require(pmm_release(pa)==PMM_OK && vm_map_device(s,va,pa,1,VM_WRITE)==VM_PHYSICAL,"mmio_free_rejected");
    require(vm_map_device(s,va,h->lfb_phys,1,VM_EXECUTE)==VM_PERMISSION,"mmio_permissions");
    require(vm_map_device(s,va,h->lfb_phys,1,VM_WRITE)==VM_OK,"mmio_map");
    require(vm_query(s,va,&m)==VM_OK && m.physical==h->lfb_phys && m.permissions==VM_WRITE && m.uncached,
            "mmio_pte_state");
    require(vm_unmap(s,va)==VM_PERMISSION && vm_protect(s,va,VM_EXECUTE)==VM_PERMISSION,"mmio_no_ordinary_edit");
    require(vm_map_device(s,va-VM_PAGE_SIZE,h->lfb_phys,2,VM_WRITE)==VM_EXISTS &&
            vm_query(s,va-VM_PAGE_SIZE,&m)==VM_NOT_MAPPED,"mmio_conflict_atomic");
    require(vm_unmap_device(s,va,2)==VM_NOT_MAPPED && vm_query(s,va,&m)==VM_OK,"mmio_unmap_atomic");
    require(vm_unmap_device(s,va,1)==VM_OK && vm_query(s,va,&m)==VM_NOT_MAPPED,"mmio_unmap");
    require(pmm_statistics(&after)==PMM_OK && after.allocated_bytes==before.allocated_bytes &&
            after.free_bytes==before.free_bytes && tables==s->table_pages && pmm_check() && vm_check(s),"mmio_balance");
    /* Exhaust actual PMM, retaining a linked list in the allocated frames.
       Leave 0..3 frames: mapping 513 pages requires four tables and must roll
       back both partial hierarchy creation and the first 512 installed leaves. */
    cpu_u64 head=0, frame;
    while (pmm_allocate(&frame)==PMM_OK) {
        cpu_u64 *link=vm_frame_access(frame); require(link!=0,"mmio_oom_access"); *link=head; head=frame;
    }
    for (unsigned int available=0; available<4; ++available) {
        if (available) {
            require(head!=0,"mmio_oom_list"); frame=head;
            head=*(cpu_u64 *)vm_frame_access(frame);
            require(pmm_release(frame)==PMM_OK,"mmio_oom_release");
        }
        require(vm_map_device(s,va,h->lfb_phys,513,VM_WRITE)==VM_OOM,"mmio_oom_result");
        require(pmm_statistics(&after)==PMM_OK && after.free_bytes==available*VM_PAGE_SIZE &&
                tables==s->table_pages && vm_query(s,va,&m)==VM_NOT_MAPPED &&
                vm_query(s,va+512*VM_PAGE_SIZE,&m)==VM_NOT_MAPPED && vm_check(s),"mmio_oom_rollback");
    }
    while (head) { frame=head; head=*(cpu_u64 *)vm_frame_access(frame); require(pmm_release(frame)==PMM_OK,"mmio_oom_restore"); }
    require(pmm_statistics(&after)==PMM_OK && after.free_bytes==before.free_bytes &&
            after.allocated_bytes==before.allocated_bytes && pmm_check(),"mmio_oom_balance");
}

void display_self_test(void)
{
    struct pmm_statistics before, after; struct heap_statistics hb, ha;
    struct sched_statistics sb, sa;
    const struct boot_fb_info *hi;
    cpu_u64 tables, pages, tables_new;
    cpu_u64 fb_bytes;

    require(cpu_interrupts_disabled(),"if0");
    tables=vm_kernel_space()->table_pages;
    require(pmm_statistics(&before)==PMM_OK && heap_statistics(&hb)==HEAP_OK &&
            scheduler_statistics(&sb),"statistics");
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage9 frame buffer\r\n"
         "[FB] self-test started\r\n");
    require(serial_flush(),"start_flush");
    if (display_validate_info((const void *)__fb_info_start) ||
        !display_device_matches((const void *)__fb_info_start)) {
        if (!display_initialize()) require(0,display_error());
        require(0,"invalid_handoff_accepted");
    }
    metadata_tests();
    display_surface_self_test();
    mmio_tests();
    if (!display_initialize()) require(0, display_error());
    require(display_initialize()==0,"double_init");
    {
        cpu_u32 w=display_width(), h=display_height();
        require(w && h,"geometry");
        require(display_put_pixel(w,0,255,0,0)==0 && display_put_pixel(0,h,0,255,0)==0 &&
                display_put_pixel(w,h,0,0,255)==0 &&
                display_fill_rect(0,0,0,0,1,2,3)==0 && display_fill_rect(w,h,1,1,1,2,3)==0 &&
                display_fill_rect(w/2,h/2,0,8,1,2,3)==0 && display_draw_text(w,h,"X",1,2,3)==0 &&
                display_draw_text(0,0,0,0,0,0)==0 && display_draw_text(0,0,"\x01",1,2,3)==0 &&
                display_put_pixel(0,h-1,255,0,0)==1 && display_read_pixel(0,h-1,0,0,0)==0 &&
                display_read_pixel(w,0,0,0,0)==0 && display_read_pixel(0,0,0,0,0)==0,
                "bounds_fail_closed");
    }
    text("[FB] metadata and guarded drawing/text tests passed\r\n"
         "[FB] MMIO ownership, UC/NX permissions and OOM rollback verified\r\n");
    pattern0();
    verify_pattern0();
    text("[FB] pattern 0 painted and read back\r\n");
    hi=(const struct boot_fb_info *)__fb_info_start;
    field("[FB] handoff magic=",hi->magic); field(" version=",hi->version);
    field(" status=",hi->status); text("\r\n");
    field("[FB] mode=",hi->mode); field(" width=",display_width()); field(" height=",display_height());
    field(" pitch=",hi->pitch); field(" bpp=",hi->bpp); field(" memory_model=",hi->memory_model);
    text("\r\n");
    field("[FB] pixel maps red=",hi->red_mask); field(" green=",hi->green_mask);
    field(" blue=",hi->blue_mask); text("\r\n");
    field("[FB] lfb=",hi->lfb_phys); field(" fb_bytes=",fb_bytes=(cpu_u64)hi->height*hi->pitch);
    field(" pages=",pages=(fb_bytes+4095u)/4096u); text("\r\n");
    field("[FB] lfb_end=",hi->lfb_phys+fb_bytes); text("\r\n");
    field("[FB] mapped va=",VM_MMIO_BASE); text("\r\n");
    tables_new=(pages+511u)/512u+2u;
    require(pmm_statistics(&after)==PMM_OK && heap_statistics(&ha)==HEAP_OK &&
            scheduler_statistics(&sa) && sa.ticks==sb.ticks && sa.switches==sb.switches &&
            after.allocated_bytes==before.allocated_bytes+tables_new*4096u &&
            after.free_bytes==before.free_bytes-tables_new*4096u &&
            vm_kernel_space()->table_pages==tables+tables_new &&
            hb.used_bytes==ha.used_bytes && hb.free_blocks==ha.free_blocks &&
            pmm_check() && vm_check(vm_kernel_space()) && heap_check() && scheduler_check(),
            "resource_balance");
    field("[FB] final allocated_bytes=",after.allocated_bytes); field(" free_bytes=",after.free_bytes);
    field(" table_pages=",vm_kernel_space()->table_pages);
    text("\r\n[TEST] framebuffer api verified\r\n");
    require(serial_flush(),"flush_final");
}
