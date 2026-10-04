/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* monster_nameplates: name, registry slot and HP over every monster in view, toggled by a
 * double tap of SELECT. The example C mod: a post helper on the HUD master-draw call. */
#include "mhfu/mhfu.h"
#include "addresses.gen.h"
#include <pspctrl.h>

#define MOD_ID "monster_nameplates"

#define SCREEN_CX   240.0f   /* half the 480x272 viewport */
#define SCREEN_CY   136.0f
#define DOUBLE_TAP  20       /* HUD frames between the two SELECT presses */
#define LABEL_LEN   48       /* UTF-16 units, terminator included */
#define MAX_LABELS  16

typedef void (*text_draw_fn)(uint32_t hud_ctx, const void *desc);

/* The engine's 12-byte text descriptor; b4..adv copy its banner template. */
typedef struct {
    uint16_t x, y;
    uint8_t  b4, b5, b6, adv;
    uint32_t text;   /* UTF-16LE, 0-terminated */
} text_desc_t;

static int g_on;
static int g_tap_age = DOUBLE_TAP;
static int g_prev_select;

/* The engine's view and projection matrices (column-major), read once per HUD frame. */
static float g_view[16], g_proj[16];

static void load_matrices(void)
{
    for (int i = 0; i < 16; i++) {
        g_view[i] = mhfu_mem_read_f32(MHFU_CAM_VIEW_MATRIX + i * 4);
        g_proj[i] = mhfu_mem_read_f32(MHFU_CAM_PROJ_MATRIX + i * 4);
    }
}

static void mat_vec(const float *m, const float *v, float *out)
{
    for (int r = 0; r < 4; r++)
        out[r] = m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r] * v[3];
}

/* World point to screen pixel; 0 when it is behind the camera. */
static int project(mhfu_vec3_t p, float *sx, float *sy)
{
    float world[4] = { p.x, p.y, p.z, 1.0f }, eye[4], clip[4];
    mat_vec(g_view, world, eye);
    mat_vec(g_proj, eye, clip);
    if (clip[3] < 1.0f) return 0;
    *sx = SCREEN_CX + clip[0] / clip[3] * SCREEN_CX;
    *sy = SCREEN_CY - clip[1] / clip[3] * SCREEN_CY;
    return 1;
}

/* Appends s to the label at n; returns the new length. */
static int put_str(uint16_t *w, int n, const char *s)
{
    while (*s && n < LABEL_LEN - 1) w[n++] = (uint8_t)*s++;
    w[n] = 0;
    return n;
}

static int put_uint(uint16_t *w, int n, unsigned v)
{
    char digits[12];
    int k = 0;
    do { digits[k++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (k && n < LABEL_LEN - 1) w[n++] = (uint8_t)digits[--k];
    w[n] = 0;
    return n;
}

static uint16_t    g_labels[MAX_LABELS][LABEL_LEN];
static text_desc_t g_desc;

static void draw_label(uint32_t hud_ctx, int x, int y, const uint16_t *text)
{
    if (x < -48 || x > 528 || y < -16 || y > 288) return;
    g_desc.x = (uint16_t)x;
    g_desc.y = (uint16_t)y;
    g_desc.b4 = 0x0E; g_desc.b5 = 0x0E; g_desc.b6 = 0x00; g_desc.adv = 0x0E;
    g_desc.text = (uint32_t)(uintptr_t)text;
    ((text_draw_fn)MHFU_HUD_TEXT_DRAW)(hud_ctx, &g_desc);
}

static void poll_toggle(void)
{
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    int select = (pad.Buttons & PSP_CTRL_SELECT) != 0;
    if (g_tap_age < DOUBLE_TAP) g_tap_age++;
    if (select && !g_prev_select) {
        if (g_tap_age < DOUBLE_TAP) {
            g_on = !g_on;
            g_tap_age = DOUBLE_TAP;
            mhfu_log("[nameplates] %s", g_on ? "on" : "off");
        } else {
            g_tap_age = 0;
        }
    }
    g_prev_select = select;
}

/* After every HUD draw, with the GE's 2D list still open; a0 was the HUD context. */
static void hud_post(mhfu_regs_t *regs)
{
    poll_toggle();
    if (!g_on || mhfu_world_screen_state() != MHFU_WORLD_SCREEN_IN_AREA) return;
    load_matrices();
    int drawn = 0;
    for (int slot = 1; slot < MHFU_ENTITY_REGISTRY_COUNT && drawn < MAX_LABELS; slot++) {
        uint32_t ent = mhfu_entity_at(slot);
        uint8_t type = mhfu_entity_monster_type(ent);   /* 0 for an empty or bad slot */
        uint16_t hp = mhfu_entity_hp(ent);
        if (type == 0 || type == 0xFF || hp == 0 || hp > 60000) continue;

        mhfu_vec3_t head = mhfu_entity_pos(ent);
        float size = mhfu_entity_size(ent);
        head.y += 150.0f + (size > 0.0f ? size : 1.0f) * 120.0f;
        float sx, sy;
        if (!project(head, &sx, &sy)) continue;

        /* "Tigrex #slot hp": the slot tells identical clones apart */
        uint16_t *w = g_labels[drawn++];
        const char *name = mhfu_monster_name(type);
        int n = put_str(w, 0, name ? name : "Monster");
        n = put_str(w, n, " #");
        n = put_uint(w, n, (unsigned)slot);
        n = put_str(w, n, " ");
        put_uint(w, n, hp);
        draw_label(regs->a0, (int)sx - 20, (int)sy, w);
    }
}

static int np_init(void)
{
    mhfu_hook_rc_t rc = mhfu_hook_call(MHFU_HUD_DRAW_CALL, MHFU_HUD_DRAW, 0, hud_post, MOD_ID);
    if (rc != MHFU_HOOK_OK) {
        mhfu_log("[nameplates] install failed rc=%d", (int)rc);
        return -1;
    }
    mhfu_log("[nameplates] installed; double-tap SELECT to toggle");
    return 0;
}

MHFU_MOD(.id = MOD_ID, .version = "0.2", .init = np_init);
