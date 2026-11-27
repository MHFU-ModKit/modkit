#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Check a built PRX: footprint under a budget, and the two firmware-only rejections.

Real firmware (PRO CFW) rejects a PRX without the PSPSDK syslib export, and a user-mode
`ModuleMgrForUser` import in the kernel bootstrap; PPSSPP loads both. The memory footprint is the
PT_LOAD `p_memsz`, which shares the user partition with the game.
"""

import argparse
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import Section

NID_MODULE_START = 0xD632ACDB
NID_MODULE_INFO = 0xF01D73A7
SYSLIB_ATTR = 0x8000
USER_MODMGR = "ModuleMgrForUser"
KERNEL_MODMGR = "ModuleMgrForKernel"

# name, version, attr, entry length in words, variable count, function count, then two pointers
_ENT = struct.Struct("<IHHBBH")
_STUB = struct.Struct("<IHHBBHII")


def _bytes_at(elf: ELFFile, addr: int, size: int) -> bytes:
    """`size` bytes of the loaded image at `addr`, or empty when no section holds them."""
    for sec in elf.iter_sections():
        if sec["sh_type"] != "SHT_PROGBITS" or not sec["sh_addr"]:
            continue
        offset = addr - sec["sh_addr"]
        if 0 <= offset and offset + size <= sec["sh_size"]:
            return bytes(sec.data()[offset : offset + size])
    return b""


def _cstr(elf: ELFFile, addr: int) -> str:
    out = b""
    while (c := _bytes_at(elf, addr + len(out), 1)) not in (b"", b"\0"):
        out += c
    return out.decode("ascii", "replace")


def memsz(elf: ELFFile) -> int:
    """`p_memsz` of the PT_LOAD segment."""
    loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
    if len(loads) != 1:
        raise ValueError(f"expected one PT_LOAD, found {len(loads)}")
    return int(loads[0]["p_memsz"])


def _section_data(elf: ELFFile, name: str) -> bytes:
    sec = elf.get_section_by_name(name)
    return bytes(sec.data()) if isinstance(sec, Section) else b""


def syslib_problems(elf: ELFFile) -> list[str]:
    """Why the `.lib.ent` is not exactly the PSPSDK syslib export (empty when it is)."""
    data = _section_data(elf, ".lib.ent")
    if len(data) != 16:
        return [f".lib.ent is {len(data)} bytes, want one 16-byte syslib entry"]
    name, _version, attr, _words, variables, functions = _ENT.unpack_from(data)
    (table,) = struct.unpack_from("<I", data, 12)
    problems = []
    if name != 0:
        problems.append("syslib export has a library name")
    if attr != SYSLIB_ATTR:
        problems.append(f"syslib attr 0x{attr:04X}, want 0x{SYSLIB_ATTR:04X}")
    nids = struct.unpack("<2I", _bytes_at(elf, table, 8) or bytes(8))
    if (functions, variables) != (1, 1) or nids != (NID_MODULE_START, NID_MODULE_INFO):
        problems.append(
            f"syslib NIDs {nids[0]:#010X}, {nids[1]:#010X} ({functions} funcs, {variables} vars), "
            f"want module_start {NID_MODULE_START:#010X} and module_info {NID_MODULE_INFO:#010X}"
        )
    return problems


def imports(elf: ELFFile) -> list[str]:
    """Names of the imported libraries."""
    data = _section_data(elf, ".lib.stub")
    names = []
    for off in range(0, len(data) - _STUB.size + 1, _STUB.size):
        names.append(_cstr(elf, _STUB.unpack_from(data, off)[0]))
    return names


def kernel_problems(elf: ELFFile) -> list[str]:
    """Why a kernel bootstrap would bind the wrong module manager."""
    libs = imports(elf)
    problems = []
    if USER_MODMGR in libs:
        problems.append(f"imports {USER_MODMGR}: a kernel thread is refused (0x80020149)")
    if KERNEL_MODMGR not in libs:
        problems.append(f"does not import {KERNEL_MODMGR}")
    return problems


def check(path: Path, budget: int, *, kernel: bool = False) -> list[str]:
    """All problems of the PRX at `path`; empty when it passes."""
    with path.open("rb") as f:
        elf = ELFFile(f)
        size = memsz(elf)
        problems = [f"memsz {size} over budget {budget}"] if size > budget else []
        problems += syslib_problems(elf)
        if kernel:
            problems += kernel_problems(elf)
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kernel", action="store_true", help="a kernel bootstrap: check its imports")
    ap.add_argument("prx", type=Path)
    ap.add_argument("budget", type=int, help="ceiling on PT_LOAD p_memsz, bytes")
    args = ap.parse_args(argv)
    with args.prx.open("rb") as f:
        print(f"{args.prx}: memsz {memsz(ELFFile(f))} of {args.budget}")
    problems = check(args.prx, args.budget, kernel=args.kernel)
    for p in problems:
        print(f"{args.prx}: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
