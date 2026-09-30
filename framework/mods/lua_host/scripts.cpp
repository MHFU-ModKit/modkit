/* Mods are .lua files in LUA_MODS_DIR: run at boot, re-run when one changes. */
#include <pspsysmem.h>
#include <pspiofilemgr.h>
#include <stdio.h>
#include <string.h>

#include "mhfu/mhfu.h"
#include "lua_host.h"

#define LUA_MODS_DIR "ms0:/PSP/PLUGINS/mhfu_framework/mods"
/* Per-file read buffer, allocated at init to keep it off the PRX load image.
 * A script over this size is skipped with only one boot-log line; keep every
 * script (and the port runtime library) under 96 KB. */
#define G_FILEBUF_SZ (96 * 1024)
static char  *g_filebuf;
static SceUID g_filebuf_uid = -1;

/* Hot reload tracks MAX_TRACKED scripts with names under NAME_CAP bytes; any other script
 * never runs, since an untracked one would re-run on every scan. */
#define MAX_TRACKED 16
#define NAME_CAP    64

typedef char script_path_t[sizeof(LUA_MODS_DIR) + NAME_CAP];

static void script_path(script_path_t &out, const char *name)
{
    snprintf(out, sizeof(out), "%s/%.*s", LUA_MODS_DIR, NAME_CAP - 1, name);
}

void mhfu_lua_scripts_init(void)
{
    g_filebuf_uid = sceKernelAllocPartitionMemory(
        2 /* PSP_MEMORY_PARTITION_USER */, "mhfu_lua_filebuf",
        PSP_SMEM_Low, G_FILEBUF_SZ, 0);
    g_filebuf = (g_filebuf_uid >= 0) ? (char *)sceKernelGetBlockHeadAddr(g_filebuf_uid) : 0;
    if (!g_filebuf) mhfu_log("[lua_host] filebuf alloc FAILED rc=0x%08X — scripts won't load",
                             (unsigned)g_filebuf_uid);
}

static int ends_with_lua(const char *s)
{
    int n = (int)strlen(s);
    if (n < 5) return 0;                 /* need at least "x.lua" */
    if (s[0] == '_') return 0;           /* '_'-prefixed = private partial (e.g. _prelude) */
    return s[n-4] == '.'
        && (s[n-3]|0x20) == 'l'
        && (s[n-2]|0x20) == 'u'
        && (s[n-1]|0x20) == 'a';
}

/* Read, compile and run one script by basename. The caller holds the VM (setup
 * is single-threaded; hot reload takes the lock). 0 ok / -1 err. */
static int load_lua_file(lua_State *L, const char *name)
{
    script_path_t path;
    script_path(path, name);
    if (!g_filebuf) { mhfu_log("[lua_host] no file scratch — skip %s", name); return -1; }
    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0);
    if (fd < 0) { mhfu_log("[lua_host] open FAILED %s rc=0x%08X", path, (unsigned)fd); return -1; }
    int n = sceIoRead(fd, g_filebuf, G_FILEBUF_SZ - 1);
    sceIoClose(fd);
    if (n < 0) { mhfu_log("[lua_host] read FAILED %s rc=0x%08X", name, (unsigned)n); return -1; }
    if (n >= (int)(G_FILEBUF_SZ - 1)) {
        mhfu_log("[lua_host] %s too big (>%uB) — skipped", name, (unsigned)(G_FILEBUF_SZ - 1));
        return -1;
    }
    g_filebuf[n] = 0;

    char chunk[NAME_CAP + 1];
    snprintf(chunk, sizeof(chunk), "@%.*s", NAME_CAP - 1, name);   /* '@': a file name */
    if (luaL_loadbuffer(L, g_filebuf, (size_t)n, chunk) != LUA_OK) {
        mhfu_log("[lua_host] compile FAILED %s: %s", name, lua_tostring(L, -1));
        lua_pop(L, 1);
        return -1;
    }
    if (lua_pcall(L, 0, 0, 0) != LUA_OK) {
        mhfu_log("[lua_host] run FAILED %s: %s", name, lua_tostring(L, -1));
        lua_pop(L, 1);
        return -1;
    }
    mhfu_log("[lua_host] loaded mod %s (%dB)", name, n);
    return 0;
}

/* ------------------------------------------------------------ hot reload
 * A script whose (size, mtime) changed is re-run in the same VM: its
 * mhfu.on_*(fn) calls replace the stored handlers, so the installed hooks call
 * the new closures with no reinstall. One shared global env: a script's module
 * locals reset on reload, two scripts on one event means the last wins, and a
 * deleted script's handlers stay registered. */
typedef struct {
    char           name[NAME_CAP];
    SceOff         size;
    ScePspDateTime mtime;
} tracked_t;
static tracked_t g_tracked[MAX_TRACKED];
static int       g_ntracked;
static int       g_refusal_logged;

static int find_tracked(const char *name)
{
    for (int i = 0; i < g_ntracked; i++)
        if (strcmp(g_tracked[i].name, name) == 0) return i;
    return -1;
}

/* name's slot, added when new; -1 past the cap or for a name that does not fit. */
static int track_slot(const char *name)
{
    int i = find_tracked(name);
    if (i >= 0) return i;
    if (g_ntracked >= MAX_TRACKED || strlen(name) >= NAME_CAP) return -1;
    snprintf(g_tracked[g_ntracked].name, NAME_CAP, "%s", name);
    return g_ntracked++;
}

static void log_refusal(const char *name)
{
    mhfu_log("[lua_host] %s not run: at most %d scripts, names under %d characters",
             name, MAX_TRACKED, NAME_CAP);
    g_refusal_logged = 1;
}

int mhfu_lua_load_dir(lua_State *L)
{
    SceUID d = sceIoDopen(LUA_MODS_DIR);
    if (d < 0) {
        mhfu_log("[lua_host] mods dir absent (%s) rc=0x%08X", LUA_MODS_DIR, (unsigned)d);
        return 0;
    }
    int loaded = 0;
    SceIoDirent ent;
    for (;;) {
        memset(&ent, 0, sizeof(ent));
        int r = sceIoDread(d, &ent);
        if (r <= 0) break;                              /* end of dir / error */
        if (FIO_S_ISDIR(ent.d_stat.st_mode)) continue;  /* skip subdirs */
        if (!ends_with_lua(ent.d_name)) continue;
        if (track_slot(ent.d_name) < 0) { log_refusal(ent.d_name); continue; }
        if (load_lua_file(L, ent.d_name) == 0) loaded++;
    }
    sceIoDclose(d);
    return loaded;
}

/* After the boot load, so the first poll does not see every file as changed. */
void mhfu_lua_prime_tracked(void)
{
    for (int i = 0; i < g_ntracked; i++) {
        script_path_t path;
        script_path(path, g_tracked[i].name);
        SceIoStat st;
        memset(&st, 0, sizeof(st));
        if (sceIoGetstat(path, &st) < 0) continue;
        g_tracked[i].size  = st.st_size;
        g_tracked[i].mtime = st.sce_st_mtime;
    }
}

/* Worker thread. The stat is recorded before the reload, so a script that fails
 * to compile is not retried every poll. A new script is loaded while there is room. */
void mhfu_lua_hot_reload_scan(void)
{
    /* Memory Stick I/O while the savedata utility loads or saves freezes the PSP. */
    if (!mhfu_world_ms0_io_safe()) return;
    SceUID d = sceIoDopen(LUA_MODS_DIR);
    if (d < 0) return;
    int reloaded = 0;
    SceIoDirent ent;
    for (;;) {
        memset(&ent, 0, sizeof(ent));
        if (sceIoDread(d, &ent) <= 0) break;
        if (FIO_S_ISDIR(ent.d_stat.st_mode)) continue;
        if (!ends_with_lua(ent.d_name)) continue;

        int idx = find_tracked(ent.d_name);
        int fresh = idx < 0;
        if (fresh && (idx = track_slot(ent.d_name)) < 0) {
            if (!g_refusal_logged) log_refusal(ent.d_name);
            continue;
        }
        script_path_t path;
        script_path(path, ent.d_name);
        SceIoStat st;
        memset(&st, 0, sizeof(st));
        if (sceIoGetstat(path, &st) < 0) continue;
        tracked_t *t = &g_tracked[idx];
        if (!fresh && t->size == st.st_size
            && memcmp(&t->mtime, &st.sce_st_mtime, sizeof(ScePspDateTime)) == 0)
            continue;
        t->size  = st.st_size;                 /* recorded first (no retry spin) */
        t->mtime = st.sce_st_mtime;

        if (!mhfu_lua_enter()) break;          /* VM busy/dead — try later */
        mhfu_log("[lua_host] hot-reload %s ...", ent.d_name);
        if (load_lua_file(mhfu_lua_vm, ent.d_name) == 0) {
            lua_getglobal(mhfu_lua_vm, "mhfu_tick");
            mhfu_lua_have_tick = lua_isfunction(mhfu_lua_vm, -1);
            lua_pop(mhfu_lua_vm, 1);
            lua_gc(mhfu_lua_vm, LUA_GCCOLLECT, 0);      /* reclaim old closures */
            reloaded++;
        }
        mhfu_lua_leave();
    }
    sceIoDclose(d);
    if (reloaded)
        mhfu_log("[lua_host] hot-reloaded %d mod(s), live=%uB",
                 reloaded, mhfu_lua_slab_live());
}
