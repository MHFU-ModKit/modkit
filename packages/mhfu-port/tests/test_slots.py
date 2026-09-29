# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import csv
import io
from pathlib import Path

import pytest
from mhfu.em.moveset import Moveset
from mhfu.files import monster_pac
from mhfu_port import slots
from mhfu_port.cli import main, parser
from mhfu_port.cli.slots import run_labels, run_slots
from mhp_formats import Channel, Clip, Keyframe, Pac, Track, fu, p3rd

ROT_X = 0x008
BRUTE_MOVESET = 5250
TIGREX = 75
PORTS = Path(__file__).parents[3] / "ports"


def clip(length, loop=0):
    return Clip([Track([Channel(ROT_X, [Keyframe(0, 0), Keyframe(0, length)])])], loop)


def reread(anim):
    return type(anim).from_bytes(anim.to_bytes())


@pytest.fixture
def donor():
    """Stream 0 lacks slot 0; stream 2 is empty."""
    return reread(p3rd.Anim([[None, clip(10), clip(20), clip(30, 1), clip(40)], [clip(5)], []]))


def build(donor, occupancy, size):
    """A port the way the builder lays one out: donor clip N in slot N, the first in the rest."""
    clips = donor.streams[0] + [None] * size
    fill = next(c for c in clips if c is not None)
    streams = [
        [(clips[i] or fill) if i in used else None for i in range(size)] for used in occupancy
    ]
    return reread(fu.Anim(streams))


@pytest.fixture
def port(donor):
    """Slot 4 plays a foreign clip; slot 5 lives in stream 1 only."""
    anim = build(donor, [{1, 2, 3, 4, 6}, set()], 7)
    anim.streams[0][4] = clip(99)
    anim.streams[1][5] = clip(10)
    anim.streams[1][2] = clip(7)  # a shorter part of slot 2's clip
    return reread(anim)


def test_correspondence(port, donor):
    got = {s: str(v) for s, v in slots.correspondence(port, donor).items()}
    assert got == {1: "same 1", 2: "same 2", 3: "same 3", 4: "unknown", 5: "fill 1", 6: "fill 1"}


def test_loop_tells_apart(donor):
    port = build(donor, [{3}], 4)
    port.streams[0][3] = clip(30)
    assert slots.correspondence(port, donor)[3].match == "unknown"


def test_empty_stream(port, donor):
    with pytest.raises(ValueError, match=r"stream 2 holds no clips; these do: \[0, 1\]"):
        slots.correspondence(port, donor, 2)
    with pytest.raises(ValueError, match="streams 0-2, not 3"):
        slots.correspondence(port, donor, 3)


def test_shared(port):
    assert slots.shared(port) == {1: 2, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2}


def test_anim_of(port):
    other = bytes(range(32))
    pac = Pac([other, port.to_bytes(), other]).to_bytes()
    assert slots.anim_of(pac).to_bytes() == port.to_bytes()
    with pytest.raises(ValueError, match="this one 0"):
        slots.anim_of(Pac([other]).to_bytes())


def test_catalog(port, donor):
    host = reread(fu.Anim([[clip(8) if i in (1, 2, 7) else None for i in range(8)]]))
    drivers = {2: [(1, 0), (0, 3)], 7: [(2, 1)], 99: [(4, 4)]}
    rows = {r.slot: r for r in slots.catalog(host, drivers, port, donor, names={2: "bite"})}
    assert sorted(rows) == [1, 2, 3, 4, 5, 6, 7]
    two = rows[2]
    assert (two.pairs, two.host_streams, two.port_streams) == (((0, 3), (1, 0)), (0,), (0, 1))
    assert (two.host_frames, two.port_frames, str(two.source)) == (8, 20, "same 2")
    assert (rows[7].port_frames, rows[7].source, rows[5].shared) == (None, None, 1)
    out = io.StringIO()
    slots.write_catalog(rows.values(), out)
    lines = out.getvalue().splitlines()
    assert lines[0] == ",".join(slots.CATALOG)
    assert lines[2] == '2,"(0,3) (1,0)",2,0,0 1,8,20,1,same 2,bite'


def test_labels(port, donor):
    text = "# a note\n1 -> idle\n 5 ->  roar \nnot a label\n9 -> nothing\n"
    labels = slots.read_labels(text)
    assert labels == {1: "idle", 5: "roar", 9: "nothing"}
    other = build(donor, [{1, 5, 9}], 10)
    rows = slots.verdicts(labels, {"a": port, "b": other}, donor, drivers={5: [(0, 1)]})
    assert [v.transfers for v in rows] == [("a", "b"), (), ()]
    assert str(rows[1].builds["a"]) == "fill 1" and rows[2].builds["a"] is None
    out = io.StringIO()
    slots.write_verdicts(rows, ["a", "b"], out)
    assert out.getvalue().splitlines()[1:3] == [
        "1,idle,,same 1,same 1,a b",
        '5,roar,"(0,1)",fill 1,fill 1,',
    ]


def test_commands():
    args = parser().parse_args(
        ["labels", "l.txt", "a.bin", "b.bin", "--host", "75", "--donor", "1"]
    )
    assert (args.run, args.stream, len(args.ports)) == (run_labels, 0, 2)
    assert parser().parse_args(["slots", "--host", "75"]).run is run_slots
    assert main(["slots"]) == 1


def test_driven(data):
    d = slots.driven(Moveset(data.fu.em(TIGREX)))
    assert d[24] == ((0, 8),) and d[25] == ((0, 9),)  # one handler, told apart by its case


def test_empty_donor_stream(data):
    donor = p3rd.Anim.from_bytes(data.p3rd.read(BRUTE_MOVESET))
    host = slots.anim_of(data.fu.read(monster_pac(TIGREX)))
    with pytest.raises(ValueError, match="stream 2 holds no clips"):
        slots.correspondence(host, donor, 2)


def test_cli(data, tmp_path, capsys):
    host = Pac.from_bytes(data.fu.read(monster_pac(TIGREX)))
    donor = p3rd.Anim.from_bytes(data.p3rd.read(BRUTE_MOVESET))
    anim = next(i for i, e in enumerate(host.entries) if fu.Anim.sniff(e))
    streams = fu.Anim.from_bytes(host.entries[anim]).streams
    used = [{i for i, c in enumerate(s) if c is not None} for s in streams]
    host.entries[anim] = build(donor, used, len(streams[0])).to_bytes()
    port = tmp_path / "port.bin"
    port.write_bytes(host.to_bytes())
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    common = ["--host", str(TIGREX), "--donor", str(BRUTE_MOVESET), *games]

    out = tmp_path / "slots.csv"
    assert main(["slots", str(port), *common, "-o", str(out)]) == 0
    rows = list(csv.DictReader(out.open()))
    assert {r["source"].split()[0] for r in rows if r["source"]} == {"same", "fill"}
    same = [r for r in rows if r["source"] == f"same {r['slot']}"]
    assert main(["slots", str(port), *common, "--stream", "2"]) == 1
    assert "stream 2 holds no clips" in capsys.readouterr().err

    labels = tmp_path / "labels.txt"
    labels.write_text(f"{same[0]['slot']} -> seen\n")
    out = tmp_path / "labels.csv"
    assert main(["labels", str(labels), str(port), *common, "-o", str(out)]) == 0
    (row,) = csv.DictReader(out.open())
    assert row["port"] == f"same {same[0]['slot']}" and row["transfers"] == "port"

    named = tmp_path / "named.csv"
    brute = str(PORTS / "brute_tigrex.toml")
    assert main(["slots", str(port), "--manifest", brute, *games, "-o", str(named)]) == 0
    assert {r["clip"]: r["slot"] for r in csv.DictReader(named.open()) if r["clip"]} == {
        "charge": "61"
    }
