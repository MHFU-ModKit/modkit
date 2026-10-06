# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import io

import pycdlib
import pytest
from mhp_formats import FormatError
from mhp_formats.databin import BLOCK, DataBin, Game, encrypt
from mhp_formats.iso import DATA_BIN, data_file_name, extract

_FILES = [b"zero", b".TMH0.14tex", b"PSMF0015", b"", b"RIFFwave", b"VAG", b"pmo\0"]
_UMD_DATA = b"|0123456789ABCDEF|0001|G|"


def _iso(path, files):
    iso = pycdlib.PyCdlib()
    iso.new()
    parts = [name.split("/") for name in files]
    for directory in sorted({"/".join(p[:i]) for p in parts for i in range(2, len(p))}):
        iso.add_directory(directory)
    for name, data in files.items():
        iso.add_fp(io.BytesIO(data), len(data), f"{name};1")
    iso.write(str(path))
    iso.close()
    return path


def _game_iso(path, code=b"ULES-01213", game=Game.MHFU):
    data = encrypt(DataBin(_FILES).to_bytes(), game)
    return _iso(
        path,
        {
            "/UMD_DATA.BIN": code + _UMD_DATA,
            "/PSP_GAME/SYSDIR/BOOT.BIN": b"\x7fELF",
            f"/{DATA_BIN}": data,
        },
    )


@pytest.mark.parametrize("game", [Game.MHFU, Game.MHP2G])
def test_extract(tmp_path, game):
    steps = []
    out = tmp_path / "out"
    code = game.value.encode()
    assert extract(_game_iso(tmp_path / "game.iso", code, game), out, steps.append) is game
    assert (out / "UMD_DATA.BIN").read_bytes().startswith(code + b"|")
    assert (out / "PSP_GAME/SYSDIR/BOOT.BIN").read_bytes() == b"\x7fELF"
    assert not (out / DATA_BIN).exists()
    names = ["00000.tmh", "00001.pmf", "00002.bin", "00003.wav", "00004.vag", "00005.bin"]
    assert sorted(p.name for p in (out / "data_files").iterdir()) == [f"file_{n}" for n in names]
    assert (out / "data_files/file_00000.tmh").read_bytes() == _FILES[1] + bytes(BLOCK - 11)
    assert (out / "data_files/file_00002.bin").read_bytes() == b""
    assert "PSP_GAME/SYSDIR/BOOT.BIN" in steps


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b".TMH0.14", "file_00000.tmh"),
        (b"PSMF0015", "file_00000.pmf"),
        (b"RIFF\0\0\0\0", "file_00000.wav"),
        (b"VAGp\0\0\0\0", "file_00000.vag"),
        (b"VAGp", "file_00000.bin"),
        (b"pmo\0" * 2, "file_00000.bin"),
    ],
)
def test_data_file_name(data, name):
    assert data_file_name(1, data) == name


def test_rejects(tmp_path):
    with pytest.raises(FormatError, match="ULUS-10391"):
        extract(_game_iso(tmp_path / "na.iso", b"ULUS-10391"), tmp_path / "out")
    with pytest.raises(FormatError, match="UMD_DATA"):
        extract(_iso(tmp_path / "bare.iso", {"/README.TXT": b"hi"}), tmp_path / "out")
    with pytest.raises(FormatError, match="DATA.BIN"):
        extract(
            _iso(tmp_path / "empty.iso", {"/UMD_DATA.BIN": b"ULES-01213" + _UMD_DATA}), tmp_path
        )
    (tmp_path / "text.iso").write_bytes(b"not an image" * 1000)
    with pytest.raises(FormatError, match="not an ISO"):
        extract(tmp_path / "text.iso", tmp_path / "out")
