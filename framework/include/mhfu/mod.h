/* Mod descriptor. A mod declares itself with MHFU_MOD(); the framework resolves `needs` and
 * `conflicts`, then calls each init() in dependency order and shutdown() in reverse.
 *
 *   static int  mymod_init(void)     { ... return 0; }
 *   static void mymod_shutdown(void) { ... }
 *   MHFU_MOD(.id = "my_mod", .version = "1.0", .conflicts = "other_mod",
 *            .init = mymod_init, .shutdown = mymod_shutdown);
 *
 * init() must not assume another mod has run; declare the order with `needs`. */
#ifndef MHFU_MOD_H
#define MHFU_MOD_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *id;
    const char *version;
    const char *needs;       /* space-separated mod ids, or NULL */
    const char *conflicts;   /* space-separated mod ids, or NULL */
    int  (*init)(void);      /* 0 = ok, negative = refuse to load */
    void (*shutdown)(void);  /* may be NULL */
} mhfu_mod_t;

/* Plain data in the `mhfu_mods` section, which the linker bounds with __start_mhfu_mods and
 * __stop_mhfu_mods; no constructor runs at load, whose order is unspecified on the PSP. */
#define MHFU_MOD(...) \
    __attribute__((used, section("mhfu_mods"), aligned(4))) \
    static const mhfu_mod_t mhfu__mod_descriptor = { __VA_ARGS__ }

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_MOD_H */
