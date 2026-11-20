@ Redirect the V2 Music Settings dispatch entry to the custom menu route.
@ patch 21 0307adce
#include "fw.inc"
    .equ    shuffle_route, 0x0307a6e0
    fb      shuffle_route
