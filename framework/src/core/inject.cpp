/*
 * Live model injection: the engine builds a big monster from our edited PAC instead of
 * the one it loaded, with no disk edits.
 *
 * Every raw sub-resource reaches the EBOOT parsers through get_subresource(pkg, type),
 * called on the raw PAC before the overlay restructures it. A prefix trampoline there
 * either redirects pkg to a grown PAC (relocate) or overwrites the raw buffer in place
 * (same size). Both match by content against the original file, so no file id is needed.
 */
#include "mhfu/inject.h"
#include "mhfu/log.h"
#include "mhfu/world.h"        /* mhfu_world_ms0_io_safe */
#include "mhfu/mips.h"
#include "mhfu/hooks.h"        /* mhfu_hook_flush_caches */
#include "mhfu/ai.h"           /* mhfu_on_ai_overlay_loaded */
#include "mhfu/events.h"
#include "internal.h"          /* mhfu_anchor_regs_t, cave, trampoline */
#include "xram.h"
#include "addresses.gen.h"

#include <pspiofilemgr.h>
#include <psputils.h>          /* sceKernelDcacheWritebackRange */
#include <string.h>
#include <stdio.h>             /* snprintf */

#define DESCR_MAX      0x200u        /* resource-table rows the engine scans */

/* Game-thread entry detour on the model-setup function, which reads the raw buffer
 * every frame. On its first call after a section load the buffer is pristine, so the
 * overwrite lands before the body transforms it. A worker-thread copy races the GE
 * list builder and crashes. Overlay code: install while JIT-cold. */
#define MODEL_SETUP_DISP0     mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20)
#define MODEL_SETUP_DISP1     mips_sw(MIPS_REG_RA, 0x1C, MIPS_REG_SP)

inject_entry_t mhfu_inject_tab[MHFU_INJECT_MAX];
static int     g_hook_installed;

static inline uint32_t rd32(uint32_t a) { return *(volatile uint32_t *)a; }
static inline int gmem_ok(uint32_t a) { return a >= MHFU_MAIN_RAM && a < MHFU_USER_RAM_END; }

static void arm_model_setup_hook(void);
static int  install_model_setup_hook(void);

static inject_entry_t *find_entry(uint32_t file_id)
{
    for (int i = 0; i < MHFU_INJECT_MAX; i++)
        if (mhfu_inject_tab[i].used && mhfu_inject_tab[i].file_id == file_id) return &mhfu_inject_tab[i];
    return 0;
}

/* Sub table of the edited PAC in e->buf: u32 count, then (off, size) pairs. */
static void parse_subs(inject_entry_t *e)
{
    e->nsubs = 0;
    if (!e->buf || e->file_size < 8) return;
    uint32_t cnt = rd32(e->buf);
    if (cnt > MHFU_MAX_SUBS) cnt = MHFU_MAX_SUBS;
    for (uint32_t t = 0; t < cnt; t++) {
        uint32_t off  = rd32(e->buf + 4 + t * 8);
        uint32_t size = rd32(e->buf + 8 + t * 8);
        if (off == 0 || size == 0 || off + size > e->file_size) {
            e->subs[t].off = e->subs[t].size = e->subs[t].magic = 0;
            e->nsubs = t + 1;
            continue;
        }
        e->subs[t].off   = off;
        e->subs[t].size  = size;
        e->subs[t].magic = rd32(e->buf + off);
        e->nsubs = t + 1;
    }
}

static int read_file(inject_entry_t *e)
{
    SceUID fd = sceIoOpen(e->path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_log("[inject] open FAILED %s rc=0x%08X", e->path, (unsigned)fd); return -1; }
    SceOff sz = sceIoLseek(fd, 0, PSP_SEEK_END);
    sceIoLseek(fd, 0, PSP_SEEK_SET);
    uint32_t fsz = (uint32_t)sz;
    if (fsz < 0x40) { sceIoClose(fd); mhfu_log("[inject] file too small %u", (unsigned)fsz); return -2; }

    if (e->buf == 0 || fsz > e->buf_cap) {
        uint32_t cap = (fsz + 0xFFFu) & ~0xFFFu;
        uint32_t b = mhfu_xram_alloc(cap);
        if (!b) { sceIoClose(fd); mhfu_log("[inject] xram exhausted (need %uKB)", (unsigned)(cap / 1024)); return -3; }
        e->buf = b; e->buf_cap = cap;
    }
    int rd = sceIoRead(fd, (void *)e->buf, (int)fsz);
    sceIoClose(fd);
    if (rd != (int)fsz) { mhfu_log("[inject] read short rc=0x%08X", (unsigned)rd); return -4; }
    e->file_size = fsz;
    parse_subs(e);

    /* <path>.orig identifies the species: a buffer must match the original before we
     * write the edit, so a look-alike monster never matches. */
    char opath[176];
    snprintf(opath, sizeof(opath), "%s.orig", e->path);
    SceUID ofd = sceIoOpen(opath, PSP_O_RDONLY, 0);
    if (ofd >= 0) {
        if (e->obuf == 0 || fsz > e->obuf_cap) {
            uint32_t cap = (fsz + 0xFFFu) & ~0xFFFu;
            uint32_t b = mhfu_xram_alloc(cap);
            if (b) { e->obuf = b; e->obuf_cap = cap; }
        }
        if (e->obuf) {
            int ord = sceIoRead(ofd, (void *)e->obuf, (int)fsz);
            if (ord != (int)fsz) { e->obuf = 0; mhfu_log("[inject] .orig read short"); }
            else mhfu_log("[inject] loaded ORIGINAL %s for unique match", opath);
        }
        sceIoClose(ofd);
    } else {
        e->obuf = 0;
        mhfu_log("[inject] no .orig (%s) -> falling back to 64B-prefix match", opath);
    }

    e->has_diff = 0;
    if (e->obuf && e->buf) {
        for (uint32_t o = 0; o + 4 <= fsz; o += 4) {
            uint32_t ew = rd32(e->buf + o), ow = rd32(e->obuf + o);
            if (ew != ow) {
                e->has_diff = 1; e->diff_off = o; e->diff_orig = ow; e->diff_edit = ew;
                break;
            }
        }
        if (e->has_diff)
            mhfu_log("[inject] diff fingerprint @+0x%X orig=0x%08X edit=0x%08X",
                     (unsigned)e->diff_off, (unsigned)e->diff_orig, (unsigned)e->diff_edit);
        else
            mhfu_log("[inject] WARNING edit == orig (no geometry change?)");
    }
    return 0;
}

/* If buf is a registered file's raw PAC and still pristine (256-byte header and the
 * diff word match the original), overwrite all of it with the edit. 1 on overwrite. */
static int try_overwrite_buffer(uint32_t buf)
{
    if (!gmem_ok(buf)) return 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->buf || !e->obuf || !e->has_diff || e->file_size < 0x40) continue;
        if (!gmem_ok(buf + e->file_size - 1)) continue;
        uint32_t g = e->file_size < 256 ? e->file_size : 256;
        if (memcmp((const void *)buf, (const void *)e->obuf, g) != 0) continue;  /* not our species */
        uint32_t cur = rd32(buf + e->diff_off);
        if (cur == e->diff_edit) return 0;     /* already our edit */
        if (cur != e->diff_orig) continue;     /* mid-write or someone else's */
        memcpy((void *)buf, (const void *)e->buf, e->file_size);
        e->applied_addr = buf;
        sceKernelDcacheWritebackRange((void *)buf, e->file_size);
        e->hits++;
        mhfu_log("[inject] OVERWROTE raw buffer file=%u @0x%08X (%uB)",
                 (unsigned)e->file_id, (unsigned)buf, (unsigned)e->file_size);
        return 1;
    }
    return 0;
}

/* If a0 is a relocate species' raw buffer (its first 256 bytes equal the original's),
 * point a0 at the grown PAC; the trampoline reloads a0 from the frame. 1 if redirected. */
static int try_redirect_pkg(mhfu_anchor_regs_t *regs)
{
    uint32_t a0 = regs->a0;
    if (!gmem_ok(a0) || !gmem_ok(a0 + 255)) return 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->relocate || e->orig_size < 0x40) continue;
        if (memcmp((const void *)a0, e->orig_hdr, 256) != 0) continue; /* not our species */
        /* stage on first use; the emulator staged at registration */
        if (!e->buf && !mhfu_inject_stage_relocate(e)) continue;   /* failed: engine uses native */
        regs->a0 = e->buf;
        if (e->redirects == 0) {
            mhfu_log("[inject] RELOCATE redirect file=%u a0 0x%08X -> 0x%08X (grown %uB)",
                     (unsigned)e->file_id, (unsigned)a0, (unsigned)e->buf, (unsigned)e->file_size);
            mhfu_xram_log("[realhw] redirect FIRED file=%u a0=0x%08X -> 0x%08X grown=%uB",
                          (unsigned)e->file_id, (unsigned)a0, (unsigned)e->buf,
                          (unsigned)e->file_size);
        }
        e->redirects++;
        return 1;
    }
    return 0;
}

/* Trampoline dispatcher on get_subresource(pkg a0, type a1). EBOOT code, so no
 * overlay-timing race. */
extern "C" void mhfu_inject_subresource_dispatch_c(const mhfu_anchor_regs_t *regs)
{
    if (try_redirect_pkg((mhfu_anchor_regs_t *)regs)) return;
    try_overwrite_buffer(regs->a0);
}

int mhfu_inject_register(uint32_t file_id, const char *path)
{
    if (!path || !path[0]) return -1;
    inject_entry_t *e = find_entry(file_id);
    if (!e) {
        for (int i = 0; i < MHFU_INJECT_MAX; i++)
            if (!mhfu_inject_tab[i].used) { e = &mhfu_inject_tab[i]; break; }
        if (!e) { mhfu_log("[inject] table full"); return -2; }
        memset(e, 0, sizeof(*e));
        e->used = 1;
        e->file_id = file_id;
    }
    snprintf(e->path, sizeof(e->path), "%s", path);

    if (!g_hook_installed) {
        int rc = mhfu_hook_trampoline(MHFU_GET_SUBRESOURCE, (uint32_t)&mhfu_inject_subresource_dispatch_c);
        g_hook_installed = (rc == 0);
        mhfu_log("[inject] get_subresource trampoline @0x%08X rc=%d", MHFU_GET_SUBRESOURCE, rc);
    }

    /* The detour keeps the raw buffer on our edit; it cannot fix the render alone. */
    arm_model_setup_hook();

    mhfu_log("[inject] register file=%u path=%s (getsub seam @0x%08X + detour @0x%08X)",
             (unsigned)file_id, e->path, MHFU_GET_SUBRESOURCE, MHFU_MODEL_SETUP);
    return 0;
}

/* Read a PAC into a fresh xram block; base, 0 on failure. */
static uint32_t load_pac_to_xram(const char *path, uint32_t *out_sz)
{
    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_log("[inject] reloc open FAILED %s rc=0x%08X", path, (unsigned)fd); return 0; }
    SceOff sz = sceIoLseek(fd, 0, PSP_SEEK_END);
    sceIoLseek(fd, 0, PSP_SEEK_SET);
    uint32_t fsz = (uint32_t)sz;
    if (fsz < 0x40) { sceIoClose(fd); return 0; }
    uint32_t cap = (fsz + 0xFFFu) & ~0xFFFu;
    uint32_t b = mhfu_xram_alloc(cap);
    if (!b) { sceIoClose(fd); mhfu_log("[inject] reloc xram exhausted (%uKB)", (unsigned)(cap / 1024)); return 0; }
    int rd = sceIoRead(fd, (void *)b, (int)fsz);
    sceIoClose(fd);
    if (rd != (int)fsz) { mhfu_log("[inject] reloc read short %s", path); return 0; }
    *out_sz = fsz;
    return b;
}

/* On a real PSP this is where volatile gets locked. One attempt per entry. */
extern "C" int mhfu_inject_stage_relocate(inject_entry_t *e)
{
    if (e->buf) return 1;
    if (e->staged) return 0;          /* attempted and failed */
    e->staged = 1;
    uint32_t gsz = 0;
    e->buf = load_pac_to_xram(e->path, &gsz);
    if (!e->buf) {
        mhfu_log("[inject] reloc grown stage FAILED %s", e->path);
        mhfu_xram_log("[realhw] relocate STAGE FAILED file=%u (volatile lock/read?) -> native",
                      (unsigned)e->file_id);
        return 0;
    }
    e->file_size = gsz;
    e->buf_cap   = (gsz + 0xFFFu) & ~0xFFFu;
    parse_subs(e);
    mhfu_log("[inject] RELOCATE staged file=%u grown=%uB@0x%08X",
             (unsigned)e->file_id, (unsigned)gsz, (unsigned)e->buf);
    mhfu_xram_log("[realhw] relocate STAGED (in-quest) file=%u grown=%uB@0x%08X",
                  (unsigned)e->file_id, (unsigned)gsz, (unsigned)e->buf);
    return 1;
}

/* Keeps only the original's 256-byte header for the match. The emulator stages the
 * grown PAC now; the real PSP defers it so volatile stays free for savedata. */
int mhfu_inject_register_relocate(uint32_t file_id, const char *grown_path,
                                  const char *orig_path)
{
    if (!grown_path || !orig_path) return -1;
    inject_entry_t *e = find_entry(file_id);
    if (!e) {
        for (int i = 0; i < MHFU_INJECT_MAX; i++)
            if (!mhfu_inject_tab[i].used) { e = &mhfu_inject_tab[i]; break; }
        if (!e) { mhfu_log("[inject] table full"); return -2; }
    }
    memset(e, 0, sizeof(*e));
    e->used = 1;
    e->file_id = file_id;
    e->relocate = 1;
    snprintf(e->path,      sizeof(e->path),      "%s", grown_path);
    snprintf(e->orig_path, sizeof(e->orig_path), "%s", orig_path);

    SceUID ofd = sceIoOpen(orig_path, PSP_O_RDONLY, 0);
    if (ofd < 0) { e->used = 0; mhfu_log("[inject] reloc orig open FAILED %s", orig_path); return -3; }
    int hrd = sceIoRead(ofd, e->orig_hdr, (int)sizeof(e->orig_hdr));
    SceOff osz = sceIoLseek(ofd, 0, PSP_SEEK_END);
    sceIoClose(ofd);
    if (hrd != (int)sizeof(e->orig_hdr) || osz < 0x40) {
        e->used = 0; mhfu_log("[inject] reloc orig header short %s", orig_path); return -3;
    }
    e->orig_size = (uint32_t)osz;

    if (!g_hook_installed) {
        int rc = mhfu_hook_trampoline(MHFU_GET_SUBRESOURCE, (uint32_t)&mhfu_inject_subresource_dispatch_c);
        g_hook_installed = (rc == 0);
        mhfu_log("[inject] get_subresource trampoline @0x%08X rc=%d", MHFU_GET_SUBRESOURCE, rc);
    }

    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode == XRAM_VOLATILE) {
        mhfu_log("[inject] RELOCATE register (lazy/volatile) file=%u grown=%s orig=%s (%uB)",
                 (unsigned)file_id, grown_path, orig_path, (unsigned)osz);
        mhfu_xram_log("[realhw] relocate REGISTERED (lazy) file=%u grown=%s orig_size=%uB"
                      " — volatile deferred to quest", (unsigned)file_id, grown_path, (unsigned)osz);
        return 0;
    }
    if (!mhfu_inject_stage_relocate(e)) { e->used = 0; mhfu_log("[inject] reloc eager stage FAILED"); return -3; }
    mhfu_log("[inject] RELOCATE register file=%u grown=%uB@0x%08X orig=%uB",
             (unsigned)file_id, (unsigned)e->file_size, (unsigned)e->buf, (unsigned)osz);
    return 0;
}

/* Overwrite any registered file's raw buffer in the resource table; count overwritten. */
static int scan_descriptor_overwrite(void)
{
    uint32_t base = rd32(MHFU_RESOURCE_TABLE);
    if (!gmem_ok(base)) return 0;
    int n = 0;
    for (uint32_t s = 0; s < DESCR_MAX; s++) {
        uint32_t ent = base + s * MHFU_RESOURCE_SLOT_SIZE;
        if (!gmem_ok(ent)) break;
        uint32_t buf = rd32(ent + MHFU_RESOURCE_SLOT_DATA);
        if (!gmem_ok(buf)) continue;
        n += try_overwrite_buffer(buf);
    }
    return n;
}

static uint32_t *g_model_wrapper;
static int       g_model_hook_installed;
static int       g_model_arm;           /* install triggers subscribed */

/* Model-setup entry, game thread, before the body reads the raw buffer. */
extern "C" void mhfu_inject_model_setup_dispatch_c(void)
{
    scan_descriptor_overwrite();
}

/* 18 words in a 20-word cave: save a0-a3 and ra, call the dispatch, restore, replay
 * the two displaced prologue words on the engine's frame, jump to entry + 8. */
static void build_model_setup_wrapper(uint32_t *w, uint32_t dispatch_addr)
{
    int i = 0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A2, 0x08, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A3, 0x0C, MIPS_REG_SP);
    w[i++] = mips_jal  (dispatch_addr);
    w[i++] = MIPS_NOP;                              /* delay slot */
    w[i++] = mips_lw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A2, 0x08, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A3, 0x0C, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    w[i++] = MODEL_SETUP_DISP0;
    w[i++] = MODEL_SETUP_DISP1;
    w[i++] = mips_j    (MHFU_MODEL_SETUP_RESUME);
    w[i++] = MIPS_NOP;                              /* delay slot */
    while (i < 20) w[i++] = MIPS_NOP;
}

/* Patch the model-setup entry only while it holds the original prologue (JIT-cold)
 * and an edit is registered, so a repeated trigger is a no-op. */
static int install_model_setup_hook(void)
{
    if (g_model_hook_installed) return 0;
    int any = 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++)
        if (mhfu_inject_tab[i].used && mhfu_inject_tab[i].buf && mhfu_inject_tab[i].obuf) { any = 1; break; }
    if (!any) return 0;

    uint32_t *w = g_model_wrapper;
    if (!w) {
        w = mhfu_cave_alloc(20);
        if (!w) { mhfu_log("[inject] cave exhausted for model-setup wrapper"); return -1; }
        build_model_setup_wrapper(w, (uint32_t)(uintptr_t)&mhfu_inject_model_setup_dispatch_c);
        mhfu_hook_flush_caches();
        g_model_wrapper = w;
    }

    uint32_t cur0 = *(volatile uint32_t *)MHFU_MODEL_SETUP;
    uint32_t cur1 = *(volatile uint32_t *)(MHFU_MODEL_SETUP + 4);
    if (cur0 != MODEL_SETUP_DISP0 || cur1 != MODEL_SETUP_DISP1) {
        mhfu_log("[inject] model-setup entry not original (0x%08X) — skip patch",
                 (unsigned)cur0);
        return -1;
    }
    *(volatile uint32_t *)MHFU_MODEL_SETUP       = mips_j((uint32_t)(uintptr_t)w);
    *(volatile uint32_t *)(MHFU_MODEL_SETUP + 4) = MIPS_NOP;
    mhfu_hook_flush_caches();
    g_model_hook_installed = 1;
    mhfu_log("[inject] model-setup detour (re)patched @ 0x%08X (wrapper 0x%08X)",
             MHFU_MODEL_SETUP, (unsigned)(uintptr_t)w);
    return 0;
}

/* The overlay just loaded, JIT-cold: install the detour. */
extern "C" void mhfu_inject_overlay_loaded_cb(const mhfu_ai_overlay_loaded_ctx_t * /*ctx*/)
{
    g_model_hook_installed = 0;
    install_model_setup_hook();
}

/* A section roam can re-translate the patch away: re-arm on each section entry. */
extern "C" void mhfu_inject_section_repatch_cb(const void * /*ctx*/)
{
    g_model_hook_installed = 0;
    install_model_setup_hook();
}

static void arm_model_setup_hook(void)
{
    if (g_model_arm) return;
    mhfu_on_ai_overlay_loaded(mhfu_inject_overlay_loaded_cb, 0);
    mhfu_event_register(MHFU_EVENT_MAP_SECTION_ENTERED,
                        (mhfu_event_cb_t)(void *)mhfu_inject_section_repatch_cb);
    g_model_arm = 1;
}

/* Worker tick: keep each same-size edit fresh in xram, re-read when the file changes.
 * The overwrite itself runs on the game thread. */
void mhfu_inject_tick(void)
{
    if (!mhfu_world_ms0_io_safe()) return;   /* sceIoGetstat is ms0 I/O */
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used) continue;
        if (e->relocate) continue;   /* set up at register; read_file would wipe obuf */
        SceIoStat st;
        memset(&st, 0, sizeof(st));
        if (sceIoGetstat(e->path, &st) < 0) continue;
        int changed = !e->primed
                    || e->st_size != st.st_size
                    || memcmp(&e->st_mtime, &st.sce_st_mtime, sizeof(ScePspDateTime)) != 0;
        if (!changed) continue;
        e->st_size = st.st_size; e->st_mtime = st.sce_st_mtime; e->primed = 1;
        if (read_file(e) == 0) {
            mhfu_log("[inject] loaded edit file=%u (%uB, %d subs) — game-thread detour applies on load",
                     (unsigned)e->file_id, (unsigned)e->file_size, e->nsubs);
            /* the overlay may have loaded before the file: install now */
            install_model_setup_hook();
        }
    }
}

uint32_t mhfu_inject_now(uint32_t file_id)
{
    inject_entry_t *e = find_entry(file_id);
    if (!e) return 0;
    if (read_file(e) != 0) return 0;
    e->primed = 0;
    return e->hits;
}

uint32_t mhfu_inject_locate(uint32_t file_id)
{
    inject_entry_t *e = find_entry(file_id);
    return e ? e->hits : 0;
}
