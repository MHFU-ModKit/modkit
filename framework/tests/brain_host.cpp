/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* brain.cpp's pure parts (src/core/brain_logic.h) for the host test, through ctypes. */
#include "brain_logic.h"

extern "C" {
int brain_host_rows(void) { return BRAIN_ROWS; }
int brain_host_words(void) { return BRAIN_WORDS; }
int brain_host_check(const uint8_t *f, uint32_t n, uint32_t rows, brain_view_t *v)
{
    return brain_check(f, n, rows, v);
}
int brain_host_head(const uint8_t *f, uint32_t n, uint32_t rows, brain_view_t *v)
{
    return brain_head(f, n, rows, v);
}
int brain_host_same(const brain_view_t *a, const brain_view_t *b) { return brain_same(a, b); }
int brain_host_relocate(uint8_t *f, const brain_view_t *v, uint32_t load)
{
    return brain_relocate(f, v, load);
}
int brain_host_patch(const uint8_t *f, const brain_view_t *v, uint32_t load, uint8_t *row,
                     uint32_t at, uint32_t buf, uint32_t bytes, uint32_t *mask)
{
    return brain_patch_row(f, v, load, row, at, buf, bytes, mask);
}
int brain_host_unpatch(const uint8_t *f, const brain_view_t *v, uint32_t load, uint8_t *row)
{
    return brain_unpatch_row(f, v, load, row);
}
uint32_t brain_host_in_image(const uint8_t *row, uint32_t load, uint32_t size)
{
    return brain_row_words(row, load, size);
}
const char *brain_host_error(int rc) { return brain_error_text(rc); }
int brain_host_stage(int raw, uint32_t taken, uint32_t *budget)
{
    const brain_stage_t p = brain_stage_plan(raw, taken);
    *budget = p.budget;
    return p.route;
}
int brain_host_stage_raw(void) { return BRAIN_STAGE_RAW; }
int brain_host_stage_part(void) { return BRAIN_STAGE_PART; }
uint32_t brain_host_stage_max(void) { return BRAIN_STAGE_PART_MAX; }
const char *brain_host_stage_name(int route) { return brain_stage_name(route); }
}
