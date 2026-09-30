-- second_monster.lua: a second Tigrex in the quest, one that fights like the first.
--
-- Run it in a quest with a native Tigrex (Absolute Power), or in the Village Elder's 2-star
-- Giadrome quest (`mhfu go-on-quest --rank 1 --quest Giadrome`), whose Giadrome becomes a
-- Tigrex first. framework.log shows "[second_monster] added a second Tigrex" as the quest
-- loads, then one "Tigrex in slot N" line for each. Both start where the quest's own monster
-- starts and roam from there; in the Snowy Mountains a Tigrex roams sections 1, 3, 6, 7 and 8.
--
-- quest_add_monster works only from on_quest_targets_building: it gives the monster a target
-- group of its own, so the engine provisions it fully and it deals damage. A quest holds two
-- groups, so two damaging big monsters is the ceiling. The quest has one AI overlay, the first
-- monster's, so the added one must be the same species; the framework knows only the Tigrex.
--
-- Don't move, resize or re-flag them every tick to make them show up: that is what breaks a big
-- monster's fighting. One out of your section is invisible until it roams in. Lua mods share
-- one handler per event, the last to register wins: don't run this next to a mhfu_port mod.

local TIGREX, GIADROME = mhfu.MON_TIGREX, mhfu.MON_GIADROME

mhfu.on_quest_targets_building(function(quest)
  if quest == 0 then return end
  if mhfu.quest_has(quest, GIADROME) then mhfu.quest_replace_monster(quest, GIADROME, TIGREX) end
  if mhfu.quest_has(quest, TIGREX) and mhfu.quest_add_monster(quest, TIGREX) then
    mhfu.log("[second_monster] added a second Tigrex")
  end
end)

mhfu.on_bigmonster_spawn(function(_, species, slot, hp)
  if species == TIGREX then
    mhfu.log(string.format("[second_monster] Tigrex in slot %d, hp %d", slot, hp))
  end
end)

mhfu.log("[second_monster] ready")
