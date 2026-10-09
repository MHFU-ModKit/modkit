# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port import build, layout, manifest, travel
from mhfu_port.manifest import Clip as Named
from mhfu_port.motion import frames
from mhp_formats import fu
from mhp_formats.anim import Channel, Clip, Keyframe, Track

PORTS = Path(__file__).parents[3] / "ports"
TIGREX = 75


def host(entries: set[int], partial: set[int] = frozenset(), slots: int = 100) -> fu.Anim:
    """Three parts filling `entries` (under 100), part 1 alone filling `partial`."""
    streams: list[list[Clip | None]] = [[None] * slots for _ in range(6)]
    c = Clip()
    for e in entries:
        for k in range(3):
            streams[2 * k][e] = c
    for e in partial:
        streams[2][e] = c
    return fu.Anim(streams)


def test_capacity():
    assert layout.capacity(host({1}), TIGREX) == 123
    assert layout.capacity(host({1}), 99) == 100
    assert layout.capacity(host({1}, slots=10), TIGREX) == 10
    odd = host({1})
    odd.streams[1][30] = odd.streams[3][30] = odd.streams[5][30] = Clip()
    assert layout.capacity(odd, 99) == 131


def test_partial():
    assert layout.partial(host({1, 2}, {24, 25}), 123) == {24, 25}


def test_pack():
    """Ids under the capacity in their own entries, past the partial one; the rest by id into
    the free entries the host fills, then the lowest others."""
    got = layout.plan({}, {1, 2, 4, 6, 100, 101, 205}, host({1, 2, 3, 5}, {4}), TIGREX)
    assert got.entries == {1: 1, 2: 2, 3: 4, 5: 205, 6: 6, 100: 100, 101: 101}
    assert (got.placed, got.partial, got.unplaced, got.ids[205]) == (set(), {4}, (), 5)


def test_manifest_first():
    named = {"run": Named(3, source=205), "idle": Named(2), "howl": Named(1, source=206)}
    got = layout.plan(named, {1, 2, 205, 206}, host({1, 2, 3}), TIGREX)
    assert got.entries == {0: 1, 1: 206, 2: 2, 3: 205}
    assert got.placed == {1, 2, 3}


def test_pin_swaps():
    """A pin takes the entry; the clip the packer had there takes the entry the pin freed."""
    ids, h = {1, 2, 4, 6, 100, 101, 205}, host({1, 2, 3, 5}, {4})
    base = layout.plan({}, ids, h, TIGREX)
    got = layout.plan({"a": Named(1, source=205)}, ids, h, TIGREX)
    assert layout.moved(base, got) == {1: (1, 5), 205: (5, 1)}
    chain = {"a": Named(1, source=205), "b": Named(5, source=101)}
    assert layout.moved(base, layout.plan(chain, ids, h, TIGREX)) == {
        1: (1, 101),
        101: (101, 5),
        205: (5, 1),
    }


def test_pin_to_empty():
    ids, h = {1, 2, 100, 101, 205}, host({1, 2, 3, 5})
    base = layout.plan({}, ids, h, TIGREX)
    got = layout.plan({"a": Named(40, source=100)}, ids, h, TIGREX)
    assert layout.moved(base, got) == {100: (100, 40)}


def test_pin_from_full():
    """A pinned clip the packer had no entry for leaves the one it takes over unplaced."""
    ids, h = {0, 1, 2, 100}, host({0}, slots=3)
    base = layout.plan({}, ids, h, TIGREX)
    got = layout.plan({"a": Named(1, source=100)}, ids, h, TIGREX)
    assert (base.unplaced, got.unplaced, got.entries[1]) == ((100,), (1,), 100)


def test_place():
    m = manifest.loads(HEAD + "[clips.run]\nslot = 1\n")
    ids, h = {1, 2, 205}, host({1, 2, 3})
    now = layout.of(m, ids, h)
    layout.place(m, now, 205, 1, "dash")
    assert {n: (c.slot, c.id) for n, c in m.clips.items()} == {"run": (3, 1), "dash": (1, 205)}
    layout.place(m, layout.of(m, ids, h), 205, 2, "rush")
    assert set(m.clips) == {"run", "rush"} and m.clips["rush"].slot == 2
    full = layout.plan({}, {0, 1, 2, 100}, host({0}, slots=3), TIGREX)
    with pytest.raises(layout.LayoutError, match="clip 100 has no entry"):
        layout.place(manifest.loads(HEAD + "[clips.a]\nslot = 1\n"), full, 100, 1, "b")
    with pytest.raises(manifest.ManifestError, match="clips.run already names clip 1"):
        layout.pin(m, "run", 205, 2)


def test_place_over_a_name():
    """A clip named but not pinned is the packer's to move: only the placed clip pins."""
    m = manifest.loads(HEAD + "[clips.run]\nsource = 1\n")
    ids, h = {1, 2, 205}, host({1, 2, 3})
    layout.place(m, layout.of(m, ids, h), 205, 1, "dash")
    assert m.clips["run"].slot is None and m.clips["dash"].slot == 1
    assert layout.of(m, ids, h).entries == {1: 205, 2: 2, 3: 1}


def test_name_clip():
    m = manifest.loads(HEAD + "[clips.run]\nslot = 3\nsource = 205\n")
    layout.name_clip(m, "walk", 2)
    layout.name_clip(m, "dash", 205)
    assert m.clips == {"walk": Named(source=2), "dash": Named(3, source=205)}
    assert (
        manifest.loads(manifest.dumps(m)) == m
        and "slot" not in manifest.dumps(m).split("[clips.walk]")[1].split("[clips.dash]")[0]
    )
    ids, h = {1, 2, 205}, host({1, 2, 3})
    got = layout.of(m, ids, h)
    assert got.placed == {3} and layout.names(m, got) == {1: "clip_01", 2: "walk", 3: "dash"}
    assert layout.where(m.clips["walk"], got.ids) == 2 and layout.where(m.clips["walk"], {}) is None
    assert layout.pinned(m).entries == {3: 205}
    with pytest.raises(manifest.ManifestError, match="needs a source or a slot"):
        manifest.loads(HEAD + "[clips.a]\nlabel = 'x'\n")
    with pytest.raises(layout.LayoutError, match="clips.a: the donor has no clip 7"):
        layout.plan({"a": Named(source=7)}, ids, h, TIGREX)


CUTS = (
    "[clips.a]\nslot = 1\nsource = 7\nstart = 0\nframes = 2\n"
    "[clips.b]\nslot = 2\nsource = 7\nstart = 2\nframes = 5\n"
)


def test_cuts():
    """Two cuts of one source: they free its entry once, the second clip they displace takes a
    free one."""
    m = manifest.loads(HEAD + CUTS)
    got = layout.of(m, {1, 2, 6, 7, 100}, host({1, 2, 3, 5}, {4}))
    assert got.entries == {1: 7, 2: 7, 3: 2, 6: 6, 7: 1, 100: 100}
    assert (got.cuts, got.placed, got.ids[7]) == ({1: (0, 2), 2: (2, 5)}, {1, 2}, 1)
    assert [layout.names(m, got)[e] for e in (1, 2, 3)] == ["a", "b", "clip_03"]
    assert layout.where(m.clips["b"], got.ids) == 2 and layout.pinned(m).cuts == got.cuts
    assert "  b = 2,  -- MHP3rd 7, frames 2..7" in layout.lua(m, got)


def test_clips():
    m = manifest.loads(HEAD + CUTS)
    got = layout.of(m, {1, 2, 6, 7, 100}, host({1, 2, 3, 5}, {4}))
    keyed = Clip([Track([Channel(0x008, [Keyframe(0, 0), Keyframe(9, 9)])])])
    donor = {cid: keyed for cid in (1, 2, 6, 7, 100)}
    clips = layout.clips(got, donor)
    assert clips[6] is keyed and [frames(clips[e]) for e in (1, 2)] == [2, 5]
    with pytest.raises(layout.LayoutError, match="entry 2, clip 7: frames 2..7"):
        layout.clips(got, donor | {7: Clip([Track([Channel(0x008, [Keyframe(0, 6)])])])})


def test_full():
    got = layout.plan({}, {0, 1, 2, 100, 101}, host({0}, slots=3), TIGREX)
    assert (got.capacity, got.unplaced) == (3, (100, 101))


@pytest.mark.parametrize(
    ("named", "why"),
    [
        (Named(3, source=7), "the donor has no clip 7"),
        (Named(4), "entry 4 on some body parts only"),
        (Named(123), "entry 123 is past the host's 123"),
    ],
)
def test_refuses(named, why):
    with pytest.raises(layout.LayoutError, match=f"clips.x: .*{why}"):
        layout.plan({"x": named}, {1, 2, 4, 123}, host({1}, {4}), TIGREX)


HEAD = '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'


def test_manifest_source():
    head = HEAD
    m = manifest.loads(head + "[clips.a]\nslot = 3\nsource = 205\n")
    assert (m.clips["a"].id, manifest.loads(manifest.dumps(m)) == m) == (205, True)
    with pytest.raises(manifest.ManifestError, match="clip 3 is placed by clips.a too"):
        manifest.loads(head + "[clips.a]\nslot = 3\n[clips.b]\nslot = 4\nsource = 3\n")
    with pytest.raises(manifest.ManifestError, match="source is 0 or more"):
        manifest.loads(head + "[clips.a]\nslot = 3\nsource = -1\n")


def test_lua():
    m = manifest.loads(
        '[port]\nname = "z"\nhost_species = 75\npac = "z.bin"\n[source]\nmodel = 5339\n'
        "[clips.dash-stop]\nslot = 1\n[clips.clip_02]\nslot = 3\nsource = 7\n"
    )
    got = layout.Layout({1: 1, 2: 205, 3: 7})
    text = layout.lua(m, got)
    assert layout.module_name(m) == "z_clips.lua" and "MHP3rd file 5341" in text
    body = [line for line in text.splitlines() if " = " in line]
    # entry 2's own name is taken by the clip in entry 3, so it goes unnamed
    assert body == ['  ["dash-stop"] = 1,  -- MHP3rd 1', "  clip_02 = 3,  -- MHP3rd 7"]


def test_turns_lua():
    m = manifest.loads(
        '[port]\nname = "z"\nhost_species = 75\npac = "z.bin"\n[source]\nmodel = 5339\n'
    )
    turn = travel.Turn(4, (0, 0x2000, 0x4000), 0, 90.0)
    text = layout.turns_lua(m, layout.Layout({3: 7}, turns={3: turn}))
    assert layout.turns_module_name(m) == "z_turns.lua"
    assert text.endswith('return {\n  [3] = "000020004000",\n}\n')


@pytest.mark.parametrize(
    ("name", "clips", "odd", "pins"),
    [("zinogre", 102, 20, {65, 73, 83}), ("brute_tigrex", 77, 19, set())],
)
def test_ports(data, name, clips, odd, pins):
    """Every source clip in an entry, a cut one in each cut: under the capacity in its own but
    the Tigrex's partial 24 and 25 and the pinned, the manifest's where it says, the rest
    packed."""
    m = manifest.load(PORTS / f"{name}.toml")
    d, h = build.donor(m, data), build.host(m, data)
    got = build.layout(m, d, h)
    held = list(got.entries.values())
    cut = len(got.cuts) - len({got.entries[e] for e in got.cuts})
    assert (len(d.clips), len(held) - cut, got.unplaced) == (clips, clips, ())
    assert set(held) == set(d.clips) and got.placed == pins
    assert got.partial == {24, 25} and not got.partial & set(got.entries)
    assert sum(e >= 100 for e in got.entries) == odd and max(got.entries) < got.capacity
    own = {c for c in d.clips if c < got.capacity and c not in got.partial}
    assert all(got.ids[c] == c for c in own - {got.entries[e] for e in pins})
    assert all(c.slot is None or got.entries[c.slot] == c.id for c in m.clips.values())
    assert set(layout.names(m, got).values()) >= set(m.clips)


@pytest.mark.parametrize("name", ["zinogre", "brute_tigrex"])
def test_naming_moves_nothing(data, name):
    """Naming each clip, one after another, pins none and changes no entry; neither does
    pinning each where the layout has it."""
    m = manifest.load(PORTS / f"{name}.toml")
    d, h = build.donor(m, data), build.host(m, data)
    base = build.layout(m, d, h)
    for cid in sorted(base.ids):
        layout.name_clip(m, f"c{cid}", cid)
        assert layout.of(m, d.clips, h.anim).entries == base.entries, cid
    assert {c.id for c in m.clips.values()} == set(d.clips)
    assert layout.of(m, d.clips, h.anim).placed == base.placed
    for cid, e in sorted(base.ids.items()):
        layout.pin(m, f"c{cid}", cid, e)
        assert layout.of(m, d.clips, h.anim).entries == base.entries, cid
