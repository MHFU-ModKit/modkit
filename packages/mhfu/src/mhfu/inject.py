"""Files for the framework's live model injection, in the memory stick's inject directory.

The framework PRX polls that directory; it overwrites a loaded file's raw buffer with
`file_<id>.bin`, or relocates a grown file (`file_<id>_grown.bin`) into extra RAM, once the
buffer matches the pristine `file_<id>.bin.orig`. The bytes come finished from a builder;
nothing here builds or checks them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MEMSTICK_ROOTS = (
    "~/.config/ppsspp/PSP",
    "~/Documents/PPSSPP/PSP",
    "~/Library/Application Support/PPSSPP/PSP",
)
"""PPSSPP memory-stick roots, most likely first; the PRX sees them as ms0:/PSP."""
INJECT_SUBDIR = "PLUGINS/mhfu_framework/inject"
ORIG = ".orig"

_NAME = re.compile(r"file_(\d{4,6})\b")


def default_inject_dir(create: bool = True) -> str:
    """The inject directory on the first memory stick found, created unless `create` is off."""
    for root in MEMSTICK_ROOTS:
        base = Path(root).expanduser()
        if base.is_dir():
            d = base / INJECT_SUBDIR
            if create:
                d.mkdir(parents=True, exist_ok=True)
            return str(d)
    raise FileNotFoundError(f"no PPSSPP memory stick in {', '.join(MEMSTICK_ROOTS)}")


def file_id_from_name(name: str | os.PathLike[str]) -> int:
    """The file id in a `file_NNNNN.bin` name or path."""
    m = _NAME.search(Path(name).name)
    if not m:
        raise ValueError(f"no file id in {str(name)!r}")
    return int(m[1])


def inject_filename(file_id: int) -> str:
    return f"file_{file_id:05d}.bin"


def relocate_filename(file_id: int) -> str:
    """The grown file the relocate path loads into extra RAM."""
    return f"file_{file_id:05d}_grown.bin"


def _atomic_write(dst: Path, data: bytes) -> None:
    """Write through a temp file, so the polling PRX never sees half a file."""
    tmp = dst.with_name(dst.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)


def _place(
    name: str,
    data: bytes,
    file_id: int,
    inject_dir: str | os.PathLike[str] | None,
    orig: bytes | None,
) -> str:
    d = Path(inject_dir if inject_dir is not None else default_inject_dir())
    d.mkdir(parents=True, exist_ok=True)
    if orig is not None:
        _atomic_write(d / (inject_filename(file_id) + ORIG), orig)
    dst = d / name
    _atomic_write(dst, data)
    return str(dst)


def write_inject_bytes(
    data: bytes,
    file_id: int,
    inject_dir: str | os.PathLike[str] | None = None,
    orig: bytes | None = None,
) -> str:
    """Place `data` as file `file_id`'s replacement and return its path. `orig`, the pristine
    file the engine loads, goes first as the `.orig` sibling the PRX matches the buffer on."""
    return _place(inject_filename(file_id), data, file_id, inject_dir, orig)


def write_relocate_bytes(
    grown: bytes,
    file_id: int,
    inject_dir: str | os.PathLike[str] | None = None,
    orig: bytes | None = None,
) -> str:
    """Place a file larger than the one the engine loaded, for the relocate path, and return
    its path; `orig` as for `write_inject_bytes`."""
    return _place(relocate_filename(file_id), grown, file_id, inject_dir, orig)
