# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which clips a move's miniature plays, and what its caption says."""

from mhfu_studio.monster.actions import Plays
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.previews import reel


def test_every_anim_in_turn(synthetic_pac: bytes) -> None:
    sc = Scene.from_bytes(synthetic_pac, "em75")
    r = reel("(1, 4) Charge", sc, Plays(2, also=(9, 1)))
    assert [c.slot for c in r.clips] == [2, 1] and r.missing == (9,)
    frames = sc.clip(1).frames
    assert r.caption(1) == f"anim 1 · {frames} frames · 2 of 2 · no clip for 9"


def test_one_anim_says_no_count(synthetic_pac: bytes) -> None:
    sc = Scene.from_bytes(synthetic_pac, "em75")
    assert reel("t", sc, Plays(1)).caption() == f"anim 1 · {sc.clip(1).frames} frames"


def test_why_there_is_nothing_to_play(synthetic_pac: bytes) -> None:
    sc = Scene.from_bytes(synthetic_pac, "em75")
    assert reel("t", sc, Plays(9)).caption() == "em75 has no clip for anim 9"
    assert reel("t", sc, Plays(None)).caption().startswith("Names no anim")
    assert reel("t", sc, Plays(None, computed=True)).caption().startswith("Its code picks")
