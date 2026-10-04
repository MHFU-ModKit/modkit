# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhfu import addresses as a
from mhfu import files, reloc
from mhfu.cli import main
from mhfu.mips import Code
from mhfu.overlay import TEXT, Overlay
from modkit_testing import mips as asm

LOAD = 0x0012_0180
CODE = LOAD + TEXT + 0x40
CALLEE = CODE + 4 * 6
DATA = CODE + 4 * 8


def extracted(tmp_path):
    """A game directory holding one synthetic em75 overlay."""
    words = [
        asm.lui("a0", asm.hi(DATA)),
        asm.jal(CALLEE),
        asm.addiu("a0", "a0", asm.lo(DATA)),
        asm.RET,
        asm.NOP,
        asm.NOP,
        asm.RET,  # CALLEE
        asm.NOP,
    ]
    text = bytes(0x40) + struct.pack(f"<{len(words)}I", *words)
    data = struct.pack("<2I", CALLEE, 7)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), len(data), 0, DATA, DATA)
    (tmp_path / "data_files").mkdir()
    ovl = header.ljust(TEXT, b"\0") + text + data
    (tmp_path / "data_files" / f"file_{files.em_overlay(75):05d}.bin").write_bytes(ovl)
    return ["--overlay", "em75", "--data", str(tmp_path)], Overlay(ovl)


def run(capsys, *args):
    assert main([str(x) for x in args]) == 0
    return capsys.readouterr().out.splitlines()


def test_dis(tmp_path, capsys):
    source, _ = extracted(tmp_path)
    lines = run(capsys, "dis", hex(CODE), 3, *source)
    assert [line.split()[2] for line in lines] == ["lui", "jal", "addiu"]
    assert lines[0].startswith(f"0x{CODE:08X}")


def test_xref(tmp_path, capsys):
    source, _ = extracted(tmp_path)
    calls = run(capsys, "xref", hex(CALLEE), *source)
    assert calls[0].split()[:2] == ["call", f"0x{CODE + 4:08X}"]
    assert calls[1].split() == ["data", f"0x{DATA:08X}"]
    (pair,) = run(capsys, "xref", hex(DATA), *source)
    assert pair.split()[:3] == ["hi/lo", f"0x{CODE:08X}", f"0x{CODE + 8:08X}"]


def test_reloc(tmp_path, capsys):
    source, ovl = extracted(tmp_path)
    out = tmp_path / "moved.bin"
    run(capsys, "reloc", "em75", "-o", out, "--data", tmp_path, "--", "-0x10000")
    assert out.read_bytes() == reloc.relocate(ovl, -0x10000)


@pytest.mark.parametrize(
    "args",
    [["dis", "0", "--overlay", "em99"], ["switch", "0x100000", "--overlay", "em75"]],
)
def test_errors(tmp_path, capsys, args):
    extracted(tmp_path)
    assert main([*args, "--data", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith(f"mhfu {args[0]}: ")


def test_named_address(game, capsys):
    (line,) = run(capsys, "dis", "ACTION_EXECUTOR", 1, "--overlay", "game_task")
    assert line.startswith(f"0x{a.ACTION_EXECUTOR:08X}")
    assert f"0x{a.OVERLAY_LOAD_CALL:08X}" in "".join(run(capsys, "xref", "OVERLAY_SEGMENT_LOAD"))


def test_switch(game, capsys):
    ovl = game.em(75)
    s = next(iter(Code(ovl, ovl.text).switches.values()))
    head, *cases = run(capsys, "switch", hex(s.jr), "--overlay", "em75")
    assert head.startswith(f"jr 0x{s.jr:08X}  table 0x{s.table:08X}")
    assert [int(c.split()[1], 16) for c in cases] == list(s.targets)
