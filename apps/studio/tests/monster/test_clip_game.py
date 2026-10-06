# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import shutil
from contextlib import nullcontext

import pytest
from mhfu import addresses as a
from mhfu.live import clips
from mhfu.memory import Image, Space
from mhfu_studio.monster import clip_game
from mhfu_studio.monster.workspace import MonsterWorkspace

MON = a.RAM.start + 0x90_0000  # entities inside the fake's memory


class Fake(Space):
    """The registry, two entities and, with `bridge`, the bridge block."""

    def __init__(self, bridge: bool = True) -> None:
        images = [Image(bytes(4 * 21), a.ENTITY_REGISTRY), Image(bytes(0x1000 * 5), MON)]
        if bridge:
            images.append(Image(bytes(0x40), a.CLI_BRIDGE_BLOCK))
        super().__init__(images)

    def monster(self, slot: int, species: int, vtable: int = 0) -> None:
        base = MON + 0x1000 * (slot - 1)
        self.write_u32(a.ENTITY_REGISTRY + 4 * slot, base)
        self.write_u32(base + a.ENTITY.VTABLE, vtable)
        self.write_u8(base + a.ENTITY.SPECIES, species)


@pytest.fixture
def game() -> Fake:
    g = Fake()
    g.monster(1, 77)
    g.monster(3, 75, a.TIGREX_VTABLE)
    return g


class Live:
    """A session on a fake's memory."""

    def __init__(self, mem: Fake) -> None:
        self.mem = mem


def played(entry: int, dispatched: bool = True) -> clips.Played:
    parts = tuple(
        clips.Part(k, True, (2 * k, entry), ((2 * k, entry),), False, 9.0, 2.0, 1.0, True)
        for k in range(3)
    )
    return clips.Played(entry, parts, dispatched, False, 0.1, (1, 0))


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """What `mhfu.live.clips` was asked to play and release, by entry and slot."""
    seen: list[tuple[str, int]] = []

    def play(s: Live, entry: int, *, slot: int, link: object) -> clips.Played:
        seen.append(("play", entry, slot))  # type: ignore[arg-type]
        return played(entry)

    def release(s: Live, slot: int, link: object) -> bool:
        seen.append(("release", slot))
        return True

    monkeypatch.setattr(clips, "play", play)
    monkeypatch.setattr(clips, "release", release)
    return seen


def test_force(game, calls):
    held = clip_game.force(Live(game), 50, 75)  # type: ignore[arg-type]
    assert held.slot == 3 and calls == [("play", 50, 3)]
    assert held.says() == "anim 50 held on monster 3 until Release: 3 of 3 body parts play it"
    late = clip_game.Held(3, played(50, dispatched=False))
    assert late.says().endswith("the monster has not taken it yet")


def test_release(game, calls):
    assert clip_game.release(Live(game), 75) == (3, True)  # type: ignore[arg-type]
    assert calls == [("release", 3)]


def test_target():
    g = Fake()
    g.monster(2, 40, a.TIGREX_VTABLE)
    g.monster(4, 75)
    assert clip_game.target(g, 75) == 4, "the host species first"
    assert clip_game.target(g, 99) == 2, "else a known big-monster class"
    with pytest.raises(LookupError, match="no big monster"):
        clip_game.target(Fake(), 75)


def test_play_button(games, ports, tmp_path, game, calls):
    """The picked clip's anim reaches the game; a clip moved since the save does not."""
    path = tmp_path / "zinogre.toml"
    shutil.copy(ports / "zinogre.toml", path)
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.open(path)
    ws.game_session = lambda: nullcontext(Live(game))  # type: ignore[assignment,arg-type,return-value]
    ws.play_source(248)
    ws.play_in_game()
    assert calls[-1] == ("play", 50, 3)
    assert ws.message == "anim 50 held on monster 3 until Release: 3 of 3 body parts play it"
    ws.play_source(2)
    ws.play_in_game()
    assert calls[-1] == ("play", 2, 3)
    ws.play_source(248)
    ws.place_clip(7)
    ws.play_in_game()
    assert "save, then build and inject" in ws.message and calls[-1] == ("play", 2, 3)
    ws.release_in_game()
    assert calls[-1] == ("release", 3) and ws.message == "monster 3 released"
    ws.game_session = lambda: nullcontext(Live(Fake(bridge=False)))  # type: ignore[assignment,arg-type,return-value]
    ws.save()
    ws.play_in_game()
    assert ws.message.startswith("not played in the game: ")


def test_attached_reads_the_lane(monkeypatch):
    seen = {}

    class Session:
        mem = "memory"

        @classmethod
        def attach(cls, port, timeout, wait_for_game):
            seen["port"] = port
            return cls()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(clip_game, "Session", Session)
    monkeypatch.setenv("MHFU_LANE", "3")
    with clip_game.attached() as s:
        assert s.mem == "memory"
    assert seen == {"port": 45103, "closed": True}
