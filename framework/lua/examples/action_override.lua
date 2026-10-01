-- action_override.lua: a Tigrex shows his spin animation in place of one of his moves, about
-- every 10 s while he is in your section.
--
-- Run it in a quest with a Tigrex: one where he is native (Absolute Power), or the Elder's
-- 2-star Giadrome quest next to second_monster.lua, which swaps one in. framework.log shows
-- "[action_override] a1 N -> 43 (spin)" each time.
--
-- This changes what you SEE, not what he DOES. on_bigmonster_action rewrites the executor's
-- a1, which picks the clip; the move itself (its timing, hitbox and damage) is the (main, sub)
-- pair his brain chose, and it runs on underneath. The spin also lasts only until that move asks
-- for its next clip. To change the move, write the pair: mhfu_port's play() (ported_brute.lua).
--
-- Replace one dispatch now and then; answering every dispatch restarts the clip each time, so
-- no animation plays through, and halts his AI. Lua mods share one handler per event, the last
-- to register wins: don't run this next to cli_bridge.lua or a mhfu_port mod.

local SPIN = 43                                   -- a Tigrex's spin clip; a1 ids are per species
local EVERY = 300                                 -- quest-timer frames (30 Hz) between swaps
local GATE = mhfu.addr.ENTITY.FREEZE_GATE
local FROZEN = 0x100 | 0x10000                    -- the gate bits that halt the AI tick

local last = {}                                   -- entity -> quest timer at its last swap

mhfu.on_bigmonster_action(function(ctx)
  local ent = ctx.entity
  if ctx.type ~= mhfu.MON_TIGREX or mhfu.entity_section(ent) ~= mhfu.get_area_index() then
    return nil
  end
  local now, prev = mhfu.get_quest_timer(), last[ent]  -- the timer counts down
  if prev and now <= prev and prev - now < EVERY then return nil end
  last[ent] = now
  -- a forced action can set the freeze gate; clear it with the swap, never per tick
  local gate = mhfu.read_u32(ent + GATE)
  if gate & FROZEN ~= 0 then mhfu.write_u32(ent + GATE, gate & ~FROZEN) end
  mhfu.log(string.format("[action_override] a1 %d -> %d (spin)", ctx.action_id, SPIN))
  return SPIN
end)

mhfu.log("[action_override] ready")
