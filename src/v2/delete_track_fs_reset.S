@ patch 0 0305be3c
#include "../../patches/v2/fw.inc"
#include "delete_track_mount.h"

@ Reuse the stock literal pool after the file-open routine.
.macro load_stock_literal reg, address
    .set pc_here, (PATCH_BASE + (. - _patch_start) + 4) & ~3
    .set distance, \address - pc_here
    .short 0x4800 | (\reg << 8) | (distance >> 2)
.endm

    movs    r1, #0
    load_stock_literal 0, 0x0305c238 @ FAT cache
    str.w   r1, [r0, #0x204]
    strb.w  r1, [r0, #0x200]
    mvns    r2, r1
    load_stock_literal 0, 0x0305c23c @ directory sector cache index
    str     r2, [r0]
    load_stock_literal 0, 0x0305c244 @ directory iterator cache index
    str     r2, [r0]
    load_stock_literal 2, 0x0305c240 @ file handle table
    movs    r3, #8
1:  strb    r1, [r2]
    addw    r2, r2, #0x27e
    subs    r3, #1
    bne     1b
    ldr     r2, =DELETE_TRACK_MOUNT_STATE
    ldr     r3, [r2]
    adds    r3, #1
    str     r3, [r2]
    bx      lr
    .ltorg
