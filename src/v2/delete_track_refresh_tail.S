@ patch 5 0307a958
#include "../../patches/v2/fw.inc"
#include "delete_resume_syms.inc"
#include "delete_track_successor.h"
    ldrb.w  r0, [r8, #4]
    mov     r1, sp
    ldr     r2, =0x03004b2e
    ldrh    r2, [r2]
    cmp.w   r2, #DELETE_TRACK_REFRESH_TAG
    fbeq    delete_track_refresh_return
    fbl     0x0306b302
    @ Reuse the stock completion epilogue at 0x0307a8ac.
    .short  0xe000 | (((0x0307a8ac - (PATCH_BASE + (. - _patch_start) + 4)) >> 1) & 0x7ff)
    .ltorg
