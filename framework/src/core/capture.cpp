/* Low-fps framebuffer capture streamed to ms0: or host0: (psplink usbhostfs) on a private
 * thread, so the game never waits on it; nothing is allocated until mhfu_capture_set(1). */
#include <pspkernel.h>
#include <pspdisplay.h>
#include <pspiofilemgr.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <stdint.h>
#include <string.h>

#include "internal.h"
#include "mhfu/log.h"

/* usbhostfs.prx only registers its USB driver; psplink's shell normally starts USB, so
 * capture does it for a host0: target. */
#define CAP_HOSTFS_DRIVER "USBHostFSDriver"   /* HOSTFSDRIVER_NAME */
#define CAP_HOSTFS_PID    0x1C9               /* HOSTFSDRIVER_PID  */

#define CAP_SCREEN_W 480
#define CAP_SCREEN_H 272
#define CAP_MAGIC    0x46505350u   /* 'P''S''P''F' little-endian */

/* --- config (changed only while inactive) --- */
static int  g_cfg_scale    = 2;                          /* 1=full,2=half,…   */
static int  g_cfg_interval = 66;                         /* ms between grabs  */
static char g_cfg_path[160] = "host0:/cap/stream.bin";

/* --- live state --- */
static volatile int      g_run    = 0;   /* request: keep looping             */
static volatile int      g_active = 0;   /* thread is alive (start/stop guard) */
static SceUID            g_thread = -1;
static SceUID            g_buf_uid = -1;
static uint8_t          *g_buf    = 0;
static volatile uint32_t g_frames = 0;
static volatile uint32_t g_bytes  = 0;
static volatile int      g_last_err = 0;

/* One record per frame, back to back on one open fd, followed by len bytes of pixels in the
 * PSP format fmt (0 565, 1 5551, 2 4444, 3 8888). */
typedef struct {
    uint32_t magic;
    uint32_t frame;
    uint16_t w, h;
    uint16_t fmt, bpp;
    uint32_t len;
} cap_rec_t;

static int g_usb_up = 0;

/* host0: needs the USB link; any other path is a plain file write. */
static int path_is_host(const char *p)
{
    return (p[0] == 'h' && p[1] == 'o' && p[2] == 's' && p[3] == 't');
}

/* mkdir the directory portion of a path (one level; parent must exist). */
static void mkdir_parent(const char *path)
{
    char d[160];
    int n = 0, last = -1;
    while (path[n] && n < (int)sizeof(d) - 1) { d[n] = path[n]; if (path[n] == '/') last = n; n++; }
    if (last > 0) { d[last] = 0; sceIoMkdir(d, 0777); }
}

/* Bring the usbhostfs link up the way psplink's start_usbhost does; the host0: open is the
 * real test. */
static void cap_usb_up(void)
{
    if (g_usb_up) return;
    /* deactivate any earlier USB function first so the host re-enumerates the device */
    sceUsbDeactivate(CAP_HOSTFS_PID);
    int rb = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    int rh = sceUsbStart(CAP_HOSTFS_DRIVER, 0, 0);
    int ra = sceUsbActivate(CAP_HOSTFS_PID);
    mhfu_log("[capture] usb up: bus=0x%08X host=0x%08X activate=0x%08X", rb, rh, ra);
    g_usb_up = 1;
}

static void cap_usb_down(void)
{
    if (!g_usb_up) return;
    sceUsbDeactivate(CAP_HOSTFS_PID);
    sceUsbStop(CAP_HOSTFS_DRIVER, 0, 0);
    sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
    g_usb_up = 0;
}

/* Grab one frame, downscale, write a record. Returns 0, or <0 on a grab or write error. */
static int grab_and_write(SceUID fd)
{
    void *top = 0;
    int   bw = 0, pf = 0;
    if (sceDisplayGetFrameBuf(&top, &bw, &pf, PSP_DISPLAY_SETBUF_IMMEDIATE) < 0)
        return -1;
    if (!top || bw <= 0) return -1;

    int bpp = (pf == PSP_DISPLAY_PIXEL_FORMAT_8888) ? 4 : 2;
    int s   = g_cfg_scale; if (s < 1) s = 1; if (s > 4) s = 4;
    int ow  = CAP_SCREEN_W / s;
    int oh  = CAP_SCREEN_H / s;

    /* read through the uncached mirror: the GE writes the framebuffer behind the cache */
    uintptr_t base = ((uintptr_t)top) | 0x40000000u;
    uint8_t  *dst  = g_buf;

    if (bpp == 2) {
        const volatile uint16_t *src = (const volatile uint16_t *)base;
        uint16_t *d = (uint16_t *)dst;
        for (int y = 0; y < oh; y++) {
            const volatile uint16_t *row = src + (size_t)(y * s) * bw;
            for (int x = 0; x < ow; x++) *d++ = row[x * s];
        }
    } else {
        const volatile uint32_t *src = (const volatile uint32_t *)base;
        uint32_t *d = (uint32_t *)dst;
        for (int y = 0; y < oh; y++) {
            const volatile uint32_t *row = src + (size_t)(y * s) * bw;
            for (int x = 0; x < ow; x++) *d++ = row[x * s];
        }
    }

    cap_rec_t rec;
    rec.magic = CAP_MAGIC;
    rec.frame = g_frames;
    rec.w = (uint16_t)ow; rec.h = (uint16_t)oh;
    rec.fmt = (uint16_t)pf; rec.bpp = (uint16_t)bpp;
    rec.len = (uint32_t)(ow * oh * bpp);

    if (sceIoWrite(fd, &rec, sizeof(rec)) != (int)sizeof(rec)) return -2;
    if (sceIoWrite(fd, dst, (int)rec.len) != (int)rec.len)     return -3;

    g_frames++;
    g_bytes += sizeof(rec) + rec.len;
    return 0;
}

/* The whole capture lifecycle runs here, so a host0: open that blocks stalls only this
 * thread and a stop never frees a buffer still being written. */
static int cap_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    g_active = 1;

    int s  = g_cfg_scale; if (s < 1) s = 1; if (s > 4) s = 4;
    int ow = CAP_SCREEN_W / s, oh = CAP_SCREEN_H / s;
    unsigned need = (unsigned)(ow * oh * 4) + 64;   /* worst case 4 bpp */

    g_buf_uid = sceKernelAllocPartitionMemory(
        PSP_MEMORY_PARTITION_USER, "mhfu_capbuf", PSP_SMEM_Low, need, 0);
    if (g_buf_uid < 0) {
        mhfu_log("[capture] buf alloc FAILED rc=0x%08X (%uKB)", g_buf_uid, need / 1024);
        g_last_err = g_buf_uid; g_buf_uid = -1;
        goto done;
    }
    g_buf = (uint8_t *)sceKernelGetBlockHeadAddr(g_buf_uid);

    if (path_is_host(g_cfg_path))
        cap_usb_up();

    mkdir_parent(g_cfg_path);
    {
        SceUID fd = sceIoOpen(g_cfg_path,
                              PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
        if (fd < 0) {
            mhfu_log("[capture] open %s FAILED rc=0x%08X — is the Mac host (usbhostfs_pc) running?",
                     g_cfg_path, fd);
            g_last_err = fd;
            goto done_free;
        }
        mhfu_log("[capture] streaming -> %s  scale=%d (%dx%d) ~%dms/frame",
                 g_cfg_path, s, ow, oh, g_cfg_interval);

        while (g_run) {
            sceDisplayWaitVblankStart();      /* less tearing */
            int rc = grab_and_write(fd);
            if (rc < 0) {
                mhfu_log("[capture] grab/write err rc=%d, stopping (frames=%u)", rc, (unsigned)g_frames);
                g_last_err = rc;
                break;
            }
            int iv = g_cfg_interval - 16;     /* the vblank wait cost about a frame */
            if (iv > 0) sceKernelDelayThread(iv * 1000);
        }
        sceIoClose(fd);
        mhfu_log("[capture] stopped — %u frames, %uKB", (unsigned)g_frames, (unsigned)(g_bytes / 1024));
    }

done_free:
    if (g_buf_uid >= 0) { sceKernelFreePartitionMemory(g_buf_uid); g_buf_uid = -1; g_buf = 0; }
done:
    cap_usb_down();   /* so XMB USB mass storage works again */
    g_active = 0;
    g_thread = -1;
    g_run    = 0;
    return 0;
}

/* ---- internal.h API ---- */

extern "C" void mhfu_capture_configure(int scale, int interval_ms, const char *path)
{
    if (g_active) return;
    if (scale >= 1 && scale <= 4) g_cfg_scale = scale;
    if (interval_ms >= 10)        g_cfg_interval = interval_ms;
    if (path && path[0]) {
        unsigned n = 0; while (path[n] && n < sizeof(g_cfg_path) - 1) { g_cfg_path[n] = path[n]; n++; }
        g_cfg_path[n] = 0;
    }
}

extern "C" int mhfu_capture_set(int on)
{
    if (on) {
        if (g_active || g_thread >= 0) return 1;   /* already running */
        g_frames = 0; g_bytes = 0; g_last_err = 0;
        g_run = 1;
        SceUID th = sceKernelCreateThread("mhfu_capture",
                                          (SceKernelThreadEntry)cap_thread,
                                          0x20 /* below the Lua worker's 0x18 */,
                                          0x8000, PSP_THREAD_ATTR_USER, 0);
        if (th < 0) {
            mhfu_log("[capture] CreateThread FAILED rc=0x%08X", th);
            g_run = 0; g_last_err = th; return 0;
        }
        g_thread = th;
        sceKernelStartThread(th, 0, 0);
        return 1;
    }
    /* stop: the thread closes its fd and frees its buffer on its next loop check */
    g_run = 0;
    return 0;
}

extern "C" int mhfu_capture_status(int *frames, int *kb, int *err)
{
    if (frames) *frames = (int)g_frames;
    if (kb)     *kb     = (int)(g_bytes / 1024);
    if (err)    *err    = g_last_err;
    return g_active;
}
