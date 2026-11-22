@ Use the same stream for the first draw after a completed pass.
@ patch 33 03081684
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    mov     r0, r5
    fbl     SHUFFLE_RANDOM_BELOW
    mov     r6, r0
    nop
    nop
    fbl     0x03065898
    .org    0x03081694 - PATCH_BASE
