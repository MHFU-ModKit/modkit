/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The exec thread (marshal.cpp): Lua allocations on the engine's game thread corrupt the heap,
 * so game-thread events hand their Lua to this thread and wait for the answer. Free of PSP
 * and Lua headers, for the host test. */
#ifndef MHFU_LUA_MARSHAL_H
#define MHFU_LUA_MARSHAL_H

#include <stdint.h>

typedef struct {
    int      kind;       /* what to run; the server's business */
    uint32_t entity;     /* entity_ptr, or the quest for a quest request */
    uint8_t  mtype;
    uint8_t  slot;
    uint16_t aux;        /* action_count (slot) / vt8_input (decided ctx) */
    uint32_t in;         /* the engine's value */
    uint32_t out;        /* the answer; in until the server sets it */
} mhfu_lua_req_t;

/* Runs one request on the exec thread. */
typedef void (*mhfu_lua_serve_fn)(mhfu_lua_req_t *q);

int  mhfu_lua_exec_start(mhfu_lua_serve_fn serve);   /* the exec thread; -1 on failure */
void mhfu_lua_exec_stop(void);                       /* marshal passes through from now */
void mhfu_lua_exec_free(void);
/* From the panic handler: stops marshalling and answers a caller blocked on the exec thread. */
void mhfu_lua_exec_panic(void);

/* Runs q on the exec thread and returns its answer, from any thread; one request at a time.
 * Passes q->in through when the exec thread is down, and when the caller is inside the VM,
 * whose exec thread would wait for it. */
uint32_t mhfu_lua_marshal(mhfu_lua_req_t *q);

/* 1 when the calling thread is inside the VM (vm.cpp). */
int mhfu_lua_vm_held(void);

#endif /* MHFU_LUA_MARSHAL_H */
