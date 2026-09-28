# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.pac import Pac


def test_layout():
    pac = Pac([b"abc", b"", b"defgh"], align=16, tail=b"\0\0")
    data = pac.to_bytes()
    assert data[:4] == (3).to_bytes(4, "little")
    assert Pac.from_bytes(data) == pac
    assert data.index(b"abc") == 32
    assert data.index(b"defgh") == 48
    assert data.endswith(b"defgh\0\0")


def test_nested_alignment():
    pac = Pac([b"abcde", b"fg"], align=4)
    data = pac.to_bytes()
    assert data.index(b"fg") == 28
    assert Pac.from_bytes(data) == pac


@pytest.mark.parametrize("data", [b"", b"\xff" * 16, (1).to_bytes(4, "little") + bytes(8)])
def test_rejects(data):
    assert not Pac.sniff(data)
    with pytest.raises(FormatError):
        Pac.from_bytes(data)


def _round_trip(data_dir: Path) -> int:
    checked = 0
    for path in sorted(data_dir.glob("file_*")):
        data = path.read_bytes()
        if Pac.sniff(data):
            assert Pac.from_bytes(data).to_bytes() == data, path.name
            checked += 1
    return checked


def test_round_trip_mhfu(mhfu_data):
    assert _round_trip(mhfu_data) > 1000


def test_round_trip_mhp3rd(mhp3rd_data):
    assert _round_trip(mhp3rd_data) > 1000
