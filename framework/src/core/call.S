/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* mhfu_call (mhfu/call.h): a0 is the function, a1..t3 its a0..a6. s0 keeps the caller's sp
 * across the call, in a frame of our own; the call itself runs below it, aligned down to 16. */
    .set noreorder
    .text
    .globl mhfu_call
    .type mhfu_call, @function
    .ent mhfu_call
mhfu_call:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)
    move    $s0, $sp
    srl     $sp, $sp, 4
    sll     $sp, $sp, 4
    addiu   $sp, $sp, -16
    move    $t9, $a0
    move    $a0, $a1
    move    $a1, $a2
    move    $a2, $a3
    move    $a3, $t0
    move    $t0, $t1
    move    $t1, $t2
    jalr    $t9
    move    $t2, $t3
    move    $sp, $s0
    lw      $s0, 8($sp)
    lw      $ra, 12($sp)
    jr      $ra
    addiu   $sp, $sp, 16
    .end mhfu_call
    .size mhfu_call, . - mhfu_call
