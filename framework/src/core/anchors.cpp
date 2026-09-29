/* The quest-event anchors: detours on two EBOOT stores that raise QUEST_BEGINNING and
 * QUEST_ENTERED through the event registry. */
#include <pspthreadman.h>

#include "mhfu/hooks.h"
#include "mhfu/mips.h"
#include "mhfu/log.h"
#include "internal.h"
#include "addresses.gen.h"

static const char *const OWNER = "mhfu_events";

typedef struct {
    uint32_t     addr;
    mhfu_wrap_fn pre;
    uint32_t     expect[2];   /* the displaced words, read once the EBOOT is resident */
    int          rc;          /* the last patch's; a failure is logged when it changes */
} anchor_t;

static anchor_t g_anchors[] = {
    {MHFU_QUEST_TIMER_INIT, mhfu_event_dispatch_quest_beginning, {0, 0}, 1},
    {MHFU_AREA_INDEX_STORE, mhfu_event_dispatch_quest_entered,   {0, 0}, 1},
};
#define N_ANCHORS ((int)(sizeof(g_anchors) / sizeof(g_anchors[0])))

static uint32_t word_at(uint32_t addr) { return *(volatile uint32_t *)addr; }

/* both anchors hold their sv.q stores (opcode 0x3E) */
static int resident(void)
{
    for (int i = 0; i < N_ANCHORS; i++)
        if ((word_at(g_anchors[i].addr) >> 26) != 0x3E) return 0;
    return 1;
}

static void patch(anchor_t *a)
{
    int rc = mhfu_hook_detour_now(a->addr, a->expect[0], a->expect[1], a->pre, OWNER);
    if (rc == MHFU_HOOK_OK || rc != a->rc) mhfu_log("[events] anchor @0x%08lx rc=%d", (unsigned long)a->addr, rc);
    a->rc = rc;
}

/* Waits until the EBOOT is resident at both anchors, installs, then watches forever and
 * re-patches an anchor whose original words are back, reusing its wrapper. */
extern "C" int mhfu_event_install_thread(SceSize args, void *argp)
{
    (void)args; (void)argp;
    for (int attempt = 0;; attempt++) {
        if (attempt >= 600) {
            mhfu_log("[events] anchors never became resident; quest events are off");
            return 0;
        }
        if (resident()) {
            sceKernelDelayThread(500 * 1000);
            if (resident()) break;
        }
        sceKernelDelayThread(100 * 1000);
    }
    for (int i = 0; i < N_ANCHORS; i++) {
        g_anchors[i].expect[0] = word_at(g_anchors[i].addr);
        g_anchors[i].expect[1] = word_at(g_anchors[i].addr + 4);
        patch(&g_anchors[i]);
    }
    for (;;) {
        sceKernelDelayThread(500 * 1000);
        for (int i = 0; i < N_ANCHORS; i++) {
            anchor_t *a = &g_anchors[i];
            if (word_at(a->addr) == a->expect[0] && word_at(a->addr + 4) == a->expect[1])
                patch(a);
        }
    }
    return 0;
}
