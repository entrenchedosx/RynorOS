#include "display-internal.h"
#include "serial.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    (void)serial_write("[FB] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
#define SENTINEL 0x5a5a5a5au
/* Synthetic guarded backing for testing the exact production algorithms.
   This is NEVER passed to display_initialize or used as physical/device RAM. */
static cpu_u32 guarded[256];
static void reset(void) { for (unsigned int i=0;i<256;++i) guarded[i]=SENTINEL; }
static void rectangle_is(cpu_u32 x, cpu_u32 y, cpu_u32 w, cpu_u32 h, cpu_u32 color)
{
    for (unsigned int i=0;i<256;++i) {
        unsigned int row=(i-4u)/16, col=(i-4u)%16;
        int painted=i>=4 && row<11 && col<13 && col>=x && row>=y &&
                    (cpu_u64)col < (cpu_u64)x+w && (cpu_u64)row < (cpu_u64)y+h;
        require(guarded[i] == (painted ? color : SENTINEL),"surface_exact_extent");
    }
}
void display_surface_self_test(void)
{
    struct display_surface s={guarded+4,13,11,64,704};
    require(display_surface_valid(&s),"surface_valid");
    const cpu_u32 corners[][2]={{0,0},{12,0},{0,10},{12,10}};
    for (unsigned int i=0;i<4;++i) {
        reset(); require(display_surface_pixel(&s,corners[i][0],corners[i][1],0x123456),"pixel_corner");
        rectangle_is(corners[i][0],corners[i][1],1,1,0x123456);
    }
    reset();
    require(!display_surface_pixel(&s,13,0,1) && !display_surface_pixel(&s,0,11,1) &&
            !display_surface_pixel(&s,~0u,~0u,1),"pixel_bounds");
    rectangle_is(0,0,0,0,0);
    const cpu_u32 rects[][4]={{0,0,13,11},{11,9,10,10},{1,1,~0u,~0u},{12,10,1,1}};
    for (unsigned int i=0;i<4;++i) {
        reset();
        require(display_surface_rect(&s,rects[i][0],rects[i][1],rects[i][2],rects[i][3],7),"rect_clip");
        rectangle_is(rects[i][0],rects[i][1],rects[i][2],rects[i][3],7);
    }
    reset();
    require(!display_surface_rect(&s,0,0,0,3,1) && !display_surface_rect(&s,0,0,3,0,1) &&
            !display_surface_rect(&s,13,0,3,3,1) && !display_surface_rect(&s,0,11,3,3,1) &&
            !display_surface_rect(&s,~0u,~0u,~0u,~0u,1),"rect_invalid");
    rectangle_is(0,0,0,0,0);
    const char *bad[]={"A\x01","a","A\x7f","\xff",(void *)0};
    for (unsigned int i=0;i<sizeof(bad)/sizeof(bad[0]);++i) {
        require(!display_surface_text(&s,0,0,bad[i],3),"text_bad_atomic");
        rectangle_is(0,0,0,0,0);
    }
    require(!display_surface_text(&s,0xfffffffcU,0," A",3) &&
            !display_surface_text(&s,0,0xfffffffcU,"A",3) &&
            !display_surface_text(&s,13,0," ",3) &&
            display_surface_text(&s,0,0,"",3) && display_surface_text(&s,0,0," ",3),"text_origin_empty");
    rectangle_is(0,0,0,0,0);
    char long_text[DISPLAY_TEXT_LIMIT+2];
    for (unsigned int i=0;i<sizeof(long_text)-1;++i) long_text[i]='A';
    long_text[sizeof(long_text)-1]=0;
    require(!display_surface_text(&s,0,0,long_text,3),"text_length_limit");
    rectangle_is(0,0,0,0,0);
    long_text[DISPLAY_TEXT_LIMIT]=0;
    require(display_surface_text(&s,0,0,long_text,3),"text_max_length");
    /* Independent known A shape, including transparent cell/pitch padding and
       right/bottom clipping; no font table read by this oracle. */
    const cpu_u8 a_rows[]={14,17,17,31,17,17,17};
    const cpu_u32 origins[][2]={{0,0},{11,8}};
    for (unsigned int n=0;n<2;++n) {
        reset(); cpu_u32 x=origins[n][0], y=origins[n][1];
        require(display_surface_text(&s,x,y,"A",9),"text_clipped");
        for (unsigned int i=0;i<256;++i) {
            unsigned int row=(i-4u)/16, col=(i-4u)%16;
            int ink=i>=4 && row<11 && col<13 && col>=x && row>=y && col-x<5 && row-y<7 &&
                    (a_rows[row-y] & (1u<<(4-(col-x))));
            require(guarded[i]==(ink?9:SENTINEL),"text_exact_glyph");
        }
    }
    reset();
    require(display_surface_text(&s,0,0," A\rA\nA",9),"text_controls");
    require(guarded[4+1]==9 && guarded[4+8]==SENTINEL && guarded[4+9]==9 &&
            guarded[4+8*16+1]==9,"text_control_positions");
    struct display_surface bad_surface=s;
    bad_surface.pitch=48;
    require(!display_surface_pixel(&bad_surface,0,0,1),"surface_short_pitch");
    bad_surface=s; bad_surface.bytes=~0ULL;
    require(!display_surface_valid(&bad_surface),"surface_size_overflow");
    bad_surface=s; bad_surface.pixels=(void *)0xfffffffffffffffcULL;
    require(!display_surface_valid(&bad_surface),"surface_pointer_overflow");
    bad_surface=s; bad_surface.pixels=(void *)0x800000000000ULL;
    require(!display_surface_valid(&bad_surface),"surface_noncanonical");
}
