# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
import json
import re
import time
from types import SimpleNamespace

import pytest
from mhfu.cli import main
from mhfu.cli import script as cli
from mhfu.em import script
from mhfu.em.script import asm, check, edit, ir, lift, source
from mhfu.em.script.opcodes import BLOCKS, BY_NAME, CLOSE
from mhfu.em.script.parse import ParseError, parse
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000
HEAD = "row 1 at 0x00100000\n"


def ins(name, *args):
    return edit.insn(name, *args)


def enter(main, sub):
    return ins("enter", main, sub, 0)


def native(p):
    """Give a program the addresses the game has: ids, `va`, `pc`, `close`, `end` and `sha1`,
    a routine's padding up to the next item its tail."""
    for k, n in enumerate(p.nodes()):
        n.id = f"{p.row}:{k:04X}"
    img = asm.assemble(p)
    order = sorted(img.items.values())
    for r in p.routines():
        off, size = img.items[r.name]
        nxt = next((o for o, _ in order if o > off), len(img.data))
        r.tail += bytes(nxt - off - size)
    img = asm.assemble(p)
    for it in p.items:
        if isinstance(it, ir.Blob):
            it.va = BASE + img.data.index(it.data)
        else:
            it.va = BASE + img.items[it.name][0]
    for n in p.nodes():
        n.pc = BASE + img.nodes[n.id][0]
        if isinstance(n, ir.Block):
            n.close = BASE + img.closes[n.id]
    p.end = BASE + len(img.data)
    p.sha1 = hashlib.sha1(img.data).hexdigest()
    return p


def world():
    """One of each construct the text has: every block shape, both layouts of set_target, an
    s8 operand, an end kind by name, a tail of two rows, lists with an
    empty slot and an External, labelled data, a blob and a header for some map types."""
    hub = edit.if_(
        "if_latch",
        then=[
            edit.if_(
                "if_combat_mode",
                0,
                then=[enter(0, 1)],
                else_=[edit.random([(8, [enter(3, 0)]), (16, [enter(0, 5), enter(3, 27)])])],
            ),
            ins("set_var", 4, 1),
            ins("call", 0),
        ],
        else_=[enter(0, 2)],
    )
    cases = edit.switch(
        "switch_area",
        cases=[(3, [ins("call", 0)]), (11, [ins("call", 1)])],
        default=[ins("call_ret", 0)],
    )
    shapes = [
        edit.repeat(3, [ins("wait", 5)]),
        edit.switch("switch_range", cases=[([enter(0, 1)],), ([enter(0, 2)],)]),
        edit.switch("switch_zone_relation", cases=[([enter(1, 0)],), ([],)]),
        edit.random([(10, [enter(0, 7)])], shift=3),
        ins("set_target", 3, 1, 0x0123),
        ins("set_target", 1, 2, 3),
        ins("walk", 0, 1, 0, 0, 0),
        edit.if_("if_pursuit_off_facing", -5, then=[ins("end", 0xF5)]),
    ]
    p = ir.Program(
        1,
        BASE,
        [
            ir.Routine("base_0", [hub, cases, ins("end", 0)], bytes(range(1, 21))),
            ir.Routine("call_0", [*shapes, ins("end", 1)]),
            ir.Routine("call_1", [ins("end", 1)]),
            ir.Table("base_l", ["base_0", None]),
            ir.Table("call_l", ["call_0", "call_1"]),
            ir.Table("walk_l", ["d0", ir.External(0x0020_0000), "d0_b"]),
            ir.Header("h0", ["base_l", "call_l", "walk_l", *[None] * 12], (0, 1, 2, 3, 5, 6, 7)),
            ir.Data("d0", bytes(range(1, 21)), {"d0": 0, "d0_b": 5}),
            ir.Blob(bytes.fromhex("0000803f") * 3),
        ],
    )
    return native(p)


def text_of(p):
    return "\n".join(line.text for line in source.render(p))


def lines_of(p):
    return text_of(p).split("\n")


def where(lines, stripped, start=0):
    """The index of the first line at or after `start` that reads `stripped`."""
    return next(i for i in range(start, len(lines)) if lines[i].strip() == stripped)


def indent(line):
    return line[: len(line) - len(line.lstrip())]


def all_nodes(p):
    return list(p.nodes())


def new_nodes(p):
    return [n for n in all_nodes(p) if n.pc is None]


def first_enter(p):
    """The node of the first `enter 0 1 0` of the program."""
    return next(
        n
        for n in all_nodes(p)
        if isinstance(n, ir.Insn) and n.args == {"main": 0, "sub": 1, "gate": 0}
    )


def native_ids(p):
    return {(n.id, n.pc) for n in all_nodes(p) if n.pc is not None}


# ---- the text


def test_arms_are_no_opcodes():
    arms = {n for marks in BLOCKS.values() for sub, n in marks.items()} - {"end"}
    assert not arms & set(BY_NAME)
    assert all(marks[CLOSE[shape]] == "end" for shape, marks in BLOCKS.items())


def test_round_trip():
    p = world()
    assert parse(text_of(p), like=p) == p


def test_round_trip_assembles_to_the_same_bytes():
    p = world()
    assert asm.assemble(parse(text_of(p), like=p)).data == asm.assemble(p).data


def test_render_of_parse_is_the_text():
    p = world()
    q = parse(text_of(p))
    assert text_of(q).split("\n")[1:] == lines_of(p)[1:]


def test_without_like_nothing_is_native():
    p = world()
    q = parse(text_of(p))
    assert (q.row, q.base, q.end, q.sha1) == (1, BASE, 0, "")
    assert all(i.va is None for i in q.items)
    assert all(n.pc is None for n in all_nodes(q))
    assert [n.id for n in all_nodes(q)] == [f"n{k}" for k in range(len(all_nodes(q)))]
    assert all(n.close is None for n in all_nodes(q) if isinstance(n, ir.Block))


def test_the_text_is_forgiving():
    p = world()
    lines = lines_of(p)
    lines.insert(2, "   # a comment in the middle, anywhere")
    lines.insert(1, "")
    lines[where(lines, "end base")] += "   # and after a line"
    text = "\n".join(["# a header comment", *lines, "", "# the end"])
    assert parse(text, like=p) == p
    assert parse(text.replace("\n", "\r\n"), like=p) == p
    assert parse(text.replace("  ", "\t"), like=p) == p
    assert parse(text.replace("  ", "    "), like=p) == p


def test_names_and_numbers():
    text = HEAD + "routine r\n  end route\n  end 4\n  end 0xF5\n  end 245\n  enter 0 0x1 0\n"
    r = parse(text).routines()[0]
    assert [n.args["kind"] for n in r.body[:4]] == [4, 4, 0xF5, 0xF5]
    assert r.body[4].args == {"main": 0, "sub": 1, "gate": 0}
    wide = parse(HEAD + "routine r\n  if_in_section 0x1F\n    end 1\n").routines()[0]
    assert wide.body[0].args == {"section": 31}


def test_label_operand():
    p = world()
    lines = lines_of(p)
    i = where(lines, "call 0")
    lines[i] = indent(lines[i]) + "call @call_1"
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert new.args == {"routine": ir.Sym("call_1")}
    assert check.check(edit.resolve(q)) == []


# ---- identity


def test_changed_line_is_one_new_node():
    p = world()
    lines = lines_of(p)
    i = where(lines, "enter 0 1 0")
    lines[i] = indent(lines[i]) + "enter 3 0 0"
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert new.args == {"main": 3, "sub": 0, "gate": 0} and new.id == "n0"
    gone = first_enter(p)
    assert native_ids(q) == native_ids(p) - {(gone.id, gone.pc)}
    assert check.check(edit.resolve(q)) == []


def test_inserted_line_keeps_its_neighbours():
    p = world()
    lines = lines_of(p)
    i = where(lines, "enter 0 1 0")
    lines.insert(i, indent(lines[i]) + "wait 5")
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert new.op.name == "wait"
    assert native_ids(q) == native_ids(p)
    assert check.check(edit.resolve(q)) == []


def test_deleted_line_keeps_the_rest():
    p = world()
    lines = lines_of(p)
    i = where(lines, "enter 0 1 0")
    gone = first_enter(p)
    del lines[i]
    q = parse("\n".join(lines), like=p)
    assert new_nodes(q) == []
    assert native_ids(q) == native_ids(p) - {(gone.id, gone.pc)}


def test_added_arm_keeps_the_code_inside():
    p = world()
    lines = lines_of(p)
    i = where(lines, "switch_area")
    j = where(lines, "default", i)
    lines[j:j] = [indent(lines[i]) + "case 27", indent(lines[i]) + "  wait 1"]
    q = parse("\n".join(lines), like=p)
    assert [(type(n).__name__, n.id) for n in new_nodes(q)] == [("Arm", "n0"), ("Insn", "n1")]
    assert native_ids(q) == native_ids(p)
    switch = next(n for n in all_nodes(q) if isinstance(n, ir.Block) and n.op.name == "switch_area")
    assert switch.pc is not None and switch.close is not None
    assert check.check(edit.resolve(q)) == []


def test_changed_arm_operand_is_a_new_arm_with_its_body():
    p = world()
    lines = lines_of(p)
    i = where(lines, "case 11")
    lines[i] = indent(lines[i]) + "case 12"
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert isinstance(new, ir.Arm) and new.args == {"case": 12} and new.body[0].pc is not None


def test_changed_opener_is_a_new_block_with_its_insides():
    p = world()
    lines = lines_of(p)
    i = where(lines, "if_pursuit_off_facing -5")
    lines[i] = indent(lines[i]) + "if_pursuit_off_facing 5"
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert isinstance(new, ir.Block) and new.args == {"deg": 5} and new.head[0].pc is not None


def test_moved_code_is_new():
    p = world()
    lines = lines_of(p)
    i = where(lines, "set_var 4 1")
    moved = lines.pop(i)
    j = where(lines, "wait 5")
    lines.insert(j, indent(lines[j]) + moved.strip())
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert new.op.name == "set_var"


def test_new_ids_follow_the_ones_in_use():
    p = world()
    p.items[0].body[-1].id = "n7"
    q = parse(text_of(p) + "\nroutine more\n  wait 1\n  wait 2\n", like=p)
    assert [n.id for n in new_nodes(q)] == ["n8", "n9"]


def test_items_take_their_address_by_name():
    p = world()
    lines = lines_of(p)
    lines[where(lines, "routine call_1")] = "routine call_2"
    lines[where(lines, "table base_l: base_0 -")] = "table base_l: base_0 - -"
    q = parse("\n".join(lines).replace("call_l: call_0 call_1", "call_l: call_0 call_2"), like=p)
    by = {i.name: i for i in q.items if not isinstance(i, ir.Blob)}
    old = {i.name: i for i in p.items if not isinstance(i, ir.Blob)}
    assert by["call_2"].va is None and all(n.pc is None for n in ir.walk(by["call_2"].body))
    assert by["base_l"].va == old["base_l"].va and by["call_0"].va == old["call_0"].va
    assert (q.base, q.end, q.sha1) == (p.base, p.end, p.sha1)


def test_blobs_match_by_their_bytes():
    p = world()
    lines = lines_of(p)
    assert parse("\n".join(lines), like=p).items[-1].va == p.items[-1].va
    lines[-1] = lines[-1].replace("3f", "3e", 1)
    assert parse("\n".join(lines), like=p).items[-1].va is None


def test_unlisted_nodes_of_a_new_routine_are_new():
    p = world()
    q = parse(text_of(p) + "\nroutine more\n  enter 0 1 0\n", like=p)
    assert q.routines()[-1].va is None and new_nodes(q)[0].pc is None


def test_like_must_be_the_row():
    p = world()
    lines = lines_of(p)
    other = "\n".join(["row 2" + lines[0][5:], *lines[1:]])
    with pytest.raises(ParseError, match="line 1: .*row 2, not row 1"):
        parse(other, like=p)
    sha = "\n".join([lines[0][:-3] + "abc", *lines[1:]])
    with pytest.raises(ParseError, match="line 1: .*another game build"):
        parse(sha, like=p)
    with pytest.raises(ParseError, match="another game build"):
        parse("\n".join(["row 1 at 0x00100000", *lines[1:]]), like=p)
    assert parse("\n".join(lines), like=p) == p


# ---- errors

ERRORS = [
    ("routine r\n  frob 1", 3, "frob is no instruction"),
    ("routine r\n  enter 0 1", 3, r"enter takes 3 operand\(s\) \(main sub gate\), not 2"),
    ("routine r\n  nop 1", 3, "nop takes 0 operand"),
    ("routine r\n  enter 0 1 x", 3, "gate is 'x', not a number"),
    ("routine r\n  enter 0 256 0", 3, "sub 256 does not fit 1 byte"),
    ("routine r\n  if_pursuit_off_facing -129", 3, "deg -129 does not fit"),
    ("routine r\n  end nonsense", 3, "kind is 'nonsense'"),
    ("routine r\n  enter @a 0 0", 3, "main is no list index"),
    ("routine r\n  call @nowhere", 3, "nothing is named nowhere"),
    ("routine r\n  if_latch\n  case 3", 4, "if_latch takes no case"),
    ("routine r\n  else", 3, "else belongs to no block"),
    ("routine r\n  wait 1\n  else", 4, "else belongs to no block"),
    ("routine r\n  repeat 2\n  else", 4, "repeat takes no else \\(its arms: none\\)"),
    ("routine r\n  if_latch\n  else\n  else", 5, "else is out of order in if_latch"),
    ("routine r\n  switch_area\n  default\n  case 1", 5, "case is out of order"),
    ("routine r\n  switch_area\n  default\n  default", 5, "default is out of order"),
    ("routine r\n  switch_section\n  case", 4, r"case takes 1 operand\(s\) \(section\)"),
    ("routine r\n  random\n" + "  branch 1\n" * 14, 17, "random has at most 13 arms"),
    ("routine r\n  wait 1\n    wait 2", 4, "wait is an instruction: nothing goes under it"),
    ("routine r\n  : 00", 3, "unexpected `:`"),
    ("routine r\n  wait: 1", 3, "unexpected `:`"),
    ("routine r\n  end 1\n  tail 00 0", 4, "'0' is no byte"),
    ("routine r\n  end 1\n  tail 00\n  end 1", 5, "only `: 00 01` rows follow the tail"),
    ("routine r\n    wait 1\n  wait 2", 4, "indentation matches no enclosing level"),
    ("routine r\nroutine r", 3, "r is named already, on line 2"),
    ("routine r\ntable r:", 3, "r is named already"),
    ("routine 1r", 2, "no name"),
    ("routine extern", 2, "no name"),
    ("routine", 2, "a routine is `routine NAME`"),
    ("table t: x", 2, "nothing is named x"),
    ("table t: extern", 2, "extern takes an address"),
    ("table t extern", 2, "a list is `table NAME: entries`"),
    ("table t:\n  wait 1", 2, "a list is `table NAME: entries`"),
    ("data d\n  d: 00\n  d: 01", 4, "d is named already, on line 2"),
    ("data d\n  e: 00\n  d: 01", 4, "d is named already"),
    ("data d\n  00", 3, "a row of bytes"),
    ("blob\n  x: 00", 3, "a row of bytes"),
    ("blob\n  : zz", 3, "'zz' is no byte"),
    ("header h variants 0-x", 2, "no list of map types"),
    ("header h\n  base -\n  walk -", 4, "field 1 is call, not walk"),
    ("header h\n  base - -", 3, "base takes one entry"),
    ("header h\n  base nowhere", 3, "nothing is named nowhere"),
    ("header h variants 3-1", 2, "no list of map types"),
    ("wait 1", 2, "wait starts no item"),
]


@pytest.mark.parametrize(("body", "line", "what"), ERRORS)
def test_errors(body, line, what):
    with pytest.raises(ParseError, match=rf"^line {line}: .*{what}") as e:
        parse(HEAD + body)
    assert e.value.line == line and e.value.column >= 1


def test_error_columns():
    with pytest.raises(ParseError) as e:
        parse(HEAD + "routine r\n  enter 0 1 x")
    assert (e.value.line, e.value.column) == (3, 13)


@pytest.mark.parametrize(
    ("text", "line", "what"),
    [
        ("", 1, "the text is empty"),
        ("# nothing but a comment\n", 1, "the text is empty"),
        ("routine r", 1, "the first line is `row N at ADDR"),
        ("row 1", 1, "the first line is `row N at ADDR"),
        ("row 1 at 0x10 end 0x20", 1, "the first line is `row N at ADDR"),
        ("row x at 0x10", 1, "the row is 'x', not a number"),
        ("\n  row 1 at 0x10", 2, "unexpected indent"),
        ("row 1 at 0x10\n  routine r", 1, "the first line is `row N at ADDR"),
    ],
)
def test_region_errors(text, line, what):
    with pytest.raises(ParseError, match=rf"^line {line}: .*{what}"):
        parse(text)


def test_region_line_forms():
    assert parse("row 1 at 0x00100000").items == []
    p = parse("row 7 at 0x0010 end 0x0020 sha1 ab12")
    assert (p.row, p.base, p.end, p.sha1) == (7, 0x10, 0, "")
    assert parse("row 7 at 0x0010 end 0x0020 sha1").items == []


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def test_game_round_trip(rows):
    slowest = (0.0, 0, 0)
    for em, (sc, progs) in rows.items():
        for row, p in progs.items():
            text = text_of(p)
            t = time.perf_counter()
            q = parse(text, like=p)
            took = time.perf_counter() - t
            assert q == p, (em, row)
            assert asm.assemble(q).data == sc.ovl.read(p.base, p.end - p.base), (em, row)
            slowest = max(slowest, (took, em, row))
    print(f"slowest parse: {slowest[0]:.2f} s, em{slowest[1]:02d} row {slowest[2]}")
    assert slowest[0] < 10


@pytest.fixture(scope="module")
def em75(rows):
    return rows[75][1][75]


def routine_start(lines, name):
    return where(lines, f"routine {name}")


def test_game_without_like(em75):
    lines = lines_of(em75)
    q = parse("\n".join(lines))
    assert text_of(q).split("\n")[1:] == lines[1:]
    assert all(n.pc is None for n in all_nodes(q)) and all(i.va is None for i in q.items)


def test_game_changed_line(em75):
    p, lines = em75, lines_of(em75)
    i = where(lines, "enter 0 1 0", routine_start(lines, "base_0"))
    lines[i] = indent(lines[i]) + "enter 3 0 0"
    q = parse("\n".join(lines), like=p)
    (new,) = new_nodes(q)
    assert new.args == {"main": 3, "sub": 0, "gate": 0}
    assert check.check(edit.resolve(q)) == []
    assert len(native_ids(q)) == len(native_ids(p)) - 1


def test_game_inserted_and_deleted_lines(em75):
    p, lines = em75, lines_of(em75)
    i = where(lines, "enter 0 1 0", routine_start(lines, "base_0"))
    more = [*lines[:i], indent(lines[i]) + "wait 5", *lines[i:]]
    q = parse("\n".join(more), like=p)
    assert [n.op.name for n in new_nodes(q)] == ["wait"] and native_ids(q) == native_ids(p)
    assert check.check(edit.resolve(q)) == []
    fewer = parse("\n".join([*lines[:i], *lines[i + 1 :]]), like=p)
    assert new_nodes(fewer) == [] and len(native_ids(fewer)) == len(native_ids(p)) - 1


def test_game_added_case(em75):
    p, lines = em75, lines_of(em75)
    i = where(lines, "switch_area", routine_start(lines, "base_1"))
    lines[i + 1 : i + 1] = [indent(lines[i]) + "case 2", indent(lines[i]) + "  wait 1"]
    q = parse("\n".join(lines), like=p)
    assert [type(n).__name__ for n in new_nodes(q)] == ["Arm", "Insn"]
    assert native_ids(q) == native_ids(p)
    assert check.check(edit.resolve(q)) == []


def test_game_misordered_case_is_found(em75):
    p, lines = em75, lines_of(em75)
    i = where(lines, "switch_area", routine_start(lines, "base_1"))
    lines[i + 1 : i + 1] = [indent(lines[i]) + "case 4", indent(lines[i]) + "  wait 1"]
    found = check.check(edit.resolve(parse("\n".join(lines), like=p)))
    assert [f.code for f in found] == ["ORDERED"]


# ---- the command


@pytest.fixture
def synthetic(monkeypatch):
    p = world()
    monkeypatch.setattr(cli, "Extracted", SimpleNamespace(find=lambda root: None))
    monkeypatch.setattr(script, "Scripts", lambda game, em: None)
    monkeypatch.setattr(lift, "lift", lambda sc, row=None: p)
    return p


def write(tmp_path, lines, name="edited.txt"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_cli_check_clean(synthetic, tmp_path, capsys):
    f = write(tmp_path, lines_of(synthetic))
    assert main(["script", "75", "--check", f]) == 0
    assert capsys.readouterr().out == ""


def test_cli_check_lists_findings(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    i = where(lines, "random_shifted 3")
    lines[i + 1] = lines[i + 1].replace("branch 10", "branch 11")
    assert main(["script", "75", "--check", write(tmp_path, lines)]) == 1
    (out,) = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"error RANDOM_WEIGHTS \S+: random_shifted weights add up to 33.*", out)


def test_cli_check_index_past_list(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    i = where(lines, "call 0")
    lines[i] = indent(lines[i]) + "call 2"
    assert main(["script", "75", "--check", write(tmp_path, lines)]) == 1
    assert "error INDEX_PAST_LIST n0: call call[2] under h0" in capsys.readouterr().out


def test_cli_check_warning_passes(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    i = where(lines, "end return", where(lines, "routine call_0"))
    lines[i] = indent(lines[i]) + "goto 1"
    assert main(["script", "75", "--check", write(tmp_path, lines)]) == 0
    assert capsys.readouterr().out.startswith("warning EMPTY_SLOT n0: goto base[1] under h0")


def test_cli_parse_error(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    lines[where(lines, "enter 0 2 0")] += " 1"
    f = write(tmp_path, lines)
    assert main(["script", "75", "--check", f]) == 1
    err = capsys.readouterr().err
    assert f in err and "enter takes 3 operand(s)" in err and "line " in err


def test_cli_build(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    out, tab = tmp_path / "out.bin", tmp_path / "out.json"
    f = write(tmp_path, lines)
    assert main(["script", "75", "--build", f, "-o", str(out), "--map", str(tab)]) == 0
    img = asm.assemble(synthetic)
    assert out.read_bytes() == img.data and "bytes" in capsys.readouterr().out
    m = json.loads(tab.read_text())
    assert m["base"] == BASE and m["size"] == len(img.data) and m["relocs"] == img.relocs
    assert m["headers"] == {f"0x{va:08X}": at for va, at in img.headers.items()}
    assert m["origin"] == {f"0x{pc:08X}": at for pc, at in img.origin.items()}
    assert m["items"]["call_0"] == list(img.items["call_0"]) and m["closes"] == img.closes


def test_cli_build_edited_at_a_base(synthetic, tmp_path):
    lines = lines_of(synthetic)
    i = where(lines, "enter 0 2 0")
    lines.insert(i, indent(lines[i]) + "wait 9")
    out = tmp_path / "out.bin"
    argv = ["script", "75", "--build", write(tmp_path, lines), "-o", str(out)]
    assert main([*argv, "--base", "0x00300000"]) == 0
    got = out.read_bytes()
    assert got != asm.assemble(synthetic).data
    q = edit.resolve(parse("\n".join(lines), like=synthetic))
    assert got == asm.assemble(q, 0x0030_0000).data


def test_cli_build_refuses_an_error(synthetic, tmp_path, capsys):
    lines = lines_of(synthetic)
    i = where(lines, "random_shifted 3")
    lines[i + 1] = lines[i + 1].replace("branch 10", "branch 11")
    out = tmp_path / "out.bin"
    assert main(["script", "75", "--build", write(tmp_path, lines), "-o", str(out)]) == 1
    assert not out.exists() and "error RANDOM_WEIGHTS" in capsys.readouterr().out


def test_cli_build_arguments(synthetic, tmp_path, capsys):
    f = write(tmp_path, lines_of(synthetic))
    assert main(["script", "75", "--build", f]) == 1
    assert "--build needs -o OUT" in capsys.readouterr().err
    assert main(["script", "75", "--build", f, "--check", f, "-o", str(tmp_path / "x")]) == 1
    assert "exclusive" in capsys.readouterr().err
    assert main(["script", "75", "--check", str(tmp_path / "missing.txt")]) == 1


def test_cli_game(game, tmp_path, capsys):
    data = str(game.root)
    src = tmp_path / "em75.txt"
    assert main(["script", "75", "--source", "-o", str(src), "--data", data]) == 0
    assert main(["script", "75", "--check", str(src), "--data", data]) == 0
    assert capsys.readouterr().out == ""
    out, tab = tmp_path / "em75.bin", tmp_path / "em75.json"
    argv = ["script", "75", "--build", str(src), "-o", str(out), "--map", str(tab), "--data", data]
    assert main(argv) == 0
    sc = script.Scripts(game, 75)
    p = lift.lift(sc)
    assert out.read_bytes() == sc.ovl.read(p.base, p.end - p.base)
    assert json.loads(tab.read_text())["headers"][f"0x{p.headers()[0].va:08X}"] >= 0
    lines = src.read_text().split("\n")
    i = where(lines, "enter 0 1 0", routine_start(lines, "base_0"))
    lines[i] = indent(lines[i]) + "enter 3 0 0"
    edited = write(tmp_path, lines)
    assert main(["script", "75", "--build", edited, "-o", str(out), "--data", data]) == 0
    got, was = out.read_bytes(), sc.ovl.read(p.base, p.end - p.base)
    assert len(got) == len(was) and [k for k in range(len(was)) if got[k] != was[k]] != []
    assert main(["script", "75", "--check", edited, "--row", "76", "--data", data]) == 1
