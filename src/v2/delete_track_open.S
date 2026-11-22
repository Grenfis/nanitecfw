@ patch 34 0309ff80
#include "../../patches/v2/fw.inc"
#include "delete_track_mount.h"

@ Replay the stock open call, including its fifth argument, then bind the
@ resulting decoder handle to the storage device and generation it opened on.
    push    {r4-r6, lr}
    sub     sp, #8
    ldr     r4, [sp, #24]
    str     r4, [sp]
    ldr     r5, =DELETE_TRACK_MOUNT_STATE
    ldr     r4, [r5]
    ldr     r6, =0x03077d08
    ldr     r6, [r6]
    fbl     0x0305be72
    ldr     r1, [r5]
    cmp     r1, r4
    it      ne
    movne   r6, #0
    cmp     r0, #8
    bhs     failed
    @ Until the library is rebuilt it still lists files deleted from
    @ playback. Stock opens a FAT entry without checking the deletion mark.
    movw    r2, #0x27e
    ldr     r3, =0x03078844
    mla     r3, r0, r2, r3
    ldrb    r2, [r3, #1]
    cmp     r2, #0xe5
    bne     opened
    movs    r2, #0
    strb    r2, [r3]
    mvns    r0, r2
failed:
    movs    r6, #0
opened:
    str     r6, [r5, #4]
    str     r4, [r5, #8]
    add     sp, #8
    pop     {r4-r6, pc}
    .ltorg
    .org    0x60
