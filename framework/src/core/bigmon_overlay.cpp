/*
 * Loads a second big-monster AI overlay into a fresh slot, relocated with mhfu_ovl_relocate.
 */
#include "mhfu/bigmon_overlay.h"
#include "mhfu/ovl_reloc.h"
#include "mhfu/log.h"
#include "addresses.gen.h"

#include <pspsysmem.h>
#include <pspiofilemgr.h>
#include <psputils.h>          /* sceKernelDcacheWritebackAll, sceKernelIcacheInvalidateAll */
#include <pspsuspend.h>        /* sceKernelVolatileMemLock */
#include <string.h>

/* MWCC static-initializer list: plain function pointers up to si_end. */
typedef void (*ctor_fn)(void);

/* PPSSPP memory=64 extra RAM, from the user partition's end, which the 32 MB game
 * never touches: a persistent home, bump-allocated 64 KB-aligned. Emulator only. */
static uint32_t g_xram_bump = MHFU_USER_RAM_END;

/* A placed overlay stays for the session; a repeated call returns the same region. */
static int               g_placed = 0;
static mhfu_ovl_region_t g_region;

int mhfu_ovl_load_relocated(const char *path, mhfu_ovl_region_t *out)
{
    if (!path || !out) return -1;
    if (g_placed) { *out = g_region; return 0; }
    memset(out, 0, sizeof(*out));

    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_log("[bigmon_ovl] open FAILED %s rc=0x%08X", path, fd); return -2; }

    SceOff sz = sceIoLseek(fd, 0, PSP_SEEK_END);
    sceIoLseek(fd, 0, PSP_SEEK_SET);
    uint32_t img_size = (uint32_t)sz;
    if (img_size < 0x40) { sceIoClose(fd); mhfu_log("[bigmon_ovl] too small"); return -3; }

    /* the 64-byte header sizes the footprint */
    mhfu_ovl_header_t hdr;
    if (sceIoRead(fd, &hdr, sizeof(hdr)) != (int)sizeof(hdr) ||
        memcmp(hdr.magic, "MWo3", 4) != 0) {
        sceIoClose(fd); mhfu_log("[bigmon_ovl] bad MWo3 header"); return -4;
    }
    uint32_t slot_base = MHFU_EM_OVERLAY_SLOT;
    uint32_t foot_hi   = hdr.load_address + img_size + hdr.bss_size;   /* old VA end */
    uint32_t foot_size = foot_hi - slot_base;                          /* incl. lower and upper bss */
    uint32_t img_off   = hdr.load_address - slot_base;                 /* file image offset in the footprint */

    /* footprint plus 64 KB alignment slack */
    uint32_t need = foot_size + 0x10000;
    mhfu_log("[bigmon_ovl] need=%uKB  user maxfree=%uKB totalfree=%uKB",
             (unsigned)(need / 1024), (unsigned)(sceKernelMaxFreeMemSize() / 1024),
             (unsigned)(sceKernelTotalFreeMemSize() / 1024));
    /* Placement, first that fits:
     *  -1 the native em slot when empty (a small-monster quest): delta 0, so the added
     *     monster's own overlay pointer resolves with no rebind; an occupied slot is kept
     *   0 partition 2, if its allocator was resized
     *   1 extra RAM: memory=64 grows RAM but not the partition-2 allocator
     *   2 volatile, last resort: the game reclaims it when streaming */
    uint32_t blk;
    int placement;                       /* -1 native, 0 partition 2, 1 extra RAM, 2 volatile */
    int uid = -1;
    if (*(volatile uint32_t *)hdr.load_address == 0) {        /* em slot is empty */
        blk = slot_base; placement = -1;
        mhfu_log("[bigmon_ovl] em slot FREE -> NATIVE placement @0x%08X (delta=0, no rebind)",
                 (unsigned)hdr.load_address);
    } else {
    uid = sceKernelAllocPartitionMemory(2 /*USER*/, "mhfu_bigmon_ovl",
                                        PSP_SMEM_High, need, 0);
    if (uid < 0)
        uid = sceKernelAllocPartitionMemory(2, "mhfu_bigmon_ovl", PSP_SMEM_Low, need, 0);

    if (uid >= 0) {
        blk = (uint32_t)sceKernelGetBlockHeadAddr(uid); placement = 0;
    } else {
        uint32_t cand = (g_xram_bump + 0xFFFFu) & ~0xFFFFu;
        int mapped = 0;
        if (cand + need <= MHFU_EXTRA_RAM_END) {
            volatile uint32_t *p = (volatile uint32_t *)cand;
            uint32_t save = *p; *p = 0xA5C3F00Du;
            mapped = (*p == 0xA5C3F00Du); *p = save;
        }
        if (mapped) {
            blk = cand; placement = 1; g_xram_bump = cand + need;
            mhfu_log("[bigmon_ovl] using EXTRA RAM (memory=64 grant) @0x%08X (%uKB left)",
                     (unsigned)blk, (unsigned)((MHFU_EXTRA_RAM_END - g_xram_bump) / 1024));
        } else {
            void *vptr = 0; int vsz = 0;
            int vrc = sceKernelVolatileMemLock(0, &vptr, &vsz);
            if (vrc < 0 || !vptr || (uint32_t)vsz < need) {
                sceIoClose(fd);
                mhfu_log("[bigmon_ovl] alloc FAILED (no part2, no extra RAM, volatile rc=0x%08X)", vrc);
                return -5;
            }
            blk = (uint32_t)vptr; placement = 2;
            mhfu_log("[bigmon_ovl] using VOLATILE @0x%08X (UNSAFE fallback, reclaimed on streaming)",
                     (unsigned)blk);
        }
    }
    }
    /* region_base >= blk with (region_base - slot_base) 64 KB-aligned */
    uint32_t lo16 = slot_base & 0xFFFF;
    uint32_t region_base = ((blk - lo16 + 0xFFFF) & ~0xFFFFu) + lo16;
    if (region_base < blk) region_base += 0x10000;
    int32_t  delta = (int32_t)(region_base - slot_base);              /* multiple of 0x10000 */
    uint32_t image_dst = region_base + img_off;                       /* == hdr.load_address + delta */

    /* zero the whole footprint (lower bss, gaps, upper bss) */
    memset((void *)region_base, 0, foot_size);

    /* read the file image to its placed VA */
    int rd = sceIoRead(fd, (void *)(image_dst + sizeof(hdr)), img_size - sizeof(hdr));
    sceIoClose(fd);
    if (rd != (int)(img_size - sizeof(hdr))) {
        if (placement == 2) sceKernelVolatileMemUnlock(0); else if (placement == 0) sceKernelFreePartitionMemory(uid);
        mhfu_log("[bigmon_ovl] read FAILED rc=0x%08X", rd);
        return -6;
    }
    memcpy((void *)image_dst, &hdr, sizeof(hdr));   /* header (incl pre-reloc fields) */

    /* relocate in place */
    mhfu_ovl_reloc_stats_t st =
        mhfu_ovl_relocate((void *)image_dst, img_size, slot_base, delta);
    if (!st.ok) {
        if (placement == 2) sceKernelVolatileMemUnlock(0); else if (placement == 0) sceKernelFreePartitionMemory(uid);
        mhfu_log("[bigmon_ovl] relocate FAILED (delta=0x%08X)", (unsigned)delta);
        return -7;
    }

    /* new code: write back dcache, invalidate icache */
    sceKernelDcacheWritebackAll();
    sceKernelIcacheInvalidateAll();

    out->uid         = (placement == 0) ? uid : -1; /* -1: nothing to free */
    out->region_base = region_base;
    out->delta       = delta;
    out->new_load    = st.new_base;
    out->img_size    = img_size;
    out->foot_size   = foot_size;
    out->si_start    = hdr.static_init_start + (uint32_t)delta;
    out->si_end      = hdr.static_init_end   + (uint32_t)delta;

    mhfu_log("[bigmon_ovl] '%s' placed @region=0x%08X load=0x%08X delta=0x%08X "
             "(jump=%u hilo=%u data=%u) si=[0x%08X,0x%08X)",
             hdr.name, (unsigned)region_base, (unsigned)st.new_base, (unsigned)delta,
             (unsigned)st.n_jump, (unsigned)st.n_hilo, (unsigned)st.n_data,
             (unsigned)out->si_start, (unsigned)out->si_end);
    g_region = *out; g_placed = 1;
    return 0;
}

/* Calls each pointer in [si_start, si_end) that lands inside the placed footprint. */
extern "C" void mhfu_ovl_run_static_inits(const mhfu_ovl_region_t *r)
{
    if (!r || r->si_start >= r->si_end) return;
    uint32_t n = 0;
    for (uint32_t p = r->si_start; p < r->si_end; p += 4) {
        uint32_t fn = *(volatile uint32_t *)p;
        if (fn >= r->region_base && fn < r->region_base + r->foot_size) {
            ((ctor_fn)fn)();
            n++;
        }
    }
    mhfu_log("[bigmon_ovl] ran %u static-initializers", (unsigned)n);
}
