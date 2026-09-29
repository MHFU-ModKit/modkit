/* The exec thread and the hand-over to it (marshal.h). */
#include "mhfu/log.h"
#include "marshal.h"

#ifndef MHFU_HOST
#include <pspthreadman.h>
#else
/* the host test supplies these */
extern "C" {
typedef int SceUID;
typedef unsigned int SceSize;
typedef int (*SceKernelThreadEntry)(SceSize args, void *argp);
SceUID sceKernelCreateSema(const char *name, unsigned int attr, int init, int max, void *opt);
int    sceKernelDeleteSema(SceUID id);
int    sceKernelSignalSema(SceUID id, int n);
int    sceKernelWaitSema(SceUID id, int n, unsigned int *timeout);
SceUID sceKernelCreateThread(const char *name, SceKernelThreadEntry entry, int prio,
                             int stack, unsigned int attr, void *opt);
int    sceKernelStartThread(SceUID id, SceSize len, void *argp);
int    sceKernelGetThreadId(void);
}
#endif

static SceUID g_prod_sema = -1;   /* one producer at a time; the exec thread never takes it */
static SceUID g_req_sema  = -1;   /* the exec thread waits here for work */
static SceUID g_resp_sema = -1;   /* the producer waits here for the answer */
static SceUID g_exec_tid  = -1;
static mhfu_lua_serve_fn       g_serve;
static mhfu_lua_req_t *volatile g_pending;   /* on the producer's stack */
static volatile int            g_ready;

/* Serves one request per loop, and always answers. */
static int exec_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    for (;;) {
        sceKernelWaitSema(g_req_sema, 1, 0);
        mhfu_lua_req_t *q = g_pending;
        if (q) g_serve(q);
        sceKernelSignalSema(g_resp_sema, 1);
    }
    return 0;
}

uint32_t mhfu_lua_marshal(mhfu_lua_req_t *q)
{
    q->out = q->in;
    if (!g_ready || mhfu_lua_vm_held()) return q->in;
    sceKernelWaitSema(g_prod_sema, 1, 0);
    if (g_ready) {                        /* a panic may have stopped it meanwhile */
        g_pending = q;
        sceKernelSignalSema(g_req_sema, 1);
        sceKernelWaitSema(g_resp_sema, 1, 0);
        g_pending = 0;
    }
    sceKernelSignalSema(g_prod_sema, 1);
    return q->out;
}

/* Priority above the worker's, so it answers the blocked game thread first. */
int mhfu_lua_exec_start(mhfu_lua_serve_fn serve)
{
    g_serve     = serve;
    g_prod_sema = sceKernelCreateSema("mhfu_lua_prod", 0, 1, 1, 0);
    g_req_sema  = sceKernelCreateSema("mhfu_lua_req",  0, 0, 1, 0);
    g_resp_sema = sceKernelCreateSema("mhfu_lua_resp", 0, 0, 1, 0);
    if (g_prod_sema < 0 || g_req_sema < 0 || g_resp_sema < 0) {
        mhfu_log("[lua_host] marshal sema create FAILED"); return -1;
    }
    SceUID ex = sceKernelCreateThread("mhfu_lua_exec", exec_thread, 0x11, 0x10000, 0, 0);
    if (ex < 0) { mhfu_log("[lua_host] exec CreateThread FAILED"); return -1; }
    g_exec_tid = ex;
    sceKernelStartThread(ex, 0, 0);
    g_ready = 1;
    return 0;
}

void mhfu_lua_exec_stop(void) { g_ready = 0; }

/* A panic on the exec thread happens inside a request: its producer is waiting. */
void mhfu_lua_exec_panic(void)
{
    g_ready = 0;
    if (g_exec_tid < 0 || sceKernelGetThreadId() != g_exec_tid) return;
    mhfu_lua_req_t *q = g_pending;
    if (q) q->out = q->in;
    sceKernelSignalSema(g_resp_sema, 1);
}

void mhfu_lua_exec_free(void)
{
    SceUID *semas[] = { &g_prod_sema, &g_req_sema, &g_resp_sema };
    for (SceUID *s : semas)
        if (*s >= 0) { sceKernelDeleteSema(*s); *s = -1; }
}
