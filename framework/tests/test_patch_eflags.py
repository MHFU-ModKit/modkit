# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import importlib.util
import struct
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "patch_prx_eflags", Path(__file__).parents[1] / "tools" / "patch_prx_eflags.py"
)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _elf(tmp_path: Path, e_flags: int) -> Path:
    """A 32-bit little-endian MIPS ELF header, followed by a marker the tool must not touch."""
    ident = b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
    header = ident + struct.pack(
        "<HHIIIIIHHHHHH", 2, 8, 1, 0, 0x34, 0, e_flags, 0x34, 0x20, 0, 0x28, 0, 0
    )
    path = tmp_path / "t.prx"
    path.write_bytes(header + b"payload")
    return path


def _flags(path: Path) -> int:
    return int(struct.unpack_from("<I", path.read_bytes(), 0x24)[0])


def test_clears_eabi32(tmp_path: Path) -> None:
    path = _elf(tmp_path, 0x10A23001)
    before = path.read_bytes()
    assert tool.clear_abi(path) == (0x10A23001, 0x10A20001)
    after = path.read_bytes()
    assert _flags(path) == 0x10A20001
    assert [i for i in range(len(after)) if after[i] != before[i]] == [0x25]


def test_idempotent(tmp_path: Path) -> None:
    path = _elf(tmp_path, 0x10A20001)
    before = path.read_bytes()
    assert tool.clear_abi(path) == (0x10A20001, 0x10A20001)
    assert path.read_bytes() == before


def test_rejects_non_elf(tmp_path: Path) -> None:
    path = tmp_path / "x.prx"
    path.write_bytes(bytes(64))
    with pytest.raises(ValueError):
        tool.clear_abi(path)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _elf(tmp_path, 0x10A23001)
    assert tool.main([str(path)]) == 0
    assert "0x10A23001 -> 0x10A20001" in capsys.readouterr().out
    assert tool.main([]) == 2
