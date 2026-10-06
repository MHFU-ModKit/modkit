# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import random
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from mhfu_port import layout
from mhfu_port.data import Data
from mhfu_port.manifest import Clip as Named
from mhfu_port.manifest import ManifestError
from mhfu_studio.monster import clip_browser as B
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhp_formats import Channel, Clip, Keyframe, Track, fu


def open_port(games: Data, path: Path) -> MonsterWorkspace:
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None  # the clips need no intel
    ws.open(path)
    return ws


@pytest.fixture
def zinogre(games: Data, zinogre_toml: Path) -> Iterator[MonsterWorkspace]:
    """The Zinogre opened from a copy of its manifest."""
    ws = open_port(games, zinogre_toml)
    yield ws
    ws.close()


def entries(ws: MonsterWorkspace) -> dict[int, int]:
    br = ws.browser()
    assert br is not None
    return br.layout().entries


def test_rows(zinogre):
    rows = zinogre.source_rows()
    assert len(rows) == 102 and all(r.id is not None and r.entry is not None for r in rows)
    by_stream = {s: sum(r.stream == s for r in rows) for s in (0, 1, 2)}
    assert by_stream == {0: 42, 1: 21, 2: 39}
    assert {r.entry for r in rows if r.id in (248, 253, 257, 258)} == {50, 53, 55, 56}
    named = {r.name: r.id for r in rows if r.name}
    assert named["welcome_howl"] == 2 and len(named) == len(zinogre.doc.manifest.clips)


def pins(ws: MonsterWorkspace) -> list[str]:
    return sorted(n for n, c in ws.doc.manifest.clips.items() if c.slot is not None)


def test_name_every_clip(zinogre):
    """All 102 named, one after another, from their own rows: none pinned, no entry moves, the
    saved manifest opens on the same layout and names; then a placement pins only itself."""
    ws, before = zinogre, entries(zinogre)
    for r in ws.source_rows():
        ws.play_source(r.id)
        assert ws.edit_slot == r.entry and ws.edit_clip == r.id
        ws.name_buf, ws.label_buf = f"c{r.id}", f"clip {r.id}"
        ws.label()
        assert ws.message == f"clips.c{r.id}", ws.message
    m = ws.doc.manifest
    assert entries(ws) == before and len(m.clips) == 102 and pins(ws) == []
    ws.save()
    again = open_port(ws.games(), ws.doc.path)
    assert entries(again) == before and again.doc.manifest == m
    assert {r.name for r in again.source_rows()} == {f"c{cid}" for cid in before.values()}
    assert "slot" not in again.doc.path.read_text(encoding="utf-8").split("[clips.")[1]
    ws.play_source(248)
    ws.place_clip(2)
    assert ws.message == "clip 2 anim 2 -> anim 50; clip 248 anim 50 -> anim 2"
    assert pins(ws) == ["c248"] and entries(ws) == {**before, 2: 248, 50: 2}
    ws.save()
    assert entries(open_port(ws.games(), ws.doc.path)) == entries(ws)


def test_place(zinogre):
    """A placement swaps with the named clip there, builds the port again, round-trips, and
    undo takes it back."""
    ws, before = zinogre, entries(zinogre)
    ws.play_source(248)
    ws.name_buf = "slam"
    ws.place_clip(2)
    assert ws.message == "clip 2 anim 2 -> anim 50; clip 248 anim 50 -> anim 2"
    now = entries(ws)
    assert now == {**before, 2: 248, 50: 2}
    m = ws.doc.manifest
    assert (m.clips["slam"].slot, m.clips["slam"].source, pins(ws)) == (2, 248, ["slam"])
    assert ws.manifest_clip(50)[0] == "welcome_howl", "the packer moved the name with its clip"
    assert (ws.scene.clip(2).frames, ws.scene.clip(2).loop) == ws.browser().prints[248]
    assert ws.source_clip(248).slot == 2, "the rebuilt port plays it"
    ws.save()
    assert entries(open_port(ws.games(), ws.doc.path)) == now
    ws.doc.undo()
    ws.refresh()
    assert entries(ws) == before and ws.scene.clip(2).frames == ws.browser().prints[2][0]


def test_place_to_an_empty_anim(zinogre):
    """Nothing else moves, and an unnamed clip takes its new anim's clip_NN."""
    ws, before = zinogre, entries(zinogre)
    free = next(e for e in range(123) if e not in before and e not in (24, 25))
    at = layout.Layout(before).ids[209]
    ws.play_source(209)
    ws.place_clip(free)
    assert ws.message == f"clip 209 anim {at} -> anim {free}"
    assert entries(ws) == {**{e: c for e, c in before.items() if e != at}, free: 209}
    assert ws.doc.manifest.clips[f"clip_{free:02d}"].source == 209


def test_place_refusals(zinogre):
    ws, before = zinogre, entries(zinogre)
    ws.play_source(209)
    ws.place_clip(24)
    assert "some body parts only" in ws.message and entries(ws) == before
    ws.place_clip(123)
    assert "past the host's 123" in ws.message and entries(ws) == before


def test_unplaced(games, ports, tmp_path, monkeypatch, zinogre_toml):
    """A host with 100 anims leaves 4 Zinogre clips out: listed, played from a preview, named
    without an anim; a placement over a pinned clip has nowhere to send it."""
    monkeypatch.setitem(layout.ENTRIES, 75, 100)
    path = zinogre_toml
    ws = open_port(games, path)
    out = [r.id for r in ws.source_rows() if r.entry is None]
    assert len(out) == 4 and len(ws.scene.clips) == 98
    cid = out[0]
    ws.play_source(cid)
    clip = ws.source_clip(cid)
    assert clip.slot == B.preview_slot(cid) and ws.edit_slot is None and ws.edit_clip == cid
    assert ws.travel(clip.slot)[1] >= 0.0
    ws.name_buf, ws.label_buf = "spare", "a clip with no anim"
    ws.label()
    assert ws.doc.manifest.clips["spare"].slot is None and ws.doc.manifest.clips["spare"].id == cid
    ws.play_source(7)
    ws.place_clip(7)
    ws.play_source(cid)
    ws.place_clip(7)
    assert "holds clips.clip_07, and clip" in ws.message, "a pinned clip has nowhere to go"
    ws.place_clip(5)
    lay = ws.browser().layout()
    assert lay.ids[cid] == 5 and 5 in lay.unplaced and pins(ws) == ["clip_07", "spare"]


def test_preview_matches_the_build(zinogre):
    ws = zinogre
    for cid in (2, 24, 209, 248):
        built = ws.source_clip(cid)
        p = B.preview(ws.doc.manifest, ws.games(), cid)
        assert (p.frames, p.loop) == (built.frames, built.loop)
        for f in (0.0, built.frames / 2, float(built.frames)):
            assert np.array_equal(ws.scene.pose(p, f).world, ws.scene.pose(built, f).world)


def test_browser_place_alone(doc):
    """`ClipBrowser.place` on a synthetic host: the named clip there swaps, a name in use is
    refused."""
    d = doc("[clips.run]\nslot = 1\n")
    filled = [None, _clip(5), _clip(5), _clip(5), *[None] * 6]
    host = fu.Anim([list(filled), [], list(filled), [], list(filled)])
    br = B.ClipBrowser(d, {1: _clip(10), 2: _clip(20), 205: _clip(30)}, host)
    assert br.layout().entries == {1: 1, 2: 2, 3: 205}
    assert br.place(205, 1, "dash") == "clip 1 anim 1 -> anim 3; clip 205 anim 3 -> anim 1"
    assert d.manifest.clips["run"] == Named(3, source=1) and d.manifest.clips["dash"].frames == 30
    with pytest.raises(ManifestError, match="clips.run already names clip 1"):
        br.place(2, 4, "run")


def _clip(frames: int) -> Clip:
    return Clip([Track([Channel(0x008, [Keyframe(0, 0), Keyframe(0, frames)])])])


def test_place_every_clip(games, ports, tmp_path, zinogre_toml):
    """All 102 placed into a shuffled layout, one after another: each lands where it was put
    and stays, the saved manifest loads on that layout, and the port builds on it."""
    from mhfu_port import build, manifest
    from mhfu_studio.monster.clips import pac_clip_table
    from mhfu_studio.monster.document import PortDocument

    path = zinogre_toml
    doc = PortDocument.open(path)
    d, h = build.donor(doc.manifest, games), build.host(doc.manifest, games)
    br = B.ClipBrowser(doc, d.clips, h.anim)
    base = br.layout().entries
    spots = sorted(base)
    random.Random(97).shuffle(spots)
    want = dict(zip(sorted(base.values()), spots, strict=True))
    for cid, e in want.items():
        br.place(cid, e, f"c{cid}")
        assert br.layout().ids[cid] == e
    assert br.layout().ids == want and len(doc.manifest.clips) == 102
    doc.save()
    m = manifest.load(path)
    assert layout.of(m, d.clips, h.anim).ids == want
    table = pac_clip_table(build.build(m, games).pac)
    assert all(table[e] == br.prints[cid] for cid, e in want.items())


def test_view_follows_a_placement(gl, games, ports, tmp_path, zinogre_toml):
    """On a viewport: the placed clip plays on in its new anim, and undo brings the old build
    back on screen."""
    path = zinogre_toml
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.setup(gl)
    try:
        ws.open(path)
        ws.play_source(248)
        ws.vp.strip_root = True
        assert ws.vp.clip.slot == 50 and ws.playing_clip() == 248
        ws.place_clip(7)
        assert ws.vp.scene is ws.scene and ws.vp.clip is ws.scene.clip(7)
        assert ws.playing_clip() == 248 and ws.vp.playback.playing and ws.vp.strip_root
        ws.doc.undo()
        ws.refresh()
        assert ws.vp.scene is ws.scene and ws.vp.clip is ws.scene.clip(50)
        assert ws.playing_clip() == 248 and ws.browser().layout().entries[7] == 7
    finally:
        ws.close()
