# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
import sys

import pytest
from mhp_formats import FormatError
from mhp_formats.databin import BLOCK, DataBin, Game, decrypt, encrypt


def _raw(words: list[int], blocks: int) -> bytes:
    toc = struct.pack(f"<{len(words)}I", *words)
    return toc + bytes(blocks * BLOCK - len(toc))


def test_layout():
    db = DataBin([b"a" * 10, b"", b"b" * 3000], {0: 10, 2: 3000}, toc_fill=b"\xaa" * 8)
    data = db.to_bytes()
    assert len(data) == 4 * BLOCK
    assert struct.unpack("<8I", data[:32]) == (1, 2, 2, 4, 0, 10, 2, 3000)
    assert data[32:48] == b"\xaa" * 8 + bytes(8)
    assert data[BLOCK : 2 * BLOCK] == b"a" * 10 + bytes(BLOCK - 10)
    assert data[2 * BLOCK :] == b"b" * 3000 + bytes(1096)
    back = DataBin.from_bytes(data)
    assert back.files == [data[BLOCK : 2 * BLOCK], b"", data[2 * BLOCK :]]
    assert back.sizes == {0: 10, 2: 3000}
    assert back.to_bytes() == data
    assert DataBin.sniff(data)


def test_empty():
    data = DataBin().to_bytes()
    assert data == _raw([1], 1)
    assert DataBin.from_bytes(data) == DataBin(toc_fill=bytes(BLOCK - 4))


def test_toc_grows():
    db = DataBin([b"x"] * 600, {i: 1 for i in range(0, 600, 2)}, toc_fill=b"\xaa" * 4000)
    data = db.to_bytes()
    assert struct.unpack("<2I", data[:8]) == (3, 4)
    assert data[3 * BLOCK - 4 : 3 * BLOCK] == b"\xaa" * 4
    assert DataBin.from_bytes(data).to_bytes() == data


def test_replace():
    db = DataBin([b"a" * BLOCK, b"b" * 5, b"c" * BLOCK], {1: 5})
    db.replace(1, b"B" * (BLOCK + 1))
    db.replace(2, b"C")
    data = db.to_bytes()
    assert struct.unpack("<6I", data[:24]) == (1, 2, 4, 5, 1, BLOCK + 1)
    assert db.sizes == {1: BLOCK + 1}
    assert data[4 * BLOCK :] == b"C" + bytes(BLOCK - 1)


@pytest.mark.parametrize(
    ("db", "match"),
    [
        (DataBin([b"a", b""]), "empty file"),
        (DataBin([b"a"], {0: BLOCK + 1}), "last block"),
        (DataBin([b"a" * 3000], {0: 100}), "last block"),
        (DataBin([b"a"], {1: 1}), "does not exist"),
    ],
)
def test_rejects_write(db, match):
    with pytest.raises(ValueError, match=match):
        db.to_bytes()


@pytest.mark.parametrize(("words", "sizes"), [([0, 5000], {}), ([1, 5, 0, 5], {1: 5})])
def test_records_end(words, sizes):
    # the size records have no count: they stop at one that does not fit, the rest is fill
    data = _raw([1, 2, 3, *words], 1) + b"f" * 2 * BLOCK
    db = DataBin.from_bytes(data)
    assert db.sizes == sizes
    assert db.toc_fill.startswith(struct.pack("<2I", *words[-2:]))
    assert db.to_bytes() == data


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes(BLOCK),
        _raw([1], 1) + b"x",
        _raw([3], 2),
        _raw([1, 5], 2),
        _raw([2, 1, 3], 3),
        _raw([2], 2),
    ],
)
def test_rejects(data):
    assert not DataBin.sniff(data)
    with pytest.raises(FormatError):
        DataBin.from_bytes(data)


# a file each game stores unencrypted, and one it encrypts
@pytest.mark.parametrize(
    ("game", "plain", "secret"), [(Game.MHFU, 42, 41), (Game.MHP2G, 22, 21), (Game.MHP3RD, 17, 16)]
)
def test_cipher(game, plain, secret):
    data = DataBin([bytes([i]) * BLOCK for i in range(64)], {3: BLOCK}).to_bytes()
    sealed = encrypt(data, game)
    assert len(sealed) == len(data)
    assert decrypt(sealed, game) == data

    def block(blob: bytes, file_id: int) -> bytes:
        return blob[(file_id + 1) * BLOCK : (file_id + 2) * BLOCK]

    assert block(sealed, plain) == block(data, plain)
    assert block(sealed, secret) != block(data, secret)
    assert sealed[:BLOCK] != data[:BLOCK]


def test_cipher_rejects():
    for data in (b"", bytes(BLOCK + 4), bytes(BLOCK)):
        with pytest.raises(FormatError):
            encrypt(data, Game.MHFU)
    with pytest.raises(FormatError):
        decrypt(encrypt(_raw([1], 1), Game.MHFU)[:BLOCK] + bytes(BLOCK), Game.MHFU)


def test_without_iso_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "mhef", None)
    monkeypatch.setitem(sys.modules, "mhef.psp", None)
    with pytest.raises(ImportError, match=r"mhp-formats\[iso\]"):
        decrypt(DataBin().to_bytes(), Game.MHFU)
