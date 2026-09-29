/*
 * mhfu_boot.prx: the real-PSP entry, a small kernel plugin that loads mhfu_framework.prx from its
 * own directory once the game shows the title or a menu. PPSSPP loads the framework directly.
 */
#include <pspkernel.h>
#include <pspsysmem.h>
#include <pspiofilemgr.h>
#include <pspthreadman.h>
#include <pspsdk.h>

#include "addresses.gen.h"
#include "mhfu/world.h"

/* Kernel mode: when plugins load the user partition is full (0x800200D9), the kernel one is not. */
PSP_MODULE_INFO("mhfu_boot", PSP_MODULE_KERNEL, 1, 1);
PSP_MAIN_THREAD_ATTR(0);

#define FRAMEWORK_NAME "mhfu_framework.prx"
#define FALLBACK_PATH  "ms0:/PSP/PLUGINS/mhfu_framework/" FRAMEWORK_NAME
/* Load at the title or a menu: the framework installs its code hooks only while the JIT is cold
 * there, so a load in-area installs none. */
/* Load anyway this many seconds after the settle if no menu is seen. */
#define LOAD_FALLBACK_S   60
/* The first ms0 I/O waits out the boot's disc read, during which it faults. */
#define BOOT_SETTLE_US (15 * 1000 * 1000)

/* libc-free helpers: this PRX links no newlib */
static unsigned slen(const char *s) { unsigned n = 0; while (s[n]) n++; return n; }
static char *u2d(char *p, unsigned v)
{
    char t[12]; int i = 0;
    if (!v) t[i++] = '0';
    while (v) { t[i++] = (char)('0' + v % 10); v /= 10; }
    while (i) *p++ = t[--i];
    return p;
}
static char *puts_(char *p, const char *s) { while (*s) *p++ = *s++; return p; }

static void wlog(const char *line, int n)
{
    SceUID fd = sceIoOpen("ms0:/PSP/mhfu_boot.txt",
                          PSP_O_WRONLY | PSP_O_CREAT | PSP_O_APPEND, 0777);
    if (fd >= 0) { sceIoWrite(fd, line, n); sceIoClose(fd); }
}
static void log_msg(const char *s) { char b[160]; char *p = puts_(b, s); *p++ = '\n'; wlog(b, (int)(p - b)); }
static void log_kv(const char *s, unsigned v) { char b[160]; char *p = puts_(b, s); p = u2d(p, v); *p++ = '\n'; wlog(b, (int)(p - b)); }
static void log_path(const char *s, const char *path) { char b[320]; char *p = puts_(b, s); p = puts_(p, path); *p++ = '\n'; wlog(b, (int)(p - b)); }

static char g_path[256];

/* The framework's path: this plugin's directory (from argp) plus FRAMEWORK_NAME, as psplink does. */
static void build_path(SceSize args, void *argp)
{
    g_path[0] = 0;
    if (argp && args > 1) {
        const char *a = (const char *)argp;
        int i = 0, slash = -1;
        while (a[i] && i < 200) { g_path[i] = a[i]; if (a[i] == '/') slash = i; i++; }
        g_path[i] = 0;
        if (slash >= 0) g_path[slash + 1] = 0;   /* dir + trailing slash */
        else g_path[0] = 0;
    }
    if (g_path[0] == 0) {
        const char *f = FALLBACK_PATH; int i = 0;
        while (f[i]) { g_path[i] = f[i]; i++; } g_path[i] = 0;
        return;
    }
    { unsigned dl = slen(g_path); const char *f = FRAMEWORK_NAME; int i = 0;
      while (f[i] && dl + i < 255) { g_path[dl + i] = f[i]; i++; } g_path[dl + i] = 0; }
}

static int boot_thread(SceSize args, void *argp)
{
    build_path(args, argp);
    sceKernelDelayThread(BOOT_SETTLE_US);
    log_path("[boot] framework=", g_path);

    int i;
    for (i = 0; i < 600; i++) {   /* up to ~10 min, 1 s poll */
        unsigned char scr = *(volatile unsigned char *)MHFU_SCREEN_STATE;
        int quiet = (scr == MHFU_WORLD_SCREEN_MENU || scr == MHFU_WORLD_SCREEN_TITLE);
        if (quiet || i >= LOAD_FALLBACK_S) {
            log_kv(quiet ? "[boot] quiet menu load, screen_state="
                         : "[boot] fallback load, screen_state=", scr);
            /* K1 cleared around the load so the path pointer passes the kernel's check */
            unsigned int k1 = pspSdkSetK1(0);
            SceUID mod = sceKernelLoadModule(g_path, 0, NULL);
            if (mod >= 0) {
                int st = sceKernelStartModule(mod, args, argp, NULL, NULL);
                pspSdkSetK1(k1);
                log_kv("[boot] loadmodule OK modid=", (unsigned)mod);
                log_kv("[boot] startmodule rc=", (unsigned)st);
                log_msg("[boot] framework loaded — done");
                return 0;
            }
            pspSdkSetK1(k1);
            log_kv("[boot] loadmodule FAILED rc=", (unsigned)mod);
            return 0;   /* a load failure does not heal, so no retry */
        }
        if ((i % 5) == 0) log_kv("[boot] waiting for menu, screen_state=", scr);
        sceKernelDelayThread(1000 * 1000);
    }
    log_msg("[boot] GAVE UP — never reached in-area (screen_state never == 17)");
    return 0;
}

int module_start(SceSize args, void *argp)
{
    SceUID th = sceKernelCreateThread("mhfu_boot", boot_thread, 0x18, 0x2000, 0, NULL);
    if (th >= 0) sceKernelStartThread(th, args, argp);
    return 0;
}

int module_stop(SceSize args, void *argp) { (void)args; (void)argp; return 0; }
void _exit(int status) { (void)status; }
