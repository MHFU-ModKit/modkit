# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em import script
from mhfu.em.script import disasm, ir, lift, walk
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000


def hexed(*parts):
    return bytes.fromhex("".join(parts))


def reader(raw):
    """The bytes at BASE; past them the sentinel."""

    def read(pc):
        return raw[pc - BASE] if 0 <= pc - BASE < len(raw) else script.SENTINEL

    return read


# ---- walk


def test_step_is_length():
    read = reader(hexed("3900", "05000100", "1c0002", "0603010001", "ff00"))
    assert [walk.step(read, BASE + o) for o in (0, 2, 6, 9)] == [2, 4, 3, 5]
    assert all(walk.step(read, BASE + o) == disasm.length(read, BASE + o) for o in (0, 2, 6, 9))


def test_step_short_and_stall():
    read = reader(hexed("940001", "940105", "9403", "00"))
    assert disasm.length(read, BASE) == 3 and walk.step(read, BASE) == 5
    assert walk.step(read, BASE + 3) == 3  # a case marker is not an opener
    assert walk.step(read, BASE + 8) == 0  # no case in the table


def test_scan_markers():
    read = reader(hexed("3900", "05000100", "3901", "05000200", "3902", "ff00"))
    s = walk.scan(read, BASE)
    assert (s.markers, s.steps, s.stop) == ([BASE + 6, BASE + 12], 2, None)
    assert disasm.markers(read, BASE) == s.markers
    assert walk.scan(read, BASE + 6).markers == [BASE + 12]  # from a marker on


def test_scan_skips_inner_blocks():
    read = reader(hexed("3900", "3900", "3901", "3902", "3901", "3902", "ff00"))
    assert walk.scan(read, BASE).markers == [BASE + 8, BASE + 10]
    assert walk.scan(read, BASE + 2).markers == [BASE + 4, BASE + 6]


def test_scan_guard():
    def scan(n):
        return walk.scan(reader(hexed("3900") + b"\x92" * n + hexed("3902")), BASE)

    assert walk.GUARD == 1000
    assert (scan(999).stop, scan(999).steps) == (None, 1000)
    assert (scan(1000).stop, scan(1000).markers) == ("guard", [])


def test_scan_stall_and_sentinel():
    assert walk.scan(reader(hexed("3900", "00", "3902")), BASE).stop == "stall"
    assert walk.scan(reader(hexed("3900", "05000100")), BASE).stop == "sentinel"
    assert walk.scan(reader(hexed("3900", "3901", "95", "3902")), BASE).markers == [BASE + 2]


def test_scan_short_opener():
    raw = hexed("940001", "940192", "05000100", "9403")
    read = reader(raw)
    assert disasm.markers(read, BASE) == [BASE + 3, BASE + 10]
    s = walk.scan(read, BASE)  # the first case is stepped over; its value reads as a nop
    assert (s.markers, s.stop) == ([BASE + 10], None)
    stalls = walk.scan(reader(hexed("940001", "940100", "9403")), BASE)
    assert (stalls.markers, stalls.stop) == ([], "stall")


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    """Every overlay's species rows, lifted: {em: (Scripts, {row: Program})}."""
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def test_game_scan(rows):
    blocks = short = longest = 0
    off = []
    for em, (sc, ps) in rows.items():
        for r in ps[em].routines():
            for n in ir.walk(r.body):
                if not isinstance(n, ir.Block):
                    continue
                s = walk.scan(sc.mem.u8, n.pc)
                blocks, longest = blocks + 1, max(longest, s.steps)
                if n.op.code == walk.SHORT:
                    short += 1
                    assert s.stop == "stall"
                else:
                    assert s.stop is None
                    if s.markers != [a.pc for a in n.arms] + [n.close]:
                        off.append((em, n.op.name))
    assert blocks > 5000 and short == 9 and longest == 794
    assert off == [(83, "random")]  # a random_shifted inside it shares its markers
