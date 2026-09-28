# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Getting a game's files out of its UMD image. Needs the `iso` extra (pycdlib, mhef)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._base import FormatError
from .databin import DataBin, Game, _iso_extra, decrypt

DATA_BIN = "PSP_GAME/USRDIR/DATA.BIN"
DATA_FILES = "data_files"
_ID_FILE = "UMD_DATA.BIN"
_EXTENSIONS = ((b".TMH0.14", ".tmh"), (b"PSMF", ".pmf"), (b"RIFF", ".wav"), (b"VAG", ".vag"))


def data_file_name(file_id: int, data: bytes) -> str:
    """The name `extract` gives engine file `file_id` (from 1), its extension from its magic.

    `file_NNNNN` is engine file NNNNN + 1 (counted from the table's second word), as every
    existing reference to these files has it."""
    magic = (ext for head, ext in _EXTENSIONS if len(data) >= 8 and data.startswith(head))
    return f"file_{file_id - 1:05d}{next(magic, '.bin')}"


def extract(
    iso_path: str | Path, out_dir: str | Path, progress: Callable[[str], object] = lambda step: None
) -> Game:
    """Copy the image's files into `out_dir`, DATA.BIN as its decrypted files in `data_files/`.

    Engine file 0 is not written, and neither is DATA.BIN itself. `progress` hears each step."""
    out = Path(out_dir)
    pycdlib = _iso_extra("pycdlib")
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(iso_path))
    except pycdlib.pycdlibexception.PyCdlibException as e:
        raise FormatError(f"{iso_path}: not an ISO image ({e})") from e
    try:
        files = _files(iso)
        game = _identify(iso, files)
        if DATA_BIN not in files:
            raise FormatError(f"{iso_path}: no {DATA_BIN}")
        for name, path in files.items():
            if name != DATA_BIN:
                progress(name)
                (out / name).parent.mkdir(parents=True, exist_ok=True)
                with (out / name).open("wb") as f:
                    iso.get_file_from_iso_fp(f, iso_path=path)
        progress(f"decrypting {DATA_BIN} of {game.name} ({game.value})")
        databin = DataBin.from_bytes(decrypt(_read(iso, files[DATA_BIN]), game))
    finally:
        iso.close()
    progress(f"writing {len(databin.files) - 1} files to {DATA_FILES}/")
    (out / DATA_FILES).mkdir(parents=True, exist_ok=True)
    for file_id, data in enumerate(databin.files[1:], 1):
        (out / DATA_FILES / data_file_name(file_id, data)).write_bytes(data)
    return game


def _files(iso: Any) -> dict[str, str]:
    """Every file in the image: its path without the ISO 9660 version, to its path in the image."""
    return {
        f"{root}/{name}".lstrip("/").partition(";")[0]: f"{root.rstrip('/')}/{name}"
        for root, _, names in iso.walk(iso_path="/")
        for name in names
    }


def _read(iso: Any, path: str) -> bytes:
    with iso.open_file_from_iso(iso_path=path) as f:
        return bytes(f.read())


def _identify(iso: Any, files: dict[str, str]) -> Game:
    if _ID_FILE not in files:
        raise FormatError(f"no {_ID_FILE}: not a UMD image")
    code = _read(iso, files[_ID_FILE]).partition(b"|")[0].decode("ascii", "replace")
    try:
        return Game(code)
    except ValueError:
        known = ", ".join(f"{g.value} ({g.name})" for g in Game)
        raise FormatError(f"unsupported game {code}: known are {known}") from None
