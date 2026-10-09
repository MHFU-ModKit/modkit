-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- village_follower.lua: a half-size Zinogre that follows the hunter around Pokke village. It walks
-- toward a point beside them, on the side away from the camera unless it is well over on the
-- other, turning as it goes, stops there, and turns on the spot to face them when they are behind
-- it. It can't be talked to and isn't solid.
--
-- Needs, once: the port built and placed for injection, from modkit with $MHFU_DATA and
-- $MHP3RD_DATA naming the extracted games,
--     uv run mhfu-port inject ports/zinogre.toml
-- (zinogre.bin in PLUGINS/mhfu_framework/inject/, zinogre_clips.lua in mods/lib/);
-- `memory = 64` in plugin.ini. Cold boot, then walk into the village.
--
-- npc_add reads the PAC from the memory stick, so it runs while mods load and once per boot (a
-- hot reload in the village keeps the NPC it added). The brain is the global mhfu_tick, 2 Hz while
-- the hunter roams: it reads npc_status and gives one order a tick, and C keeps turning and
-- chaining clips between ticks. Every walk arms npc_arrive, so C stops it at its aim on the frame
-- it gets there, also while a dialogue or a prompt zone holds the tick. What plays is learned from
-- status.entry, so a respawn or a clip that ended on its own needs no bookkeeping. Don't run it
-- next to a mhfu_port mod or cli_bridge.lua: they own the tick.

local PAC = "ms0:/PSP/PLUGINS/mhfu_framework/inject/zinogre.bin"
local FPS = 30                                    -- game frames a second: what a rate counts

local function deg(d) return d * 0x10000 // 360 end

local FAR, FASTER = 600, 900                      -- from the hunter: walk from idle, walk faster
local MOVED = 200                                 -- or once they moved this far from where it rested
local SIDE = 250                                  -- aims this far beside the hunter, not at them
local SWAP = 60                                   -- changes side once this far over their line
local CAM = 100                                   -- or to the aim this much farther from the camera
-- the stop clip slides ~290 at half size (~300 out of the faster walk): the arrival fires this far
-- short of the aim, less how far the aim draws off over LEAD seconds of the hunter's walk; never
-- under ARRIVE_MIN, so a tick held while the hunter walks lets it slide past the aim by 150 at most
local SLIDE, SLIDE_FAST, LEAD, ARRIVE_MIN = 290, 300, 2, 150
local WALK_MIN = 150                              -- a walk from idle covers this before it stops
local TURN_MIN = deg(50)                          -- idle turns start with the hunter this far off
local TURN_DIST = 120                             -- closer, the bearing is noise
local WALK_RATE = deg(150) // FPS                 -- YAW units a frame
local TURN_RATE = 0x4000 // 50                    -- a quarter turn over the turn clip's 50 frames
local BLEND = 6                                   -- frames a clip cross-fades

local ok, C = pcall(require, "zinogre_clips")     -- the entries, as the PAC was built
if not (ok and C.idle and C.start_walk_forward and C.stop_walk_forward
        and C.walk_forwards_faster and C.turn_right and C.turn_left) then
  mhfu.log("[village_follower] needs zinogre_clips.lua (mhfu-port inject) naming idle, "
           .. "start_walk_forward, stop_walk_forward, walk_forwards_faster, turn_right, turn_left")
  return
end
local IDLE, START, STOP_CLIP, FAST = C.idle, C.start_walk_forward, C.stop_walk_forward,
                                     C.walk_forwards_faster
local TURN_R, TURN_L = C.turn_right, C.turn_left

local slot = _G.village_follower_slot
if slot == nil then
  local err
  slot, err = mhfu.npc_add(PAC, { size = 0.5 })
  if not slot then mhfu.log("[village_follower] npc_add failed: " .. tostring(err)) end
  _G.village_follower_slot = slot or false
end
if not slot then return end

local function say(fmt, ...) mhfu.log(string.format("[village_follower] " .. fmt, ...)) end

-- the hunter's own position: mhfu.player_pos() is the camera eye
local HUNTER = mhfu.addr.PLAYER_ENTITY + mhfu.addr.ENTITY.TRANSLATION
local HUNTER_YAW = mhfu.addr.PLAYER_ENTITY + mhfu.addr.ENTITY.YAW

local seen                                        -- the last status, nil while not shown
local was                                         -- the hunter at the last tick
local rested                                      -- where the hunter was when it came to rest
local side = SIDE                                 -- the walk's aim: right of the hunter, or left

--- The hunter: where, facing (radians, as YAW), and their speed since the last tick.
local function hunter(st)
  local h = { x = mhfu.read_f32(HUNTER), z = mhfu.read_f32(HUNTER + 8),
              yaw = mhfu.read_u16(HUNTER_YAW) * math.pi / 0x8000, vx = 0, vz = 0 }
  local dt = was and (st.frames - was.frames) / FPS or 0
  if dt > 0 and dt < 2 then h.vx, h.vz = (h.x - was.x) / dt, (h.z - was.z) / dt end
  was = { x = h.x, z = h.z, frames = st.frames }
  return h
end

--- Distance from the NPC to the point `side` right of the hunter (left when negative), where
--- they will be after `lead` seconds at their speed.
local function to_aim(st, h, lead)
  local x = h.x + h.vx * lead - side * math.cos(h.yaw)
  local z = h.z + h.vz * lead + side * math.sin(h.yaw)
  return math.sqrt((x - st.x) ^ 2 + (z - st.z) ^ 2)
end

--- Picks `side`: the one whose aim is farther from the camera at `eye`, so it hides the hunter
--- less, unless it is well over on the other side (more beside the hunter than behind them); else
--- the side it is on, so a turn of theirs never has it cross them. CAM and SWAP keep it from
--- flipping each tick.
local function pick_side(st, h, eye)
  local c, s = math.cos(h.yaw), math.sin(h.yaw)
  local dx, dz = st.x - h.x, st.z - h.z
  local right, behind = dz * s - dx * c, -(dx * s + dz * c)
  local far = 0                                   -- the right aim's distance from the eye, less the left's
  if eye then
    far = math.sqrt((h.x - SIDE * c - eye.x) ^ 2 + (h.z + SIDE * s - eye.z) ^ 2)
        - math.sqrt((h.x + SIDE * c - eye.x) ^ 2 + (h.z - SIDE * s - eye.z) ^ 2)
  end
  local want = far > CAM and SIDE or far < -CAM and -SIDE or nil
  if want and right * want / SIDE >= -math.max(SWAP, behind) then
    side = want
  elseif math.abs(right) > SWAP then
    side = right < 0 and -SIDE or SIDE
  end
end

--- How far from its aim the arrival stops a walk on `entry`, and how far the aim is now.
local function arrival(st, h, entry)
  local now = to_aim(st, h, 0)
  local slide = entry == FAST and SLIDE_FAST or SLIDE
  return math.max(ARRIVE_MIN, slide - (to_aim(st, h, LEAD) - now)), now
end

--- Arms the stop of a walk on `entry` at its aim; npc_face disarms it, so it follows every face.
local function arm(st, h, entry)
  mhfu.npc_arrive(slot, (arrival(st, h, entry)), STOP_CLIP, BLEND, IDLE)
end

--- Walks on `entry` (none: the one playing) toward `side` of the hunter, stopping there.
local function walk(st, h, entry)
  if entry then mhfu.npc_play(slot, entry, BLEND) end
  mhfu.npc_face(slot, "hunter", side, 0, WALK_RATE)
  arm(st, h, entry or st.entry)
end

--- YAW units from the NPC's facing to the hunter, in -0x8000..0x7FFF. Positive is toward its left,
--- the way YAW grows, which is turn_left's way.
local function bearing_error(st, h)
  local want = math.floor(math.atan(h.x - st.x, h.z - st.z) * 32768 / math.pi + 0.5)
  return (want - st.yaw + 0x8000) % 0x10000 - 0x8000
end

local function stand(blend)
  mhfu.npc_play(slot, IDLE, blend)
  mhfu.npc_face(slot, "still")
end

local function from_idle(st, h, eye)
  local err = bearing_error(st, h)
  rested = rested or { x = h.x, z = h.z }
  if st.dist > TURN_DIST and math.abs(err) > TURN_MIN then
    say("turn %s, %.0f deg off", err > 0 and "left" or "right", math.abs(err) * 360 / 0x10000)
    mhfu.npc_play(slot, err > 0 and TURN_L or TURN_R, BLEND, IDLE)
    mhfu.npc_face(slot, "hunter", 0, 0, TURN_RATE)
    return
  end
  pick_side(st, h, eye)
  local moved = math.sqrt((h.x - rested.x) ^ 2 + (h.z - rested.z) ^ 2)
  local entry = st.dist > FASTER and FAST or START
  local stop, now = arrival(st, h, entry)
  if now - stop > WALK_MIN and (st.dist > FAR or moved > MOVED) then
    say("walk, %.0f away", st.dist)
    rested = nil
    walk(st, h, entry)
  else
    mhfu.npc_face(slot, "still")                  -- a turn's gentle facing ends with its clip
  end
end

--- C stops the walk at its aim; each tick re-aims it as the hunter moves.
local function from_walk(st, h, eye)
  local was_side = side
  pick_side(st, h, eye)
  if st.entry == START and st.dist > FASTER then
    say("walk faster, %.0f away", st.dist)
    walk(st, h, FAST)                             -- never back: START would replay its start-up
  elseif side ~= was_side then
    walk(st, h)
  else
    arm(st, h, st.entry)
  end
end

function mhfu_tick()
  local st = mhfu.npc_status(slot)
  if not st or st.object == 0 then seen = nil; return end
  local born = not seen or st.object ~= seen.object or st.frames < seen.frames
  seen = st
  local h = hunter(st)
  local ex, _, ez = mhfu.player_pos()
  local eye = ez and { x = ex, z = ez }
  local e = st.entry
  if born then
    say("spawned")
    rested = nil
    stand(0)
  elseif e == IDLE then
    from_idle(st, h, eye)
  elseif e == START or e == FAST then
    from_walk(st, h, eye)
  elseif (e ~= STOP_CLIP and e ~= TURN_R and e ~= TURN_L) or not st.playing then
    stand(BLEND)                                  -- a clip nobody asked for, or one that ended
  end
end

say("ready")
