/* Monster names for logs (mhfu/ids.h). */
#include "mhfu/ids.h"
#include <stdio.h>

extern "C" const char *mhfu_monster_name(unsigned int t)
{
    switch ((mhfu_monster_type_t)(t & 0xFF)) {
        case MHFU_MONSTER_ANTEKA:   return "ANTEKA";
        case MHFU_MONSTER_POPO:     return "POPO";
        case MHFU_MONSTER_TIGREX:   return "TIGREX";
        case MHFU_MONSTER_GIADROME: return "GIADROME";
        default: break;
    }
    static char buf[8];
    snprintf(buf, sizeof(buf), "0x%02X", t & 0xFF);
    return buf;
}
