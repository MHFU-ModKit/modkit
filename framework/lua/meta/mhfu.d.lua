---@meta
-- The mhfu table lua_host gives every mod: the C bindings and their constants. This file is also
-- the declaration: tools/lua_api.py builds the registered table from it, so a function exists in
-- the game exactly when it is declared here. The prelude (lua/lib/_prelude.lua) adds
-- mhfu.world, mhfu.entity, mhfu.mem, mhfu.MON and mhfu.AREA on top.

---@class mhfu
---@field addr mhfu.addr every game address and struct offset, built on first read
mhfu = {}

---The API's version, raised whenever a declaration here changes; a mod that needs something added
---in version N checks `mhfu.api_version >= N`.
mhfu.api_version = 1

-- log, memory and world state (bind_memory.cpp) ----------------------------------------------

---TODO
function mhfu.log(...) end

---TODO
function mhfu.read_u8(...) end

---TODO
function mhfu.read_u16(...) end

---TODO
function mhfu.read_u32(...) end

---TODO
function mhfu.mem_valid(...) end

---TODO
function mhfu.write_u8(...) end

---TODO
function mhfu.write_u16(...) end

---TODO
function mhfu.write_u32(...) end

---TODO
function mhfu.read_f32(...) end

---TODO
function mhfu.write_f32(...) end

---TODO
function mhfu.get_screen_state(...) end

---TODO
function mhfu.get_area_index(...) end

---TODO
function mhfu.get_quest_timer(...) end

---TODO
function mhfu.player_pos(...) end

---TODO
function mhfu.get_player_hp(...) end

---TODO
function mhfu.paint_map(...) end

-- entities, clones and collision nodes (bind_entity.cpp) -------------------------------------

---TODO
function mhfu.entity_at(...) end

---TODO
function mhfu.entity_type(...) end

---TODO
function mhfu.entity_hp(...) end

---TODO
function mhfu.entity_size(...) end

---TODO
function mhfu.entity_set_size(...) end

---TODO
function mhfu.entity_alive(...) end

---TODO
function mhfu.entity_pos(...) end

---TODO
function mhfu.entity_set_pos(...) end

---TODO
function mhfu.entity_yaw(...) end

---TODO
function mhfu.entity_set_yaw(...) end

---TODO
function mhfu.entity_ai_state(...) end

---TODO
function mhfu.entity_set_ai_state(...) end

---TODO
function mhfu.entity_engaged(...) end

---TODO
function mhfu.entity_set_engaged(...) end

---TODO
function mhfu.entity_calm(...) end

---TODO
function mhfu.entity_section(...) end

---TODO
function mhfu.entity_set_section(...) end

---TODO
function mhfu.entity_make_visible(...) end

---TODO
function mhfu.entity_force_aggro(...) end

---TODO
function mhfu.entities_of_type(...) end

---TODO
function mhfu.entity_clone(...) end

---TODO
function mhfu.node_clone(...) end

---TODO
function mhfu.node_of(...) end

---TODO
function mhfu.node_linked(...) end

---TODO
function mhfu.node_relink(...) end

---TODO
function mhfu.node_sync(...) end

---TODO
function mhfu.node_detach(...) end

---Anteka's monster id.
mhfu.MON_ANTEKA = 0x45 -- MHFU_MONSTER_ANTEKA
---Popo's monster id.
mhfu.MON_POPO = 0x46 -- MHFU_MONSTER_POPO
---Tigrex's monster id.
mhfu.MON_TIGREX = 0x4B -- MHFU_MONSTER_TIGREX
---Giadrome's monster id.
mhfu.MON_GIADROME = 0x4D -- MHFU_MONSTER_GIADROME

-- the quest's monsters (bind_quest.cpp) ------------------------------------------------------

---TODO
function mhfu.quest_has(...) end

---TODO
function mhfu.quest_replace_monster(...) end

---TODO
function mhfu.quest_add_monster(...) end

---TODO
function mhfu.quest_monster_count(...) end

---TODO
function mhfu.quest_first_monster(...) end

-- relocated overlays and model injection (bind_inject.cpp) -----------------------------------

---TODO
function mhfu.load_relocated_overlay(...) end

---TODO
function mhfu.inject_register(...) end

---TODO
function mhfu.inject_relocate(...) end

---TODO
function mhfu.inject_now(...) end

---TODO
function mhfu.inject_locate(...) end

-- input (bind_input.cpp) ---------------------------------------------------------------------

---TODO
function mhfu.buttons(...) end

---The SELECT bit of `mhfu.buttons()`.
mhfu.CTRL_SELECT = 0x0001 -- PSP_CTRL_SELECT
---The START bit of `mhfu.buttons()`.
mhfu.CTRL_START = 0x0008 -- PSP_CTRL_START
---The d-pad up bit of `mhfu.buttons()`.
mhfu.CTRL_UP = 0x0010 -- PSP_CTRL_UP
---The d-pad right bit of `mhfu.buttons()`.
mhfu.CTRL_RIGHT = 0x0020 -- PSP_CTRL_RIGHT
---The d-pad down bit of `mhfu.buttons()`.
mhfu.CTRL_DOWN = 0x0040 -- PSP_CTRL_DOWN
---The d-pad left bit of `mhfu.buttons()`.
mhfu.CTRL_LEFT = 0x0080 -- PSP_CTRL_LEFT
---The L trigger bit of `mhfu.buttons()`.
mhfu.CTRL_L = 0x0100 -- PSP_CTRL_LTRIGGER
---The R trigger bit of `mhfu.buttons()`.
mhfu.CTRL_R = 0x0200 -- PSP_CTRL_RTRIGGER
---The triangle bit of `mhfu.buttons()`.
mhfu.CTRL_TRIANGLE = 0x1000 -- PSP_CTRL_TRIANGLE
---The circle bit of `mhfu.buttons()`.
mhfu.CTRL_CIRCLE = 0x2000 -- PSP_CTRL_CIRCLE
---The cross bit of `mhfu.buttons()`.
mhfu.CTRL_CROSS = 0x4000 -- PSP_CTRL_CROSS
---The square bit of `mhfu.buttons()`.
mhfu.CTRL_SQUARE = 0x8000 -- PSP_CTRL_SQUARE

-- AI (bind_ai.cpp) ---------------------------------------------------------------------------

---TODO
function mhfu.action_ptr_for(...) end

-- em_vhook, the big-monster vtable seams (bind_em.cpp) ---------------------------------------

---TODO
function mhfu.em_installed(...) end

---TODO
function mhfu.em_request(...) end

---TODO
function mhfu.em_substitute(...) end

---TODO
function mhfu.em_rule(...) end

---TODO
function mhfu.em_clear(...) end

---TODO
function mhfu.em_status(...) end

---Any sub state, for `mhfu.em_substitute` and `mhfu.em_rule`.
mhfu.EM_ANY = 0xFE -- MHFU_EM_SUB_ANY
---A fire budget that never runs out (-1 in the 32-bit lua_Integer).
mhfu.EM_UNLIMITED = -1 -- MHFU_EM_UNLIMITED

-- screen capture (bind_capture.cpp) ----------------------------------------------------------

---TODO
function mhfu.capture(...) end

---TODO
function mhfu.capture_status(...) end

-- combat for clones and scripted movesets (combat.cpp) ---------------------------------------

---TODO
function mhfu.resolve_attack(...) end

---TODO
function mhfu.bone_pos(...) end

---TODO
function mhfu.spawn_effect(...) end

---TODO
function mhfu.clone_combat(...) end

---TODO
function mhfu.clones_set(...) end

---TODO
function mhfu.combat_swap(...) end

---TODO
function mhfu.combat_nodes(...) end

---TODO
function mhfu.combat_register_all(...) end

-- events (events.cpp) ------------------------------------------------------------------------

---TODO
function mhfu.on_quest_targets_building(...) end

---TODO
function mhfu.on_bigmonster_spawn(...) end

---TODO
function mhfu.on_bigmonster_death(...) end

---TODO
function mhfu.on_bigmonster_damaged(...) end

---TODO
function mhfu.on_ai_overlay_loaded(...) end

---TODO
function mhfu.on_bigmonster_slot_picked(...) end

---TODO
function mhfu.on_bigmonster_action_input(...) end

---TODO
function mhfu.on_bigmonster_action_decided(...) end

---TODO
function mhfu.on_bigmonster_action(...) end
