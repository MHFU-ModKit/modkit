/*
 * Live model injection: the engine builds a big monster from our edited PAC instead of
 * the one it loaded, with no disk edits.
 *
 * Every raw sub-resource reaches the EBOOT parsers through get_subresource(pkg, type),
 * called on the raw PAC before the overlay restructures it. A detour there either
 * redirects pkg to a grown PAC (relocate) or overwrites the raw buffer in place (same
 * size). Both match by content against the original file, so no file id is needed.
 */
#include "mhfu/inject.h"
#include "mhfu/log.h"
#include "mhfu/world.h"        /* mhfu_world_ms0_io_safe */
#include "mhfu/mips.h"
#include "mhfu/ai.h"           /* mhfu_on_ai_overlay_loaded */
#include "mhfu/events.h"
#include "internal.h"
#include "xram.h"
#include "addresses.gen.h"

#include <pspiofilemgr.h>
#include <psputils.h>          /* sceKernelDcacheWritebackRange */
#include <string.h>
#include <stdio.h>             /* snprintf */

static const char k_owner[] = "mhfu_inject";

#define DESCR_MAX      0x200u        /* resource-table rows the engine scans */

/* The prologue words each detour displaces; the detour refuses an address that holds
 * anything else. */
#define GETSUB_DISP0          mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x10)
#define GETSUB_DISP1          mips_sw(MIPS_REG_RA, 0x0C, MIPS_REG_SP)
#define MODEL_SETUP_DISP0     mips_addiu(MIPS_REG_SP, MIPS_REG_SP, -0x20)
#define MODEL_SETUP_DISP1     mips_sw(MIPS_REG_RA, 0x1C, MIPS_REG_SP)

inject_entry_t  mhfu_inject_tab[MHFU_INJECT_MAX];
static int      g_getsub_hooked;
static int      g_model_armed;          /* overlay and section events subscribed */
static uint32_t g_joint_skel;           /* the skeleton handed to the joint fix, 0 none */

static inline uint32_t rd32(uint32_t a) { return *(volatile uint32_t *)a; }
static inline int gmem_ok(uint32_t a) { return a >= MHFU_MAIN_RAM && a < MHFU_USER_RAM_END; }

static void install_model_setup_hook(void);

/* Clears what e last loaded, keeping its registration and its xram blocks, which a bump
 * allocator cannot free and the next read reuses. */
static void reset_entry(inject_entry_t *e)
{
    if (g_joint_skel && e->buf && g_joint_skel - e->buf < e->buf_cap) {
        mhfu_joint_fix_skeleton(0);
        g_joint_skel = 0;
    }
    e->primed = 0; e->file_size = 0; e->nsubs = 0; e->has_diff = 0;
    e->redirects = 0; e->ready = 0; e->tried = 0;
}

static inject_entry_t *find_entry(uint32_t file_id)
{
    for (int i = 0; i < MHFU_INJECT_MAX; i++)
        if (mhfu_inject_tab[i].used && mhfu_inject_tab[i].file_id == file_id) return &mhfu_inject_tab[i];
    return 0;
}

/* file_id's entry, or a free one claimed for it; 0 when the table is full. */
static inject_entry_t *claim_entry(uint32_t file_id)
{
    inject_entry_t *e = find_entry(file_id);
    if (e) return e;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        e = &mhfu_inject_tab[i];
        if (e->used) continue;
        reset_entry(e);
        e->used = 1;
        e->file_id = file_id;
        e->path[0] = 0;
        return e;
    }
    mhfu_log("[inject] table full");
    return 0;
}

/* Sub table of the PAC in e->buf: u32 count, then (off, size) pairs. */
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

/* Reads path into the xram block *buf, allocating one when it is missing or too small;
 * the file's size, 0 on failure. */
static uint32_t read_into(const char *path, uint32_t *buf, uint32_t *cap)
{
    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_log("[inject] open FAILED %s rc=0x%08X", path, (unsigned)fd); return 0; }
    uint32_t fsz = (uint32_t)sceIoLseek(fd, 0, PSP_SEEK_END);
    sceIoLseek(fd, 0, PSP_SEEK_SET);
    if (fsz < 0x40) { sceIoClose(fd); mhfu_log("[inject] %s too small (%uB)", path, (unsigned)fsz); return 0; }
    if (!*buf || fsz > *cap) {
        uint32_t c = (fsz + 0xFFFu) & ~0xFFFu;
        uint32_t b = mhfu_xram_alloc(c);
        if (!b) { sceIoClose(fd); mhfu_log("[inject] xram exhausted (need %uKB)", (unsigned)(c / 1024)); return 0; }
        *buf = b; *cap = c;
    }
    int rd = sceIoRead(fd, (void *)*buf, (int)fsz);
    sceIoClose(fd);
    if (rd != (int)fsz) { mhfu_log("[inject] read short %s rc=0x%08X", path, (unsigned)rd); return 0; }
    return fsz;
}

/* Same-size edit: the edit and <path>.orig, which identifies the species so a look-alike
 * monster never matches. 0 once the edit is ready to apply. */
static int read_file(inject_entry_t *e)
{
    e->has_diff = 0;                         /* no overwrite from a half-read buffer */
    e->file_size = read_into(e->path, &e->buf, &e->buf_cap);
    if (!e->file_size) return -1;
    parse_subs(e);

    char opath[176];
    snprintf(opath, sizeof(opath), "%s.orig", e->path);
    uint32_t osz = read_into(opath, &e->obuf, &e->obuf_cap);
    if (!osz) { mhfu_log("[inject] no usable %s: the edit is not applied", opath); return -2; }
    if (osz != e->file_size) {
        mhfu_log("[inject] %s is %uB, the edit %uB: a same-size edit only (else inject_relocate)",
                 opath, (unsigned)osz, (unsigned)e->file_size);
        return -3;
    }

    for (uint32_t o = 0; o + 4 <= e->file_size; o += 4) {
        uint32_t ew = rd32(e->buf + o), ow = rd32(e->obuf + o);
        if (ew != ow) {
            e->has_diff = 1; e->diff_off = o; e->diff_orig = ow; e->diff_edit = ew;
            break;
        }
    }
    if (!e->has_diff) { mhfu_log("[inject] WARNING edit == orig (no geometry change?)"); return -4; }
    mhfu_log("[inject] diff fingerprint @+0x%X orig=0x%08X edit=0x%08X",
             (unsigned)e->diff_off, (unsigned)e->diff_orig, (unsigned)e->diff_edit);
    return 0;
}

/* Reads a same-size edit when forced or when its size or mtime changed; 0 on a new
 * read, 1 when unchanged, <0 on failure. */
static int refresh(inject_entry_t *e, int force)
{
    SceIoStat st;
    memset(&st, 0, sizeof(st));
    if (sceIoGetstat(e->path, &st) < 0) return -1;
    if (!force && e->primed && e->st_size == st.st_size
        && memcmp(&e->st_mtime, &st.sce_st_mtime, sizeof(ScePspDateTime)) == 0)
        return 1;
    e->st_size = st.st_size; e->st_mtime = st.sce_st_mtime; e->primed = 1;
    if (read_file(e) != 0) return -2;
    mhfu_log("[inject] loaded edit file=%u (%uB, %d subs), applied on the game thread",
             (unsigned)e->file_id, (unsigned)e->file_size, e->nsubs);
    install_model_setup_hook();              /* the overlay may have loaded before the file */
    return 0;
}

/* If buf is a registered file's raw PAC and still pristine (256-byte header and the
 * diff word match the original), overwrite all of it with the edit. 1 on overwrite. */
static int try_overwrite_buffer(uint32_t buf)
{
    if (!gmem_ok(buf)) return 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || e->relocate || !e->has_diff) continue;
        if (!gmem_ok(buf + e->file_size - 1)) continue;
        uint32_t g = e->file_size < 256 ? e->file_size : 256;
        if (memcmp((const void *)buf, (const void *)e->obuf, g) != 0) continue;  /* not our species */
        uint32_t cur = rd32(buf + e->diff_off);
        if (cur == e->diff_edit) return 0;     /* already our edit */
        if (cur != e->diff_orig) continue;     /* mid-write or someone else's */
        memcpy((void *)buf, (const void *)e->buf, e->file_size);
        sceKernelDcacheWritebackRange((void *)buf, e->file_size);
        e->hits++;
        mhfu_log("[inject] OVERWROTE raw buffer file=%u @0x%08X (%uB)",
                 (unsigned)e->file_id, (unsigned)buf, (unsigned)e->file_size);
        return 1;
    }
    return 0;
}

extern "C" void mhfu_inject_relocate_ready(inject_entry_t *e, uint32_t buf, uint32_t cap,
                                           uint32_t size)
{
    e->buf = buf; e->buf_cap = cap; e->file_size = size;
    e->ready = 1; e->tried = 0;
    parse_subs(e);
    for (int t = 0; t < e->nsubs; t++) {
        if (e->subs[t].magic != MHFU_SKELETON_MAGIC) continue;
        g_joint_skel = buf + e->subs[t].off;
        mhfu_joint_fix_skeleton(g_joint_skel);
        break;
    }
}

/* Still registered: staged again at its next use. */
extern "C" void mhfu_inject_relocate_drop(inject_entry_t *e)
{
    reset_entry(e);
    e->buf = 0; e->buf_cap = 0;
}

/* Stages a relocate entry's grown PAC into xram; on a real PSP this is where volatile
 * gets locked. One attempt until the entry is released. 1 once ready. */
static int stage_relocate(inject_entry_t *e)
{
    if (e->ready) return 1;
    if (e->tried) return 0;
    uint32_t gsz = read_into(e->path, &e->buf, &e->buf_cap);   /* keeps a new block on failure */
    if (!gsz) {
        e->tried = 1;
        mhfu_xram_log("[realhw] relocate STAGE FAILED file=%u (volatile lock/read?) -> native",
                      (unsigned)e->file_id);
        return 0;
    }
    mhfu_inject_relocate_ready(e, e->buf, e->buf_cap, gsz);
    mhfu_log("[inject] RELOCATE staged file=%u grown=%uB@0x%08X",
             (unsigned)e->file_id, (unsigned)gsz, (unsigned)e->buf);
    mhfu_xram_log("[realhw] relocate STAGED file=%u grown=%uB@0x%08X",
                  (unsigned)e->file_id, (unsigned)gsz, (unsigned)e->buf);
    return 1;
}

/* If a0 is a relocate species' raw buffer (its first 256 bytes equal the original's),
 * point a0 at the grown PAC. 1 if redirected. */
static int try_redirect_pkg(mhfu_regs_t *regs)
{
    uint32_t a0 = regs->a0;
    if (!gmem_ok(a0) || !gmem_ok(a0 + 255)) return 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (!e->used || !e->relocate || e->orig_size < 0x40) continue;
        if (memcmp((const void *)a0, e->orig_hdr, 256) != 0) continue; /* not our species */
        if (!stage_relocate(e)) continue;   /* failed: the engine uses the native PAC */
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

/* get_subresource(pkg a0, type a1), game thread. EBOOT code, so no overlay-timing race. */
static void subresource_pre(mhfu_regs_t *regs)
{
    if (try_redirect_pkg(regs)) return;
    try_overwrite_buffer(regs->a0);
}

static void install_getsub_hook(void)
{
    if (g_getsub_hooked) return;
    mhfu_hook_rc_t rc = mhfu_hook_detour_now(MHFU_GET_SUBRESOURCE, GETSUB_DISP0, GETSUB_DISP1,
                                             subresource_pre, k_owner);
    g_getsub_hooked = (rc == MHFU_HOOK_OK);
    mhfu_log("[inject] get_subresource detour @0x%08X rc=%d", MHFU_GET_SUBRESOURCE, (int)rc);
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

/* Game-thread entry detour on the model-setup function, which reads the raw buffer every
 * frame. On its first call after a section load the buffer is pristine, so the overwrite
 * lands before the body transforms it. A worker-thread copy races the GE list builder and
 * crashes. */
static void model_setup_pre(mhfu_regs_t * /*regs*/)
{
    scan_descriptor_overwrite();
}

/* Overlay code: patched only while it holds the original prologue, i.e. while the overlay
 * is loaded and JIT-cold, and only once a same-size edit is ready. */
static void install_model_setup_hook(void)
{
    int any = 0;
    for (int i = 0; i < MHFU_INJECT_MAX; i++)
        if (mhfu_inject_tab[i].used && mhfu_inject_tab[i].has_diff) { any = 1; break; }
    if (!any) return;
    static int s_last_rc = 1;               /* log each change, not every section */
    mhfu_hook_rc_t rc = mhfu_hook_detour_now(MHFU_MODEL_SETUP, MODEL_SETUP_DISP0,
                                             MODEL_SETUP_DISP1, model_setup_pre, k_owner);
    if ((int)rc != s_last_rc)
        mhfu_log("[inject] model-setup detour @0x%08X rc=%d", MHFU_MODEL_SETUP, (int)rc);
    s_last_rc = (int)rc;
}

/* The overlay just loaded, JIT-cold: install the detour. */
static void overlay_loaded(const mhfu_ai_overlay_loaded_ctx_t * /*ctx*/)
{
    install_model_setup_hook();
}

/* A section roam can re-translate the patch away: re-arm on each section entry. */
static void section_entered(const mhfu_map_section_ctx_t * /*ctx*/)
{
    install_model_setup_hook();
}

int mhfu_inject_register(uint32_t file_id, const char *path)
{
    if (!path || !path[0]) return -1;
    inject_entry_t *e = claim_entry(file_id);
    if (!e) return -2;
    if (e->relocate || strcmp(e->path, path) != 0) reset_entry(e);   /* read it afresh */
    e->relocate = 0;
    snprintf(e->path, sizeof(e->path), "%s", path);

    install_getsub_hook();
    /* The detour keeps the raw buffer on our edit; it cannot fix the render alone. */
    if (!g_model_armed) {
        int r1 = mhfu_on_ai_overlay_loaded(overlay_loaded, 0, k_owner);
        int r2 = mhfu_on_map_section_entered(section_entered, 0, k_owner);
        if (r1 || r2) mhfu_log("[inject] model-setup events rc=%d/%d", r1, r2);
        g_model_armed = 1;
    }
    mhfu_log("[inject] register file=%u path=%s", (unsigned)file_id, e->path);
    return 0;
}

/* Keeps only the original's 256-byte header for the match. The emulator stages the
 * grown PAC now; the real PSP defers it so volatile stays free for savedata. */
int mhfu_inject_register_relocate(uint32_t file_id, const char *grown_path,
                                  const char *orig_path)
{
    if (!grown_path || !orig_path) return -1;
    inject_entry_t *e = claim_entry(file_id);
    if (!e) return -2;
    reset_entry(e);
    snprintf(e->path, sizeof(e->path), "%s", grown_path);

    SceUID ofd = sceIoOpen(orig_path, PSP_O_RDONLY, 0);
    if (ofd < 0) { e->used = 0; mhfu_log("[inject] reloc orig open FAILED %s", orig_path); return -3; }
    int hrd = sceIoRead(ofd, e->orig_hdr, (int)sizeof(e->orig_hdr));
    SceOff osz = sceIoLseek(ofd, 0, PSP_SEEK_END);
    sceIoClose(ofd);
    if (hrd != (int)sizeof(e->orig_hdr) || osz < 0x40) {
        e->used = 0; mhfu_log("[inject] reloc orig header short %s", orig_path); return -3;
    }
    e->relocate = 1;
    e->orig_size = (uint32_t)osz;

    install_getsub_hook();

    if (!mhfu_xram_mode) mhfu_xram_init();
    if (mhfu_xram_mode == XRAM_VOLATILE) {
        mhfu_log("[inject] RELOCATE register (lazy/volatile) file=%u grown=%s orig=%s (%uB)",
                 (unsigned)file_id, grown_path, orig_path, (unsigned)osz);
        mhfu_xram_log("[realhw] relocate REGISTERED (lazy) file=%u grown=%s orig_size=%uB,"
                      " volatile deferred to quest", (unsigned)file_id, grown_path, (unsigned)osz);
        return 0;
    }
    if (!stage_relocate(e)) { e->used = 0; mhfu_log("[inject] reloc eager stage FAILED"); return -3; }
    mhfu_log("[inject] RELOCATE register file=%u grown=%uB@0x%08X orig=%uB",
             (unsigned)file_id, (unsigned)e->file_size, (unsigned)e->buf, (unsigned)osz);
    return 0;
}

/* Worker tick: keep each same-size edit fresh in xram, re-read when the file changes.
 * The overwrite itself runs on the game thread. */
void mhfu_inject_tick(void)
{
    if (!mhfu_world_ms0_io_safe()) return;   /* sceIoGetstat is ms0 I/O */
    for (int i = 0; i < MHFU_INJECT_MAX; i++) {
        inject_entry_t *e = &mhfu_inject_tab[i];
        if (e->used && !e->relocate) refresh(e, 0);
    }
}

uint32_t mhfu_inject_now(uint32_t file_id)
{
    inject_entry_t *e = find_entry(file_id);
    if (!e) return 0;
    if (e->relocate) {
        /* restaged into its block now on the emulator, at its next use on a real PSP */
        reset_entry(e);
        if (mhfu_xram_mode == XRAM_RAW && !stage_relocate(e)) return 0;
        return e->hits;
    }
    if (refresh(e, 1) != 0) return 0;
    return e->hits;
}

uint32_t mhfu_inject_locate(uint32_t file_id)
{
    inject_entry_t *e = find_entry(file_id);
    return e ? e->hits : 0;
}
