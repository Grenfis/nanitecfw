@ Count the current track as played when switching to shuffle during playback.
@ patch 33 03084394
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    ldrh.w  r1, [sl, #2]
    subs    r1, #1
    cmp     r1, r0
    blo     have_first_track
    fbl     SHUFFLE_RANDOM_BELOW
    mov     r1, r0
have_first_track:
    str     r1, [r5, #80]
    fb      0x030843de
