-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- ported_zinogre.lua: the Zinogre, ported from MHP3rd, in place of a Giadrome, fighting with the
-- moves and rules of ports/zinogre.toml and one decision of its own.
--
-- Needs, once: the port built and placed for injection, from modkit with $MHFU_DATA and
-- $MHP3RD_DATA naming the extracted games,
--     uv run mhfu-port inject ports/zinogre.toml
-- (zinogre.bin in PLUGINS/mhfu_framework/inject/, zinogre_clips.lua and zinogre_moves.lua in
-- mods/lib/); mhfu_port.lua in mods/lib/; `memory = 64` in plugin.ini.
--
-- P.define reads the manifest's moves and rules from zinogre_moves.lua: the lunge claimed for
-- the host's charges and its skid, and rules that play the own move `stamp` in C on every AI
-- frame. The brain adds one decision at 2 Hz: the hunter 1500 to 3000 away and nothing playing,
-- so the own move `dash` at him, at most every 10 s; its `after` plays the stop.

local port = require("mhfu_port")

local GAP = 20   -- ticks between two dashes

port.mod("ported_zinogre", function(P)
  local zin = P.define{
    name = "zinogre",
    species = mhfu.MON_TIGREX,
    replace = { mhfu.MON_GIADROME },
    pac = "zinogre.bin",
    orig = "file_06185.bin.orig",
    fid = 6186,
  }

  local last = -GAP
  zin:brain(function(s)
    if s.move or not s.native or s.dist < 1500 or s.dist > 3000 or s.tick - last < GAP then
      return
    end
    zin:face(s.px, s.pz)
    if zin:move("dash") then
      last = s.tick
      P.log("[ported_zinogre] dash at the hunter, %d away", math.floor(s.dist))
    end
  end)
end)
