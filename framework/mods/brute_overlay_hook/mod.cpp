/*
 * brute_overlay_hook: hands the joint builder the Brute Tigrex port's real skeleton when
 * the engine passes it a bad skeleton pointer.
 */

#include <string.h>
#include "mhfu/mhfu.h"
#include "mhfu/mips.h"
#include "addresses.gen.h"

#define MOD_ID  "brute_overlay_hook"

/* The joint builder takes (entity a0, bone array a1, skeleton blob a2) and reads the
 * bone count from a2. On a Brute quest a2 has no skeleton magic, the count is garbage
 * and the joint-array allocation crashes. a1 is a2 plus a fixed offset, so it is off too.
 *
 * The patch goes on the builder's `move s2, a2` after the prologue: patching the entry
 * would run its `sw ra` delay slot on an unadjusted sp. There s5 = entity, s4 = a1 and
 * a2 is live; the displaced delay slot (mov.s f22, f12) is harmless. The stub resumes
 * at MHFU_JOINT_BUILDER_RESUME.
 *
 * The relocated Brute PAC sits at the injection window base; its 46-bone skeleton sub is at +0x40. */
#define BRUTE_SKEL          (MHFU_INJECT_XRAM + 0x40u)
#define SKEL_MAGIC          0xC0000000u

#define JB_STUB_INSNS  12
static uint32_t g_jb_stub[JB_STUB_INSNS]
    __attribute__((aligned(64)));   /* cache-line aligned */

/* R-type encoder: rd = rs <fn> rt. mips.h lacks subu/addu/xor. */
#define MIPS_R3(rs, rt, rd, fn) \
    ((uint32_t)(((rs) << 21) | ((rt) << 16) | ((rd) << 11) | (fn)))
#define MIPS_FN_SUBU  0x23u
#define MIPS_FN_ADDU  0x21u
#define MIPS_FN_XOR   0x26u

/* Frame-free and branchless: the big-monster construction thread's stack reaches into
 * the PRX, so a jal to C here corrupts it, and a branch trips the JIT's block markers.
 *   t5 = [a2] ^ SKEL_MAGIC          0 for a valid skeleton (native monsters pass through)
 *   t4 = BRUTE_SKEL + (s4 - a2)     the bone array rebased on the real skeleton
 *   s2 = a2 (the displaced insn); if t5: s2 = BRUTE_SKEL, s4 = t4
 * Clobbers t0-t5, which the builder tolerates at the resume point. */
static void build_jb_stub(void)
{
    int i = 0;
    uint32_t *s = g_jb_stub;
    s[i++] = mips_lw(MIPS_REG_T0, 0, MIPS_REG_A2);
    s[i++] = mips_lui(MIPS_REG_T1, SKEL_MAGIC >> 16);
    s[i++] = MIPS_R3(MIPS_REG_T0, MIPS_REG_T1, MIPS_REG_T5, MIPS_FN_XOR);
    s[i++] = mips_lui(MIPS_REG_T2, BRUTE_SKEL >> 16);
    s[i++] = mips_ori(MIPS_REG_T2, MIPS_REG_T2, BRUTE_SKEL & 0xFFFFu);
    s[i++] = MIPS_R3(MIPS_REG_S4, MIPS_REG_A2, MIPS_REG_T3, MIPS_FN_SUBU);
    s[i++] = MIPS_R3(MIPS_REG_T2, MIPS_REG_T3, MIPS_REG_T4, MIPS_FN_ADDU);
    s[i++] = mips_move(MIPS_REG_S2, MIPS_REG_A2);
    s[i++] = mips_movn(MIPS_REG_S2, MIPS_REG_T2, MIPS_REG_T5);
    s[i++] = mips_movn(MIPS_REG_S4, MIPS_REG_T4, MIPS_REG_T5);
    s[i++] = mips_j(MHFU_JOINT_BUILDER_RESUME);
    s[i++] = MIPS_NOP;                               /* j delay slot */

    while (i < JB_STUB_INSNS) s[i++] = MIPS_NOP;

    mhfu_hook_flush_caches();
}

/* Call-site overrides that need only a0 (the framework builds the wrapper). An entry
 * stays disabled until its call site is known. */
typedef enum { OVR_CALL_SITE = 0, OVR_ENTRY_PATCH } override_kind_t;
typedef int  override_mode_t;

typedef struct {
    const char      *name;
    uint32_t         call_site;
    uint32_t         target_fn;
    override_kind_t  kind;
    override_mode_t  mode;
    void           (*handler)(uint32_t a0);
    int              enabled;
} override_entry_t;

static void fk_bufsz_log(uint32_t a0)
{
    /* log only, until sizeof(Joint) is known to rescale the FK buffer for 46 bones */
    mhfu_log("[bovh] fk_bufsz called a0=0x%08X", (unsigned)a0);
}

static override_entry_t g_overrides[] = {
    /* FK output-buffer sizing */
    {
        .name      = "fk_bufsz_log",
        .call_site = 0x00000000u,   /* open: a jal site of the FK walker */
        .target_fn = MHFU_FK_WALKER,
        .kind      = OVR_CALL_SITE,
        .mode      = MHFU_HOOK_WRAP_PREFIX,
        .handler   = fk_bufsz_log,
        .enabled   = 0,
    },
    { .name = 0 },
};

static int install_table_overrides(void)
{
    int ok = 0, skip = 0;
    for (override_entry_t *e = g_overrides; e->name; e++) {
        if (!e->enabled) { skip++; continue; }
        mhfu_hook_rc_t rc = mhfu_hook_call(
            e->call_site, e->target_fn, e->handler, e->mode, MOD_ID);
        if (rc == MHFU_HOOK_OK) {
            mhfu_log("[bovh] queued '%s' @0x%08X", e->name, (unsigned)e->call_site);
            ok++;
        } else {
            mhfu_log("[bovh] FAILED '%s' rc=%d", e->name, (int)rc);
        }
    }
    if (skip)
        mhfu_log("[bovh] %d table overrides skipped (OPEN addresses)", skip);
    return ok;
}

static int brute_overlay_hook_init(void)
{
    mhfu_log("[bovh] init — joint_builder fix for Brute Tigrex (46 bones)");

    /* EBOOT code: queued for the JIT-cold title/menu window */
    build_jb_stub();
    uint32_t orig_patch = mhfu_mem_read_u32(MHFU_JOINT_BUILDER_HOOK);
    mhfu_hook_rc_t rc = mhfu_hook_word_when_quiet(
        MHFU_JOINT_BUILDER_HOOK,
        orig_patch,
        mips_j((uint32_t)(uintptr_t)g_jb_stub),
        MOD_ID);
    if (rc == MHFU_HOOK_OK)
        mhfu_log("[bovh] joint_builder fix queued @0x%08X -> stub 0x%08X",
                 MHFU_JOINT_BUILDER_HOOK, (unsigned)(uintptr_t)g_jb_stub);
    else
        mhfu_log("[bovh] joint_builder fix FAILED rc=%d", (int)rc);

    install_table_overrides();

    return 0;
}

static void brute_overlay_hook_shutdown(void)
{
    mhfu_hook_release(MOD_ID);
}

MHFU_MOD(.id      = MOD_ID,
         .version = "0.2",
         .needs   = 0,   /* pure C, runs with or without lua_host */
         .conflicts = 0,
         .init    = brute_overlay_hook_init,
         .shutdown = brute_overlay_hook_shutdown);
