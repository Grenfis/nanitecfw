@ Select a random All Songs row only for the Shuffle All sentinel title.
@ The M3U title (0x5e) and every other browser key pass through unchanged.
@ patch 23 0307b180
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    .equ    get_key, 0x030632da
shuffle_key:
    push    {r4, lr}
    fbl     get_key
    ldr     r1, =0x03004b12
    ldrh    r2, [r1]
    cmp     r2, #0x82
    bne     1f
    movs    r2, #0x5a
    strh    r2, [r1]
    @ Module 24 copies this browser title into the playback return path.
    ldr     r1, =0x0300e704
    strh    r2, [r1]
    ldr     r4, =0x0300e968
    ldrh    r1, [r4]
    cbz     r1, 1f
    mov     r0, r1
    fbl     SHUFFLE_RANDOM_BELOW
    @ Module 24 launches from the depth-zero cursor at +0x0c.
    strh    r0, [r4, #0x0c]
    strh    r0, [r4, #0x16]
    ldr     r0, =0x40000040
1:  pop     {r4, pc}
    .ltorg
