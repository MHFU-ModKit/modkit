# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from types import SimpleNamespace

from mhfu_port import layout, manifest, motion, moves, slots
from mhfu_port.brain.splice import Spliced
from mhfu_port.cli import main
from mhfu_port.cli.build import modules
from mhfu_port.cli.own import describe
from mhfu_port.cli.splice import summary

PORTS = Path(__file__).parents[3] / "ports"
TEXT = """
schema = 2
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5248
[clips.charge]
source = 61
[brain]
donor = 23
splice = "base_3"
entry = "base_12"
[brain.pairs]
"7,13" = "own"
"""


def brute() -> manifest.Manifest:
    return manifest.load(PORTS / "brute_tigrex.toml")


def test_the_cli_needs_an_own_pair(tmp_path, capsys):
    path = tmp_path / "x.toml"
    path.write_text(TEXT.replace('"7,13" = "own"', '"7,13" = [2, 8]'))
    assert main(["own", str(path)]) == 1
    assert "no [brain.pairs] entry" in capsys.readouterr().err


def test_the_list_shows_the_frames_a_move_plays():
    m = manifest.loads(TEXT + "[moves.p3_7_13]\nclip = 'charge'\nstart = 24\nstop = 86\n")
    assert describe(m, "p3_7_13", 3).startswith("  p3_7_13 (slot 3)  clip 61 charge  frames 24-86")
    plain = manifest.loads(TEXT + "[moves.p3_7_13]\nclip = 'charge'\nstart = 24\n")
    assert "frames 24-end" in describe(plain, "p3_7_13", 3)
    assert "frames" not in describe(
        manifest.loads(TEXT + "[moves.p3_7_13]\nclip = 'charge'\n"), "p3_7_13", 3
    )


def test_the_list_shows_the_motion_a_move_makes_by_code():
    m = manifest.loads(TEXT + "[moves.p3_7_13]\nclip = 'charge'\n")
    mv = m.moves["p3_7_13"]
    mv.travel = [(0.0, 0.0), (2.0, 5.5)]
    mv.lunge = manifest.Lunge((0, 8), 1.0, 2.0, -3.0, 5.0)
    mv.steer = manifest.Steer(turn="fixed", angle=-90.0, window=(2, 8))
    line = describe(m, "p3_7_13", 3)
    assert "turns -90 degrees in frames (2, 8)" in line and "travels 2, 5.5 over 2 samples" in line
    assert "lunges -3 to 2" in line and "rises" not in line
    m.clips["charge"].lift = [0.0, 3.0, -9.0]
    assert "rises -9" in describe(m, "p3_7_13", 3)


def test_the_list_names_the_pair_and_the_chain_a_step_is_shared_with():
    m = manifest.loads(TEXT + "[moves.p3_7_13]\nclip = 'charge'\nlabel = 'MHP3rd 7,13'\n")
    assert describe(m, "p3_7_13", 3).endswith("clip 61 charge  (MHP3rd 7,13)")
    assert describe(m, "p3_7_13", 3, "p3_7_12").endswith("(MHP3rd 7,13)  shared with p3_7_12")


def test_the_cli_lists_the_chains_across_hand_overs(data, capsys):
    path = PORTS / "zinogre.toml"
    code = main(["own", str(path), "--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)])
    out = capsys.readouterr().out
    assert code == 0 and f"49 pairs, 125 own moves of {manifest.OWN_MOVES}" in out
    lines = out.splitlines()
    head = lines.index("7,12  p3_7_12 + 3 more")
    chain = [line.split()[0] for line in lines[head + 1 :] if line.startswith("  p3_")][:4]
    assert chain == ["p3_7_12", "p3_7_12_2", "p3_7_12_3", "p3_7_156"]
    assert any("p3_7_156 (slot" in line and "(MHP3rd 7,156)" in line for line in lines)
    held = [line for line in lines if line.startswith("  p3_7_0 (slot")]
    assert len(held) == 5, "its own chain and the hand-overs of four pairs"
    assert not held[0].endswith("shared with p3_7_0") and held[1].endswith("shared with p3_7_0")
    again = [line for line in lines if line.startswith("  p3_7_14_2 (slot")]
    assert len(again) == 2 and again[1].endswith("shared with p3_7_14"), "marked once per chain"
    assert not held[2].endswith("shared with p3_7_0"), "(7, 16)'s tail was marked at its first step"
    assert held[3].endswith("shared with p3_7_0") and not held[4].endswith("shared with p3_7_0")


def test_the_cli_lists_the_moves(data, capsys):
    path = PORTS / "brute_tigrex.toml"
    code = main(["own", str(path), "--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)])
    out = capsys.readouterr().out
    assert code == 0 and "p3_7_13 (slot 6)  clip 61 charge" in out
    assert "donor 21 -> host record 7, frames 135-165" in out
    assert "donor 26 -> host record 34 (spare), frames 85-144" in out


def test_the_moves_module_carries_the_derived_moves(data, tmp_path):
    m = brute()
    donor = motion.moveset(data.p3rd.read(m.source.anim))
    placed = layout.of(m, donor, slots.anim_of(data.fu.read(m.port.host_frame)))
    seen: list = []
    modules(m, SimpleNamespace(layout=placed, tip=None), tmp_path, data, seen.append)  # type: ignore[arg-type]
    text = (tmp_path / moves.module_name(m)).read_text()
    entry = placed.ids[59]
    assert f'p3_7_14 = {{ entry = {entry}, slot = 7, clip = "p3_59"' in text
    assert "attacks = { { 135, 7, 165 }, { 138, 8, 148 } }" in text
    assert 'turn = "hunter", rate = 546, window = { 10, 40 }' in text
    assert 'after = "p3_7_14_2"' in text and any(f.code == "P3_OWN_SPARE" for f in seen)


def test_the_splice_enters_the_derived_moves(data, capsys):
    args = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["splice", str(PORTS / "brute_tigrex.toml"), *args]) == 0
    out = capsys.readouterr().out
    assert "pair 7,13 -> own p3_7_13 (slot 6)" in out and "pair 7,14 -> own p3_7_14 (slot 7)" in out


def test_the_summary_sorts_a_pair_that_turns_then_plays_a_move():

    uses = {
        ((7, 23), (1, 2)): 3,
        ((7, 23), "p3_7_0"): 3,
        ((7, 0), "p3_7_0"): 1,
        ((2, 5), (1, 2)): 4,
    }
    spliced = Spliced(None, [], {}, uses, {"p3_7_0": 5})  # type: ignore[arg-type]
    lines = summary(spliced, SimpleNamespace(image=None), []).splitlines()  # type: ignore[arg-type]
    assert [line for line in lines if "pair" in line] == [
        "  pair 2,5 -> 1,2  x4",
        "  pair 7,0 -> own p3_7_0 (slot 5)  x1",
        "  pair 7,23 -> 1,2  x3",
        "  pair 7,23 -> own p3_7_0 (slot 5)  x3",
    ]
