# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port import build, layout, manifest
from mhfu_port.manifest import Clip as Named
from mhp_formats import fu
from mhp_formats.anim import Clip

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
    """Own ids first, then the rest by id into the free entries the host fills, then the lowest
    others, past the partial one."""
    got = layout.plan({}, {1, 2, 4, 6, 100, 101, 205}, host({1, 2, 3, 5}, {4}), TIGREX)
    assert got.entries == {0: 101, 1: 1, 2: 2, 3: 4, 5: 100, 6: 6, 7: 205}
    assert (got.placed, got.partial, got.unplaced, got.ids[205]) == (set(), {4}, (), 7)


def test_manifest_first():
    named = {"run": Named(3, source=205), "idle": Named(2), "howl": Named(1, source=206)}
    got = layout.plan(named, {1, 2, 205, 206}, host({1, 2, 3}), TIGREX)
    assert got.entries == {0: 1, 1: 206, 2: 2, 3: 205}
    assert got.placed == {1, 2, 3}


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


def test_manifest_source():
    head = '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'
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


@pytest.mark.parametrize(("name", "clips", "odd"), [("zinogre", 102, 4), ("brute_tigrex", 77, 0)])
def test_ports(data, name, clips, odd):
    """Every source clip in an entry: stream 0 in its own but the Tigrex's partial 24 and 25,
    the manifest's where it says, the rest packed."""
    m = manifest.load(PORTS / f"{name}.toml")
    d, h = build.donor(m, data), build.host(m, data)
    got = build.layout(m, d, h)
    assert (len(d.clips), len(got.entries), got.unplaced) == (clips, clips, ())
    assert sorted(got.entries.values()) == sorted(d.clips)
    assert got.partial == {24, 25} and not got.partial & set(got.entries)
    assert sum(e >= 100 for e in got.entries) == odd and max(got.entries) < got.capacity
    assert all(got.ids[c] == c for c in d.clips if c < 100 and c not in got.partial)
    assert all(got.entries[c.slot] == c.id for c in m.clips.values())
