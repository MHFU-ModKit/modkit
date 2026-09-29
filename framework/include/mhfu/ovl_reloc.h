/* MWo3 overlay relocator: moves a position-dependent em*.ovl image to another address. */
#ifndef MHFU_OVL_RELOC_H
#define MHFU_OVL_RELOC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char     magic[4];          /* "MWo3" */
    uint32_t overlay_id;
    uint32_t load_address;       /* address of file offset 0 */
    uint32_t text_size;
    uint32_t data_size;
    uint32_t bss_size;
    uint32_t static_init_start;
    uint32_t static_init_end;
    char     name[32];
} mhfu_ovl_header_t;

typedef struct {
    int      ok;                 /* 1 = success */
    uint32_t n_jump;             /* MIPS_26 sites relocated */
    uint32_t n_hilo;             /* HI16/LO16 pairs relocated */
    uint32_t n_data;             /* MIPS_32 data pointers relocated */
    uint32_t new_base;           /* load_address + delta */
} mhfu_ovl_reloc_stats_t;

/* Relocate image (image_size = 64 + text + data bytes) in place by delta, which must be
 * 64 KB aligned. slot_base is the footprint base (MHFU_EM_OVERLAY_SLOT for em overlays): the
 * code reaches BSS below the image, so the footprint starts there. stats.ok is 0 on a bad
 * header or delta. */
mhfu_ovl_reloc_stats_t mhfu_ovl_relocate(void *image, uint32_t image_size,
                                         uint32_t slot_base, int32_t delta);

#ifdef __cplusplus
}
#endif

#endif /* MHFU_OVL_RELOC_H */
