/* Plain types several public headers share. */
#ifndef MHFU_TYPES_H
#define MHFU_TYPES_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { float x, y, z; } mhfu_vec3_t;

typedef uint32_t mhfu_quest_t;            /* the quest singleton; 0 = none */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_TYPES_H */
