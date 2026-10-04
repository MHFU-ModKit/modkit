-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
---@meta
-- The declaration of the mhfu API: tools/lua_api.py builds the registered table from it, so a
-- function exists in the game exactly when it is declared here.

---The C bindings lua_host gives every mod, and what lua/lib/_prelude.lua adds to them.
---@class mhfu
---@field addr mhfu.addr every game address and struct offset, built on first read
mhfu = {}

---The API's version, raised whenever a declaration here changes; a mod that needs something added
---in version N checks `mhfu.api_version >= N`.
mhfu.api_version = 1

-- log, memory and world state (bind_memory.cpp) ----------------------------------------------

---Appends a line to framework.log, cut at 255 bytes; lines logged outside an area are held until
---the game is in one.
---@param msg string
function mhfu.log(msg) end

---Reads the u8 at addr, unchecked: an unmapped address crashes the game.
---@param addr integer
---@return integer
function mhfu.read_u8(addr) end

---Reads the u16 at addr (2-aligned), unchecked.
---@param addr integer
---@return integer
function mhfu.read_u16(addr) end

---Reads the u32 at addr (4-aligned), unchecked. lua_Integer is 32 bits, so a value from
---0x80000000 up is negative: test bits with `&`.
---@param addr integer
---@return integer
function mhfu.read_u32(addr) end

---True if addr is in user RAM; the extra-RAM window, where clones live, is not. Check a pointer
---read from the game before following it.
---@param addr integer
---@return boolean
function mhfu.mem_valid(addr) end

---Writes the low 8 bits of v at addr, unchecked.
---@param addr integer
---@param v integer
function mhfu.write_u8(addr, v) end

---Writes the low 16 bits of v at addr (2-aligned), unchecked.
---@param addr integer
---@param v integer
function mhfu.write_u16(addr, v) end

---Writes v at addr (4-aligned), unchecked; a code word takes only while the JIT is cold (screen
---state 1 or 4).
---@param addr integer
---@param v integer
function mhfu.write_u32(addr, v) end

---Reads the f32 at addr (4-aligned), unchecked.
---@param addr integer
---@return number
function mhfu.read_f32(addr) end

---Writes v as an f32 at addr (4-aligned), unchecked.
---@param addr integer
---@param v number
function mhfu.write_f32(addr, v) end

---The screen-state id; its values are listed on mhfu.addr.SCREEN_STATE (17 = in an area).
---@return integer
function mhfu.get_screen_state() end

---The visible map section, in the encoding entity_section uses.
---@return integer
function mhfu.get_area_index() end

---Quest frames left, at 30 Hz.
---@return integer
function mhfu.get_quest_timer() end

---The camera eye (mhfu.addr.CAM_VIEW_EYE), about 500 units off the hunter: not his own position.
---@return number x
---@return number y
---@return number z
function mhfu.player_pos() end

---The hunter's current HP.
---@return integer
function mhfu.get_player_hp() end

---Shows every big monster on the map; repeat at 2 Hz or faster to keep them shown.
function mhfu.paint_map() end

-- entities, clones and collision nodes (bind_entity.cpp) -------------------------------------
-- An entity is its pointer. The getters return 0 for a pointer outside RAM; the setters do nothing.

---The entity in registry slot 0..20, or 0 if the slot is empty; monsters are in 1..20.
---@param slot integer
---@return integer ent
function mhfu.entity_at(slot) end

---The entity's monster type (mhfu.MON_*).
---@param ent integer
---@return integer
function mhfu.entity_type(ent) end

---Current HP.
---@param ent integer
---@return integer
function mhfu.entity_hp(ent) end

---The size multiplier.
---@param ent integer
---@return number
function mhfu.entity_size(ent) end

---Sets the size multiplier, in every copy the engine keeps of it.
---@param ent integer
---@param size number
function mhfu.entity_set_size(ent, size) end

---True while ent is in the entity registry; says nothing about its HP.
---@param ent integer
---@return boolean
function mhfu.entity_alive(ent) end

---World position.
---@param ent integer
---@return number x
---@return number y
---@return number z
function mhfu.entity_pos(ent) end

---Moves ent, its transform's translation row with it.
---@param ent integer
---@param x number
---@param y number
---@param z number
function mhfu.entity_set_pos(ent, x, y, z) end

---Facing: 0..0xFFFF is one turn, atan2(dx, dz).
---@param ent integer
---@return integer
function mhfu.entity_yaw(ent) end

---Sets the facing (0..0xFFFF is one turn).
---@param ent integer
---@param yaw integer
function mhfu.entity_set_yaw(ent, yaw) end

---The byte at ENTITY.ANIM_SPEED: it tracks the action playing, and 2 is part of the engage
---signature (entity_force_aggro); not a real AI state.
---@param ent integer
---@return integer
function mhfu.entity_ai_state(ent) end

---Writes the byte entity_ai_state reads; the next action the engine installs overwrites it.
---@param ent integer
---@param state integer
function mhfu.entity_set_ai_state(ent, state) end

---True while ent has noticed a target and pursues it (ENTITY.ENGAGE >= 0.5).
---@param ent integer
---@return boolean
function mhfu.entity_engaged(ent) end

---Writes ENTITY.ENGAGE as 1.0 or 0.0; the engine rewrites it every frame.
---@param ent integer
---@param engaged boolean
function mhfu.entity_set_engaged(ent, engaged) end

---Zeroes ENGAGE and both detection ranges; the engine never restores the ranges, so ent stays calm.
---@param ent integer
function mhfu.entity_calm(ent) end

---The section the engine places ent in (get_area_index's encoding); lags a transition.
---@param ent integer
---@return integer
function mhfu.entity_section(ent) end

---Sets the section the engine places ent in.
---@param ent integer
---@param section integer
function mhfu.entity_set_section(ent, section) end

---Makes a swapped monster draw in section (pass get_area_index()) by setting what the visibility
---gate checks. Once, not every tick.
---@param ent integer
---@param section integer
function mhfu.entity_make_visible(ent, section) end

---Engages ent on the point x, y, z as the engine does, for where its own target resolver finds
---nothing: pursuit vector, ENGAGE, ai state 2 and the acquired flag in one go.
---@param ent integer
---@param x number
---@param y number
---@param z number
function mhfu.entity_force_aggro(ent, x, y, z) end

---Up to 16 live entities of one monster type, in registry order.
---@param species integer
---@return integer[]
function mhfu.entities_of_type(species) end

---Clones src into a free registry slot, calm and sharing its model and overlay; 0 on failure.
---Needs `memory = 64` when partition RAM is short. A clone deals no damage until it has a node.
---@param src integer
---@return integer ent
function mhfu.entity_clone(src) end

---Gives clone ent a collision node copied from a native's node template, so it can damage the
---hunter; 0 on failure. A nonzero uid becomes the node id (16 at most; a large one crashes).
---Once per clone.
---@param template integer
---@param ent integer
---@param uid integer
---@return integer node
function mhfu.node_clone(template, ent, uid) end

---ent's collision node, or 0: without one it cannot deal damage.
---@param ent integer
---@return integer node
function mhfu.node_of(ent) end

---True while node is reachable from the collision list's head.
---@param node integer
---@return boolean
function mhfu.node_linked(node) end

---Re-inserts node into the collision list after a section change; true if it had to.
---@param node integer
---@return boolean
function mhfu.node_relink(node) end

---Copies ent's position into node.
---@param node integer
---@param ent integer
function mhfu.node_sync(node, ent) end

---Unlinks node; call before its clone goes away, since a dangling node crashes the game.
---@param node integer
function mhfu.node_detach(node) end

---Anteka's monster id.
mhfu.MON_ANTEKA = 0x45 -- MHFU_MONSTER_ANTEKA
---Popo's monster id.
mhfu.MON_POPO = 0x46 -- MHFU_MONSTER_POPO
---Tigrex's monster id.
mhfu.MON_TIGREX = 0x4B -- MHFU_MONSTER_TIGREX
---Giadrome's monster id.
mhfu.MON_GIADROME = 0x4D -- MHFU_MONSTER_GIADROME

-- the quest's big monsters (bind_quest.cpp) --------------------------------------------------
-- quest is the handle on_quest_targets_building passes; the list is editable only in that event.

---True if the quest lists species among its big monsters.
---@param quest integer
---@param species integer
---@return boolean
function mhfu.quest_has(quest, species) end

---Retags monster from as to in place, so the engine loads to natively; false if from is missing
---or to's record layout is unknown (only Tigrex's is known).
---@param quest integer
---@param from integer
---@param to integer
---@return boolean
function mhfu.quest_replace_monster(quest, from, to) end

---Adds species as another big monster in its own target group, which is what lets it deal
---damage; false once the groups are taken or for an unknown layout (only Tigrex's is known).
---@param quest integer
---@param species integer
---@param x? number spawn point; 0 or nil keeps the source record's
---@param z? number
---@return boolean
function mhfu.quest_add_monster(quest, species, x, z) end

---How many big monsters the quest lists (8 at most).
---@param quest integer
---@return integer
function mhfu.quest_monster_count(quest) end

---The monster id of the first big-monster record, or -1.
---@param quest integer
---@return integer
function mhfu.quest_first_monster(quest) end

-- relocated overlays and model injection (bind_inject.cpp) -----------------------------------
-- file_id is the engine's file id: the extracted file number + 1.

---Loads a second em*.ovl at a fresh address, relocated, bss zeroed; once per session, and not
---wired to the engine. On failure returns nil and a negative error code (framework.log says why).
---@param path string
---@param run_inits? boolean run its static initializers
---@return integer? base the footprint's start
---@return integer load_or_err the relocated load address, or the error code
---@return integer? delta
function mhfu.load_relocated_overlay(path, run_inits) end

---Replaces a big-monster PAC as the game loads it, with no disk edit: the PAC at path overwrites
---the raw buffer that byte-matches <path>.orig (same size), again whenever the file changes.
---@param file_id integer
---@param path string
---@return boolean ok
function mhfu.inject_register(file_id, path) end

---inject_register for a PAC larger than the engine's buffer: the load reads grown_path from extra
---RAM instead, and orig_path (the unedited PAC) recognises the engine's buffer.
---@param file_id integer
---@param grown_path string
---@param orig_path string
---@return boolean ok
function mhfu.inject_relocate(file_id, grown_path, orig_path) end

---Re-reads file_id's PAC now, ignoring its mtime; returns the overwrites so far, 0 if file_id is
---unknown or the read failed.
---@param file_id integer
---@return integer
function mhfu.inject_now(file_id) end

---The overwrites of file_id so far, 0 if it is unknown.
---@param file_id integer
---@return integer
function mhfu.inject_locate(file_id) end

-- input (bind_input.cpp) ---------------------------------------------------------------------

---The controller now.
---@return integer buttons the mhfu.CTRL_* bits held
---@return integer lx stick x, 0..255, centred near 128
---@return integer ly stick y, 0..255, centred near 128
function mhfu.buttons() end

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

---What the vt[8] picker last returned for input on this monster type (a per-run pointer for
---Tigrex), or 0 before it picked it once: a valid answer for on_bigmonster_action_decided.
---@param species integer
---@param input integer
---@return integer
function mhfu.action_ptr_for(species, input) end

-- em_vhook, the big-monster vtable seams (bind_em.cpp) ---------------------------------------
-- A behaviour pair (main, sub) is the MOVE a big monster runs, hitbox and damage included.

---True while a big monster's vtable is wrapped: from its spawn to the next quest.
---@return boolean
function mhfu.em_installed() end

---Enters (main, sub) on the next AI frame through the engine's own dispatcher, so the pair is
---provisioned like a native one; false while nothing is wrapped.
---@param main integer
---@param sub integer
---@param mode? integer the enter-action's mode argument, default 0
---@return boolean
function mhfu.em_request(main, sub, mode) end

---Substitution slot 0..3: the engine's own choice with main in from_mask (bit k = main k) and sub
---from_sub (or mhfu.EM_ANY) is entered as (to_main, to_sub) instead, count times.
---@param slot integer
---@param from_mask integer
---@param from_sub integer
---@param to_main integer
---@param to_sub integer
---@param count integer mhfu.EM_UNLIMITED for a standing entry, 0 clears the slot
---@return true
function mhfu.em_substitute(slot, from_mask, from_sub, to_main, to_sub, count) end

---Rule slot 0..3: a brain rule the AI step checks every frame (30 Hz), with no Lua in the loop.
---@param slot integer
---@param rule mhfu.EmRule? nil clears the slot
---@return true
function mhfu.em_rule(slot, rule) end

---Drops every substitution, rule and pending request.
---@return true
function mhfu.em_clear() end

---The seam's counters and its last enter-actions.
---@return mhfu.EmStatus
function mhfu.em_status() end

---Any sub state, for `mhfu.em_substitute` and `mhfu.em_rule`.
mhfu.EM_ANY = 0xFE -- MHFU_EM_SUB_ANY
---A fire budget that never runs out (-1 in the 32-bit lua_Integer).
mhfu.EM_UNLIMITED = -1 -- MHFU_EM_UNLIMITED

---"In a pair from `from_mask`/`from_sub` for `min_frames`, the hunter's XZ distance in
---[dist_lo, dist_hi) -> enter (to_main, to_sub, mode)"; every field is optional.
---@class mhfu.EmRule
---@field from_mask? integer bit k: may fire from main state k; default 0, never
---@field from_sub? integer exact sub, default mhfu.EM_ANY
---@field to_main? integer default 0
---@field to_sub? integer default 0
---@field mode? integer default 0
---@field min_frames? integer frames the pair must have stood, default 0
---@field dist_lo? number default 0
---@field dist_hi? number default 1e9
---@field receding? boolean only while the distance grows
---@field closing? boolean only while the distance shrinks
---@field cooldown? integer frames between two fires, default 0
---@field count? integer fires allowed, default mhfu.EM_UNLIMITED

---What `mhfu.em_status()` returns.
---@class mhfu.EmStatus
---@field installed boolean
---@field ai_ticks integer AI frames seen
---@field act_enters integer enter-actions seen
---@field last_main integer the last pair entered
---@field last_sub integer
---@field frames integer frames the current pair has stood
---@field dist number the hunter's XZ distance at the last measurement
---@field sub_hits integer substitutions made
---@field sub_landed integer substitutions whose pair reached the state cells
---@field sub_last_main integer the last substituted enter-action, before the rewrite
---@field sub_last_sub integer
---@field sub_last_mode integer
---@field brain_fires integer rule fires
---@field req_pending integer 1 while a request waits for its AI frame
---@field req_done integer requests entered
---@field req_main integer the last request's pair
---@field req_sub integer
---@field ring mhfu.EmEnter[] the last 8 enter-actions, oldest first
---@field rule_fired integer[] fires per rule slot
---@field rule_left integer[] budget left per rule slot
---@field sub_left integer[] budget left per substitution slot

---One enter-action in `mhfu.EmStatus.ring`.
---@class mhfu.EmEnter
---@field main integer
---@field sub integer
---@field mode integer
---@field subst integer nonzero if a substitution rewrote it

-- screen capture (bind_capture.cpp) ----------------------------------------------------------

---Starts or stops streaming the framebuffer on its own thread; the options apply only when
---starting with scale given. Returns whether it runs.
---@param on boolean
---@param scale? integer 1 full size, 2 half (default), up to 4
---@param interval_ms? integer between frames, default 66 (about 15 fps)
---@param path? string default host0:/cap/stream.bin (psplink usbhostfs)
---@return boolean
function mhfu.capture(on, scale, interval_ms, path) end

---The capture's state.
---@return boolean active
---@return integer frames written
---@return integer kb written
---@return integer last_err the last error code, 0 for none
function mhfu.capture_status() end

-- combat for clones and scripted movesets (combat.cpp) ---------------------------------------

---Runs the engine's attack resolution (timers, hitboxes, damage) for ent, which the engine does
---only for its ~2 managed combatants. Only from an override callback, never from mhfu_tick.
---@param ent integer
function mhfu.resolve_attack(ent) end

---World position of bone in ent's loaded skeleton; nothing for a bad entity or bone.
---@param ent integer
---@param bone integer 0..255, the loaded skeleton's numbering
---@return number? x
---@return number? y
---@return number? z
function mhfu.bone_pos(ent, bone) end

---Spawns engine effect effect_id at ent's bone. 0 means not spawned, which is what happens while
---ent is outside the hunter's section, not a bad id. Only from an override callback.
---@param ent integer
---@param effect_id integer
---@param bone? integer default 0, the loaded skeleton's numbering
---@return integer handle
function mhfu.spawn_effect(ent, effect_id, bone) end

---Experimental: runs each clones_set clone's AI tick and attack resolver alongside the native's,
---so clones fight. Its code patch lands only at a title or menu screen, so enable it at load. It
---can crash the game.
---@param enable boolean
function mhfu.clone_combat(enable) end

---The clone pointers clone_combat and combat_nodes drive (12 at most, zeros skipped); {} stops.
---@param clones integer[]
function mhfu.clones_set(clones) end

---Points the monster manager at the combat-node wrapper, and again whenever a quest reload reverts
---it; call each tick while combat_nodes is on.
function mhfu.combat_swap() end

---Arms or disarms a collision node per clone each frame, copied from an engaged native Tigrex's,
---so clones damage the hunter; needs combat_swap.
---@param enable boolean
function mhfu.combat_nodes(enable) end

---Does nothing; combat_swap does its work.
---@deprecated
function mhfu.combat_register_all() end

-- events (events.cpp) ------------------------------------------------------------------------
-- Each event holds one Lua handler: a second mhfu.on_X(fn) replaces the first, and priority
-- only orders it against C subscribers (higher first; the first call's stays). The game thread
-- waits for every fn but spawn, death and damaged's, so keep them short. "Register at load":
-- the event's code patch lands at the next title or menu screen.

---Calls fn while the quest builds its monster list, before the models load: where
---quest_replace_monster and quest_add_monster go. Register at load.
---@param fn fun(quest: integer)
---@param priority? integer
function mhfu.on_quest_targets_building(fn, priority) end

---Calls fn when a big monster enters the entity registry (5 Hz poll).
---@param fn fun(ent: integer, species: integer, slot: integer, hp: integer)
---@param priority? integer
function mhfu.on_bigmonster_spawn(fn, priority) end

---Calls fn once when a big monster's HP reaches 0 (5 Hz poll).
---@param fn fun(ent: integer, species: integer, slot: integer)
---@param priority? integer
function mhfu.on_bigmonster_death(fn, priority) end

---Calls fn when a big monster's HP dropped since the last 5 Hz poll (hits in between add up),
---after the engine's own flinch; intercept that with on_bigmonster_action.
---@param fn fun(ent: integer, species: integer, amount: integer, hp: integer, slot: integer)
---@param priority? integer
function mhfu.on_bigmonster_damaged(fn, priority) end

---Calls fn after a species AI overlay is copied in and before the JIT translates it: the window
---to patch overlay code. Register at load.
---@param fn fun()
---@param priority? integer
function mhfu.on_ai_overlay_loaded(fn, priority) end

---fn returns the body slot the slot loop processes next (cur is the engine's); one outside
---0..ctx.count-1 is ignored. Register at load.
---@param fn fun(ctx: mhfu.SlotCtx, cur: integer): integer?
---@param priority? integer
function mhfu.on_bigmonster_slot_picked(fn, priority) end

---fn returns the input the vt[8] picker reads instead of ctx.input. Fires for one body slot only,
---so forcing here desyncs the body: force with on_bigmonster_action.
---@param fn fun(ctx: mhfu.PickCtx): integer?
---@param priority? integer
function mhfu.on_bigmonster_action_input(fn, priority) end

---fn returns the vt[8] picker's outcome instead of value: a u16 action id for Popo, Anteka and
---Giadrome, a per-run pointer for Tigrex (action_ptr_for), never a small literal.
---@param fn fun(ctx: mhfu.PickCtx, value: integer): integer?
---@param priority? integer
function mhfu.on_bigmonster_action_decided(fn, priority) end

---fn returns the action id the executor runs, which the engine fans out to every body slot. It
---moves the ANIMATION only, not the move and its damage (em_request). Register at load.
---@param fn fun(ctx: mhfu.ActionCtx): integer?
---@param priority? integer
function mhfu.on_bigmonster_action(fn, priority) end

---What on_bigmonster_action passes. Check type: other entities use the executor too.
---@class mhfu.ActionCtx
---@field entity integer
---@field type integer monster type
---@field action_id integer the action the engine asked for

---What the vt[8] picker events pass.
---@class mhfu.PickCtx
---@field entity integer
---@field type integer monster type
---@field slot integer body slot
---@field input integer the slot's picker input

---What on_bigmonster_slot_picked passes.
---@class mhfu.SlotCtx
---@field entity integer
---@field type integer monster type
---@field slot integer the slot the loop started from
---@field count integer body slots
