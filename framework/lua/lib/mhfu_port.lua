-- mhfu_port: the ported-monster runtime. It loads a port's assets into a quest and drives the
-- monster so that the move it executes and the clip on screen are the same thing.
--
-- A big monster runs on two channels: the executor's action id picks the CLIP, the behaviour pair
-- (ENTITY.MAIN_STATE, SUB_STATE) picks the MOVE, whose handler owns hitbox and damage. A port's
-- clips sit in the host's slots by position, not meaning, so a port declares the mapping
--
--   moves = { charge = { main = 3, sub = 6, clip = "charge", after = "skid", hold_max = 8 },
--             skid   = { main = 0, sub = 3, clip = "stop" } }
--
-- and port:play("charge") enters the pair and latches the port's clip in one call.
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
local OFF_PREV_MAIN = E.PREV_MAIN      -- act_set stores the outgoing pair here; handlers
local OFF_PREV_SUB  = E.PREV_SUB       -- read it on transitions
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
local MAX_SUBS, MAX_RULES = 4, 4

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
  mhfu.write_u8(ent + OFF_PREV_MAIN, mhfu.read_u8(ent + OFF_MAIN))
  mhfu.write_u8(ent + OFF_PREV_SUB,  mhfu.read_u8(ent + OFF_SUB))
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

--- Declare a ported monster.
--
--   name    identifier, also the hot-reload key
--   species host species id the port rides on (mhfu.MON_TIGREX)
--   pac     ported PAC filename under the framework's inject/ directory
--   orig    the original PAC it replaces, same directory
--   fid     cosmetic file id for the injector (matching is by content)
--   replace list of quest monster ids to swap for `species`
--   clips   name -> executor a1, the port's own animation vocabulary
--   moves   name -> { main, sub, clip }: which host behaviour runs and which of the port's
--           clips is shown while it does. `claim = { main = 1 }` (or `{ main = {0, 1}, sub = 7 }`)
--           enters this move in place of every host-brain pair in that set, so a port with one
--           attack rides the host's whole attack timing.
--
-- Returns a port handle. Safe to call again (hot reload): the injector is armed once per boot.
function P.define(spec)
  -- a redefine keeps the bound entity: the spawn event that bound it will not fire again, and
  -- ent = 0 would stop the brain. The rest of the state resets.
  local prev = P.ports[spec.name]
  local self = setmetatable({
    name    = spec.name,
    species = spec.species,
    clips   = spec.clips or {},
    moves   = spec.moves or {},
    replace = spec.replace or {},
    ent     = prev and prev.ent or 0,
    clip    = nil,      -- currently latched executor a1, nil = hands off
    move    = nil,      -- currently scripted move name
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
  }, Port)
  -- declared pairs the engine enters on its own get the port's clip too (the action hook paints
  -- them), so the mapping holds whoever picked the move; first name wins for a pair declared twice
  local names = {}
  for n in pairs(self.moves) do names[#names + 1] = n end
  table.sort(names)
  for _, n in ipairs(names) do
    local mv = self.moves[n]
    local k = (mv.main or 0) * 256 + (mv.sub or 0)
    if self._by_pair[k] == nil then self._by_pair[k] = n end
  end

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
--- for debugging; `opts.raw = true` writes the cells by hand even with the seam live (debugging
--- the seam itself).
function Port:play(move_name, min_gap, opts)
  local mv = self.moves[move_name]
  if not mv then log("[port:%s] no such move '%s'", self.name, tostring(move_name)); return false end
  if self.ent == 0 then return false end
  min_gap = min_gap or 2
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
          self.move == move_name and " (ours)" or " (the engine's own)",
          n > 1 and string.format(" [x%d]", n) or "")
    end
    return false
  end
  self.move, self._played, self._played_at = move_name, move_name, self._tick
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
  local lm, ls = mhfu.read_u8(self.ent + OFF_MAIN), mhfu.read_u8(self.ent + OFF_SUB)
  if lm == nm.main and ls == nm.sub then
    self.move, self._played, self._played_at = nxt, nxt, self._tick
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
  self.move, self.clip, self._played, self._clip_uses = nil, nil, nil, 0
  self._req, self._entered = nil, nil
  self:unpin()
end

--- A native 30 Hz brain rule, evaluated every frame by the seam's AI-step stub with no Lua in
--- the loop:
---
---   port:rule{ from = "lunge",            -- a move name, or { main = 1, sub = 4 },
---              from_main = { 0, 1 },      -- ...or a set of main states (any sub)
---              min_frames = 15,           -- the pair must have stood this long
---              dist = { 250, 1e9 },       -- player XZ distance window [lo, hi)
---              receding = true,           -- only while the gap is GROWING
---              closing = false,           -- only while it is SHRINKING
---              play = "lunge_stop",       -- the move to enter (its main, sub)
---              mode = 0, cooldown = 30, count = mhfu.EM_UNLIMITED }
---
--- Fires at most once per entry into `from` (the pair changes when it fires),
--- then `cooldown` frames must pass. Up to 4 rules per port; declared in the
--- mod's setup and installed when the seam is live. Without the seam the rule
--- is inert and logged as such — the 2 Hz brain is the fallback.
function Port:rule(spec)
  if #self._rules >= MAX_RULES then
    log("[port:%s] rule ignored: the seam holds %d", self.name, MAX_RULES)
    return self
  end
  local r = { mask = 0, sub = EM_ANY, min_frames = spec.min_frames or 0,
              dist_lo = spec.dist and spec.dist[1] or 0,
              dist_hi = spec.dist and spec.dist[2] or 1.0e9,
              receding = spec.receding and true or false,
              closing = spec.closing and true or false,
              mode = spec.mode or 0, cooldown = spec.cooldown or 0,
              count = spec.count or EM_UNLIMITED, play = spec.play, label = spec.label }
  local from = spec.from
  if type(from) == "string" then
    local mv = self.moves[from]
    if not mv then log("[port:%s] rule: no such move '%s'", self.name, from); return self end
    r.mask, r.sub = 1 << mv.main, mv.sub
  elseif type(from) == "table" then
    r.mask, r.sub = 1 << (from.main or 0), from.sub or EM_ANY
  end
  local fm = spec.from_main
  if type(fm) == "number" then fm = { fm } end
  for _, m in ipairs(fm or {}) do r.mask = r.mask | (1 << m) end
  local to = self.moves[spec.play or ""]
  if not to then
    log("[port:%s] rule: play='%s' is not a declared move", self.name, tostring(spec.play))
    return self
  end
  r.to_main, r.to_sub = to.main, to.sub
  if r.mask == 0 then
    log("[port:%s] rule -> '%s': no `from` pair, ignored", self.name, spec.play)
    return self
  end
  self._rules[#self._rules + 1] = r
  self._native_armed = false          -- (re)install on the next tick
  return self
end

--- Install every claim and rule on the seam. Runs on the first tick the seam
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
  for i = 1, MAX_RULES do
    local r = self._rules[i]
    if r then
      mhfu.em_rule(i - 1, { from_mask = r.mask, from_sub = r.sub, to_main = r.to_main,
                            to_sub = r.to_sub, mode = r.mode, min_frames = r.min_frames,
                            dist_lo = r.dist_lo, dist_hi = r.dist_hi, receding = r.receding,
                            closing = r.closing, cooldown = r.cooldown, count = r.count })
      log("[port:%s] rule %d: main 0x%02X%s >=%d frames d[%d,%s)%s%s -> '%s' (%d,%d)",
          self.name, i, r.mask, r.sub == EM_ANY and "" or (" sub " .. r.sub),
          r.min_frames, math.floor(r.dist_lo),
          r.dist_hi >= 1e9 and "inf" or tostring(math.floor(r.dist_hi)),
          r.receding and " receding" or "", r.closing and " closing" or "",
          r.play, r.to_main, r.to_sub)
    else
      mhfu.em_rule(i - 1, nil)
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
-- The port's own hurtboxes, hitzones and attacks, written into the game. The editor exports a
-- port's [[hurtbox]] / [[hitzone]] as a generated `<name>_hit.lua` that calls P.hit(name, tbl).
--
-- Both hang off the species row (SPECIES_TABLE + species * SPECIES.STRIDE): HURTBOX_SET is the
-- HIT_VOLUME set the species walks (the overlay holds several and only this pointer picks one),
-- HITZONE_STATES points at its hitzone grids. Volumes go in place over the original records, then
-- a sentinel: shorter is fine, longer is not (the bytes past the original sentinel are someone
-- else's), so the original count, measured on first contact, is the cap. Nothing is relocated
-- and no pointer rewritten.
--
-- Species data is map-wide per species id: as a replace the port is the only one of its species
-- in the quest, but beside a native of the same species this re-skins the native too.
--
-- Applied once the entity is live in-area, re-checked every tick against one record and one grid
-- byte, and re-applied with a log line if either changed (an overlay reload shows up here) or a
-- re-export registers a new content id.
--
-- Attacks go the other way: a handler spawns an attack by id, and ATTACK_RECORD `id` at
-- attack_tables.records names a volume set (VOLUME_SET), reached through the overlay's pointer
-- table at attack_tables.volumes + set*4; both are the species overlay's static addresses, from
-- the export. Each authored set goes in place over the host set of that index, then a sentinel;
-- each `attacks` entry writes only the levers it names. The addresses are not handed over by the
-- engine, so a set is written only if its live record count equals the exported `cap` on first
-- contact; otherwise it is left alone with a log line.
local HV = mhfu.addr.HIT_VOLUME
local SPECIES_TABLE  = mhfu.addr.SPECIES_TABLE
local SPECIES_STRIDE = mhfu.addr.SPECIES.STRIDE
local F_SPHERES      = mhfu.addr.SPECIES.HURTBOX_SET
local F_STATES       = mhfu.addr.SPECIES.HITZONE_STATES
local REC            = HV.SIZE
local GRID_BLOCK     = mhfu.addr.HITZONE_GRID.SIZE
local GRID_ROWS, GRID_COLS = 7, 10
local SENTINEL       = 0xFFFF
local MAX_RECORDS    = 512    -- a walk bound; the longest set in the game is 49
local ATK_REC        = mhfu.addr.ATTACK_RECORD.SIZE
local ATK_POWER, ATK_ELEMENT, ATK_VOLUME = mhfu.addr.ATTACK_RECORD.POWER,
  mhfu.addr.ATTACK_RECORD.ELEMENT, mhfu.addr.ATTACK_RECORD.VOLUME_SET

P._hit = P._hit or {}         -- port name -> table, from the generated module

--- Register a port's hit tables. `tbl` = { species, id, volumes = {{bone, shape,
--- row, part, flags, radius, ax, ay, az, bx, by, bz}, ...} | nil, grid = {{row*7}
--- x states} | nil }. Keyed by PORT name so the data module and the brain module
--- can load in either order; the tick joins them.
function P.hit(name, tbl)
  P._hit[name] = tbl            -- a new id applies on the next tick (hit_tick)
  local nsets = 0
  if tbl.attack_sets then for _ in pairs(tbl.attack_sets) do nsets = nsets + 1 end end
  log("[port:%s] hit tables registered: %s volume(s), %s grid state(s), %d attack "
      .. "set(s), %s attack record(s), id %s",
      name, tbl.volumes and #tbl.volumes or "no", tbl.grid and #tbl.grid or "no",
      nsets, tbl.attacks and #tbl.attacks or "no", tostring(tbl.id))
end

local function species_row(port)
  return SPECIES_TABLE + port.species * SPECIES_STRIDE
end

local function write_record(at, r)
  mhfu.write_u16(at + HV.BONE, r[1])
  mhfu.write_u16(at + HV.SHAPE, r[2])
  mhfu.write_u16(at + HV.HITZONE_ROW, r[3])
  mhfu.write_u16(at + HV.PART, r[4])
  mhfu.write_u32(at + HV.FLAGS, r[5])
  wf(at + HV.RADIUS, r[6])
  wf(at + HV.OFFSET_A, r[7]); wf(at + HV.OFFSET_A + 4, r[8]); wf(at + HV.OFFSET_A + 8, r[9])
  wf(at + HV.OFFSET_B, r[10]); wf(at + HV.OFFSET_B + 4, r[11]); wf(at + HV.OFFSET_B + 8, r[12])
end

local function write_sentinel(at)
  for i = 0, 3 do mhfu.write_u16(at + i * 2, SENTINEL) end
  for i = 8, REC - 4, 4 do mhfu.write_u32(at + i, 0) end
end

--- The live address of attack volume set `set`, through the overlay's pointer
--- table, or nil (+why) when the export names a set the table does not have.
local function attack_set_base(tbl, set)
  local at = tbl.attack_tables
  if not at or not at.volumes then return nil, "no attack_tables in the export" end
  if at.n_sets and set >= at.n_sets then
    return nil, string.format("set %d but the host has %d", set, at.n_sets)
  end
  local base = mhfu.read_u32(at.volumes + set * 4)
  if base == 0 or not mhfu.mem_valid(base) then
    return nil, string.format("set %d pointer 0x%08X invalid", set, base)
  end
  return base
end

--- Sorted set indices, so the log and the writes are in one order every time.
local function attack_set_indices(tbl)
  local idx = {}
  if tbl.attack_sets then
    for k in pairs(tbl.attack_sets) do idx[#idx + 1] = k end
  end
  table.sort(idx)
  return idx
end

--- Does the live table still carry what we wrote? One record and one byte —
--- cheap enough for every tick, specific enough to catch a reload.
local function hit_intact(port, tbl)
  local row = species_row(port)
  if tbl.volumes then
    local base = mhfu.read_u32(row + F_SPHERES)
    if base == 0 then return false end
    local n = math.min(#tbl.volumes, port._hit_cap or #tbl.volumes)
    if n == 0 then
      if mhfu.read_u16(base) ~= SENTINEL then return false end
    else
      local r = tbl.volumes[1]
      if mhfu.read_u16(base) ~= r[1] then return false end
      if math.abs(rf(base + HV.RADIUS) - r[6]) > 0.01 then return false end
      if mhfu.read_u16(base + n * REC) ~= SENTINEL then return false end
    end
  end
  if tbl.grid and tbl.grid[1] then
    local stt = mhfu.read_u32(row + F_STATES)
    if stt == 0 then return false end
    local blk = mhfu.read_u32(stt)
    if blk == 0 or mhfu.read_u8(blk + 1) ~= tbl.grid[1][1][2] then return false end
  end
  for _, set in ipairs(attack_set_indices(tbl)) do
    local spec = tbl.attack_sets[set]
    local base = attack_set_base(tbl, set)
    if not base then return false end
    local cap = (port._atk_caps and port._atk_caps[set]) or spec.cap or #spec.volumes
    local n = math.min(#spec.volumes, cap)
    if n == 0 then
      if mhfu.read_u16(base) ~= SENTINEL then return false end
    else
      local r = spec.volumes[1]
      if mhfu.read_u16(base) ~= r[1] then return false end
      if math.abs(rf(base + HV.RADIUS) - r[6]) > 0.01 then return false end
      if mhfu.read_u16(base + n * REC) ~= SENTINEL then return false end
    end
  end
  if tbl.attacks and tbl.attack_tables and tbl.attack_tables.records then
    for _, a in ipairs(tbl.attacks) do
      local rec = tbl.attack_tables.records + a.id * ATK_REC
      if a.power and mhfu.read_u8(rec + ATK_POWER) ~= a.power then return false end
      if a.element and mhfu.read_u8(rec + ATK_ELEMENT) ~= a.element then return false end
      if a.volume and mhfu.read_u8(rec + ATK_VOLUME) ~= a.volume then return false end
    end
  end
  return true
end

local function hit_apply(port, tbl)
  local row = species_row(port)
  local wrote = {}
  if tbl.volumes then
    local base = mhfu.read_u32(row + F_SPHERES)
    if base == 0 or not mhfu.mem_valid(base) then
      return false, string.format("no set pointer at 0x%08X", row + F_SPHERES)
    end
    -- the cap is the original count, measured once per boot (P._once): after a library reload,
    -- measuring again would count our shorter table and shrink the cap
    local capkey = "hitcap:" .. tostring(port.species)
    if not P._once[capkey] then
      local n = 0
      while n < MAX_RECORDS and mhfu.read_u16(base + n * REC) ~= SENTINEL do
        n = n + 1
      end
      P._once[capkey] = n
      log("[port:%s] host set 0x%08X holds %d record(s) — the in-place cap",
          port.name, base, n)
    end
    port._hit_cap = P._once[capkey]
    local n = #tbl.volumes
    if n > port._hit_cap then
      log("[port:%s] %d volume(s) but only %d fit in place — TRUNCATED",
          port.name, n, port._hit_cap)
      n = port._hit_cap
    end
    for i = 1, n do write_record(base + (i - 1) * REC, tbl.volumes[i]) end
    write_sentinel(base + n * REC)
    wrote[#wrote + 1] = string.format("%d volume(s) @0x%08X", n, base)
  end
  if tbl.grid then
    local stt = mhfu.read_u32(row + F_STATES)
    if stt == 0 or not mhfu.mem_valid(stt) then
      return false, string.format("no state table at 0x%08X", row + F_STATES)
    end
    -- the state count is stored nowhere: the pointer table sits right after
    -- the last block it points at, so it is (table - first_block) / 0x48
    local b0 = mhfu.read_u32(stt)
    local have = (stt - b0) // GRID_BLOCK
    if have < 1 or have > 8 then
      return false, string.format("state table 0x%08X -> 0x%08X: %d states?",
                                  stt, b0, have)
    end
    local n = math.min(#tbl.grid, have)
    if #tbl.grid ~= have then
      log("[port:%s] grid: manifest has %d state(s), the species %d — writing %d",
          port.name, #tbl.grid, have, n)
    end
    for s = 1, n do
      local blk = mhfu.read_u32(stt + (s - 1) * 4)
      for r = 1, GRID_ROWS do
        local rowv = tbl.grid[s][r]
        for c = 1, GRID_COLS do
          mhfu.write_u8(blk + (r - 1) * GRID_COLS + (c - 1), rowv[c])
        end
      end
    end
    wrote[#wrote + 1] = string.format("%d grid state(s) @0x%08X", n, b0)
  end
  -- attack sets, each in place over the host set of that index. The live count is measured once
  -- per set (P._once, as for the hurtbox cap) and must equal the exported `cap`, or nothing is
  -- written: the address is static, and a wrong count means a different overlay is loaded.
  local sets = attack_set_indices(tbl)
  if #sets > 0 then
    port._atk_caps = port._atk_caps or {}
    for _, set in ipairs(sets) do
      local spec = tbl.attack_sets[set]
      local base, why = attack_set_base(tbl, set)
      if not base then return false, "attack " .. why end
      local capkey = string.format("atkcap:%d:%d", port.species, set)
      if not P._once[capkey] then
        local n = 0
        while n < MAX_RECORDS and mhfu.read_u16(base + n * REC) ~= SENTINEL do
          n = n + 1
        end
        if spec.cap and n ~= spec.cap then
          return false, string.format(
            "attack set %d @0x%08X holds %d record(s), the export expected %d — "
            .. "not the table the export was built against; NOT written",
            set, base, n, spec.cap)
        end
        P._once[capkey] = n
        log("[port:%s] attack set %d @0x%08X holds %d record(s) — the in-place cap",
            port.name, set, base, n)
      end
      port._atk_caps[set] = P._once[capkey]
    end
    local n_sets, n_vols = 0, 0
    for _, set in ipairs(sets) do
      local spec = tbl.attack_sets[set]
      local base = attack_set_base(tbl, set)
      local cap = port._atk_caps[set]
      local n = #spec.volumes
      if n > cap then
        log("[port:%s] attack set %d: %d volume(s) but only %d fit in place — "
            .. "TRUNCATED", port.name, set, n, cap)
        n = cap
      end
      for i = 1, n do write_record(base + (i - 1) * REC, spec.volumes[i]) end
      write_sentinel(base + n * REC)
      n_sets, n_vols = n_sets + 1, n_vols + n
    end
    wrote[#wrote + 1] = string.format("%d attack set(s)/%d volume(s) via 0x%08X",
                                      n_sets, n_vols, tbl.attack_tables.volumes)
  end
  if tbl.attacks and #tbl.attacks > 0 then
    local at = tbl.attack_tables
    if not at or not at.records then
      return false, "attacks but no attack_tables.records in the export"
    end
    local n = 0
    for _, a in ipairs(tbl.attacks) do
      if at.n_records and a.id >= at.n_records then
        log("[port:%s] attack record %d but the host has %d — skipped",
            port.name, a.id, at.n_records)
      else
        local rec = at.records + a.id * ATK_REC
        if a.power   then mhfu.write_u8(rec + ATK_POWER,   a.power)   end
        if a.element then mhfu.write_u8(rec + ATK_ELEMENT, a.element) end
        if a.volume  then mhfu.write_u8(rec + ATK_VOLUME,  a.volume)  end
        n = n + 1
      end
    end
    wrote[#wrote + 1] = string.format("%d attack record(s) @0x%08X", n, at.records)
  end
  return true, table.concat(wrote, ", ")
end

--- Called from the tick for a live, in-area port. Applies on the first
--- opportunity, on a new export id and whenever the live bytes stop matching.
local function hit_tick(port)
  local tbl = P._hit[port.name]
  if not tbl then return end
  if port._hit_id == tbl.id and hit_intact(port, tbl) then return end
  -- _hit_id is what is in place (nil after a failed apply, so it retries); _hit_last names
  -- what got in last, for the log
  local id = tostring(tbl.id)
  local why = port._hit_last == nil and "first contact"
           or (port._hit_last == id and "live table changed under us" or "new export")
  local ok, what = hit_apply(port, tbl)
  if ok then
    port._hit_id, port._hit_last = tbl.id, id
    log("[port:%s] HIT TABLES APPLIED (%s): %s  id=%s", port.name, why, what, id)
  else
    port._hit_id = nil
    if (port._hit_fail or 0) % 20 == 0 then
      log("[port:%s] hit tables NOT applied: %s", port.name, tostring(what))
    end
    port._hit_fail = (port._hit_fail or 0) + 1
  end
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
    if ctx.entity == port.ent then
      if port.clip and (port._clip_uses or 0) > 0 then
        port._clip_uses = port._clip_uses - 1
        if port.clip ~= ctx.action_id then
          log("[port:%s] clip %d -> %d  (move=%s)", port.name, ctx.action_id,
              port.clip, tostring(port.move))
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
    move = port.move, pinned = (port.pinned ~= nil),
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

function mhfu_tick()
  drain()
  mhfu.paint_map()
  g_tick = g_tick + 1

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
          port._req, port.move, port.clip, port._clip_uses = nil, nil, nil, 0
          port._entered = nil
        elseif g_tick - rq.at >= 3 then
          log("[port:%s] '%s' request never issued after %d ticks (req_done %s) — "
              .. "is the seam still latched?", port.name, rq.name, g_tick - rq.at,
              tostring(st and st.req_done))
          port._req, port.move, port.clip, port._clip_uses = nil, nil, nil, 0
          port._entered = nil
        end
      end
      if port.move then
        local mv = port.moves[port.move]
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
              port.move, em, es, held, lm, ls)
          local ended = port.move
          port.last_move, port.last_move_ticks = ended, held
          port.move, port.clip, port._clip_uses, port._entered = nil, nil, 0, nil
          port:_walk_after(mv, ended, "ended")
        elseif mv then
          -- still standing. `hold_max` ends it from here; otherwise watch the
          -- phase byte, because a pair whose phase stops moving has nothing
          -- left to do and will not leave by itself.
          if mv.hold_max and held >= mv.hold_max then
            log("[port:%s] move '%s' held %d ticks (hold_max %d) on (%d,%d)", port.name,
                port.move, held, mv.hold_max, lm, ls)
            local ended = port.move
            port.last_move, port.last_move_ticks = ended, held
            port.move, port.clip, port._clip_uses, port._entered = nil, nil, 0, nil
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
                  port.name, port.move, lm, ls, phase, port._phase_ticks)
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

P.mod = function(name, fn) run_setup(name, fn, "") end

for name, fn in pairs(P._mods) do run_setup(name, fn, " (relink)") end

log("[port] mhfu_port runtime ready")

return P
