/* lua_host's shared state between its files; not part of the SDK. */
#ifndef MHFU_LUA_HOST_H
#define MHFU_LUA_HOST_H

#include <stdint.h>

extern "C" {
#include "lua.h"
#include "lualib.h"
#include "lauxlib.h"
}

/* --- the VM (vm.cpp) --- */
extern lua_State   *mhfu_lua_vm;
extern volatile int mhfu_lua_have_tick;   /* a global mhfu_tick is defined */
extern volatile int mhfu_lua_ready;       /* setup done: events may enter the VM */
extern unsigned     mhfu_lua_slab_peak;   /* high-water mark, for the log */

int      mhfu_lua_vm_init(void);          /* the lock and the slab; -1 on failure */
int      mhfu_lua_vm_open(void);          /* the state on the slab; -1 on failure */
void     mhfu_lua_vm_close(void);
unsigned mhfu_lua_slab_live(void);
/* Every entry into the VM, from any thread, sits between these; 0 = VM down. */
int      mhfu_lua_enter(void);
void     mhfu_lua_leave(void);

/* --- the event bridge (events.cpp) --- */
int  mhfu_lua_exec_start(void);           /* the exec thread; -1 on failure */
void mhfu_lua_exec_stop(void);            /* game-thread events pass through from now */
void mhfu_lua_exec_free(void);

/* --- memory-stick scripts (scripts.cpp) --- */
void mhfu_lua_scripts_init(void);         /* the read buffer */
int  mhfu_lua_load_dir(lua_State *L);     /* runs every script; returns how many ran */
void mhfu_lua_prime_tracked(void);        /* records each script's stat for hot reload */
void mhfu_lua_hot_reload_scan(void);

/* --- the mhfu table: each adds its functions and constants to the table on top --- */
void mhfu_lua_bind_memory(lua_State *L);
void mhfu_lua_bind_entity(lua_State *L);
void mhfu_lua_bind_quest(lua_State *L);
void mhfu_lua_bind_inject(lua_State *L);
void mhfu_lua_bind_input(lua_State *L);
void mhfu_lua_bind_ai(lua_State *L);
void mhfu_lua_bind_em(lua_State *L);
void mhfu_lua_bind_capture(lua_State *L);
void mhfu_lua_bind_freecam(lua_State *L);
void mhfu_lua_bind_combat(lua_State *L);
void mhfu_lua_bind_events(lua_State *L);

#endif /* MHFU_LUA_HOST_H */
