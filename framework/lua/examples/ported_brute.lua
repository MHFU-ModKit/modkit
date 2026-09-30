-- ported_brute.lua: the Brute Tigrex, ported from MHP3rd, in place of a Giadrome, with a brain
-- that sends him charging at you.
--
-- Needs, once: the port built and placed for injection, from modkit with $MHFU_DATA and
-- $MHP3RD_DATA naming the extracted games,
--     uv run mhfu-port inject ports/brute_tigrex.toml
-- (it writes brute_tigrex.bin and file_06185.bin.orig to the memory stick's
-- PLUGINS/mhfu_framework/inject/); mhfu_port.lua in mods/lib/; `memory = 64` in plugin.ini.
--
-- Cold boot into the Village Elder's 2-star Giadrome quest (`mhfu go-on-quest --rank 1 --quest
-- Giadrome`). The Giadrome becomes a Tigrex wearing the Brute's model, skeleton and clips, and
-- he fights with the Tigrex's brain. When he is in your section, 1250 to 3600 units away and no
-- scripted move is running, this brain plays "charge": the host move (2, 8) runs, its hitbox
-- included, and opens on the Brute's charge clip. framework.log shows "[ported_brute] charge
-- from D" for each.
--
-- The P.define values mirror ports/brute_tigrex.toml; clip slots move between builds, so take
-- them from there. A brain runs at 2 Hz, and a charge covers 650 to 1100 units between two
-- calls. mhfu_port owns the one mhfu_tick: a mod never defines it.

local port = require("mhfu_port")

local NEAR, FAR = 1250, 3600    -- nearer, his own brain fights; farther, the move bounces out
local REST = 10                 -- ticks between scripted charges; his own brain runs meanwhile

port.mod("ported_brute", function(P)
  local brute = P.define{
    name = "brute_tigrex",
    species = mhfu.MON_TIGREX,
    replace = { mhfu.MON_GIADROME },
    pac = "brute_tigrex.bin",
    orig = "file_06185.bin.orig",
    fid = 6186,
    clips = { charge = 61 },
    moves = { charge = { main = 2, sub = 8, clip = "charge" } },
  }

  brute:brain(function(s)
    if s.move or not s.same_section or s.since_play < REST then return end
    if s.dist >= NEAR and s.dist <= FAR and brute:play("charge") then
      P.log("[ported_brute] charge from %d", math.floor(s.dist))
    end
  end)
end)
