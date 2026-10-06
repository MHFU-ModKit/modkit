# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The move player's host side: the MOVE bytes and the report."""

import struct

from mhfu import addresses as a
from mhfu.cli import moves as cli
from mhfu.live import moves


def test_pack_follows_the_table() -> None:
    atk = (moves.Attack(6, 56, 80), moves.Attack(31, 90))
    raw = moves.Move(46, atk, back=(0, 2, 1), skip=True, part=1).pack()
    assert a.MOVE.size and len(raw) == a.MOVE.size
    assert struct.unpack_from("<HH", raw, a.MOVE.ENTRY) == (46, 0)
    assert raw[a.MOVE.CARRIER_MAIN : a.MOVE.ATTACK_COUNT + 1] == bytes([0, 2, 0, 2, 1, 1, 1, 2])
    assert struct.unpack_from("<8H", raw, a.MOVE.ATTACKS) == (56, 6, 80, 0, 90, 31, 0, 0)


def test_no_back_pair_is_ff() -> None:
    raw = moves.Move(46).pack()
    assert raw[a.MOVE.BACK_MAIN] == moves.NO_PAIR and raw[a.MOVE.HOST_ATTACKS] == 0
    assert moves.Move(46, host_attacks=True).pack()[a.MOVE.HOST_ATTACKS] == 1


def test_attack_word() -> None:
    assert cli._attack("6@56") == moves.Attack(6, 56)
    assert cli._attack("6@56-90") == moves.Attack(6, 56, 90)


def test_report_times_the_hit_against_the_spawn() -> None:
    hit = moves.HpWrite(1.0, 0x1000, 0x2000, 150, 58.0, (0, 1), 29)
    r = moves.Played(
        moves.Move(46, (moves.Attack(6, 56, 90),)),
        1,
        (0, 1),
        114,
        0,
        (moves.Part(0, (0, 46), ((0, 46),), 228.0, 228.0),),
        (moves.Spawn(6, 56, 28, 56.0, 0x09C12000, 90, 45, 2),),  # noaddr
        (hit,),
        ((0.0, (1, 3)), (0.1, (0, 1))),
        3.9,
        141,
        0,
    )
    text = "\n".join(cli.report(r))
    assert "played to its end" in text and "6@56-90" in text and "AI frame 45: ended" in text
    assert "hunter -9 HP" in text and "+1 from the spawn" in text
