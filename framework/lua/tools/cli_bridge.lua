-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- cli_bridge.lua: the in-game side of the debug shell's command block.
--
-- The shell (`mhfu shell`, `mhfu clips`) writes a command block (struct CLI_BRIDGE) in extra
-- RAM; this script polls it each tick and applies holds a debugger cannot do smoothly: force a
-- big monster's action (held coherently through the executor hook), freeze or unfreeze its AI,
-- and clear.
--
-- Deploy: copy to ms0:/PSP/PLUGINS/mhfu_framework/mods/cli_bridge.lua; it hot-reloads. Needs
-- memory=64 (the block is in extra RAM) and a big monster.
--
-- Lua has one executor hook (mhfu.on_bigmonster_action), and mhfu_port.lua owns it when it is
-- on the stick: a monster a port drives is forced through that port's latch, any other through
-- the bridge's own hook, registered on first use.

local BR        = mhfu.addr.CLI_BRIDGE_BLOCK
local CB        = mhfu.addr.CLI_BRIDGE
local MAGIC     = 0x4D484252          -- 'MHBR'
local FREEZE    = 0x100 | 0x10000     -- ENTITY.FREEZE_GATE bits that halt the AI tick
local OFF_GATE  = mhfu.addr.ENTITY.FREEZE_GATE
local HOLD      = 0x3FFFFFFF          -- latch uses: held until cleared

local CMD_FORCE = 1
local CMD_FREEZE = 2
local CMD_CLEAR = 3

-- loaded before the tick is wrapped below, so the library's mhfu_tick is the one wrapped
local has_port, port_lib = pcall(require, "mhfu_port")
local PORTS = has_port and type(port_lib) == "table" and port_lib or nil

local last_seq   = -1
local g_force    = nil                 -- forced action id, or nil
local g_ent      = nil                 -- the entity it is forced on
local g_port     = nil                 -- the port that latches it, or nil for the own hook
local g_freeze   = false
local g_slot     = 1                   -- target registry slot
local g_frozen   = nil                 -- slot whose AI tick we halted, or nil

local function entity(slot)
  local e = mhfu.entity_at(slot)
  if e and e ~= 0 then return e end
  return nil
end

local function port_of(ent)
  if not PORTS then return nil end
  for _, p in pairs(PORTS.ports) do
    if p.ent == ent then return p end
  end
  return nil
end

-- every dispatch of the forced entity returns OUR id, so the engine fans it coherently to all
-- body parts (no desync); nil leaves the engine's choice
_G.__cli_bridge_hooked = _G.__cli_bridge_hooked or false
local function own_hook()
  if _G.__cli_bridge_hooked then return end
  _G.__cli_bridge_hooked = true
  mhfu.on_bigmonster_action(function(ctx)
    local f, e = cli_bridge_force()
    if f ~= nil and ctx.entity == e then return f end
    return ctx.action_id
  end, 90)
end

-- global, so the hook (registered once) reads the state of the freshest reload
function cli_bridge_force() return g_force, g_ent end

local function force(slot, id)
  local ent = entity(slot)
  if g_port then g_port:release() end
  g_force, g_ent, g_port = id, ent, nil
  local p = ent and port_of(ent)
  if p then
    p:latch(id, HOLD)
    g_port = p
  else
    own_hook()
  end
end

local function unforce()
  if g_port then g_port:release() end
  g_force, g_ent, g_port = nil, nil, nil
end

-- clear the AI-halting bits once: the engine sets them too while an action is forced
local function unfreeze(slot)
  local ent = slot and entity(slot)
  if not ent then return end
  local f = mhfu.read_u32(ent + OFF_GATE)
  if (f & FREEZE) ~= 0 then mhfu.write_u32(ent + OFF_GATE, f & ~FREEZE) end
end

-- the monster moves again: nothing held, nothing halted
local function release(slot)
  unfreeze(slot)
  if g_frozen ~= slot then unfreeze(g_frozen) end
  g_frozen = nil
end

-- global (not local) so the chained wrapper resolves the FRESH definition after a
-- hot-reload instead of a stale captured upvalue
function cli_bridge_tick()
  -- poll the command block (only act on a new seq)
  if mhfu.read_u32(BR + CB.MAGIC) == MAGIC then
    local seq = mhfu.read_u32(BR + CB.SEQ)
    if seq ~= last_seq then
      last_seq = seq
      local cmd = mhfu.read_u32(BR + CB.CMD)
      local a0  = mhfu.read_u32(BR + CB.SLOT)
      local a1  = mhfu.read_u32(BR + CB.ARG)   -- action id / on-off
      g_slot = a0
      if cmd == CMD_FORCE then
        force(a0, a1)
      elseif cmd == CMD_FREEZE then
        g_freeze = (a1 ~= 0)
        if not g_freeze then release(a0) end
      elseif cmd == CMD_CLEAR then
        unforce()
        g_freeze = false
        release(a0)
        mhfu.write_u32(BR + CB.STATUS, 0)   -- nothing held
      end
      mhfu.write_u32(BR + CB.ACK, seq)
      mhfu.log(string.format("[cli_bridge] cmd=%d slot=%d a1=%d (seq=%d)%s", cmd, a0, a1, seq,
                             g_port and (" via port " .. g_port.name) or ""))
    end
  end

  local ent = entity(g_slot)
  if not ent then return end

  if g_force ~= nil then
    unfreeze(g_slot)   -- keep the AI tick alive so the forced action keeps (re)playing
    mhfu.write_u32(BR + CB.STATUS, g_force)   -- the action being held
  elseif g_freeze then
    -- halt the AI tick -> the monster holds its current pose
    if g_frozen ~= g_slot then unfreeze(g_frozen) end
    g_frozen = g_slot
    local f = mhfu.read_u32(ent + OFF_GATE)
    if (f & FREEZE) ~= FREEZE then mhfu.write_u32(ent + OFF_GATE, f | FREEZE) end
  end
end

-- lua_host calls one global `mhfu_tick`: chain onto a tick another script already
-- set instead of replacing it. Guarded against re-wrapping on hot reload (the
-- wrapper stays, cli_bridge_tick is redefined). Scripts load in directory order; a
-- script that sets mhfu_tick after this one replaces the wrapper, so re-save
-- cli_bridge.lua to re-wrap.
if not _G.__cli_bridge_installed then
  _G.__cli_bridge_prev_tick = _G.mhfu_tick   -- sandbox has no rawget; plain read is fine
  _G.__cli_bridge_installed = true
  function mhfu_tick()
    local p = _G.__cli_bridge_prev_tick
    if p then pcall(p) end
    cli_bridge_tick()
  end
end

mhfu.log(string.format("[cli_bridge] ready, polling 0x%08X%s", BR,
                       PORTS and "; ported monsters through mhfu_port" or ""))
