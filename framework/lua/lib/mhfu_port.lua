-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- mhfu_port: the ported-monster runtime. It loads a port's assets into a quest and drives the
-- monster so that the move it executes and the clip on screen are the same thing.
--
-- A big monster runs on two channels: the executor's action id picks the CLIP, the behaviour pair
-- (ENTITY.MAIN_STATE, SUB_STATE) picks the MOVE, whose handler owns hitbox and damage. A port's
-- clips sit in the executor entries its manifest gives them (<name>_clips.lua, P.layout), not by
-- meaning, so a port declares which clip each move shows
--
--   moves = { charge = { main = 3, sub = 6, clip = "charge", after = "skid", hold_max = 8 },
--             skid   = { main = 0, sub = 3, clip = "stop" } }
--
-- and port:play("charge") enters the pair and latches the port's clip in one call. A move with an
-- executor `entry` and no pair is the port's own: port:move("stamp") plays it through the
-- framework's move player (mhfu.em_move). A port's moves and rules default to its manifest's,
-- from <name>_moves.lua (P.behaviour), whose `vars` and `signals` name the monster's board:
-- port:var("kills") reads a counter the rules change, port:fire("rage") raises a signal.
-- port:enrage() and port:calm() start and end the monster's rage through the engine's own levers,
-- and the module's `sever_below` (an HP percent) and `natural_rage = false` set the tail cut's
-- gate and keep the species' anger from starting rage.
--
-- A mod requires the library at its top level and registers a setup function, which runs at once
-- and again after a library reload; registering the same name again replaces it:
--
--   local port = require("mhfu_port")
--   port.mod("my_mod", function(P)
--     local m = P.define{ ... }
--     m:brain(function(s) ... end)
--   end)
--
-- The library owns the one global mhfu_tick; a mod never defines it and uses m:brain(fn). The
-- tick is 2 Hz (every 5th pass of lua_host's 10 Hz worker; the executor hook fires only on a new
-- dispatch, ~0.5/s), so a brain's thresholds absorb 500 ms of travel, ~650 units in a charge.
--
-- More: framework/README.md (Lua mods); a port's clips and moves: ports/README.md.

if (mhfu.api_version or 0) < 1 then
  error("mhfu_port needs mhfu.api_version >= 1, this framework has "
        .. tostring(mhfu.api_version or 0))
end

local P = package.loaded.mhfu_port or {}
P.ports  = {}              -- name -> port handle, rebuilt on a library reload
P._mods  = P._mods or {}   -- name -> setup fn, kept so a library reload rebuilds P.ports
P._once  = P._once or {}   -- side effects that happen once per boot

-- ------------------------------------------------------------- offsets
-- Named here so a brain never needs a raw offset.
local E = mhfu.addr.ENTITY
local OFF_POS       = E.POSITION
local OFF_YAW       = E.YAW            -- the rotation control: the matrix is rebuilt from it
local OFF_MAIN      = E.MAIN_STATE
local OFF_SUB       = E.SUB_STATE
local OFF_TICK_MAIN = E.TICK_MAIN      -- the pair the action tick last ran; it copies
local OFF_TICK_SUB  = E.TICK_SUB       -- MAIN/SUB here first thing every tick
local OFF_PHASE     = E.PHASE          -- per-action phase cursor, 3 bytes
local OFF_SECTION   = E.SECTION
local OFF_HP        = E.HP             -- current HP; MAX_HP is only the clamp
local OFF_MAX_HP    = E.MAX_HP
local OFF_HZ_STATE  = E.HITZONE_STATE
local OFF_FLAGS     = E.FLAGS          -- 0x8000 visible; bit 0x20 tracks HITZONE_STATE
local OFF_FREEZE    = E.FREEZE_GATE
-- who the monster has committed to, not whether it has noticed anyone (see port_state)
local OFF_TARGET    = E.TARGET
local OFF_ACQUIRED  = E.TARGET_ACQUIRED
local FREEZE_BITS   = 0x100 | 0x10000
-- a move whose phase byte has not moved for this many ticks and that declares neither `after`
-- nor `hold_max` is logged as parked, once per play
local PARKED_TICKS  = 10

-- The player's world position is the combat entity's transform row; mhfu.player_pos() reads the
-- camera eye.
local PLAYER_ENT  = mhfu.addr.PLAYER_ENTITY
local PLAYER_XYZ  = E.TRANSLATION

-- ------------------------------------------------------------- floats
-- Use mhfu.read_f32/write_f32. The hand decoder scripts copy, `if u >= 0x80000000 then sign =
-- -1.0 ...`, is always true where lua_Integer is 32 bits (the PSP) and returns a clean sign flip:
-- invisible in a distance, a teleport to the mirror image once a position is written back. This
-- is the fixed form, kept only for the spawn cross-check.
local function u32_to_float(u)
  local sign = ((u & 0x80000000) ~= 0) and -1.0 or 1.0
  local exp, mant = (u >> 23) & 0xFF, u & 0x7FFFFF
  if exp == 0   then return sign * mant * (2.0 ^ -149) end
  if exp == 255 then return sign * math.huge end
  return sign * (1.0 + mant * (2.0 ^ -23)) * (2.0 ^ (exp - 127))
end
local function rf(a) return mhfu.read_f32(a) end
local function wf(a, v) mhfu.write_f32(a, v) end

-- math.atan2 is the pre-5.3 spelling
---@diagnostic disable-next-line: deprecated
local atan2 = math.atan2 or math.atan

-- ------------------------------------------------------------- logging
-- mhfu.log takes one string (extra args vanish). Lines queue here and only the tick writes them:
-- lines logged at load or from an event callback go missing from framework.log. The queue lives
-- on P, so handlers registered before a library reload still reach it.
P._pend = P._pend or {}
local PEND = P._pend
local function log(fmt, ...)
  PEND[#PEND + 1] = (select("#", ...) == 0) and fmt or string.format(fmt, ...)
end
P.log = log

local function drain()
  local n = #PEND
  for i = 1, n do mhfu.log(PEND[i]); PEND[i] = nil end
end

-- ------------------------------------------------------------- native seams
-- em_vhook's seams, live once it has latched the monster's vtable (mhfu.em_installed()). Then
-- play() enters a pair through the engine's own enter-action on the game thread, provisioned
-- (mhfu.em_request); a move's `claim` is entered in place of the host brain's pairs in its set
-- (mhfu.em_substitute: the host decides when, the port what); port:rule{} is a 30 Hz trigger with
-- no Lua in the loop (mhfu.em_rule). The latch happens on the spawn event that binds port.ent, so
-- claims and rules go in on the first tick after it (_arm_native). Without the seam, act_set below
-- is the fallback and the log says so once.
local EM_ANY       = mhfu.EM_ANY or 0xFE
local EM_UNLIMITED = mhfu.EM_UNLIMITED or -1
local MAX_SUBS     = 4
local MAX_RULES    = mhfu.addr.EM_CFG.RULES_COUNT     -- rules the seam holds
local MAX_OWN      = mhfu.addr.EM_MOVES.MOVES_COUNT  -- own moves the framework's registry holds
local MAX_CONDS    = mhfu.addr.EM_RULE.CONDS_COUNT    -- conditions and effects a rule holds
local MAX_EFFECTS  = mhfu.addr.EM_RULE.EFFECTS_COUNT
local MAX_SIGNALS  = mhfu.addr.EM_BOARD.SIGNALS_COUNT -- the board's signals

local function native_ready()
  return mhfu.em_installed ~= nil and mhfu.em_installed() == true
end
P.native_ready = native_ready

--- The seam's counters, or nil without it. `ring` is the last 8 enter-action dispatches (oldest
--- first: main, sub, mode, subst), what to read when a request or a substitution did not land.
function P.native_status()
  if mhfu.em_status == nil then return nil end
  return mhfu.em_status()
end

local function ring_text(st)
  if not st or not st.ring then return "?" end
  local t = {}
  for _, e in ipairs(st.ring) do
    t[#t + 1] = string.format("(%d,%d,m%d)%s", e.main, e.sub, e.mode,
                              e.subst ~= 0 and "*" or "")
  end
  return table.concat(t, " ")
end

--- A move's `claim` as a main-state bitmask + sub: `{ main = 1 }`, `{ main = {0, 1} }`,
--- `{ main = 1, sub = 7 }`. Returns mask, sub (EM_ANY when none).
local function claim_of(mv)
  local c = mv.claim
  if c == nil then return nil end
  if type(c) == "number" then c = { main = c } end
  local mains = c.main
  if type(mains) == "number" then mains = { mains } end
  local mask = 0
  for _, m in ipairs(mains or {}) do mask = mask | (1 << m) end
  if mask == 0 then return nil end
  return mask, c.sub or EM_ANY
end

-- ------------------------------------------------------------- act_set
-- The engine's act_set as plain memory writes, the fallback while the seam is not live. It runs a
-- complete, damaging move, but unprovisioned: the engine's way in runs the per-main translator
-- first (the charge's run budget), so a pair forced here parks in its last phase with its hitbox
-- spent, and `after`/`hold_max` end it. It does the unconditional part of the engine's cursor
-- reset, the minimum a handler needs to run from its first phase.
--
-- Pulse it, never per tick: rewriting the pair every tick restarts the move before its hitbox
-- frames, and repeated forcing sets the freeze bits that halt the AI tick, so each pulse clears
-- them.
local function act_set(ent, main, sub)
  mhfu.write_u8(ent + OFF_TICK_MAIN, mhfu.read_u8(ent + OFF_MAIN))
  mhfu.write_u8(ent + OFF_TICK_SUB,  mhfu.read_u8(ent + OFF_SUB))
  mhfu.write_u8(ent + OFF_MAIN, main)
  mhfu.write_u8(ent + OFF_SUB,  sub)
  mhfu.write_u8(ent + OFF_PHASE,     0)
  mhfu.write_u8(ent + OFF_PHASE + 1, 0)
  mhfu.write_u8(ent + OFF_PHASE + 2, 0)
  local g = mhfu.read_u32(ent + OFF_FREEZE)
  if (g & FREEZE_BITS) ~= 0 then mhfu.write_u32(ent + OFF_FREEZE, g & ~FREEZE_BITS) end
end
P.act_set = act_set

-- ------------------------------------------------------------- Port handle
local Port = {}
Port.__index = Port

-- What a redefine keeps: the spawn event that bound `ent` will not fire again (ent = 0 would stop
-- the brain), and the hit tables in the game belong to the entity, not to the declaration.
local KEEP = { "ent", "_hit_id", "_hit_last" }

--- A port's clips, name -> executor entry, from mods/lib/<name>_clips.lua: `mhfu-port inject`
--- generates it from the manifest with the PAC, so the two agree. Empty, logged once, without it.
function P.layout(name)
  local ok, t = pcall(require, name .. "_clips")
  if ok and type(t) == "table" then return t end
  if not P._once["layout:" .. name] then
    P._once["layout:" .. name] = true
    log("[port:%s] no clip layout, so no clip names (mhfu-port inject writes %s_clips.lua): %s",
        name, name, tostring(t))
  end
  return {}
end

--- A port's moves and rules from mods/lib/<name>_moves.lua, which `mhfu-port inject` generates
--- from the manifest with the clips module: { moves = {...}, rules = {...}, vars = {name = index},
--- signals = {name = index}, sever_below = pct, natural_rage = false }. Empty, logged once, without
--- it.
function P.behaviour(name)
  local ok, t = pcall(require, name .. "_moves")
  if ok and type(t) == "table" then return t end
  if not P._once["behaviour:" .. name] then
    P._once["behaviour:" .. name] = true
    log("[port:%s] no moves module, so no moves or rules from the manifest (mhfu-port inject "
        .. "writes %s_moves.lua): %s", name, name, tostring(t))
  end
  return {}
end

--- An own move: an executor entry the move player plays, with no host pair.
local function is_own(mv) return mv ~= nil and mv.main == nil and mv.entry ~= nil end
P.is_own = is_own

--- Declare a ported monster.
--
--   name    identifier, also the hot-reload key
--   species host species id the port rides on (mhfu.MON_TIGREX)
--   pac     ported PAC filename under the framework's inject/ directory
--   orig    the original PAC it replaces, same directory
--   fid     cosmetic file id for the injector (matching is by content)
--   replace list of quest monster ids to swap for `species`
--   clips   name -> executor a1, the port's own animation vocabulary; by default every clip of
--           its source, from the module `mhfu-port inject` writes (P.layout)
--   moves   name -> { main, sub, clip }: which host behaviour runs and which of the port's
--           clips is shown while it does. `claim = { main = 1 }` (or `{ main = {0, 1}, sub = 7 }`)
--           enters this move in place of every host-brain pair in that set, so a port with one
--           attack rides the host's whole attack timing. A move with `entry` and no `main` is
--           an own move (port:move). Default: the manifest's (P.behaviour)
--   rules   port:rule{} specs installed with the port; default: the manifest's
--   vars, signals  name -> index of the board's counters and signals (port:var, port:fire); default:
--           the manifest's
--   sever_below  an HP percent: the tail cuts only below it (mhfu.em_sever_gate); default: the
--           manifest's, none
--   natural_rage false keeps the species' anger from starting rage (mhfu.em_natural_rage); default:
--           the manifest's, true
--   tip     { {joint, carrier}, ... }: the tail tip's joints follow the carriers until the tail
--           drops (mhfu.em_tip); default: the manifest's
--
-- Returns a port handle. Safe to call again (hot reload): the injector is armed once per boot.
function P.define(spec)
  local prev = P.ports[spec.name]
  local made = (spec.moves == nil or spec.rules == nil) and P.behaviour(spec.name) or {}
  local self = setmetatable({
    name    = spec.name,
    species = spec.species,
    clips   = spec.clips or P.layout(spec.name),
    moves   = spec.moves or made.moves or {},
    tip     = spec.tip or made.tip,
    _vars   = spec.vars or made.vars or {},          -- board counter name -> index
    _signals = spec.signals or made.signals or {},   -- board signal name -> index
    _sever_below = spec.sever_below or made.sever_below,   -- the tail cuts only below this HP percent
    _natural_rage = spec.natural_rage ~= false and made.natural_rage ~= false,
    replace = spec.replace or {},
    ent     = 0,
    clip    = nil,      -- currently latched executor a1, nil = hands off
    scripted = nil,     -- the pair move scripted now (port:play), by name
    pinned  = nil,      -- {x, y, z} while the coordinate lock is on
    -- how many ticks the last scripted move survived: a pair whose handler declines the
    -- situation ends on its first tick, and counting is the only way a brain finds out
    last_move = nil, last_move_ticks = 0,
    slip    = 0,        -- units the pin had to correct on the last tick
    _brain  = nil,
    _by_pair = {},      -- main*256+sub -> move name, for pairs the ENGINE enters
    _refused = {},      -- move name -> refusals, so the log says it once
    _rules  = {},       -- Port:rule{} declarations, installed by _arm_native
    _native_armed = false,
    _req    = nil,      -- a play() issued through the seam, until it lands
    own     = nil,      -- the own move the move player plays
    _slot   = {},       -- own move name -> its slot in the framework's registry
    _own_at = {},       -- slot -> own move name
    -- the moves module the moves or rules came from: a re-run of it (a new build deployed while
    -- the game runs) redefines the port (relink_changed)
    _made   = (spec.moves == nil or spec.rules == nil) and package.loaded[spec.name .. "_moves"]
              or nil,
  }, Port)
  if prev then for _, k in ipairs(KEEP) do self[k] = prev[k] end end
  -- declared pairs the engine enters on its own get the port's clip too (the action hook paints
  -- them), so the mapping holds whoever picked the move; first name wins for a pair declared twice
  local names = {}
  for n in pairs(self.moves) do names[#names + 1] = n end
  table.sort(names)
  for _, n in ipairs(names) do
    local mv = self.moves[n]
    if is_own(mv) then
      if #self._own_at < MAX_OWN then
        self._slot[n] = #self._own_at
        self._own_at[#self._own_at + 1] = n
      else
        log("[port:%s] own move '%s' ignored: the framework holds %d", self.name, n, MAX_OWN)
      end
    elseif mv.main then
      local k = mv.main * 256 + (mv.sub or 0)
      if self._by_pair[k] == nil then self._by_pair[k] = n end
    end
  end
  for _, r in ipairs(spec.rules or made.rules or {}) do self:rule(r) end

  if spec.pac and not P._once["inject:" .. spec.name] then
    P._once["inject:" .. spec.name] = true
    local dir  = spec.inject_dir or "ms0:/PSP/PLUGINS/mhfu_framework/inject"
    local ok = mhfu.inject_relocate(spec.fid, dir .. "/" .. spec.pac,
                                    dir .. "/" .. spec.orig)
    log("[port:%s] inject_relocate %s fid=%d '%s'", spec.name,
        ok and "OK" or "FAILED", spec.fid, spec.pac)
  end

  P.ports[spec.name] = self
  return self
end

--- Register the per-tick brain. `fn(state)` where state carries the monster,
--- the player, the distance between them and the live behaviour pair.
function Port:brain(fn) self._brain = fn; return self end

--- Run a declared move: enter the behaviour pair AND latch the port's clip.
---
--- Refuses the same move again within `min_gap` ticks: re-pulsing the moment a move ends is a
--- per-tick force, which restarts the move before its hitbox frames and sets the freeze bits.
---
--- Refuses a pair that is already running, ours or the engine's: entering it zeroes the phase
--- cursor and restarts the action (the clip, the once-per-entry hitbox, the engine's own chain),
--- so a brain waits for the move to end (`s.move == nil`). `opts.force = true` restarts anyway,
--- for debugging (an own move: plays past the notice, see Port:move); `opts.raw = true` writes the cells by hand even with the seam live (debugging
--- the seam itself).
function Port:play(move_name, min_gap, opts)
  local mv = self.moves[move_name]
  if not mv then log("[port:%s] no such move '%s'", self.name, tostring(move_name)); return false end
  if is_own(mv) then return self:move(move_name, opts) end
  if self.ent == 0 then return false end
  min_gap = min_gap or mv.min_gap or 2
  if self._played == move_name and (self._tick - (self._played_at or -99)) < min_gap then
    return false
  end
  local live_main, live_sub = mhfu.read_u8(self.ent + OFF_MAIN), mhfu.read_u8(self.ent + OFF_SUB)
  if live_main == mv.main and live_sub == mv.sub and not (opts and opts.force) then
    local n = (self._refused[move_name] or 0) + 1
    self._refused[move_name] = n
    if n == 1 or n % 20 == 0 then
      log("[port:%s] play('%s') refused: (%d,%d) is already running%s — writing it "
          .. "again restarts it from phase 0 (the hitbox spawns once per ENTRY). Let it "
          .. "end (after=/hold_max=), or play(name, gap, {force=true}) for a debug restart.%s",
          self.name, move_name, mv.main, mv.sub,
          self.scripted == move_name and " (ours)" or " (the engine's own)",
          n > 1 and string.format(" [x%d]", n) or "")
    end
    return false
  end
  self.scripted, self._played, self._played_at = move_name, move_name, self._tick
  self._phase_seen, self._phase_ticks, self._parked = nil, 0, false
  self._entered = { mv.main, mv.sub }
  self.clip = mv.clip and self.clips[mv.clip] or mv.anim
  -- With the seam live nothing is written here: the enter-action runs on the next AI frame
  -- (<= 33 ms) and provisions the pair. The clip is latched first because the dispatch that
  -- opens the move happens in that frame; `_req` checks the landing on the next tick.
  if native_ready() and not (opts and opts.raw) then
    local st = P.native_status()
    self._req = { name = move_name, at = self._tick, done = st and st.req_done or 0,
                  main = mv.main, sub = mv.sub }
    mhfu.em_request(mv.main, mv.sub, mv.mode or 0)
  else
    if not P._once.raw_warned and mhfu.em_installed == nil then
      P._once.raw_warned = true
      log("[port] this framework has no em_vhook seam: pairs are written by hand "
          .. "(unprovisioned — a forced charge parks; after=/hold_max= end it)")
    end
    act_set(self.ent, mv.main, mv.sub)
  end
  -- The latch covers one dispatch, the one that opens the move. One pair runs a sequence of
  -- executor dispatches, and overriding each restarts the clip from frame 0, so no animation
  -- would play to its end. `latch = <n>` on a move buys more.
  self._clip_uses = mv.latch or 1
  return true
end

--- Play own move `name` (a move with `entry` and no pair) through the framework's move player:
--- it starts at the monster's next AI frame, its clip on every body part, its attacks and steer
--- as declared, and its `after` follows in C when it ends on its clip, its length or a wall.
--- Asked while the monster's notice runs (mhfu.monster_state), it waits for combat unless
--- `opts.force`. A scripted pair move ends here. False while the seam is not live; false,
--- "stale" when `opts.build` (not 0) is not the build of the moves module the port runs.
function Port:move(name, opts)
  local want = opts ~= nil and opts.build or 0
  if want ~= 0 and not (self._made and self._made.build == want) then return false, "stale" end
  local mv = self.moves[name]
  if not is_own(mv) then
    log("[port:%s] move('%s'): not an own move", self.name, tostring(name))
    return false
  end
  if self.ent == 0 then return false end
  if not native_ready() or mhfu.em_play == nil then
    if not P._once["own:" .. self.name] then
      P._once["own:" .. self.name] = true
      log("[port:%s] own moves need the em_vhook seam, which is not live", self.name)
    end
    return false
  end
  if not self._native_armed then self:_arm_native() end
  local slot = self._slot[name]
  if slot == nil or not mhfu.em_play(self.ent, slot, opts ~= nil and opts.force == true) then
    log("[port:%s] move('%s') refused (slot %s)", self.name, name, tostring(slot))
    return false
  end
  -- the move player's own dispatch passes the executor hook: nothing may be latched over it
  self.scripted, self.clip, self._clip_uses, self._req, self._entered = nil, nil, 0, nil, nil
  self.own, self._own_last = name, name
  return true
end

--- The own move the move player is playing, or nil.
function Port:own_playing()
  if mhfu.em_playing == nil then return nil end
  local slot = mhfu.em_playing()
  return slot >= 0 and self._own_at[slot + 1] or nil
end

--- Latch a clip on the animation channel alone, with no behaviour write: the instrument under
--- play(), for asking which clip is in slot N of this build and whether it plays. It lands on
--- the next executor dispatch (a new action, ~0.5/s); `uses` defaults to 1 for the reason play()
--- latches once. Not for shipping mods: a clip with no behaviour under it is the clip/behaviour
--- mismatch this library removes.
function Port:latch(a1, uses)
  if self.ent == 0 then return false end
  self.clip, self._clip_uses = a1, uses or 1
  return true
end

--- The declared chain: `mv.after`, entered now. Returns true when a next link
--- was taken. If the engine already moved him INTO that pair (its own hand-off
--- landed where the declaration points) the move is adopted rather than
--- re-written — writing it would restart what the engine just started.
function Port:_walk_after(mv, ended, why)
  local nxt = mv.after
  if not nxt then return false end
  local nm = self.moves[nxt]
  if not nm then
    log("[port:%s] move '%s' declares after='%s', which is not a declared move",
        self.name, ended, tostring(nxt))
    return false
  end
  if is_own(nm) then
    local ok = self:move(nxt)
    if ok then log("[port:%s] '%s' %s -> after='%s' (own)", self.name, ended, why, nxt) end
    return ok
  end
  local lm, ls = mhfu.read_u8(self.ent + OFF_MAIN), mhfu.read_u8(self.ent + OFF_SUB)
  if lm == nm.main and ls == nm.sub then
    self.scripted, self._played, self._played_at = nxt, nxt, self._tick
    self._phase_seen, self._phase_ticks, self._parked = nil, 0, false
    self._entered = { nm.main, nm.sub }
    self.clip = nm.clip and self.clips[nm.clip] or nm.anim
    self._clip_uses = nm.latch or 1
    log("[port:%s] '%s' %s -> after='%s': the engine is already in (%d,%d), adopted",
        self.name, ended, why, nxt, lm, ls)
    return true
  end
  if self:play(nxt) then
    log("[port:%s] '%s' %s -> after='%s' (%d,%d)", self.name, ended, why, nxt,
        nm.main, nm.sub)
    return true
  end
  return false
end

--- Hand both channels back to the engine's own AI.
function Port:release()
  self.scripted, self.clip, self._played, self._clip_uses = nil, nil, nil, 0
  self._req, self._entered = nil, nil
  self:unpin()
end

--- A native 30 Hz brain rule, evaluated every AI frame in C by the seam with no Lua in the loop:
---
---   port:rule{ from = "lunge",            -- a move name, or { main = 1, sub = 4 },
---              from_main = { 0, 1 },      -- ...or a set of main states (any sub)
---              min_frames = 15,           -- the pair (or own move) must have stood this long
---              dist = { 250, 1e9 },       -- player XZ distance window [lo, hi)
---              receding = true,           -- only while the gap is GROWING
---              closing = false,           -- only while it is SHRINKING
---              play = "lunge_stop",       -- the move: its pair entered, or an own move played
---              mode = 0, cooldown = 30, count = mhfu.EM_UNLIMITED }
---
--- `from` an own move fires while it plays, its AI frames the dwell; a rule from a pair waits
--- while any own move plays. Fires at most once per entry into `from` (the pair changes when it
--- fires), then `cooldown` frames must pass; at most one rule that plays fires an AI frame. `on =
--- "<event>"` (a monster event, mhfu.MonsterEventKind) fires in the AI frame the event is seen
--- instead, `from` still gating when given;
--- `part` keeps a flinch or break to that part. `on = "flinch"` plays its own move in place of
--- the host's reaction. `force` plays an own move while the monster's notice runs, which would
--- otherwise wait for combat (a rule on "noticed" needs it). Up to mhfu.addr.EM_CFG.RULES_COUNT
--- rules per port, installed when the seam is live. Without the seam the rule is inert and logged
--- as such: the 2 Hz brain is the fallback.
---
--- A rule also takes conditions that must all hold (`conds = { {"hp_below", 0, 30}, ... }`: op,
--- arg, value; mhfu.EmCond), effects it applies each time it fires (`effects = { {"var_add", 2, 1} }`),
--- a `signal` (0-based index) that fires it in place of `on`, and `no_play = true`: it plays
--- nothing (no `play`) and applies its effects: each AI frame all such rules that hold run first,
--- wherever they sit, then the first rule that plays. The numbers are board indices, which the
--- moves module's `vars` and `signals` give names.
function Port:rule(spec)
  if #self._rules >= MAX_RULES then
    log("[port:%s] rule ignored: the seam holds %d", self.name, MAX_RULES)
    return self
  end
  local from = spec.from
  if type(from) == "string" and not self.moves[from] then
    log("[port:%s] rule: no such move '%s'", self.name, from)
    return self
  end
  local no_play = spec.no_play and true or false
  if not no_play and not self.moves[spec.play or ""] then
    log("[port:%s] rule: play='%s' is not a declared move", self.name, tostring(spec.play))
    return self
  end
  local label = no_play and "(no play)" or spec.play
  local fm = spec.from_main
  if type(fm) == "number" then fm = { fm } end
  if from == nil and #(fm or {}) == 0 and spec.on == nil and spec.signal == nil then
    log("[port:%s] rule -> '%s': no `from`, `on` or `signal`, ignored", self.name, label)
    return self
  end
  if #(spec.conds or {}) > MAX_CONDS or #(spec.effects or {}) > MAX_EFFECTS then
    log("[port:%s] rule -> '%s': the seam holds %d conditions and %d effects a rule, ignored",
        self.name, label, MAX_CONDS, MAX_EFFECTS)
    return self
  end
  if spec.signal ~= nil and (spec.signal < 0 or spec.signal >= MAX_SIGNALS) then
    log("[port:%s] rule -> '%s': signal %s is not 0..%d, ignored", self.name, label,
        tostring(spec.signal), MAX_SIGNALS - 1)
    return self
  end
  self._rules[#self._rules + 1] = {
    from = from, from_main = fm or {}, play = not no_play and spec.play or nil, label = spec.label,
    no_play = no_play, conds = spec.conds, effects = spec.effects, signal = spec.signal,
    on = spec.on, part = spec.part, force = spec.force and true or false,
    min_frames = spec.min_frames or 0,
    dist_lo = spec.dist and spec.dist[1] or 0, dist_hi = spec.dist and spec.dist[2] or 1.0e9,
    receding = spec.receding and true or false, closing = spec.closing and true or false,
    mode = spec.mode or 0, cooldown = spec.cooldown or 0, count = spec.count or EM_UNLIMITED,
  }
  self._native_armed = false          -- (re)install on the next tick
  return self
end

--- A rule's em_rule table: its moves resolved to pairs and own move slots; nil if it cannot be.
function Port:_seam_rule(r)
  local t = { from_mask = 0, from_sub = EM_ANY, mode = r.mode, min_frames = r.min_frames,
              dist_lo = r.dist_lo, dist_hi = r.dist_hi, receding = r.receding,
              closing = r.closing, cooldown = r.cooldown, count = r.count, on = r.on,
              part = r.part, force = r.force, conds = r.conds, effects = r.effects,
              signal = r.signal, no_play = r.no_play }
  local from = r.from
  if type(from) == "string" then
    local mv = self.moves[from]
    if is_own(mv) then t.from_move = self._slot[from]
    else t.from_mask, t.from_sub = 1 << mv.main, mv.sub end
  elseif type(from) == "table" then
    t.from_mask, t.from_sub = 1 << (from.main or 0), from.sub or EM_ANY
  end
  for _, m in ipairs(r.from_main) do t.from_mask = t.from_mask | (1 << m) end
  if t.from_mask == 0 and t.from_move == nil and r.on == nil and r.signal == nil then return nil end
  if r.no_play then return t end
  local to = self.moves[r.play]
  if is_own(to) then t.play_move = self._slot[r.play]
  else t.to_main, t.to_sub = to.main, to.sub end
  if (is_own(to) and t.play_move == nil) or (r.on == "flinch" and not is_own(to)) then
    return nil
  end
  return t
end

--- Counter `name` of the monster's board, which the moves module's `vars` declares: its value,
--- set to `v` first when given. 0, logged once, for a name it does not declare.
function Port:var(name, v)
  local k = self._vars[name]
  if k == nil or mhfu.em_var == nil then
    if k == nil and not P._once["var:" .. self.name .. ":" .. tostring(name)] then
      P._once["var:" .. self.name .. ":" .. tostring(name)] = true
      log("[port:%s] var('%s'): not in the moves module's vars", self.name, tostring(name))
    end
    return 0
  end
  return mhfu.em_var(k, v)
end

--- Raises signal `name`, which the moves module's `signals` declares: the rules on it fire in the
--- monster's next AI frame. False, logged once, for a name it does not declare.
function Port:fire(name)
  local k = self._signals[name]
  if k == nil or mhfu.em_signal == nil then
    if k == nil and not P._once["signal:" .. self.name .. ":" .. tostring(name)] then
      P._once["signal:" .. self.name .. ":" .. tostring(name)] = true
      log("[port:%s] fire('%s'): not in the moves module's signals", self.name, tostring(name))
    end
    return false
  end
  return mhfu.em_signal(k) == true
end

--- Starts the monster's rage in its next AI frame, through the engine's own start (it roars): the
--- same as a rule's `enrage` effect. False while the seam is not live.
function Port:enrage()
  return mhfu.em_rage ~= nil and mhfu.em_rage(true) == true
end

--- Ends the monster's rage in its next AI frame, through the engine's own end.
function Port:calm()
  return mhfu.em_rage ~= nil and mhfu.em_rage(false) == true
end

--- An own move's em_move table: its `after` as a slot, or as the back pair of a pair move.
function Port:_seam_move(name)
  local mv = self.moves[name]
  local t = {}
  for k, v in pairs(mv) do t[k] = v end
  t.after = nil
  local nm = mv.after and self.moves[mv.after]
  if is_own(nm) then t.after = self._slot[mv.after]
  elseif nm and nm.main then t.back = { nm.main, nm.sub, 0 } end
  return t
end

--- Install every claim, rule and the tail tip on the seam. Runs on the first tick the seam
--- is live for this port, and again after a redefine (hot reload). Idempotent:
--- the table is rewritten slot by slot, unused slots cleared.
function Port:_arm_native()
  self._native_armed = true
  local names = {}
  for n, mv in pairs(self.moves) do if claim_of(mv) then names[#names + 1] = n end end
  table.sort(names)
  local slot = 0
  for _, n in ipairs(names) do
    local mv = self.moves[n]
    local mask, sub = claim_of(mv)
    ---@cast mask integer
    ---@cast sub integer
    if slot < MAX_SUBS then
      mhfu.em_substitute(slot, mask, sub, mv.main, mv.sub, EM_UNLIMITED)
      log("[port:%s] claim: host enter-actions with main in 0x%02X%s -> '%s' (%d,%d), standing",
          self.name, mask, sub == EM_ANY and "" or string.format(" sub %d", sub),
          n, mv.main, mv.sub)
    else
      log("[port:%s] claim on '%s' ignored: the seam holds %d", self.name, n, MAX_SUBS)
    end
    slot = slot + 1
  end
  for k = slot, MAX_SUBS - 1 do mhfu.em_substitute(k, 0, EM_ANY, 0, 0, 0) end
  if mhfu.em_move ~= nil then
    mhfu.em_moves_clear()
    for k, n in ipairs(self._own_at) do
      if not mhfu.em_move(k - 1, self:_seam_move(n)) then
        log("[port:%s] own move '%s' not registered: no room for its turn keys", self.name, n)
      end
    end
    if #self._own_at > 0 then
      log("[port:%s] %d own move(s) registered", self.name, #self._own_at)
    end
  end
  for i = 1, MAX_RULES do
    local r = self._rules[i]
    local t = r and self:_seam_rule(r)
    if t then
      mhfu.em_rule(i - 1, t)
      log("[port:%s] rule %d: %s%s >=%d frames d[%d,%s)%s%s%s -> %s%s", self.name, i,
          r.on and string.format("on %s%s, ", r.on, r.part and (" part " .. r.part) or "") or "",
          type(r.from) == "string" and ("'" .. r.from .. "'")
            or string.format("main 0x%02X", t.from_mask),
          r.min_frames, math.floor(r.dist_lo),
          r.dist_hi >= 1e9 and "inf" or tostring(math.floor(r.dist_hi)),
          r.receding and " receding" or "", r.closing and " closing" or "",
          r.signal and (" signal " .. r.signal) or "",
          r.no_play and "no play" or ("'" .. r.play .. "'"),
          r.label and (" (" .. r.label .. ")") or "")
    else
      if r then
        log("[port:%s] rule %d -> %s cannot be installed", self.name, i,
            r.no_play and "no play" or ("'" .. r.play .. "'"))
      end
      mhfu.em_rule(i - 1, nil)
    end
  end
  if mhfu.em_sever_gate ~= nil then
    local pct = self._sever_below or 0
    if mhfu.em_sever_gate(pct) and pct > 0 then
      log("[port:%s] tail cut only below %d%% HP", self.name, pct)
    end
  end
  if mhfu.em_natural_rage ~= nil then
    mhfu.em_natural_rage(self._natural_rage)
    if not self._natural_rage then log("[port:%s] natural rage off", self.name) end
  end
  if self.tip then
    if mhfu.em_tip == nil then
      if not P._once["tip:" .. self.name] then
        P._once["tip:" .. self.name] = true
        log("[port:%s] this framework has no mhfu.em_tip: the tail tip stays unattached",
            self.name)
      end
    else
      log("[port:%s] tail tip: %d joint(s) %s", self.name, #self.tip,
          mhfu.em_tip(self.tip) and "follow their carriers until the drop" or "refused")
    end
  end
end

--- Point the monster at (x, z). Writes ENTITY.YAW only, so the engine's own rotator renders the
--- turn. The angle is atan2(dx, dz), the engine's convention, packed 0..0xFFFF over 0..2pi. The
--- cell is obeyed exactly, so a wrong heading is the formula, not a contested write.
function Port:face(x, z)
  if self.ent == 0 then return end
  local mx = rf(self.ent + OFF_POS)
  local mz = rf(self.ent + OFF_POS + 8)
  local hw = math.floor(atan2(x - mx, z - mz) * 32768.0 / math.pi) % 65536
  mhfu.entity_set_yaw(self.ent, hw)
end

--- Lock the monster's XZ where it stands. Y is left to the engine, which drives it to the floor
--- every frame; fighting that sinks him.
---
--- The lock rewrites the position at 2 Hz against a per-frame engine, so it always undoes
--- something: ~45 units a tick over a pair the engine dwells in, 500+ over a pursuit state, which
--- shows as a slide and snap-back. No pair is truly stationary, so the pin stays. It is a per-tick
--- write, acceptable only because it is conditional and scoped to one brain phase; unpin logs the
--- total, and per tick it should be near 45, not 500.
function Port:pin()
  if self.ent == 0 or self.pinned then return end
  self.pinned = { rf(self.ent + OFF_POS),
                  rf(self.ent + OFF_POS + 4),
                  rf(self.ent + OFF_POS + 8) }
end
function Port:unpin()
  if self.pinned and (self._slip_n or 0) > 0 then
    log("[port:%s] pin released after correcting %d ticks, %d units total",
        self.name, self._slip_n, math.floor(self._slip_sum or 0))
  end
  self.pinned, self._slip_n, self._slip_sum = nil, 0, 0
end

function Port:hold_pin()
  local p = self.pinned
  if not p or self.ent == 0 then return 0 end
  local x = rf(self.ent + OFF_POS)
  local z = rf(self.ent + OFF_POS + 8)
  local dx, dz = x - p[1], z - p[3]
  local slip = math.floor(math.sqrt(dx * dx + dz * dz))
  if slip > 0 then
    wf(self.ent + OFF_POS,     p[1])
    wf(self.ent + OFF_POS + 8, p[3])
  end
  return slip
end

-- ------------------------------------------------------------- hit tables
-- A port's hurtboxes, hitzone grid and attacks, written into the game. The studio exports them
-- as a generated `<name>_hit.lua` calling P.hit(name, tbl), where `tbl.writes` is the whole plan
-- (mhfu_studio.monster.runtime.plan, which also feeds the studio's debugger push): each `data`
-- goes at `at` once every `guard` reads back as given. Where a record goes, how many fit and what
-- is refused are decided there; this only writes what it is given.
--
-- Species data is map-wide per species id: beside a native of the same species this re-skins
-- the native too.
--
-- Applied once the port is live in-area, compared byte for byte every tick, and re-applied with a
-- log line when the live bytes change (an overlay reload) or a re-export registers a new id.
P._hit = P._hit or {}         -- port name -> table, from the generated module

local function unhex(s)
  return (s:gsub("%s", ""):gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end))
end

local function hex(s)
  return (s:gsub(".", function(c) return string.format("%02X", c:byte()) end))
end

--- The u32 at byte `i` of `bin`, as read_u32 returns it.
local function word(bin, i)
  local b0, b1, b2, b3 = bin:byte(i, i + 3)
  return b0 | b1 << 8 | b2 << 16 | b3 << 24
end

--- The live bytes at `at` are `bin`: a word at a time where aligned.
local function holds(at, bin)
  local i, n = 1, #bin
  while i <= n do
    local x = at + i - 1
    if x % 4 == 0 and i + 3 <= n then
      if mhfu.read_u32(x) ~= word(bin, i) then return false end
      i = i + 4
    else
      if mhfu.read_u8(x) ~= bin:byte(i) then return false end
      i = i + 1
    end
  end
  return true
end

local function put(at, bin)
  local i, n = 1, #bin
  while i <= n do
    local x = at + i - 1
    if x % 4 == 0 and i + 3 <= n then
      mhfu.write_u32(x, word(bin, i)); i = i + 4
    else
      mhfu.write_u8(x, bin:byte(i)); i = i + 1
    end
  end
end

local function live(at, n)
  local out = {}
  for i = 0, n - 1 do out[#out + 1] = string.format("%02X", mhfu.read_u8(at + i)) end
  return table.concat(out)
end

--- Register a port's hit tables: `tbl` = { species, id, notes, writes = {{ what, at,
--- guard = {{at, hex}, ...}, data = {hex, ...} }, ...} }. Keyed by PORT name so the data module
--- and the brain module can load in either order; the tick joins them.
function P.hit(name, tbl)
  if not tbl.writes then
    log("[port:%s] hit tables ignored: an export without `writes`, from an older studio; "
        .. "re-export it", name)
    return
  end
  local ws, size = {}, 0
  for i, w in ipairs(tbl.writes) do
    local guards = {}
    for _, g in ipairs(w.guard or {}) do guards[#guards + 1] = { at = g[1], bin = unhex(g[2]) } end
    ws[i] = { what = w.what, at = w.at, bin = unhex(table.concat(w.data)), guards = guards }
    size = size + #ws[i].bin
  end
  tbl._w = ws
  P._hit[name] = tbl            -- a new id applies on the next tick (hit_tick)
  log("[port:%s] hit tables registered: %d write(s), %d byte(s), id %s", name, #ws, size,
      tostring(tbl.id))
  for _, note in ipairs(tbl.notes or {}) do log("[port:%s] hit tables: %s", name, note) end
end

--- The first guard that does not hold, with what it reads.
local function failing(ws)
  for _, w in ipairs(ws) do
    for _, g in ipairs(w.guards) do
      if not holds(g.at, g.bin) then
        return string.format("%s: 0x%08X reads %s, the export expected %s", w.what, g.at,
                             live(g.at, #g.bin), hex(g.bin))
      end
    end
  end
end

--- Called from the tick for a live, in-area port. Applies on the first opportunity, on a new
--- export id and whenever the live bytes stop matching; all or nothing.
local function hit_tick(port)
  local tbl = P._hit[port.name]
  if not tbl then return end
  local intact = true
  for _, w in ipairs(tbl._w) do
    if not holds(w.at, w.bin) then intact = false; break end
  end
  if intact and port._hit_id == tbl.id then return end
  if tbl.species ~= port.species and not port._hit_species_warned then
    port._hit_species_warned = true
    log("[port:%s] hit tables are for species %s, the port runs as %s", port.name,
        tostring(tbl.species), tostring(port.species))
  end
  if not intact then
    local why = failing(tbl._w)
    if why then
      port._hit_id = nil
      if (port._hit_fail or 0) % 20 == 0 then
        log("[port:%s] hit tables NOT written: %s (not the table the export was built "
            .. "against: another overlay, or a relocated one)", port.name, why)
      end
      port._hit_fail = (port._hit_fail or 0) + 1
      return
    end
    for _, w in ipairs(tbl._w) do put(w.at, w.bin) end
  end
  -- _hit_id is what is in place; _hit_last names what got in last, for the log
  local id = tostring(tbl.id)
  local why = port._hit_last == nil and "first contact"
           or (port._hit_last == id and "live table changed under us" or "new export")
  port._hit_id, port._hit_last, port._hit_fail = tbl.id, id, 0
  local size = 0
  for _, w in ipairs(tbl._w) do size = size + #w.bin end
  log("[port:%s] HIT TABLES APPLIED (%s%s): %d write(s), %d byte(s)  id=%s", port.name, why,
      intact and ", already in place" or "", #tbl._w, size, id)
end

-- ------------------------------------------------------------- events
-- Registered once per boot and dispatched to whatever ports exist, so a mod reload re-runs its
-- setup, rebuilds its ports and never stacks a handler.

-- Every hit the port takes, with the amount. It fires from the 5 Hz monster poll on an HP drop,
-- so two hits within 200 ms arrive as one line with their sum. `st` is HITZONE_STATE, the grid the
-- hit went through (it follows bit 0x20 of FLAGS and flips mid-fight). Registered outside the
-- once block on purpose: the binding installs its trampoline once and replaces the closure, so a
-- library reload picks up a new format without stacking.
mhfu.on_bigmonster_damaged(function(ent, mtype, amount, hp, slot)
  for _, port in pairs(P.ports) do
    if port.ent == ent then
      port._hits = (port._hits or 0) + 1
      log("[port:%s] HIT #%d  -%d  hp=%d  st=%d f638=0x%X", port.name, port._hits,
          amount, hp, mhfu.read_u8(ent + OFF_HZ_STATE), mhfu.read_u32(ent + OFF_FLAGS))
    end
  end
end)

if not P._once.events then
P._once.events = true

mhfu.on_quest_targets_building(function(quest)
  if quest == 0 then return end
  for name, port in pairs(P.ports) do
    port._hit_last = nil        -- the quest's first hit apply logs as first contact
    for _, victim in ipairs(port.replace) do
      if mhfu.quest_has(quest, victim) then
        local ok = mhfu.quest_replace_monster(quest, victim, port.species)
        log("[port:%s] swap %d -> %d %s", name, victim, port.species,
            ok and "applied" or "FAILED")
      end
    end
  end
end)

mhfu.on_bigmonster_spawn(function(ent, mtype, slot, hp)
  for name, port in pairs(P.ports) do
    if mtype == port.species and port.ent == 0 then
      port.ent = ent
      port._native_armed = false   -- a new quest's seam starts empty
      log("[port:%s] SPAWN ent=0x%08X slot=%d hp=%d sec=%d pos=(%d,%d,%d)",
          name, ent, slot, hp, mhfu.read_u16(ent + OFF_SECTION),
          math.floor(rf(ent + OFF_POS)), math.floor(rf(ent + OFF_POS + 4)),
          math.floor(rf(ent + OFF_POS + 8)))
      -- the two decoders must agree; if they diverge, believe the debugger (see floats)
      log("[port:%s] f32 check  binding=%d  hand=%d", name,
          math.floor(mhfu.read_f32(ent + OFF_POS)),
          math.floor(u32_to_float(mhfu.read_u32(ent + OFF_POS))))
    end
  end
end)

mhfu.on_bigmonster_death(function(ent)
  for _, port in pairs(P.ports) do
    if port.ent == ent then port.ent = 0; port:release() end
  end
end)

-- The alignment seam: the host handler has just asked the executor for its own clip; with a
-- scripted move active the port's is returned instead. nil abstains and the engine's choice stands.
mhfu.on_bigmonster_action(function(ctx)
  for _, port in pairs(P.ports) do
    -- an own move's dispatch is the move player's: never painted over
    if ctx.entity == port.ent and mhfu.em_playing ~= nil and mhfu.em_playing() >= 0 then
      return nil
    end
    if ctx.entity == port.ent then
      if port.clip and (port._clip_uses or 0) > 0 then
        port._clip_uses = port._clip_uses - 1
        if port.clip ~= ctx.action_id then
          log("[port:%s] clip %d -> %d  (move=%s)", port.name, ctx.action_id,
              port.clip, tostring(port.scripted))
        end
        return port.clip
      end
      -- no scripted move, but the engine entered a declared pair itself: its clip paints it for
      -- as many dispatches as its latch says. No `move`, no `after`: the engine walks its chain.
      if port.ent ~= 0 and next(port._by_pair) ~= nil then
        local k = mhfu.read_u8(port.ent + OFF_MAIN) * 256 + mhfu.read_u8(port.ent + OFF_SUB)
        local name = port._by_pair[k]
        local mv = name and port.moves[name]
        local clip = mv and (mv.clip and port.clips[mv.clip] or mv.anim)
        if clip then
          local paint = port._paint
          if not paint or paint.key ~= k then
            paint = { key = k, uses = mv.latch or 1 }
            port._paint = paint
          end
          if paint.uses > 0 then
            paint.uses = paint.uses - 1
            if clip ~= ctx.action_id then
              log("[port:%s] clip %d -> %d  (engine entered '%s' itself)", port.name,
                  ctx.action_id, clip, name)
            end
            return clip
          end
        end
      end
    end
  end
  return nil
end, 10)

end  -- P._once.events

-- ------------------------------------------------------------- the tick
local g_tick = 0

local function port_state(port)
  -- travelled since the last tick is a 2 Hz brain's only speed signal: ~650 units in a charge,
  -- ~180 in the pursuit walk
  local ent = port.ent
  local mx = rf(ent + OFF_POS)
  local my = rf(ent + OFF_POS + 4)
  local mz = rf(ent + OFF_POS + 8)
  local px = rf(PLAYER_ENT + PLAYER_XYZ)
  local py = rf(PLAYER_ENT + PLAYER_XYZ + 4)
  local pz = rf(PLAYER_ENT + PLAYER_XYZ + 8)
  local sec, area = mhfu.read_u16(ent + OFF_SECTION), mhfu.get_area_index()
  -- every section has its own world frame: positions from either side of a section change do
  -- not subtract, so the first tick after one has no previous sample
  local dist = math.sqrt((mx - px) ^ 2 + (mz - pz) ^ 2)
  local last = port._last
  local travelled, closing = 0, 0
  if last and last[3] == sec then
    travelled = math.sqrt((mx - last[1]) ^ 2 + (mz - last[2]) ^ 2)
    -- a "does he reach the hunter" projection uses closing, not travelled: a hunter walking in
    -- shrinks the gap by both speeds. travelled says which pair is running.
    closing = last[4] - dist
  end
  port._last = { mx, mz, sec, dist }
  local target = mhfu.read_u32(ent + OFF_TARGET)
  return {
    travelled = travelled, closing = closing,
    move = port.scripted or port.own, own = port.own, pinned = (port.pinned ~= nil),
    slip = port.slip,
    -- what the LAST scripted move achieved, so a brain can drop a pair the
    -- engine refuses instead of re-issuing it forever
    last_move = port.last_move, last_move_ticks = port.last_move_ticks,
    since_play = g_tick - (port._played_at or -999),
    phase = mhfu.read_u8(ent + OFF_PHASE),
    port = port, ent = ent, tick = g_tick,
    x = mx, y = my, z = mz, px = px, py = py, pz = pz,
    dist = dist,
    section = sec, area = area, same_section = (sec == area),
    -- true on the first tick of a new frame: dist is readable, travelled is not
    reframed = (last == nil or last[3] ~= sec),
    -- true while the em_vhook seam drives play()/claims/rules
    native = native_ready(),
    -- has noticed a target and pursues it (the '!'), not full combat
    engaged = mhfu.entity_engaged(ent),
    -- who he has committed to: picked by a priority index where the Felyne outranks the hunter,
    -- so it flickers between them up close and sits on the cat at range. A diagnostic, not a
    -- gate: a brain gating on targets_player stutters. `acquired` is no combat signal either;
    -- it reads 0 on an engaged native mid-attack.
    target = target, targets_player = (target == PLAYER_ENT),
    acquired = mhfu.read_u8(ent + OFF_ACQUIRED),
    main = mhfu.read_u8(ent + OFF_MAIN), sub = mhfu.read_u8(ent + OFF_SUB),
    hp = mhfu.read_u16(ent + OFF_HP),
    max_hp = mhfu.read_u16(ent + OFF_MAX_HP),
    hz_state = mhfu.read_u8(ent + OFF_HZ_STATE),
    player_hp = mhfu.get_player_hp(),
  }
end

local relink_changed   -- below, beside the setups it re-runs

function mhfu_tick()
  drain()
  mhfu.paint_map()
  g_tick = g_tick + 1
  relink_changed()

  for _, port in pairs(P.ports) do
    port._tick = g_tick
    -- no entity but one of the species is live: adopt it (a missed spawn event, or a reload)
    if port.ent == 0 then
      local live = mhfu.entities_of_type(port.species)
      if live and live[1] and live[1] ~= 0 then
        port.ent = live[1]
        log("[port:%s] adopted live entity 0x%08X (no spawn event)", port.name, port.ent)
      end
    end
    if port.ent ~= 0 and mhfu.entity_alive(port.ent) then
      -- a paint of an engine-entered pair is per entry: forget it once he has left that pair,
      -- so his next native entry is painted too
      if port._paint then
        local k = mhfu.read_u8(port.ent + OFF_MAIN) * 256 + mhfu.read_u8(port.ent + OFF_SUB)
        if k ~= port._paint.key then port._paint = nil end
      end
      -- the seam comes up on the spawn event; the first tick after it is where
      -- the claims and rules go in (and again after a redefine)
      if not port._native_armed and native_ready() then
        if not P._once["native:" .. port.name] then
          P._once["native:" .. port.name] = true
          log("[port:%s] native seam live: play() enters through the engine's "
              .. "enter-action (provisioned); claims and rules installed", port.name)
        end
        port:_arm_native()
      end
      -- the own move playing; once ours has ended into its `after` pair move, that move is
      -- adopted as scripted, so its own after/hold_max apply
      port.own = port:own_playing()
      if port.own then
        port._own_last = port.own
      elseif port._own_last then
        local mv = port.moves[port._own_last]
        local nm = mv and mv.after and port.moves[mv.after]
        port._own_last = nil
        if nm and nm.main and not port.scripted and mhfu.read_u8(port.ent + OFF_MAIN) == nm.main
           and mhfu.read_u8(port.ent + OFF_SUB) == nm.sub then
          port.scripted, port._played, port._played_at = mv.after, mv.after, g_tick
          port._phase_seen, port._phase_ticks, port._parked = nil, 0, false
          port._entered = { nm.main, nm.sub }
          log("[port:%s] own move ended into after='%s' (%d,%d), adopted", port.name, mv.after,
              nm.main, nm.sub)
        end
      end
      -- a play() issued through the seam: did the engine take it?
      if port._req then
        local rq, st = port._req, P.native_status()
        local lm, ls = mhfu.read_u8(port.ent + OFF_MAIN), mhfu.read_u8(port.ent + OFF_SUB)
        if lm == rq.main and ls == rq.sub then
          log("[port:%s] '%s' entered natively (%d,%d), provisioned", port.name, rq.name, lm, ls)
          port._req = nil
        elseif ls == rq.sub then
          -- the translator's alternative main (em75 id 4: (1,4) or (2,4))
          log("[port:%s] '%s' entered natively as (%d,%d) — the translator's main for "
              .. "id %d; tracking that pair", port.name, rq.name, lm, ls, rq.sub)
          port._entered = { lm, ls }
          port._req = nil
        elseif st and st.req_done > rq.done and g_tick - rq.at >= 1
               and st.req_main == rq.main and st.req_sub == rq.sub then
          -- it landed (the cells read our pair right after the call) and was over within the
          -- tick, as a charge a 30 Hz rule ends does; the ended path below adopts or plays `after`
          log("[port:%s] '%s' entered natively (%d,%d) and was over within the tick "
              .. "(now (%d,%d))", port.name, rq.name, rq.main, rq.sub, lm, ls)
          port._req = nil
        elseif st and st.req_done > rq.done and g_tick - rq.at >= 1 then
          -- issued (req_done moved) but the cells never showed it: the enter-
          -- action declined it, or something re-entered in the same frame
          log("[port:%s] '%s' (%d,%d) was requested and issued but the cells read "
              .. "(%d,%d) — result after the call (%d,%d); last enter-actions: %s",
              port.name, rq.name, rq.main, rq.sub, lm, ls, st.req_main, st.req_sub,
              ring_text(st))
          port._req, port.scripted, port.clip, port._clip_uses = nil, nil, nil, 0
          port._entered = nil
        elseif g_tick - rq.at >= 3 then
          log("[port:%s] '%s' request never issued after %d ticks (req_done %s) — "
              .. "is the seam still latched?", port.name, rq.name, g_tick - rq.at,
              tostring(st and st.req_done))
          port._req, port.scripted, port.clip, port._clip_uses = nil, nil, nil, 0
          port._entered = nil
        end
      end
      if port.scripted then
        local mv = port.moves[port.scripted]
        local lm, ls = mhfu.read_u8(port.ent + OFF_MAIN), mhfu.read_u8(port.ent + OFF_SUB)
        local held = g_tick - (port._played_at or g_tick)
        local em, es = mv and mv.main, mv and mv.sub
        if port._entered then em, es = port._entered[1], port._entered[2] end
        if port._req then
          -- not landed yet (<= 1 tick): nothing to judge
        elseif mv and (lm ~= em or ls ~= es) then
          -- the pair is the ground truth: once the engine has left ours the move is over, and
          -- its clip goes with it, or it would paint whatever the engine does next
          log("[port:%s] move '%s' (%d,%d) ended after %d ticks -> (%d,%d)", port.name,
              port.scripted, em, es, held, lm, ls)
          local ended = port.scripted
          port.last_move, port.last_move_ticks = ended, held
          port.scripted, port.clip, port._clip_uses, port._entered = nil, nil, 0, nil
          port:_walk_after(mv, ended, "ended")
        elseif mv then
          -- still standing. `hold_max` ends it from here; otherwise watch the
          -- phase byte, because a pair whose phase stops moving has nothing
          -- left to do and will not leave by itself.
          if mv.hold_max and held >= mv.hold_max then
            log("[port:%s] move '%s' held %d ticks (hold_max %d) on (%d,%d)", port.name,
                port.scripted, held, mv.hold_max, lm, ls)
            local ended = port.scripted
            port.last_move, port.last_move_ticks = ended, held
            port.scripted, port.clip, port._clip_uses, port._entered = nil, nil, 0, nil
            if not port:_walk_after(mv, ended, "hold_max") then port:release() end
          elseif not mv.after then
            local phase = mhfu.read_u8(port.ent + OFF_PHASE)
            if phase == port._phase_seen then
              port._phase_ticks = (port._phase_ticks or 0) + 1
            else
              port._phase_seen, port._phase_ticks = phase, 0
            end
            if port._phase_ticks >= PARKED_TICKS and not port._parked then
              port._parked = true
              log("[port:%s] move '%s' PARKED: (%d,%d) phase %d unchanged for %d ticks. "
                  .. "A parked pair spawns nothing more — the engine provisions this pair "
                  .. "on its own way in (a run budget, a target) and act_set does not. "
                  .. "Declare after=/hold_max= on the move, or release().",
                  port.name, port.scripted, lm, ls, phase, port._phase_ticks)
            end
          end
        end
      end
      if port.pinned then
        port.slip = port:hold_pin()
        -- one log line per 20 corrections, not per tick: the running total is what matters
        port._slip_n = (port._slip_n or 0) + (port.slip > 4 and 1 or 0)
        port._slip_sum = (port._slip_sum or 0) + port.slip
        if port.slip > 40 and (port._slip_n == 1 or port._slip_n % 20 == 0) then
          log("[port:%s] pin has corrected %d ticks, %d units total (last %d) t=%d",
              port.name, port._slip_n, math.floor(port._slip_sum), port.slip, g_tick)
        end
      else
        port.slip = 0
      end
      -- the hit tables land once he is live in-area, and are re-checked here
      if mhfu.get_screen_state() == 17 then
        local ok, err = pcall(hit_tick, port)
        if not ok then log("[port:%s] hit error: %s", port.name, tostring(err)) end
      end
      if port._brain then
        local ok, err = pcall(port._brain, port_state(port))
        if not ok then log("[port:%s] brain error: %s", port.name, tostring(err)) end
      end
    elseif port.ent ~= 0 then
      port.ent = 0; port:release()
    end
  end
  drain()
end

-- ------------------------------------------------------------- ready
-- port.mod keeps a setup by name (the same name replaces it) and runs it now. A library reload
-- resets P.ports, so every kept setup runs again to rebuild them.
local function run_setup(name, fn, why)
  P._mods[name] = fn
  local ok, err = pcall(fn, P)
  log("[port] mod '%s' setup %s%s", name, ok and "ok" or ("FAILED: " .. tostring(err)), why)
end

-- A port's moves module re-ran in place (lua_host re-runs a changed library): every kept setup
-- runs again, so the ports take the new moves and rules and re-arm the seam.
relink_changed = function()
  for _, port in pairs(P.ports) do
    if port._made ~= nil and package.loaded[port.name .. "_moves"] ~= port._made then
      for name, fn in pairs(P._mods) do run_setup(name, fn, " (moves module changed)") end
      return
    end
  end
end

P.mod = function(name, fn) run_setup(name, fn, "") end

for name, fn in pairs(P._mods) do run_setup(name, fn, " (relink)") end

log("[port] mhfu_port runtime ready")

return P
