# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Play in game for an own move: saved, the port's modules on a memory stick in tmp_path, the
move asked of a faked game by name."""

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from mhfu import inject
from mhfu.live import moves as live_moves
from mhfu_port import layout, manifest, moves
from mhfu_port.data import Data
from mhfu_studio.monster import move_game, runtime
from mhfu_studio.monster.workspace import MonsterWorkspace


@pytest.fixture
def mods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    stick = tmp_path / "PSP"
    (stick / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(stick),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return stick / inject.MODS_SUBDIR


@pytest.fixture
def asked() -> list[tuple[str, bool]]:
    return []


@pytest.fixture
def zinogre(
    games: Data, zinogre_toml: Path, mods: Path, asked: list[tuple[str, bool]]
) -> MonsterWorkspace:
    """The Zinogre with a game that takes every own move asked of it."""
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.open(zinogre_toml)
    ws.game_session = lambda: nullcontext("session")  # type: ignore[assignment,arg-type,return-value]
    ws.game_running = lambda: True

    def play_own(s: Any, name: str, force: bool) -> bool:
        assert s == "session"
        asked.append((name, force))
        return True

    ws.play_own = play_own
    return ws


def own_and_pair(ws: MonsterWorkspace) -> tuple[str, str]:
    m = ws.manifest
    assert m is not None
    own = next(n for n, mv in m.moves.items() if mv.own)
    pair = next(n for n, mv in m.moves.items() if not mv.own)
    return own, pair


def test_saves_deploys_and_asks(
    zinogre: MonsterWorkspace, mods: Path, asked: list[tuple[str, bool]]
) -> None:
    ws, doc = zinogre, zinogre.doc
    assert doc is not None
    name, _ = own_and_pair(ws)
    ws.select_move(name)
    assert ws.set_move(label="edited, unsaved") and doc.dirty
    assert ws.play_move_blocker() is None
    ws.play_move_in_game()
    assert asked == [(name, False)] and not doc.dirty, ws.message
    m = manifest.load(doc.path)  # type: ignore[arg-type]
    assert m.moves[name].label == "edited, unsaved"
    lib = mods / layout.LIB
    text = (lib / moves.module_name(m)).read_text()
    assert f"{name} = {{ entry = " in text
    assert (lib / layout.module_name(m)).is_file()
    assert (lib / runtime.LIBRARY).read_bytes() == runtime.library().read_bytes()
    assert ws.message.startswith(f"{name} plays in the game; saved, ")
    assert moves.module_name(m) in ws.message
    ws.play_move_in_game(force=True)
    assert asked[-1] == (name, True)


def test_not_taken(zinogre: MonsterWorkspace) -> None:
    name, _ = own_and_pair(zinogre)
    zinogre.select_move(name)
    zinogre.play_own = lambda s, n, f: False
    zinogre.play_move_in_game()
    assert zinogre.message.startswith(f"the game did not take {name}: no port rides")


def test_bridge_missing(zinogre: MonsterWorkspace) -> None:
    def play_own(s: Any, name: str, force: bool) -> bool:
        raise TimeoutError("no ack from cli_bridge.lua")

    zinogre.select_move(own_and_pair(zinogre)[0])
    zinogre.play_own = play_own
    zinogre.play_move_in_game()
    assert zinogre.message == "not played in the game: no ack from cli_bridge.lua"


def test_blocked(zinogre: MonsterWorkspace, mods: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = zinogre
    own, pair = own_and_pair(ws)
    ws.select_move(pair)
    assert "rides the base monster's" in (ws.play_move_blocker() or "")
    ws.play_move_in_game()
    assert ws.message.startswith("not played in the game: ") and "rides the base" in ws.message
    ws.select_move(own)
    ws.game_running = lambda: False
    assert ws.play_move_blocker() == "no game running: start PPSSPP with the port in a quest"
    ws.game_running = lambda: True
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(mods.parent / "nowhere"),))
    assert "memory stick" in (ws.play_move_blocker() or "")


def test_moved_clip_refused(zinogre: MonsterWorkspace, asked: list[tuple[str, bool]]) -> None:
    """A move whose clip the unsaved layout put elsewhere: the game holds the injected build."""
    ws, doc = zinogre, zinogre.doc
    assert doc is not None
    name, _ = own_and_pair(ws)
    br = ws.browser()
    assert br is not None
    cid = doc.manifest.clips[doc.manifest.moves[name].clip or ""].id
    at = br.layout().ids[cid]
    other = next(e for e in sorted(br.layout().entries) if e != at and e not in br.layout().partial)
    ws.edit_clip, ws.name_buf = cid, ""
    ws.place_clip(other)
    ws.select_move(name)
    ws.play_move_in_game()
    assert "in the saved manifest: save, then build and inject" in ws.message
    assert asked == [] and doc.dirty


def test_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, ...]] = []

    def play_own(s: object, name: str, **kw: bool) -> bool:
        seen.append((s, name, kw))
        return True

    monkeypatch.setattr(live_moves, "play_own", play_own)
    assert move_game.play_own("s", "stamp") is True  # type: ignore[arg-type]
    assert move_game.play_own("s", "stamp", force=True) is True  # type: ignore[arg-type]
    assert seen == [("s", "stamp", {"force": False}), ("s", "stamp", {"force": True})]


def test_running_scans_at_most_every_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    import ppsspp_debug

    scans: list[int] = []
    monkeypatch.setattr(ppsspp_debug, "emulators", lambda: scans.append(1) or [])
    clock = iter([10.0, 11.0, 12.5])
    running = move_game.Running(ttl=2.0, clock=lambda: next(clock))
    assert [running(), running(), running()] == [False, False, False]
    assert len(scans) == 2
