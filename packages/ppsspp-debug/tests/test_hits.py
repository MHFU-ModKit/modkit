# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from ppsspp_debug import Hit, parse_hit


@pytest.mark.parametrize(
    ("line", "hit"),
    [
        (
            "BKP PC=00401234 (z_un_00401234)",
            Hit("exec", 0x401234, 0x401234, False, message="z_un_00401234"),
        ),
        ("BKP PC=00401234: a0=1", Hit("exec", 0x401234, 0x401234, False, message="a0=1")),
        (
            "CHK Write16(CPU) at 00402000 (data), PC=00401234 (z_un_00401234)\n",
            Hit("memory", 0x402000, 0x401234, False, "write", 2, "CPU", "data"),
        ),
        (
            "CHK Read32(CPU) at 00402000: v=5",
            Hit("memory", 0x402000, None, False, "read", 4, "CPU", "v=5"),
        ),
        ("Loading savestate", None),
    ],
)
def test_parse_hit(line, hit):
    assert parse_hit(line) == hit
