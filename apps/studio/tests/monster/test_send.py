# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Send to game: the hit tables as they are now, onto a memory stick in tmp_path."""

from pathlib import Path

import pytest
from mhfu import inject
from mhfu.em.intel import SpeciesIntel
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.workspace import MonsterWorkspace


@pytest.fixture
def mods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    stick = tmp_path / "PSP"
    (stick / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(stick),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    return stick / inject.MODS_SUBDIR


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


def radius(ws: MonsterWorkspace, r: float) -> None:
    assert ws.doc is not None
    ws.doc.edit(lambda m: setattr(m.hurtboxes[0], "radius", r))


def test_send_copies_the_module_and_the_library(ws: MonsterWorkspace, mods: Path) -> None:
    assert ws.send_blocker() is None
    ws.send()
    text = (mods / "t_hit.lua").read_text()
    assert "GENERATED" in text and (mods / "lib" / "mhfu_port.lua").is_file()
    assert ws.message.startswith("sent t_hit.lua and lib/mhfu_port.lua to ")
    assert "port 't' and the monster is in the area" in ws.message
    assert not Path("t_hit.lua").exists(), "nothing lands in the working directory"
    ws.send()
    assert ws.message.startswith("sent t_hit.lua to "), "the library is in step now"


def test_send_is_the_document_now(ws: MonsterWorkspace, mods: Path) -> None:
    assert ws.doc is not None
    ws.export_hit()
    assert str(Path("t_hit.lua").resolve()) in ws.message
    radius(ws, 77.0)
    ws.save()
    ws.send()
    assert "77.0," in (mods / "t_hit.lua").read_text(), "not the earlier export"
    radius(ws, 55.0)
    ws.send()
    text = (mods / "t_hit.lua").read_text()
    assert "55.0," in text and "t.toml, unsaved edits" in text and ws.doc.dirty


def test_blockers(
    ws: MonsterWorkspace, mods: Path, synthetic_pac: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ws.doc is not None
    assert (MonsterWorkspace().send_blocker() or "").startswith("no port open")
    bare = MonsterWorkspace()
    bare.load(Scene.from_bytes(synthetic_pac, "t"))
    assert "bare PAC" in (bare.send_blocker() or "")

    def empty(m: object) -> None:
        for f in ("hurtboxes", "hitzones", "hitboxes", "attacks", "effects"):
            setattr(m, f, [])

    ws.doc.edit(empty)
    why = ws.send_blocker() or ""
    assert "nothing to send" in why and "Copy the base monster's" in why
    ws.doc.undo()
    ws.intel_cache[75], intel = None, ws.intel_cache[75]
    ws.intel_errors[75] = "set MHFU_DATA"
    why = ws.send_blocker() or ""
    assert (
        why == "cannot send hitboxes or attacks. No attack data for Tigrex (em75): set MHFU_DATA."
    )
    ws.intel_cache[75] = intel
    gone = Path(inject.MEMSTICK_ROOTS[0]).parent / "nowhere"
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(gone),))
    why = ws.send_blocker() or ""
    assert why.startswith("no memory stick to send to") and str(gone) in why


def test_send_failure_is_a_message(ws: MonsterWorkspace, mods: Path) -> None:
    mods.rmdir()
    ws.send()
    assert ws.message.startswith("send failed: ")
