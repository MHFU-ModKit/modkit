/* Monster types: the species byte at ENTITY.SPECIES, also a quest record's emId. Incomplete;
 * an unknown monster keeps its raw value. */
#ifndef MHFU_IDS_H
#define MHFU_IDS_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MHFU_MONSTER_ANTEKA   = 0x45,
    MHFU_MONSTER_POPO     = 0x46,
    MHFU_MONSTER_TIGREX   = 0x4B,
    MHFU_MONSTER_GIADROME = 0x4D,
} mhfu_monster_type_t;

/* Upper-case name, or "0xNN" for an unknown type; static storage. */
const char *mhfu_monster_name(unsigned int monster_type);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_IDS_H */
