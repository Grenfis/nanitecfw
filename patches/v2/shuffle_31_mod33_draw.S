@ Draw a uniform offset into the unplayed suffix of the stock history table.
@ patch 33 030816f0
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    ldrh.w  r0, [r3, #2]
    subs    r0, r0, r2
    push    {r1-r3, lr}
    fbl     SHUFFLE_RANDOM_BELOW
    pop.w   {r1-r3, lr}
    adds    r0, r0, r2
    .org    0x03081702 - PATCH_BASE
