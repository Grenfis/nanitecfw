@ patch 23 0307b180
#include "../../patches/v2/fw.inc"
#include "delete_resume_syms.inc"
#include "delete_track_successor.h"
    ldr     r0, =0x03004b12
    ldrh    r0, [r0]
    cmp     r0, #0x82
    bne     1f
    push    {r4, lr}
    movs    r0, #34
    movs    r1, #2
    fbl     0x0306b754
    pop.w   {r4, lr}
    fb      delete_track_browser_key
1:  fb      0x030632da
    .ltorg

    .org    0x24
    movw    r2, #0x4b2e
    movt    r2, #0x0300
    ldrh    r2, [r2]
    movw    r3, #DELETE_TRACK_PLAY_TAG
    cmp     r2, r3
    fbeq    delete_track_launch_successor
    fb      0x0306b302
