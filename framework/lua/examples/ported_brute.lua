-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- ported_brute.lua: the Brute Tigrex, ported from MHP3rd, in place of a Giadrome.
--
-- Needs, once: the port built and placed for injection, from modkit with $MHFU_DATA and
-- $MHP3RD_DATA naming the extracted games,
--     uv run mhfu-port inject ports/brute_tigrex.toml
-- (it writes brute_tigrex.bin and file_06185.bin.orig to the memory stick's
-- PLUGINS/mhfu_framework/inject/); mhfu_port.lua in mods/lib/; `memory = 64` in plugin.ini.
--
-- Cold boot into the Village Elder's 2-star Giadrome quest (`mhfu go-on-quest --rank 1 --quest
-- Giadrome`). The Giadrome becomes a Tigrex wearing the Brute's model, skeleton and clips, and
-- he fights with the Tigrex's brain. The brain here only reports: framework.log shows
-- "[ported_brute] in your section, D units away" when he comes to you.
--
-- A brain can also script moves: brute:play(name) enters a host (main, sub) pair declared in
-- P.define's `moves` and shows the port's clip with it. The engine bounces or parks a pair it
-- does not enter on its own, so pick pairs that hold when forced (`mhfu census`); mhfu_port logs
-- which happened. No move is verified for this port yet.
--
-- The P.define values mirror ports/brute_tigrex.toml. A brain runs at 2 Hz, and mhfu_port owns
-- the one mhfu_tick: a mod never defines it.

local port = require("mhfu_port")

port.mod("ported_brute", function(P)
  local brute = P.define{
    name = "brute_tigrex",
    species = mhfu.MON_TIGREX,
    replace = { mhfu.MON_GIADROME },
    pac = "brute_tigrex.bin",
    orig = "file_06185.bin.orig",
    fid = 6186,
  }

  local here = false
  brute:brain(function(s)
    if s.same_section == here then return end
    here = s.same_section
    if here then
      P.log("[ported_brute] in your section, %d units away", math.floor(s.dist))
    else
      P.log("[ported_brute] left your section")
    end
  end)
end)
