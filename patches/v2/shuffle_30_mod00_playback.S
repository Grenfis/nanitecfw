@ Replace the random-selection portion of the stock previous/next handler.
@ patch 0 030605dc
#include "fw.inc"
#include "../../src/v2/shuffle_random.h"
    .equ playback_lock, 0x0306af7c
    .equ playback_finish, 0x030607d6
    .equ shuffle_history, 0x03081740
    .equ saved_forward_track, 0x117

    movs    r0, #35
    fbl     playback_lock
    ldrh    r0, [r8]
    cmp     r0, #1
    bls     single_track
    cmp     r0, #2
    beq     two_tracks

    sxtb.w  r8, r5
    ldrd    r2, r3, [fp, #12]
    orrs    r2, r3
    beq     ordinary_draw
    cmp     r5, #0
    ble     previous_with_history

    movw    r0, #saved_forward_track
    fbl     0x0306b0f4
    cbz     r0, forward_with_history
    ldrh    r0, [fp, #8]
    strh    r0, [r4, #2]
    bl      advance_history
    b       finish

forward_with_history:
    bl      draw_track
    bl      advance_history
    ldr     r2, [fp, #12]
    cmp     r2, #1
    bne     finish_forward
    ldr     r2, [fp, #4]
    cmp     r0, r2
    beq     skip_forward_boundary
    ldrh    r2, [r4]
    cmp     r1, r2
    blt     finish_forward
skip_forward_boundary:
    bl      draw_track
    bl      advance_history
    strd    sl, r9, [fp, #12]
finish_forward:
    ldrh    r0, [r4]
    adds    r0, #1
    cmp     r1, r0
    ble     finish
    b       clear_history_flags

previous_with_history:
    ldr     r0, [fp, #20]
    cmp     r0, #2
    beq     save_forward_track
    blt     previous_at_boundary

    ldrh    r0, [r4, #2]
    str     r0, [sp, #4]
    bl      draw_track
    bl      advance_history
    ldr     r1, [fp, #4]
    cmp     r0, r1
    bne     check_previous_track
    ldr     r1, [fp, #16]
    cmp     r1, #1
    beq     skip_previous_boundary
check_previous_track:
    ldr     r1, [sp, #4]
    cmp     r0, r1
    bne     finish
skip_previous_boundary:
    bl      draw_track
    bl      advance_history
    strd    r9, sl, [fp, #12]
    b       finish

save_forward_track:
    movw    r0, #saved_forward_track
    fbl     msg_post
    ldrh    r0, [r4, #2]
    str     r0, [fp, #8]
    ldrh    r0, [fp, #4]
    strh    r0, [r4, #2]
    bl      advance_history
    b       finish

previous_at_boundary:
    bl      draw_track
clear_history_flags:
    strd    sl, sl, [fp, #12]
    b       finish

ordinary_draw:
    bl      draw_track
    b       finish

two_tracks:
    ldrh    r0, [r8, #2]
    cmp     r0, #1
    bne     single_track
    movs    r0, #2
    b       store_track
single_track:
    movs    r0, #1
store_track:
    strh    r0, [r4, #2]
finish:
    fb      playback_finish

advance_history:
    ldr     r1, [fp, #20]
    add     r1, r5
    str     r1, [fp, #20]
    bx      lr

draw_track:
    push    {r3, lr}
    ldrh    r0, [r4]
    cmp     r0, #8192
    bhi     large_queue
    mov     r0, r8
    movs    r1, #0
    fbl     shuffle_history
    adds    r0, #1
    b       save_draw
large_queue:
    @ The stock history buffer has space for 8192 entries. For larger queues,
    @ draw uniformly from all rows except the current one.
    subs    r0, #1
    bl      random_below
    adds    r0, #1
    ldrh    r1, [r4, #2]
    cmp     r0, r1
    it      hs
    addhs   r0, #1
save_draw:
    strh    r0, [r4, #2]
    pop     {r3, pc}

    .org    SHUFFLE_RANDOM_BELOW - PATCH_BASE
random_below:
    @ Seed xorshift32 once from both clocks. Keep the nonzero state in
    @ resident RAM so overlay reloads and new queues continue the stream.
    cmp     r0, #1
    bls     random_zero
    adr     r3, random_state
random_retry:
    ldrex   r1, [r3]
    cbnz    r1, random_step
    ldr     r1, =0x0300c02c
    ldr     r1, [r1]
    ldr     r2, =0xe000e018
    ldr     r2, [r2]
    eor.w   r1, r2, r1, ror #13
    @ Mix nearby clock samples before starting the stream.
    ldr     r2, =0x045d9f3b
    eor.w   r1, r1, r1, lsr #16
    muls    r1, r2, r1
    eor.w   r1, r1, r1, lsr #16
    muls    r1, r2, r1
    eor.w   r1, r1, r1, lsr #16
    cbnz    r1, random_step
    movs    r1, #1
random_step:
    eor.w   r1, r1, r1, lsl #13
    eor.w   r1, r1, r1, lsr #17
    eor.w   r1, r1, r1, lsl #5
    strex   r2, r1, [r3]
    cmp     r2, #0
    bne     random_retry
    @ Convert the nonzero stream to 0..0xfffffffe and reject the incomplete
    @ final bucket before taking the remainder.
    subs    r1, #1
    udiv    r2, r1, r0
    mls     r2, r0, r2, r1
    subs    r1, r1, r2
    cmn     r1, r0
    bhs     random_retry
    mov     r0, r2
    bx      lr
random_zero:
    movs    r0, #0
    bx      lr
    .balign 4
random_state:
    .word   0
    .ltorg
    .org    playback_finish - PATCH_BASE
