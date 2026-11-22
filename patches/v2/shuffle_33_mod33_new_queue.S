@ Count the selected track as played when the queue is opened in shuffle mode.
@ patch 33 03083bd2
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    ldrh.w  r1, [r8, #2]
    subs    r1, #1
    cmp     r1, r0
    blo     have_first_track
    fbl     SHUFFLE_RANDOM_BELOW
    mov     r1, r0
have_first_track:
    str     r1, [r5, #80]
    fb      0x03083c1a
