# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).parents[1] / "tools" / "check_prx.py"
_spec = importlib.util.spec_from_file_location("check_prx", TOOL)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

START, INFO = tool.NID_MODULE_START, tool.NID_MODULE_INFO
BASE = 0x1000
MEMSZ = 5000


def _prx(
    tmp_path: Path,
    *,
    imports: tuple[str, ...] = (),
    ent: bytes | None = None,
    attr: int = 0x8000,
    nids: tuple[int, ...] = (START, INFO),
    memsz: int = MEMSZ,
    stub_words: int = 5,
) -> Path:
    """A 32-bit MIPS ELF with PT_LOAD, `.lib.ent`, `.rodata.sceResident` and `.lib.stub`."""
    names = b"".join(n.encode() + b"\0" for n in imports)
    resident = struct.pack(f"<{len(nids)}I", *nids)
    stub_at = BASE + len(resident)
    ent_at = stub_at + len(names)
    stubs = b""
    off = stub_at
    for n in imports:
        stubs += struct.pack("<IHHBBHII", off, 0, 0x9, stub_words, 0, 1, 0, 0)
        stubs += bytes(4 * (stub_words - 5))
        off += len(n) + 1
    if ent is None:
        ent = struct.pack("<IHHBBHI", 0, 0, attr, 4, 1, 1, BASE)
    parts = [
        (".rodata.sceResident", resident),
        (".rodata.names", names),
        (".lib.ent", ent),
        (".lib.stub", stubs),
    ]
    blob = b""
    addrs = {}
    for name, data in parts:
        addrs[name] = (BASE + len(blob), len(blob), len(data))
        blob += data
    assert addrs[".rodata.names"][0] == stub_at and addrs[".lib.ent"][0] == ent_at
    shstr = b"\0" + b"".join(n.encode() + b"\0" for n, _ in parts) + b".shstrtab\0"
    first = 52 + 32
    shstr_off = first + len(blob)
    sh_off = shstr_off + len(shstr)
    sections = [bytes(40)]
    cursor = 1
    for name, _ in parts:
        vaddr, rel, size = addrs[name]
        sections.append(struct.pack("<10I", cursor, 1, 2, vaddr, first + rel, size, 0, 0, 4, 0))
        cursor += len(name) + 1
    sections.append(struct.pack("<10I", cursor, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    ident = b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
    ehdr = ident + struct.pack(
        "<HHIIIIIHHHHHH", 0xFFA0, 8, 1, 0, 52, sh_off, 0, 52, 32, 1, 40, len(sections), 5
    )
    phdr = struct.pack("<8I", 1, first, BASE, BASE, len(blob), memsz, 5, 4)
    path = tmp_path / "t.prx"
    path.write_bytes(ehdr + phdr + blob + shstr + b"".join(sections))
    return path


def _problems(path: Path, budget: int = MEMSZ, *, kernel: bool = False) -> list[str]:
    return list(tool.check(path, budget, kernel=kernel))


def test_pass(tmp_path: Path) -> None:
    assert _problems(_prx(tmp_path, imports=("IoFileMgrForUser",))) == []


def test_budget_edge(tmp_path: Path) -> None:
    path = _prx(tmp_path)
    assert _problems(path, MEMSZ) == []
    assert any("over budget" in p for p in _problems(path, MEMSZ - 1))


def test_no_syslib(tmp_path: Path) -> None:
    assert any(".lib.ent" in p for p in _problems(_prx(tmp_path, ent=b"")))


def test_two_entries(tmp_path: Path) -> None:
    ent = struct.pack("<IHHBBHI", 0, 0, 0x8000, 4, 1, 1, BASE) * 2
    assert any(".lib.ent" in p for p in _problems(_prx(tmp_path, ent=ent)))


def test_wrong_attr(tmp_path: Path) -> None:
    assert any("attr" in p for p in _problems(_prx(tmp_path, attr=0x0009)))


def test_named_export(tmp_path: Path) -> None:
    ent = struct.pack("<IHHBBHI", BASE, 0, 0x8000, 4, 1, 1, BASE)
    assert any("name" in p for p in _problems(_prx(tmp_path, ent=ent)))


@pytest.mark.parametrize("nids", [(START, 0), (0, INFO), (INFO, START)])
def test_wrong_nid(tmp_path: Path, nids: tuple[int, int]) -> None:
    assert any("NIDs" in p for p in _problems(_prx(tmp_path, nids=nids)))


def test_kernel_ok(tmp_path: Path) -> None:
    path = _prx(tmp_path, imports=("IoFileMgrForKernel", "ModuleMgrForKernel"))
    assert _problems(path, kernel=True) == []


def test_kernel_user_import(tmp_path: Path) -> None:
    path = _prx(tmp_path, imports=("ModuleMgrForKernel", "ModuleMgrForUser"))
    assert any("ModuleMgrForUser" in p for p in _problems(path, kernel=True))


def test_kernel_no_kernel_import(tmp_path: Path) -> None:
    path = _prx(tmp_path, imports=("IoFileMgrForKernel",))
    assert any("does not import" in p for p in _problems(path, kernel=True))


def test_stub_entry_length(tmp_path: Path) -> None:
    path = _prx(tmp_path, imports=("IoFileMgrForKernel", "ModuleMgrForUser"), stub_words=6)
    assert any("ModuleMgrForUser" in p for p in _problems(path, kernel=True))


def test_user_import_free_without_flag(tmp_path: Path) -> None:
    assert _problems(_prx(tmp_path, imports=("ModuleMgrForUser",))) == []


def _cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True, check=False
    )


def test_cli(tmp_path: Path) -> None:
    path = _prx(tmp_path)
    ok = _cli(path, MEMSZ)
    assert ok.returncode == 0 and ok.stdout.strip() == f"{path}: memsz {MEMSZ} of {MEMSZ}"
    bad = _cli(path, 1)
    assert bad.returncode == 1 and "over budget" in bad.stderr


BUILD = Path(__file__).parents[1] / "build"


@pytest.mark.parametrize("name,kernel", [("mhfu_framework", False), ("mhfu_boot", True)])
def test_real_build(name: str, kernel: bool) -> None:
    path = BUILD / f"{name}.prx"
    if not path.exists():
        pytest.skip("no PRX build")
    assert _problems(path, 1 << 30, kernel=kernel) == []
