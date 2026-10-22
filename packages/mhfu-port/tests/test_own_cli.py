# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from types import SimpleNamespace

from mhfu_port import layout, manifest, motion, moves, slots
from mhfu_port.cli import main
from mhfu_port.cli.build import modules

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
