/* Player and world helpers (mhfu/world.h). */
#include "mhfu/world.h"
#include "mhfu/memory.h"
#include "addresses.gen.h"

extern "C" {

mhfu_vec3_t mhfu_world_player_pos(void)
{
    mhfu_vec3_t p;
    p.x = mhfu_mem_read_f32(MHFU_CAM_VIEW_EYE + 0);
    p.y = mhfu_mem_read_f32(MHFU_CAM_VIEW_EYE + 4);
    p.z = mhfu_mem_read_f32(MHFU_CAM_VIEW_EYE + 8);
    return p;
}

void mhfu_world_paint_map(void)
{
    mhfu_mem_write_u8(MHFU_MAP_PAINT, 0xFF);
}

} /* extern "C" */
