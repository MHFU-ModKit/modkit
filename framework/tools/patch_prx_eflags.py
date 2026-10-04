# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Clear the MIPS ABI field of a PRX's ELF e_flags, in place.

The toolchain marks its output EF_MIPS_ABI_EABI32 and psp-prxgen copies that into the PRX; old
firmware loaders (retail 6.x, PRO CFW) reject an ABI they do not know with 0x800200D9, PPSSPP does
not look. The code is unchanged, only the header word.
"""

import struct
import sys
from pathlib import Path

E_FLAGS_OFFSET = 0x24  # in a 32-bit ELF header
EF_MIPS_ABI = 0x0000F000


def clear_abi(path: Path) -> tuple[int, int]:
    """Clear the ABI field of `path`; return (old, new) e_flags."""
    with path.open("r+b") as f:
        if f.read(4) != b"\x7fELF":
            raise ValueError(f"{path}: not an ELF file")
        f.seek(E_FLAGS_OFFSET)
        (old,) = struct.unpack("<I", f.read(4))
        new = old & ~EF_MIPS_ABI
        if new != old:
            f.seek(E_FLAGS_OFFSET)
            f.write(struct.pack("<I", new))
    return old, new


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: patch_prx_eflags.py <file.prx>...", file=sys.stderr)
        return 2
    for arg in argv:
        old, new = clear_abi(Path(arg))
        if new != old:
            print(f"{arg}: e_flags 0x{old:08X} -> 0x{new:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
