# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The prelude against the declared API, in lupa."""

from pathlib import Path
from typing import Any

PRELUDE = Path(__file__).parents[1] / "lua" / "lib" / "_prelude.lua"


def test_runs_against_the_declared_api(lua: Any) -> None:
    lua.execute(PRELUDE.read_text(encoding="utf-8"))
    assert lua.eval("mhfu.world.player() ~= nil and mhfu.MON.TIGREX == mhfu.MON_TIGREX")
    assert any("[prelude]" in s for s in lua.eval("mhfu.logs").values())
