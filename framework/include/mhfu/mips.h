/* Allegrex (MIPS) instruction encoder for building stubs word by word. Each emitter returns
 * the instruction word; no delay slot is emitted, so place one after every jump and branch. */
#ifndef MHFU_MIPS_H
#define MHFU_MIPS_H

#include <stdint.h>

#define MIPS_REG_ZERO  0
#define MIPS_REG_AT    1
#define MIPS_REG_V0    2
#define MIPS_REG_V1    3
#define MIPS_REG_A0    4
#define MIPS_REG_A1    5
#define MIPS_REG_A2    6
#define MIPS_REG_A3    7
#define MIPS_REG_T0    8
#define MIPS_REG_T1    9
#define MIPS_REG_T2   10
#define MIPS_REG_T3   11
#define MIPS_REG_T4   12
#define MIPS_REG_T5   13
#define MIPS_REG_T6   14
#define MIPS_REG_T7   15
#define MIPS_REG_S0   16
#define MIPS_REG_S1   17
#define MIPS_REG_S2   18
#define MIPS_REG_S3   19
#define MIPS_REG_S4   20
#define MIPS_REG_S5   21
#define MIPS_REG_S6   22
#define MIPS_REG_S7   23
#define MIPS_REG_T8   24
#define MIPS_REG_T9   25
#define MIPS_REG_SP   29
#define MIPS_REG_RA   31

#define MIPS_NOP  0x00000000u

static inline uint32_t mips_jal(uint32_t target_addr) {
    /* the target keeps the top 4 bits of PC+4 */
    return (0x03u << 26) | ((target_addr >> 2) & 0x03FFFFFFu);
}

static inline uint32_t mips_j(uint32_t target_addr) {
    return (0x02u << 26) | ((target_addr >> 2) & 0x03FFFFFFu);
}

static inline uint32_t mips_jr(uint32_t rs) {
    return ((rs & 0x1Fu) << 21) | 0x08u;
}

static inline uint32_t mips_move(uint32_t rd, uint32_t rs) {
    /* addu rd, rs, $zero */
    return ((rs & 0x1Fu) << 21) | ((rd & 0x1Fu) << 11) | 0x21u;
}

static inline uint32_t mips_addiu(uint32_t rt, uint32_t rs, int16_t imm) {
    return (0x09u << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)imm);
}

static inline uint32_t mips_lui(uint32_t rt, uint16_t imm) {
    return (0x0Fu << 26) | ((rt & 0x1Fu) << 16) | imm;
}

static inline uint32_t mips_ori(uint32_t rt, uint32_t rs, uint16_t imm) {
    return (0x0Du << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16) | imm;
}

static inline uint32_t mips_lw(uint32_t rt, int16_t off, uint32_t base) {
    return (0x23u << 26) | ((base & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off);
}

/* LBU rt, offset(base): load byte unsigned. */
static inline uint32_t mips_lbu(uint32_t rt, int16_t off, uint32_t base) {
    return (0x24u << 26) | ((base & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off);
}

static inline uint32_t mips_sw(uint32_t rt, int16_t off, uint32_t base) {
    return (0x2Bu << 26) | ((base & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off);
}

static inline uint32_t mips_beq(uint32_t rs, uint32_t rt, int16_t off_insns) {
    /* off_insns counts instructions from PC+4 */
    return (0x04u << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off_insns);
}

static inline uint32_t mips_bne(uint32_t rs, uint32_t rt, int16_t off_insns) {
    return (0x05u << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off_insns);
}

static inline uint32_t mips_jalr(uint32_t rs) {
    /* links in $ra */
    return ((rs & 0x1Fu) << 21) | (31u << 11) | 0x09u;
}

/* R-type with shamt 0. */
static inline uint32_t mips_r3(uint32_t rd, uint32_t rs, uint32_t rt, uint32_t fn) {
    return ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((rd & 0x1Fu) << 11) | (fn & 0x3Fu);
}

static inline uint32_t mips_addu(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x21u);
}
static inline uint32_t mips_subu(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x23u);
}
static inline uint32_t mips_xor(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x26u);
}
static inline uint32_t mips_or(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x25u);
}
/* AND rd, rs, rt: combines 0/1 predicates without a branch. */
static inline uint32_t mips_and(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x24u);
}

/* SLL rd, rt, shamt (all zero is NOP). */
static inline uint32_t mips_sll(uint32_t rd, uint32_t rt, uint32_t shamt) {
    return ((rt & 0x1Fu) << 16) | ((rd & 0x1Fu) << 11) | ((shamt & 0x1Fu) << 6);
}

static inline uint32_t mips_andi(uint32_t rt, uint32_t rs, uint16_t imm) {
    return (0x0Cu << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16) | imm;
}
static inline uint32_t mips_xori(uint32_t rt, uint32_t rs, uint16_t imm) {
    return (0x0Eu << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16) | imm;
}
/* SLTIU rt, rs, imm: rt = (unsigned)rs < imm; imm 1 gives a branchless rs == 0. */
static inline uint32_t mips_sltiu(uint32_t rt, uint32_t rs, uint16_t imm) {
    return (0x0Bu << 26) | ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16) | imm;
}

/* MOVN: if (rt != 0) rd = rs. */
static inline uint32_t mips_movn(uint32_t rd, uint32_t rs, uint32_t rt) {
    return ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((rd & 0x1Fu) << 11) | 0x0Bu;
}

/* MOVZ: if (rt == 0) rd = rs. */
static inline uint32_t mips_movz(uint32_t rd, uint32_t rs, uint32_t rt) {
    return ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((rd & 0x1Fu) << 11) | 0x0Au;
}

/* The encodings below were each checked against the same instruction in the game's code. */

/* SLTU rd, rs, rt: rd = (unsigned)rs < rt. Non-negative floats order like their bit
 * patterns, so after mfc1 this is also a float compare with no FP condition bit. */
static inline uint32_t mips_sltu(uint32_t rd, uint32_t rs, uint32_t rt) {
    return mips_r3(rd, rs, rt, 0x2Bu);
}

/* SRLV rd, rt, rs: rd = rt >> (rs & 31); rs is the shift amount. (mask >> main) & 1 tests
 * a bitmask without a branch. */
static inline uint32_t mips_srlv(uint32_t rd, uint32_t rt, uint32_t rs) {
    return ((rs & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16) | ((rd & 0x1Fu) << 11) | 0x06u;
}

/* SB rt, offset(base): store byte. */
static inline uint32_t mips_sb(uint32_t rt, int16_t off, uint32_t base) {
    return (0x28u << 26) | ((base & 0x1Fu) << 21) | ((rt & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off);
}

/* LWC1 ft, offset(base): load a float into COP1 register ft. */
static inline uint32_t mips_lwc1(uint32_t ft, int16_t off, uint32_t base) {
    return (0x31u << 26) | ((base & 0x1Fu) << 21) | ((ft & 0x1Fu) << 16)
         | ((uint32_t)(uint16_t)off);
}

/* COP1 single precision: fd = fs OP ft; funct 0 add, 1 sub, 2 mul. */
static inline uint32_t mips_fop_s(uint32_t fd, uint32_t fs, uint32_t ft, uint32_t funct) {
    return (0x11u << 26) | (0x10u << 21) | ((ft & 0x1Fu) << 16)
         | ((fs & 0x1Fu) << 11) | ((fd & 0x1Fu) << 6) | (funct & 0x3Fu);
}
static inline uint32_t mips_add_s(uint32_t fd, uint32_t fs, uint32_t ft) { return mips_fop_s(fd, fs, ft, 0x00u); }
static inline uint32_t mips_sub_s(uint32_t fd, uint32_t fs, uint32_t ft) { return mips_fop_s(fd, fs, ft, 0x01u); }
static inline uint32_t mips_mul_s(uint32_t fd, uint32_t fs, uint32_t ft) { return mips_fop_s(fd, fs, ft, 0x02u); }

/* MFC1 rt, fs: the raw bits of COP1 register fs into GPR rt. */
static inline uint32_t mips_mfc1(uint32_t rt, uint32_t fs) {
    return (0x11u << 26) | ((rt & 0x1Fu) << 16) | ((fs & 0x1Fu) << 11);
}

#endif /* MHFU_MIPS_H */
