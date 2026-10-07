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


def test_steer_packs_as_the_spec():
    sp = moves.Steer((0, 0x2000, 0xFFFF), "fixed", 64, -90.0, 30, True, 90.0, (0, 6, 1))
    b = sp.pack()
    assert len(b) == a.STEER_SPEC.size
    turn, walls, rate, total, frames, dir_ = struct.unpack_from("<BBHiHH", b, a.STEER_SPEC.STEER)
    assert (turn, walls, rate, total, frames, dir_) == (3, 1, 64, -0x4000, 30, 0x4000)
    assert struct.unpack_from("<HBBB", b, a.STEER_SPEC.KEY_COUNT) == (3, 0, 6, 1)
    assert struct.unpack_from("<3H", b, a.STEER_SPEC.KEYS) == (0, 0x2000, 0xFFFF)


def test_turns_of_a_clips_module():
    text = 'return {\n  dash = 20,\n  _turns = {\n    [46] = "00001000ffff",\n  },\n}\n'
    assert moves.turns_of(text) == {46: (0, 0x1000, 0xFFFF)}


def test_unpack_reads_back_pack() -> None:
    mv = moves.Move(46, (moves.Attack(6, 56, 80), moves.Attack(31, 90)), (0, 1), (0, 3, 1), 9)
    assert moves.Move.unpack(mv.pack()) == mv


def test_hp_line_takes_the_timing_part() -> None:
    line = "1000 8804000 8805000 42600000 42640000 42680000 96 0201 1c"
    w = moves._parse(line, 0x1000 - 2_000_000, part=1)
    assert w is not None and (w.phase, w.hp, w.pair, w.frame) == (57.0, 150, (1, 2), 28)
    assert w.t == 2.0 and moves._parse("junk", 0) is None
