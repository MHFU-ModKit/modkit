/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* AI scripts from the memory stick (brain.cpp): an 'MHAI' stick container for one species row
 * (docs/formats/ai_script.md, built by `mhfu brain build`), staged in extra RAM and relocated
 * there.
 *
 * The row's header words live in the AI overlay, which every quest load re-reads, so C points
 * them at the image after each read: a monster that starts its script later runs the container's.
 * Data writes only; a monster already running keeps the script it has. On a real PSP extra RAM
 * is the volatile partition, lent for a quest only, so nothing is staged there and an install
 * fails. */
#ifndef MHFU_BRAIN_H
#define MHFU_BRAIN_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_BRAIN_MAX 8   /* rows installed at once */

/* mhfu_brain_install's failures; mhfu_brain_error words them. */
enum {
    MHFU_BRAIN_E_ARG     = -1,   /* no path */
    MHFU_BRAIN_E_STAGE   = -2,   /* unreadable, no extra RAM left, or a real PSP */
    MHFU_BRAIN_E_SHORT   = -3,   /* shorter than a container's header */
    MHFU_BRAIN_E_MAGIC   = -4,   /* not an 'MHAI' container */
    MHFU_BRAIN_E_VERSION = -5,   /* a version this does not read */
    MHFU_BRAIN_E_LOAD    = -6,   /* not a stick container: its load address is not 0 */
    MHFU_BRAIN_E_ROW     = -7,   /* its row is no species row */
    MHFU_BRAIN_E_SECTION = -8,   /* a section lies outside the file */
    MHFU_BRAIN_E_RELOC   = -9,   /* a relocation lies outside the image */
    MHFU_BRAIN_E_PATCH   = -10,  /* a header patch is missing, misplaced or has no address */
    MHFU_BRAIN_E_FULL    = -11,  /* MHFU_BRAIN_MAX other rows are installed */
    MHFU_BRAIN_E_HOOK    = -12,  /* the chunk listener could not be registered */
};

typedef struct {
    uint32_t id;       /* the container's id: its image's SHA-1 at base 0, first 4 bytes */
    uint32_t image;    /* where the relocated image sits */
    uint32_t size;     /* the image's bytes */
    uint32_t headers;  /* the headers it carries: native header address -> image */
    uint32_t patched;  /* row words pointed at the image since the install, install included */
    uint32_t words;    /* bit k: the row's word for map type k points into the image now */
} mhfu_brain_status_t;

/* Installs the container at path, a path as mhfu_inject_register takes it, for the row it names,
 * and points that row's words at it now and after every overlay read. Installing a row again
 * replaces it; the old image stays in extra RAM, which is never freed. The row (>= 0), or a
 * MHFU_BRAIN_E_* failure, logged. Reads the Memory Stick: call it while mods load. */
int mhfu_brain_install(const char *path);

/* The words of a MHFU_BRAIN_E_* code. */
const char *mhfu_brain_error(int rc);

/* row's install into out; 0 if the row has none. */
int mhfu_brain_status(int row, mhfu_brain_status_t *out);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_BRAIN_H */
