# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster on the start page: the ports, the warm-up before an open, the next steps."""

from pathlib import Path

import pytest
from mhfu import inject
from mhfu.em.intel import SpeciesIntel
from mhfu_port.data import Data
from mhfu_studio.monster import workspace as mw
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell import places
from mhfu_studio.shell.workspace import SEND_KEY


@pytest.fixture
def ws(
    port_doc: PortDocument, synthetic_pac: bytes, intel75: SpeciesIntel, tmp_path: Path
) -> MonsterWorkspace:
    w = MonsterWorkspace()
    w.intel_cache[75] = intel75
    scene = Scene.from_bytes(
        synthetic_pac, "t", manifest=port_doc.manifest, path=tmp_path / "t.bin"
    )
    w.load(scene, port_doc)
    return w


def test_ports(ports: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (shelf,) = MonsterWorkspace().start()
    names = {c.path.name: (c.label, c.detail) for c in shelf.choices if c.path is not None}
    assert names["zinogre.toml"] == ("Zinogre", "on Tigrex (em75)")
    assert names["brute_tigrex.toml"][0] == "Brute Tigrex" and shelf.note == ""
    monkeypatch.setattr(mw, "__file__", str(tmp_path / "a" / "b" / "c" / "d" / "e" / "f.py"))
    (shelf,) = MonsterWorkspace().start()
    assert shelf.choices == () and "No ports" in shelf.note and shelf.browse is not None


def test_shown_and_steps(
    ws: MonsterWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert MonsterWorkspace().shown() is None and MonsterWorkspace().next_steps() == ()
    assert ws.shown() == (ws.doc.path if ws.doc else None, "t")
    sess = ws.attack_session
    assert sess is not None
    sess.drop_set(2)
    assert [s.done for s in ws.next_steps()] == [False, False, False, False]
    assert ws.next_steps()[3].key == SEND_KEY
    ws.pick_clip(1)
    host = ws.host_attacks()
    assert host is not None
    sess.adopt_set(2, host.sets[2].spheres)
    assert [s.done for s in ws.next_steps()] == [True, True, False, False]
    sess.edit_volume(0, radius=55.0)
    assert [s.done for s in ws.next_steps()] == [True, True, True, False]
    stick = tmp_path / "PSP"
    (stick / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    places.remember(places.MEMSTICK, stick)
    ws.send()
    assert ws.next_steps()[3].done, ws.message
    sess.edit_volume(0, radius=56.0)
    assert not ws.next_steps()[3].done


def test_no_warmup_for_a_pac(tmp_path: Path) -> None:
    assert MonsterWorkspace().warmup(tmp_path / "x.pac") is None


def test_warmup_without_the_games(monkeypatch: pytest.MonkeyPatch, ports: Path) -> None:
    for p in places.PLACES:
        monkeypatch.delenv(p.env, raising=False)
    ws = MonsterWorkspace()
    assert ws.warmup(ports / "zinogre.toml") is None  # open says what is missing
    with pytest.raises(places.Missing):
        ws.open(ports / "zinogre.toml")


def test_open_takes_the_warmup(
    games: Data, em75: SpeciesIntel, ports: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = em75
    path = ports / "zinogre.toml"
    warm = ws.warmup(path)
    assert warm is not None and warm.done is not None
    warm.done(warm.run())
    monkeypatch.setattr(Scene, "from_manifest", None)  # open builds nothing now
    ws.open(path)
    assert ws.scene is not None and ws.doc is not None and ws.doc.path == path
