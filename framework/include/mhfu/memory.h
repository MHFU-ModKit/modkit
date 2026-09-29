/* Game-memory access: plain volatile loads and stores, since the PRX runs on the PSP itself. */
#ifndef MHFU_MEMORY_H
#define MHFU_MEMORY_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint8_t  mhfu_mem_read_u8 (uint32_t addr);
uint16_t mhfu_mem_read_u16(uint32_t addr);
uint32_t mhfu_mem_read_u32(uint32_t addr);
void     mhfu_mem_write_u8 (uint32_t addr, uint8_t  v);
void     mhfu_mem_write_u16(uint32_t addr, uint16_t v);
void     mhfu_mem_write_u32(uint32_t addr, uint32_t v);

float    mhfu_mem_read_f32 (uint32_t addr);
void     mhfu_mem_write_f32(uint32_t addr, float v);

/* 1 if addr is in [MHFU_MAIN_RAM, MHFU_USER_RAM_END); check a pointer read from the game
 * before following it. The extra-RAM window is not included. */
int      mhfu_mem_valid(uint32_t addr);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_MEMORY_H */
