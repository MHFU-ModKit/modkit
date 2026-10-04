/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Host stand-ins for marshal.cpp's PSP side: semaphores and threads on the C++ runtime, the
 * VM a mutex, and a server that doubles its input while it holds the VM. */
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

#include "../mods/lua_host/marshal.h"

namespace {

struct Sema {
    std::mutex              m;
    std::condition_variable cv;
    int                     count = 0, max = 1;
};

/* Never destroyed: the exec thread is detached and still waits on one at exit, and glibc's
 * condition-variable destructor blocks until its waiters wake, so pytest would never exit. */
Sema *const      g_semas = new Sema[8];
std::atomic<int> g_n_semas{0};
std::atomic<int> g_next_tid{100};
thread_local int t_tid = 0;

typedef int (*entry_t)(unsigned int, void *);
entry_t g_entry;
int     g_entry_tid;

int my_tid()
{
    if (!t_tid) t_tid = g_next_tid++;
    return t_tid;
}

std::mutex       g_vm;
std::atomic<int> g_vm_holder{0};
std::atomic<int> g_release_parked{0};

void vm_enter() { g_vm.lock(); g_vm_holder = my_tid(); }
void vm_leave() { g_vm_holder = 0; g_vm.unlock(); }

enum { KIND_DOUBLE = 1, KIND_PANIC = 2 };

/* Holds the VM for a while, so overlapping producers would cross their requests. */
void serve(mhfu_lua_req_t *q)
{
    vm_enter();
    std::this_thread::sleep_for(std::chrono::microseconds(100));
    if (q->kind == KIND_PANIC) {   /* as lua_host_panic: unlock, answer, park */
        vm_leave();
        mhfu_lua_exec_panic();
        while (!g_release_parked) std::this_thread::sleep_for(std::chrono::milliseconds(5));
        return;
    }
    q->out = q->in * 2;
    vm_leave();
}

} /* namespace */

int mhfu_lua_vm_held(void) { return g_vm_holder == my_tid(); }

extern "C" {

void mhfu_log(const char *, ...) {}

int sceKernelCreateSema(const char *, unsigned int, int init, int max, void *)
{
    int id = g_n_semas++;
    g_semas[id].count = init;
    g_semas[id].max = max;
    return id;
}
int sceKernelDeleteSema(int) { return 0; }
int sceKernelSignalSema(int id, int n)
{
    Sema &s = g_semas[id];
    std::lock_guard<std::mutex> lock(s.m);
    if (s.count + n > s.max) return -1;
    s.count += n;
    s.cv.notify_all();
    return 0;
}
int sceKernelWaitSema(int id, int n, unsigned int *)
{
    Sema &s = g_semas[id];
    std::unique_lock<std::mutex> lock(s.m);
    s.cv.wait(lock, [&] { return s.count >= n; });
    s.count -= n;
    return 0;
}
int sceKernelCreateThread(const char *, entry_t entry, int, int, unsigned int, void *)
{
    g_entry = entry;
    g_entry_tid = g_next_tid++;
    return g_entry_tid;
}
int sceKernelStartThread(int, unsigned int, void *)
{
    std::thread([] { t_tid = g_entry_tid; g_entry(0, nullptr); }).detach();
    return 0;
}
int sceKernelGetThreadId(void) { return my_tid(); }

int  host_start(void) { return mhfu_lua_exec_start(serve); }
void host_stop(void) { mhfu_lua_exec_stop(); }
void host_release_parked(void) { g_release_parked = 1; }

uint32_t host_marshal(int kind, uint32_t in)
{
    mhfu_lua_req_t q = {};
    q.kind = kind;
    q.in = in;
    return mhfu_lua_marshal(&q);
}

/* A thread inside the VM reaching a marshalled hook, as mhfu_tick calling into the engine. */
uint32_t host_marshal_in_vm(uint32_t in)
{
    vm_enter();
    uint32_t r = host_marshal(KIND_DOUBLE, in);
    vm_leave();
    return r;
}

} /* extern "C" */
