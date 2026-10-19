# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import struct

import pytest
from mhfu.cli import main
from mhfu.em import script
from mhfu.em.script import BadScript, Flow, Header, Ref, Shape, Source
from mhfu.files import EM_SPECIES
from mhfu.memory import Unmapped
from mhfu.overlay import Overlay

BASE = 0x0010_0000


def reader(*parts: str, base: int = BASE):
    """A byte reader over hex strings laid end to end; past them is unmapped."""
    blob = bytes.fromhex("".join(parts))

    def read(pc: int) -> int:
        if not base <= pc < base + len(blob):
            raise Unmapped(f"0x{pc:08X}")
        return blob[pc - base]

    return read


def names(r: script.Routine) -> list[str]:
    return [i.mnemonic for i in r.insns]


LENGTHS = [
    ("04", 1),
    ("05031b00", 4),
    ("0601000a", 4),
    ("060300010203", 5),
    ("0f0102030405", 6),
    ("8203ff", 3),
    ("ff01", 2),
    ("3900", 2),
    ("3901", 2),
    ("3902", 2),
    ("0b0007", 3),
    ("0b01", 2),
    ("0e000102", 4),
    ("1c0002", 3),
    ("1c0105", 3),
    ("1c02", 2),
    ("1c03", 2),
    ("1500", 3),
    ("15010074", 4),
    ("790001", 4),
    ("24000a", 3),
    ("2401", 2),
    ("80ff", 2),
    ("800003", 3),
    ("800108", 3),
    ("850002ff", 4),
    ("830002", 3),
    ("8301", 2),
    ("83ff", 2),
    ("6200", 2),
    ("62ff", 2),
    ("3907", 1),
]


@pytest.mark.parametrize(("hexed", "n"), LENGTHS)
def test_length(hexed, n):
    assert script.length(reader(hexed + "ee" * 8), BASE) == n


def test_unknown_opcode():
    for code in ("00", "23", "37", "95", "98", "fe"):
        with pytest.raises(BadScript, match="no case"):
            script.length(reader(code + "00"), BASE)


def test_table():
    assert len(script.OPCODES) == 130
    for code, op in script.OPCODES.items():
        assert op.code == code
        assert op.kind in {"flow", "condition", "action", "state", "random", "wait", "other"}
        assert op.marker in script.OPCODES
        if op.coded:
            marks = script.BLOCKS[op.shape]
            assert set(op.lengths) == {0, *marks}
            assert list(marks.values()).count("end") == 1
    assert len({op.name for op in script.OPCODES.values()}) == 130
    assert script.OPCODES[0x85].marker == 0x80
    assert {o.code for o in script.OPCODES.values() if o.flow is Flow.GOTO} == {0x04, 0x07}


def test_roles():
    op = script.OPCODES[0x1C]
    assert [op.role(s) for s in (0, 1, 2, 3, 4, None)] == [
        "open",
        "mid",
        "mid",
        "close",
        None,
        None,
    ]
    assert op.mnemonic(2) == "switch_hp.default" and op.mnemonic(0) == "switch_hp"
    assert script.OPCODES[0x80].mnemonic(0xFF) == "random.end"
    assert script.OPCODES[0x05].role(None) is None


def test_fetch():
    ins = script.fetch(reader("0e00ff74"), BASE)
    assert (ins.op.name, ins.sub, ins.args, ins.length) == (
        "if_in_section",
        0,
        {"section": 0xFF74},
        4,
    )
    assert script.fetch(reader("0603aabbcc"), BASE).args == {
        "kind": 3,
        "a": 0xAA,
        "section": 0xBBCC,
    }
    assert script.fetch(reader("0601aabbcc"), BASE).args == {"kind": 1, "a": 0xAA, "b": 0xBB}
    assert script.fetch(reader("420020"), BASE).args == {"deg": 0x20}
    assert script.fetch(reader("420080"), BASE).args == {"deg": -128}
    assert script.fetch(reader("ff00"), BASE).mnemonic == "end.base"


def test_fetch_refs():
    assert script.fetch(reader("0f0102030405"), BASE).refs == (
        Ref("stages", 2, 3),
        Ref("walk", 2, 4),
        Ref("walk", 2, 5),
    )
    assert script.fetch(reader("820304"), BASE).refs == (Ref("call", 18, 4),)
    assert script.fetch(reader("1605"), BASE).refs == (Ref("call", 9, 5),)
    assert script.fetch(reader("04"), BASE).refs == (Ref("goto", 0, 0),)


def test_if_else():
    r = script.decode(reader("3900", "05000100", "3901", "05000200", "3902", "ff00"), BASE)
    assert r.clean and names(r) == [
        "if_latch",
        "enter",
        "if_latch.else",
        "enter",
        "if_latch.end",
        "end.base",
    ]
    assert [i.depth for i in r.insns] == [0, 1, 0, 1, 0, 0]
    head = r.insns[0]
    assert head.succ == (BASE + 2, BASE + 8)  # the true body and the else's body
    assert r.insns[2].succ == (BASE + 14,)  # reaching else skips to after the end
    assert r.insns[-1].succ == ()


def test_if_nested_same_op():
    code = ("3900", "3900", "ff00", "3902", "3901", "05000100", "3902", "ff00")
    r = script.decode(reader(*code), BASE)
    assert r.clean
    assert r.insns[0].succ[1] == BASE + 2 + 2 + 2 + 2 + 2  # past the outer else, not the inner end
    assert [i.depth for i in r.insns] == [0, 1, 2, 1, 0, 1, 0, 0]
    assert script.markers(reader(*code), BASE) == [BASE + 8, BASE + 14]


def test_if_unlike_ops_nest():
    r = script.decode(reader("3900", "0b0000", "0b02", "ff00", "3902", "ff00"), BASE)
    assert r.clean and [i.depth for i in r.insns] == [0, 1, 1, 1, 0, 0]


def test_if_no_else():
    r = script.decode(reader("3900", "ff00", "3902", "ff00"), BASE)
    assert r.insns[0].succ == (BASE + 2, BASE + 6)
    assert len(r.insns) == 4


SWITCH = ("1c0002", "1c0105", "05000100", "1c0109", "05000200", "1c02", "05000300", "1c03", "ff00")


def test_switch():
    r = script.decode(reader(*SWITCH), BASE)
    assert r.clean and len(r.insns) == 9
    assert names(r)[:2] == ["switch_hp", "switch_hp.case"]
    assert [i.depth for i in r.insns] == [0, 0, 1, 0, 1, 0, 1, 0, 0]
    assert r.insns[0].args == {"n": 2}
    assert r.insns[1].args == {"case": 5}
    # the header reaches each case body and the default's; the default catches whatever
    # matches no case, so the end is reached only by falling through
    assert r.insns[0].succ == (BASE + 6, BASE + 13, BASE + 19)
    assert r.insns[2].succ == (BASE + 10,)  # a body runs into the next case's marker ...
    assert r.insns[3].succ == (BASE + 25,)  # ... which skips to the end


def test_switch_no_default():
    r = script.decode(
        reader("1c0002", "1c0105", "05000100", "1c0109", "05000200", "1c03", "ff00"), BASE
    )
    end = BASE + 3 + 3 + 4 + 3 + 4 + 2
    assert r.clean and end in r.insns[0].succ


def test_switch_section_labels():
    r = script.decode(reader("150001", "15010074", "05000100", "1503", "ff00"), BASE)
    assert r.clean and r.insns[1].args == {"section": 0x74}


def test_random():
    r = script.decode(
        reader("800002", "800110", "05000100", "800210", "05000200", "80ff", "ff00"), BASE
    )
    assert r.clean and r.insns[0].kind == "random"
    assert r.insns[0].succ == (BASE + 6, BASE + 13)  # each branch's body; no fall-through
    assert [i.depth for i in r.insns] == [0, 0, 1, 0, 1, 0, 0]
    assert r.insns[1].args == {"weight": 16}


def test_random_shifted():
    r = script.decode(
        reader("85000205", "800110", "05000100", "800210", "05000200", "80ff", "ff00"), BASE
    )
    assert r.clean and names(r)[:2] == ["random_shifted", "random.branch"]
    assert r.insns[0].args == {"n": 2, "shift": 5}


def test_range():
    r = script.decode(
        reader("830001", "8301", "05000100", "8302", "05000200", "83ff", "ff00"), BASE
    )
    assert r.clean and names(r)[1] == "switch_range.bucket"
    assert r.insns[0].succ == (BASE + 5, BASE + 11)


def test_zone():
    r = script.decode(reader("6200", "05000100", "6202", "05000200", "62ff", "ff00"), BASE)
    assert r.clean and r.insns[0].succ == (BASE + 2, BASE + 8)


def test_repeat():
    r = script.decode(reader("240003", "05000100", "2401", "ff00"), BASE)
    assert r.clean and r.insns[2].succ == (BASE + 9, BASE + 3)  # on, and back to the body
    assert [i.depth for i in r.insns] == [0, 1, 0, 0]


def test_calls_and_gotos():
    r = script.decode(reader("8103", "820102", "1604", "0f0102030405", "07", "02"), BASE)
    assert [i.succ for i in r.insns[:4]] == [(BASE + 2,), (BASE + 5,), (BASE + 7,), (BASE + 13,)]
    assert r.insns[4].succ == () and r.clean
    assert len(r.insns) == 5  # the byte after a goto is not code


def test_end_kinds():
    for kind in (0, 3, 5, 0xF5, 0xFB, 0xFF):
        assert script.decode(reader(f"ff{kind:02x}"), BASE).clean
    r = script.decode(reader("ff07"), BASE)
    assert [p.kind for p in r.problems] == ["end"] and not r.insns


def test_unknown_opcode_problem():
    r = script.decode(reader("3900", "05000100", "00", "3902", "ff00"), BASE)
    assert not r.clean and r.unknown == 1  # the scan and the path both find it: one problem
    assert r.problems[0].pc == BASE + 6
    assert names(r) == ["if_latch", "enter"]


def test_bad_sub_code():
    r = script.decode(reader("3907"), BASE)
    assert [p.kind for p in r.problems] == ["sub-code"] and r.unknown == 0


def test_unclosed_block():
    r = script.decode(reader("3900", "05000100", "ff00", "95"), BASE)
    assert [p.kind for p in r.problems] == ["structure"]
    assert names(r) == ["if_latch", "enter", "end.base"]


def test_runs_off_the_end():
    r = script.decode(reader("05000100", "0b00"), BASE)
    assert [p.kind for p in r.problems] == ["unmapped"] and len(r.insns) == 1


def test_sentinel_stops_scan():
    with pytest.raises(BadScript, match="no end"):
        script.markers(reader("3900", "95", "3902"), BASE)


def test_stats():
    code = ("3900", "05000100", "3901", "05000100", "3902", "0b0001", "ff00", "0b02", "ff00")
    s = script.Stats.of([script.decode(reader(*code), BASE)])
    assert (s.routines, s.instructions, s.unknown, s.unterminated) == (1, 9, 0, 0)
    assert s.by_kind == {"condition": 2, "action": 2, "flow": 2}  # markers are not counted
    assert s.enters == {(0, 1): 2} and s.conditions == {0x39: 1, 0x0B: 1}
    assert s.by_op == {0x05: 2, 0x0B: 1, 0x39: 1, 0xFF: 2}
    assert s.as_dict()["conditions"] == {
        "0x0B": {"name": "if_combat_mode", "count": 1},
        "0x39": {"name": "if_latch", "count": 1},
    }


def test_stats_share():
    a = script.decode(reader("ff00"), BASE)
    assert script.Stats.of([a, a]).instructions == 1


def overlay(data: bytes) -> Overlay:
    text = bytes(0x40)
    head = struct.pack("<4sIIIIIII", b"MWo3", 7, BASE, len(text), len(data), 0x100, 0, 0)
    return Overlay(head.ljust(0x40, b"\0") + text + data)


def synthetic() -> tuple[Overlay, dict[str, int]]:
    """A header whose base routine calls, calls an empty slot, walks and goes to itself."""
    routines = {
        "base": "810081050f01020100020700",
        "call": "ff01",
        "step": "10fffe",
        "arrive": "ffff",
        "int10": "fffd",
    }
    slots = {"base": 2, "call": 2, "walk": 4, "int10": 2}  # the lists, in field order
    at: dict[str, int] = {}
    lists: list[int] = []
    pos = BASE + 0x80  # the data follows the 0x40 header and the 0x40 of text
    for n in slots.values():
        lists.append(pos)
        pos += 4 * n
    at["header"] = pos
    pos += 4 * 15
    code = b""
    for name, hexed in routines.items():
        at[name], code = pos + len(code), code + bytes.fromhex(hexed)
    at["array"] = pos + len(code)
    code += bytes([1, 2, 3, 0, 0, 0, 0, 0])
    words = [
        [at["base"], 0],
        [at["call"], 0],
        [at["step"], at["array"], at["arrive"], at["array"]],
        [at["int10"], 0],
    ]
    data = b"".join(struct.pack(f"<{len(w)}I", *w) for w in words)
    fields = struct.pack("<15I", *lists, *[0] * 11)
    return overlay(data + fields + code), at


def test_header():
    ovl, at = synthetic()
    h = Header.read(ovl, at["header"])
    assert len(h.fields) == 15 and set(h.lists) == {0, 1, 2, 3}
    assert h.lists[0] == (at["base"], 0)
    assert h.interrupts == {0x10: at["int10"]}
    assert [(str(s), w) for s, w in h.entries()][:3] == [
        ("base[0]", at["base"]),
        ("call[0]", at["call"]),
        ("walk[0]", at["step"]),
    ]
    assert h.target(Ref("call", 1, 0)) == at["call"]
    assert h.target(Ref("call", 1, 1)) is None and h.target(Ref("call", 1, 9)) is None
    assert h.target(Ref("call", 40, 0)) is None


def test_header_rejects_data():
    ovl, at = synthetic()
    with pytest.raises(BadScript, match="not a script header"):
        Header.read(ovl, at["base"])


def test_disassemble():
    ovl, at = synthetic()
    lst = script.disassemble(ovl.u8, Header.read(ovl, at["header"]))
    assert set(lst.routines) == {at[k] for k in ("base", "call", "step", "arrive", "int10")}
    assert all(r.clean for r in lst.routines.values())
    assert lst.sources[at["step"]] == (Source(2, 0),)
    assert lst.sources[at["arrive"]] == (Source(2, 2),)
    assert lst.stages == (at["array"],)
    assert [str(s) for s in lst.idle] == ["walk[3]"]  # nothing names the second array's slot
    assert [(ref.field, ref.index) for _, ref in lst.unresolved] == [(1, 5)]
    assert "walk_loop" in lst.text() and "; call -> ?" in lst.text()
    assert lst.text([at["call"]]).splitlines()[0] == f"; routine 0x{at['call']:08X}  call[0]"
    assert json.dumps(lst.to_dict())


def test_overlap():
    ovl, at = synthetic()
    ovl.write(at["call"] - 2, bytes([0x10, 0x10]))  # base no longer ends: it runs into call
    lst = script.disassemble(ovl.u8, Header.read(ovl, at["header"]))
    assert [p.kind for p in lst.routines[at["base"]].problems] == ["overlap"]
    assert lst.routines[at["call"]].clean


def test_game(game):
    unresolved = 0
    for species in EM_SPECIES:
        listings = script.Scripts(game, species).listings()
        for va, listing in listings.items():
            assert listing.header.va == va
            s = listing.stats()
            assert s.routines > 15 and s.unknown == 0 and s.unterminated == 0, (species, va)
            assert set(listing.header.interrupts) <= {0x02 << k for k in range(7)}
            unresolved += len(listing.unresolved)
    assert unresolved == 1  # em82 calls a slot that is empty


def test_game_tigrex(game):
    sc = script.Scripts(game, 75)
    assert list(sc.variants()) == [sc.header_at(0)]
    lst = sc.disassemble()
    base = lst.header.lists[0][0]
    assert lst.routines[base].insns[0].mnemonic == "if_latch"
    assert lst.header.interrupts[0x08] in lst.routines
    notice = lst.routines[lst.header.interrupts[0x20]]
    assert notice.insns[-1].mnemonic == "end.notice"


def test_game_rows(game):
    sc = script.Scripts(game, 75)
    for row in (76, 81, 88):
        assert sc.stats(row).unterminated == 0
    assert sc.stats(70).unterminated == 0  # a species whose header sits in game_task


def test_game_variant(game):
    sc = script.Scripts(game, 1)
    assert len(sc.variants()) > 1
    with pytest.raises(ValueError, match="map type"):
        sc.header_at(17)
    with pytest.raises(ValueError, match="not in the table"):
        sc.header_at(0, row=100_000)
    with pytest.raises(ValueError, match="neither game_task nor em01"):
        sc.header(0, row=0)


def test_cli(game, capsys):
    data = str(game.root)
    assert main(["script", "75", "--stats", "--data", data]) == 0
    assert "0 unknown opcodes, 0 unterminated" in capsys.readouterr().out
    sc = script.Scripts(game, 75)
    entry = sc.header().lists[0][0]
    assert main(["script", "em75", "--routine", hex(entry), "--json", "--data", data]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["entry"] for r in doc["routines"]] == [entry]
    assert doc["stats"]["routines"] == 1
    assert main(["script", "75", "--routine", hex(entry), "--data", data]) == 0
    assert capsys.readouterr().out.startswith(f"; routine 0x{entry:08X}  base[0]")


def test_cli_all(game, capsys):
    assert main(["script", "--all", "--data", str(game.root)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(EM_SPECIES) and lines[0].startswith("em01  headers 7")
    assert all("unknown 0  unterminated 0" in line for line in lines)


def test_cli_needs_species(game):
    assert main(["script", "--data", str(game.root)]) == 1


def test_shapes():
    assert {op.shape for op in script.OPCODES.values()} == set(Shape)
