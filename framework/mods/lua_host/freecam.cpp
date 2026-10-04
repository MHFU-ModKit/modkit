/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/*
 * Freecam: flies the camera by overriding the engine's orbit-camera inputs, so
 * the engine still builds eye, matrix and GE upload itself. Toggle: double-tap
 * SELECT. Not installed, and without Lua bindings until it is: that toggle collides
 * with monster_nameplates' (see freecam_install).
 *
 * The quest camera update (MHFU_CAM_ORBIT_UPDATE) builds eye = pivot +
 * R(euler angles) * offset:
 *   - injection 1, MHFU_CAM_ORBIT_INPUTS (quest only): after pivot and angles
 *     are set, before they are consumed; writes our pivot x/z and freezes the
 *     angle temps (radians);
 *   - injection 3, MHFU_CAM_EYE_Y_STORE: eye_y follows our height, so vertical
 *     flight moves the camera only;
 *   - injection 4, MHFU_CAM_VIEW_DIR, entry (quest and village): the toggle, the
 *     pad, the village eye and look-at = eye + aim.
 */
#include <pspctrl.h>
#include <math.h>

#include "mhfu/mhfu.h"
#include "internal.h"

/* displaced instructions the stubs replay */
#define ENC_SWC1_F1_S4_44  0xE6810044u
#define ENC_LWC1_F1_S4_58  0xC6810058u
#define ENC_SWC1_F0_SP_1C  0xE7A0001Cu
#define ENC_LWC1_F0_SP_1C  0xC7A0001Cu

static volatile int   g_fc_active   = 0;
static volatile int   g_fc_inited   = 0;
static volatile int   g_fc_ang_cap  = 0;   /* quest orbit-angle seed captured        */
static float    g_fc_pivot[3] = {0,0,0};   /* camera focus x/z (eye anchor)           */
static float    g_fc_height    = 0.0f;     /* camera focus Y (eye_y anchor)           */
static float    g_fc_ang_seed[3] = {0,0,0};/* frozen orbit angles (no eye-orbit)      */
static float    g_fc_yaw       = 0.0f;     /* AIM yaw   (free-look)                    */
static float    g_fc_pitch     = 0.0f;     /* AIM pitch (free-look)                    */
static float    g_fc_move_spd  = 25.0f;
static float    g_fc_rot_spd   = 0.035f;
static float    g_fc_look_dist = 300.0f;   /* look-at distance ahead of the eye      */
static int      g_fc_prev_sel  = 0;
static int      g_fc_tap_age   = 99999;
static uint32_t *g_cam_w1 = 0;
static uint32_t *g_cam_w3 = 0;
static uint32_t *g_cam_w4 = 0;

/* injection 4, quest and village: the toggle and the pad; flies the village eye
 * (nothing else writes it) and sets the look-at to eye + aim. */
static void mhfu_lua_cam_target_c(void)
{
    SceCtrlData pad; sceCtrlPeekBufferPositive(&pad, 1);
    unsigned b = pad.Buttons;
    int sel = (b & PSP_CTRL_SELECT) ? 1 : 0;
    if (g_fc_tap_age < 1000000) g_fc_tap_age++;
    if (sel && !g_fc_prev_sel) {
        if (g_fc_tap_age < 20) { g_fc_active = !g_fc_active; g_fc_inited = 0; g_fc_ang_cap = 0; g_fc_tap_age = 1000000; }
        else g_fc_tap_age = 0;
    }
    g_fc_prev_sel = sel;
    if (!g_fc_active) return;

    float *eye = (float *)MHFU_CAM_EYE;
    float *tgt = (float *)MHFU_CAM_VIEW_EYE;
    float ox=*(float*)MHFU_CAM_OFFSET, oy=*(float*)(MHFU_CAM_OFFSET+4), oz=*(float*)(MHFU_CAM_OFFSET+8);
    int village = (ox==0.0f && oy==0.0f && oz==0.0f);   /* quest uses the offset cell */

    if (!g_fc_inited) {
        /* seed position: village flies the eye cell; quest flies the orbit pivot */
        float *src = village ? eye : (float *)MHFU_CAM_PIVOT;
        g_fc_pivot[0]=src[0]; g_fc_pivot[2]=src[2];
        g_fc_height = eye[1];
        if (village) {
            float dx=tgt[0]-eye[0], dy=tgt[1]-eye[1], dz=tgt[2]-eye[2];
            g_fc_yaw   = atan2f(dx, dz);
            g_fc_pitch = atan2f(dy, sqrtf(dx*dx+dz*dz));
        } else {
            g_fc_yaw   = atan2f(-ox, -oz);
            g_fc_pitch = atan2f(-oy, sqrtf(ox*ox+oz*oz));
        }
        g_fc_inited = 1;
    }

    if (b & PSP_CTRL_LEFT)  g_fc_yaw   -= g_fc_rot_spd;
    if (b & PSP_CTRL_RIGHT) g_fc_yaw   += g_fc_rot_spd;
    if (b & PSP_CTRL_UP)    g_fc_pitch += g_fc_rot_spd;
    if (b & PSP_CTRL_DOWN)  g_fc_pitch -= g_fc_rot_spd;
    if (g_fc_pitch >  1.4f) g_fc_pitch =  1.4f;
    if (g_fc_pitch < -1.4f) g_fc_pitch = -1.4f;

    float cp=cosf(g_fc_pitch), sp=sinf(g_fc_pitch);
    float cy=cosf(g_fc_yaw),   sy=sinf(g_fc_yaw);
    float mv=((int)pad.Ly - 128)/128.0f;
    float st=((int)pad.Lx - 128)/128.0f;
    if (mv>-0.12f && mv<0.12f) mv=0;
    if (st>-0.12f && st<0.12f) st=0;
    g_fc_pivot[0]+=(sy*mv + cy*st)*g_fc_move_spd;
    g_fc_pivot[2]+=(cy*mv - sy*st)*g_fc_move_spd;
    if (b & PSP_CTRL_RTRIGGER) g_fc_height+=g_fc_move_spd;
    if (b & PSP_CTRL_LTRIGGER) g_fc_height-=g_fc_move_spd;

    /* the quest's eye comes from the orbit pipeline (injections 1 and 3) */
    if (village) { eye[0]=g_fc_pivot[0]; eye[1]=g_fc_height; eye[2]=g_fc_pivot[2]; }

    /* from the live eye cell, so it matches the rendered eye in both modes */
    float d=g_fc_look_dist;
    tgt[0]=eye[0]+cp*sy*d; tgt[1]=eye[1]+sp*d; tgt[2]=eye[2]+cp*cy*d;
}

/* injection 3: eye_y = our height + the rotated offset's y. */
static void mhfu_lua_cam_eye_y_c(uint32_t s4base, uint32_t player)
{
    if (!g_fc_active) return;
    float *eye_y = (float *)(s4base + MHFU_CAM_ORBIT_EYE + 4);
    float py = *(float *)(player + MHFU_ENTITY_POSITION + 4);
    *eye_y = g_fc_height + (*eye_y - py);
}

/* injection 1, quest only: our position into the orbit pivot, and the orbit
 * angles frozen so the d-pad (aim, injection 4) does not also orbit the eye. */
static void mhfu_lua_cam_orbit_c(uint32_t cam_sp, uint32_t player)
{
    (void)player;
    if (!g_fc_active) return;
    float *ang = (float *)(cam_sp + MHFU_CAM_ORBIT_FRAME_ANGLES);
    float *piv = (float *)MHFU_CAM_PIVOT;
    if (!g_fc_ang_cap) {                 /* capture orbit-angle seed once */
        g_fc_ang_seed[0]=ang[0]; g_fc_ang_seed[1]=ang[1]; g_fc_ang_seed[2]=ang[2];
        g_fc_ang_cap = 1;
    }
    piv[0]=g_fc_pivot[0]; piv[2]=g_fc_pivot[2];
    ang[0]=g_fc_ang_seed[0]; ang[1]=g_fc_ang_seed[1]; ang[2]=g_fc_ang_seed[2];
}

/* injection 1 stub; replays addiu v1,sp,0x180 and addiu v0,sp,0x17C. */
static int build_cam_w1(uint32_t *w)
{
    int i=0;
    w[i++] = mips_move (MIPS_REG_T0, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, (int16_t)-0x20);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_T0, 0x14, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A1, 0x10, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x0C, MIPS_REG_SP);
    w[i++] = mips_move (MIPS_REG_A0, MIPS_REG_T0);
    w[i++] = mips_move (MIPS_REG_A1, MIPS_REG_S3);
    w[i++] = mips_jal  ((uint32_t)(uintptr_t)&mhfu_lua_cam_orbit_c);
    w[i++] = MIPS_NOP;
    w[i++] = mips_lw   (MIPS_REG_A0, 0x0C, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A1, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_T0, 0x14, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    w[i++] = mips_addiu(MIPS_REG_V1, MIPS_REG_SP, 0x180);
    w[i++] = mips_j    (MHFU_CAM_ORBIT_INPUTS_RESUME);
    w[i++] = mips_addiu(MIPS_REG_V0, MIPS_REG_SP, 0x17C);
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

/* injection 3 stub */
static int build_cam_w3(uint32_t *w)
{
    int i=0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, (int16_t)-0x20);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = ENC_SWC1_F0_SP_1C;
    w[i++] = ENC_SWC1_F1_S4_44;
    w[i++] = mips_move (MIPS_REG_A0, MIPS_REG_S4);
    w[i++] = mips_move (MIPS_REG_A1, MIPS_REG_S3);
    w[i++] = mips_jal  ((uint32_t)(uintptr_t)&mhfu_lua_cam_eye_y_c);
    w[i++] = MIPS_NOP;
    w[i++] = ENC_LWC1_F0_SP_1C;
    w[i++] = mips_lw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    w[i++] = ENC_LWC1_F1_S4_58;
    w[i++] = mips_j    (MHFU_CAM_EYE_Y_STORE_RESUME);
    w[i++] = MIPS_NOP;
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

/* injection 4 stub: calls mhfu_lua_cam_target_c, then replays the displaced prologue. */
static int build_cam_w4(uint32_t *w)
{
    int i=0;
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, (int16_t)-0x20);
    w[i++] = mips_sw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A2, 0x0C, MIPS_REG_SP);
    w[i++] = mips_sw   (MIPS_REG_A3, 0x08, MIPS_REG_SP);
    w[i++] = mips_jal  ((uint32_t)(uintptr_t)&mhfu_lua_cam_target_c);
    w[i++] = MIPS_NOP;
    w[i++] = mips_lw   (MIPS_REG_A0, 0x10, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A1, 0x14, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A2, 0x0C, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_A3, 0x08, MIPS_REG_SP);
    w[i++] = mips_lw   (MIPS_REG_RA, 0x18, MIPS_REG_SP);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, 0x20);
    w[i++] = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, (int16_t)-0x70);  /* displaced 0 */
    w[i++] = mips_sw   (MIPS_REG_RA, 0x0C, MIPS_REG_SP);            /* displaced 1 */
    w[i++] = mips_j    (MHFU_CAM_VIEW_DIR_RESUME);
    w[i++] = MIPS_NOP;
    while (i < 20) w[i++] = MIPS_NOP;
    return i;
}

/* Not called: its double-tap SELECT toggle collides with monster_nameplates. To
 * restore freecam, call it from lua_host_init (queued for the JIT-cold window) and
 * give one of the two another toggle. */
__attribute__((unused))
static int freecam_install(void)
{
    if (g_cam_w1) return 0;
    uint32_t *w1 = mhfu_cave_alloc(20);
    if (!w1) { mhfu_log("[freecam] cave exhausted (1)"); return -1; }
    build_cam_w1(w1); mhfu_hook_flush_caches(); g_cam_w1 = w1;
    uint32_t o0 = mips_addiu(MIPS_REG_V1, MIPS_REG_SP, 0x180);
    uint32_t o1 = mips_addiu(MIPS_REG_V0, MIPS_REG_SP, 0x17C);
    mhfu_hook_rc_t r0 = mhfu_hook_word_when_quiet(MHFU_CAM_ORBIT_INPUTS+0, o0, mips_j((uint32_t)(uintptr_t)w1), "freecam");
    mhfu_hook_rc_t r1 = mhfu_hook_word_when_quiet(MHFU_CAM_ORBIT_INPUTS+4, o1, MIPS_NOP, "freecam");

    uint32_t *w3 = mhfu_cave_alloc(20);
    if (!w3) { mhfu_log("[freecam] cave exhausted (3)"); return -1; }
    build_cam_w3(w3); mhfu_hook_flush_caches(); g_cam_w3 = w3;
    mhfu_hook_rc_t r4 = mhfu_hook_word_when_quiet(MHFU_CAM_EYE_Y_STORE+0, ENC_SWC1_F1_S4_44, mips_j((uint32_t)(uintptr_t)w3), "freecam");
    mhfu_hook_rc_t r5 = mhfu_hook_word_when_quiet(MHFU_CAM_EYE_Y_STORE+4, ENC_LWC1_F1_S4_58, MIPS_NOP, "freecam");

    uint32_t *w4 = mhfu_cave_alloc(20);
    if (!w4) { mhfu_log("[freecam] cave exhausted (4)"); return -1; }
    build_cam_w4(w4); mhfu_hook_flush_caches(); g_cam_w4 = w4;
    uint32_t t0w = mips_addiu(MIPS_REG_SP, MIPS_REG_SP, (int16_t)-0x70);
    uint32_t t1w = mips_sw(MIPS_REG_RA, 0x0C, MIPS_REG_SP);
    mhfu_hook_rc_t r6 = mhfu_hook_word_when_quiet(MHFU_CAM_VIEW_DIR+0, t0w, mips_j((uint32_t)(uintptr_t)w4), "freecam");
    mhfu_hook_rc_t r7 = mhfu_hook_word_when_quiet(MHFU_CAM_VIEW_DIR+4, t1w, MIPS_NOP, "freecam");

    mhfu_log("[freecam] queued: orbit@0x%08X(%d,%d) eyeY@0x%08X(%d,%d) lookdir@0x%08X(%d,%d)",
             MHFU_CAM_ORBIT_INPUTS,(int)r0,(int)r1, MHFU_CAM_EYE_Y_STORE,(int)r4,(int)r5, MHFU_CAM_VIEW_DIR,(int)r6,(int)r7);
    return (r0==MHFU_HOOK_OK&&r1==MHFU_HOOK_OK&&r4==MHFU_HOOK_OK&&r5==MHFU_HOOK_OK
            &&r6==MHFU_HOOK_OK&&r7==MHFU_HOOK_OK) ? 0 : -1;
}
