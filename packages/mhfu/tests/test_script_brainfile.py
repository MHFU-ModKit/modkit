# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The brain file: edits per species row as TOML, code as source text."""

import re
import tomllib
from pathlib import Path

import pytest
from mhfu.em import script
from mhfu.em.script import brainfile, build, edit, editset, ir, parse
from mhfu.em.script.brainfile import BrainFileError
from mhfu.em.script.editset import Anchor, EditSet, Insert, ListEdit
from mhfu.files import EM_SPECIES

REPO = Path(__file__).parents[3]
SHA = "ab" * 20


def enter(main, sub):
    return edit.insn("enter", main, sub, 0)


def sample():
    """An edit set with every kind of edit, code built by hand and as text."""
    es = EditSet(75, SHA, also=[76, 88])
    es.node("75:00000001").args["weight"] = 10
    es.node("75:00000002").delete = True
    es.node("75:00000003").bypass = "then"
    es.node("75:00000004").move = Anchor("after", "75:00000005")
    es.node("75:00000006").arms = ["75:00000008", "75:00000007"]
    es.inserts["u2"] = Insert(
        Anchor("before", "75:00000009"),
        [edit.if_("if_enraged", then=[enter(3, 0), edit.insn("call", ir.Sym("spin"))])],
    )
    es.inserts["u10"] = Insert(
        Anchor("into", "75:0000000A", 0),
        parse.nodes("if_in_pair 0 1\n  enter 3 0 0\nelse\n  call 2\nrepeat 2\n  enter 0 1 0\n"),
    )
    es.inserts["u3"] = Insert(Anchor("after", "u2.0"), [], ir.Arm(1, {"weight": 4}, [enter(0, 2)]))
    es.routines["spin"] = [
        edit.random([(16, [enter(3, 3)]), (8, [enter(3, 27)])]),
        edit.switch("switch_hp", cases=[(1, [enter(2, 0)])], default=[enter(2, 1)]),
        edit.insn("end", 1),
    ]
    es.lists["h0.call"] = ListEdit({1: "spin", 3: None}, ["spin"], fork=True)
    es.lists["h0.deep0"] = ListEdit({0: ir.External(0x1234)})
    es.names["base_0"] = "Top"
    es.notes["75:00000001"] = "more of the bite"
    return es


def filled():
    bf = brainfile.new(75, "Tigrex")
    bf.pairs.update({(3, 27): "tail spin", (0, 2): "alert hub"})
    bf.put(sample())
    return bf


# ---- the format


def test_a_new_file_is_a_header():
    bf = brainfile.new(75, "Tigrex")
    bf.pairs[(1, 4)] = "charge"
    assert brainfile.dumps(bf) == (
        'schema = 1\noverlay = 75\nlabel = "Tigrex"\n\n[pairs]\n"1,4" = "charge"\n'
    )
    assert brainfile.dumps(brainfile.new(7)) == "schema = 1\noverlay = 7\n"


def test_names_of_variables_and_ranges():
    bf = brainfile.new(75)
    bf.vars.update({4: "in_hunt_area", 2: ""})
    bf.ranges[5] = "out of reach"
    text = brainfile.dumps(bf)
    assert text.endswith('[vars]\n4 = "in_hunt_area"\n\n[ranges]\n5 = "out of reach"\n')
    back = brainfile.loads(text)
    assert (back.vars, back.ranges) == ({4: "in_hunt_area"}, {5: "out of reach"})
    assert brainfile.dumps(back.copy()) == text


def test_round_trip_in_normal_form():
    bf = filled()
    text = brainfile.dumps(bf)
    back = brainfile.loads(text)
    assert brainfile.dumps(back) == text
    assert (back.overlay, back.label, back.pairs) == (75, "Tigrex", bf.pairs)
    assert editset.equal(back.rows[75], bf.rows[75])


def test_normal_form_is_sorted():
    text = brainfile.dumps(filled())
    sections = re.findall(r"^\[(.*)\]$", text, re.M)
    assert sections[:2] == ["pairs", "row.75"]
    assert [s for s in sections if re.fullmatch(r"row\.75\.insert\.u\d+", s)] == [
        "row.75.insert.u2",
        "row.75.insert.u3",
        "row.75.insert.u10",
    ]
    nodes = [s for s in sections if ".node." in s and not s.endswith("move")]
    assert len(nodes) == 4 and nodes == sorted(nodes)
    assert tomllib.loads(text)["row"]["75"]["also"] == [76, 88]


def test_code_is_text():
    text = brainfile.dumps(filled())
    assert 'code = """\nif_enraged\n  enter 3 0 0\n  call @spin\n"""' in text
    assert "[row.75.routine]" in text and 'spin = """\nrandom\n' in text


def test_the_repo_file_is_in_normal_form():
    path = REPO / "brains" / "em75.toml"
    bf = brainfile.load(path)
    assert (bf.overlay, bf.label, bf.rows) == (75, "Tigrex", {})
    assert bf.pairs[(0, 2)] == "alert hub"
    assert brainfile.dumps(bf) == path.read_text()


def test_every_brain_file_in_the_repo_is_named_for_its_overlay():
    for path in sorted((REPO / "brains").glob("em*.toml")):
        bf = brainfile.load(path)
        assert path.name == brainfile.filename(bf.overlay)
        assert brainfile.dumps(bf) == path.read_text()
    assert brainfile.filename(1) == "em01.toml" and brainfile.filename(75) == "em75.toml"


def test_only_what_differs_is_written():
    bf = brainfile.new(75)
    bf.put(EditSet(75, SHA))
    bf.put(EditSet(81, SHA, also=[]))
    assert brainfile.dumps(bf) == "schema = 1\noverlay = 75\n"
    only_notes = EditSet(75, SHA)
    only_notes.notes["75:00000001"] = "keep"
    bf.put(only_notes)
    assert "[row.75.note]" in brainfile.dumps(bf)
    pruned = EditSet(75, SHA)
    pruned.node("75:00000001")  # an edit with nothing in it
    bf.put(pruned)
    assert "row" not in tomllib.loads(brainfile.dumps(bf))


def test_the_build_is_written_in_lower_case_and_also_sorted():
    bf = brainfile.new(75)
    es = sample()
    es.build, es.also = SHA.upper(), [88, 76, 88]
    bf.put(es)
    d = tomllib.loads(brainfile.dumps(bf))["row"]["75"]
    assert d["build"] == SHA and d["also"] == [76, 88]


def test_edits_are_found_by_row_and_alias():
    bf = filled()
    other = EditSet(81, SHA)
    other.names["x"] = "y"
    bf.put(other)
    assert bf.edits(75) is bf.rows[75]
    assert bf.edits(76) is bf.rows[75] and bf.edits(88) is bf.rows[75]
    assert bf.edits(81) is other
    assert bf.edits(82) is None


def test_copy_is_deep():
    bf = filled()
    twin = bf.copy()
    twin.rows[75].inserts.clear()
    twin.pairs.clear()
    assert bf.rows[75].inserts and bf.pairs


# ---- snippets


def snippet_cases():
    pick = edit.random([(16, [enter(3, 3)]), (8, [enter(3, 27)])], shift=None)
    return [
        [enter(3, 0)],
        [edit.if_("if_enraged", then=[enter(3, 0)], else_=[enter(0, 1)])],
        [edit.if_("if_in_section", 0x0102, then=[enter(1, 1)])],
        [pick],
        [edit.switch("switch_hp", cases=[(1, [enter(2, 0)]), (4, [enter(2, 1)])], default=[])],
        [edit.repeat(3, [enter(0, 1), edit.insn("call", ir.Sym("spin"))]), edit.insn("end", 1)],
        [],
    ]


@pytest.mark.parametrize("nodes", snippet_cases())
def test_a_snippet_reads_back_as_built(nodes):
    assert parse.nodes(brainfile.code(nodes)) == nodes


def test_snippet_text_has_no_comments_or_ids():
    text = brainfile.code(snippet_cases()[1])
    assert text == "if_enraged\n  enter 3 0 0\nelse\n  enter 0 1 0\n"
    assert all(n.id == "" and n.pc is None for n in ir.walk(parse.nodes(text)))


def test_snippets_are_numbered_past_a_program():
    p = ir.Program(1, 0, [ir.Routine("r", [edit.insn("end", 1)])])
    p.routines()[0].body[0].id = "n7"
    got = parse.nodes("if_enraged\n  enter 3 0 0\n", like=p)
    assert [n.id for n in ir.walk(got)] == ["n8", "n9"]


def test_a_snippet_error_names_its_line():
    with pytest.raises(parse.ParseError, match="line 2: enter takes 3 operand"):
        parse.nodes("if_enraged\n  enter 3\n")
    with pytest.raises(parse.ParseError, match="belongs to no block"):
        parse.nodes("else\n")


# ---- errors


def loads_error(text):
    with pytest.raises(BrainFileError) as e:
        brainfile.loads(text)
    return str(e.value)


def test_a_file_that_is_wrong():
    assert "not TOML" in loads_error("schema = ")
    assert "schema is 2" in loads_error("schema = 2\noverlay = 75\n")
    assert "schema is None" in loads_error("overlay = 75\n")
    assert "no colour" in loads_error("schema = 1\noverlay = 75\ncolour = 1\n")
    assert "overlay is no number" in loads_error('schema = 1\noverlay = "75"\n')
    assert "not one of the em overlays" in loads_error("schema = 1\noverlay = 76\n")
    assert "label is no text" in loads_error("schema = 1\noverlay = 75\nlabel = 1\n")
    assert 'no pair: "main,sub"' in loads_error('schema = 1\noverlay = 75\n[pairs]\n"1" = "x"\n')
    assert "no text" in loads_error('schema = 1\noverlay = 75\n[pairs]\n"1,2" = 3\n')
    assert "row is no table" in loads_error("schema = 1\noverlay = 75\nrow = 1\n")
    assert "vars: 'x' is no number" in loads_error('schema = 1\noverlay = 75\n[vars]\nx = "a"\n')
    assert "vars: 10 is not 1 to 9" in loads_error('schema = 1\noverlay = 75\n[vars]\n10 = "a"\n')
    assert "ranges: 0 is not 1 to 5" in loads_error('schema = 1\noverlay = 75\n[ranges]\n0 = "a"\n')
    assert "no text" in loads_error("schema = 1\noverlay = 75\n[ranges]\n1 = 2\n")


def test_rows_that_cannot_be_read():
    head = "schema = 1\noverlay = 75\n"
    assert "no species row number" in loads_error(head + '[row.x]\nbuild = "a"\n')
    assert "not a species row" in loads_error(head + '[row.0]\nbuild = "a"\n')
    assert "row 75: the edit set has no colour" in loads_error(head + "[row.75]\ncolour = 1\n")
    assert "row 75: " in loads_error(head + "[row.75.node.x]\nargs = 1\n")
    assert "lists itself" in loads_error(head + '[row.75]\nbuild = "a"\nalso = [75]\n')
    two = head + '[row.75]\nbuild = "a"\nalso = [76]\n[row.76]\nbuild = "a"\n'
    assert "row 76 is in the sections of row 75 and 76" in loads_error(two)
    three = '[row.75]\nbuild = "a"\nalso = [88]\n[row.81]\nbuild = "a"\nalso = [88]\n'
    assert "row 88 is in the sections of row 75 and 81" in loads_error(head + three)


def test_code_that_does_not_parse_is_found():
    text = (
        'schema = 1\noverlay = 75\n[row.75]\nbuild = "a"\n'
        '[row.75.insert.u1]\nafter = "75:00000001"\ncode = "enter 3 0 0\\n"\n'
        '[row.75.insert.u2]\nafter = "75:00000001"\ncode = "enter 3\\n"\n'
    )
    assert "row 75 insert u2: line 1: enter takes 3 operand(s)" in loads_error(text)
    head = 'schema = 1\noverlay = 75\n[row.75]\nbuild = "a"\n'
    text = head + '[row.75.routine]\nspin = "frob\\n"\n'
    assert "row 75 routine spin: line 1: frob is no instruction" in loads_error(text)


def test_the_bad_code_is_found_past_other_bad_data():
    text = (
        'schema = 1\noverlay = 75\n[row.75]\nbuild = "a"\n'
        '[row.75.insert.u1]\nafter = "75:00000001"\ncode = "enter 3\\n"\n'
        "[row.75.insert]\nu2 = 1\n"
    )
    assert "row 75 insert u1: line 1: enter takes 3 operand(s)" in loads_error(text)


def test_a_file_is_refused_with_its_path(tmp_path):
    path = tmp_path / "em75.toml"
    path.write_text("schema = 7\n")
    with pytest.raises(BrainFileError, match=re.escape(str(path)) + ": schema is 7"):
        brainfile.load(path)


def test_a_file_that_cannot_be_written():
    bf = brainfile.new(75)
    bf.overlay = 76
    with pytest.raises(BrainFileError, match="not one of the em overlays"):
        brainfile.dumps(bf)
    bf = brainfile.new(75)
    bf.rows[75] = EditSet(81, SHA)
    with pytest.raises(BrainFileError, match="under row 75 are row 81's"):
        brainfile.dumps(bf)
    with pytest.raises(BrainFileError):
        brainfile.new(3)


# ---- on disk


def test_save_and_load(tmp_path):
    path = tmp_path / "brains" / "em75.toml"
    bf = filled()
    assert brainfile.save(bf, path) == path
    assert path.read_text() == brainfile.dumps(bf)
    assert editset.equal(brainfile.load(path).rows[75], bf.rows[75])
    bf.label = "again"
    brainfile.save(bf, path)
    assert 'label = "again"' in path.read_text()
    assert [p.name for p in path.parent.iterdir()] == ["em75.toml"]


def test_save_replaces_through_a_temp_file(tmp_path):
    path = tmp_path / "em75.toml"
    path.write_text("old")
    brainfile.save(brainfile.new(75), path)
    assert path.read_text() == "schema = 1\noverlay = 75\n"
    assert [p.name for p in tmp_path.iterdir()] == ["em75.toml"]


# ---- with the game


@pytest.fixture(scope="module")
def lifts(game):
    """Every species row of every overlay as lifted, by overlay."""
    return {em: script.lift.lift_all(script.Scripts(game, em)) for em in EM_SPECIES}


def test_twins_are_rows_that_lift_the_same(lifts):
    assert brainfile.twins(lifts[75]) == [[76, 88]]
    assert brainfile.twins(lifts[1]) == [[1, 37, 42], [11, 41, 49]]
    assert brainfile.twins(lifts[33]) == []
    assert [76, 88] in brainfile.twins(lifts[75])
    assert sorted(r for g in brainfile.twins(lifts[14]) for r in g) == [14, 26, 43, 44]


def test_snippet_ids_survive_a_save_and_load(game, lifts, tmp_path):
    lf = lifts[75][75]
    es = EditSet.of(lf)
    first = lf.routines()[0].body[0]
    es.inserts["u1"] = Insert(
        Anchor("before", first.id), parse.nodes("if_enraged\n  enter 3 0 0\n")
    )
    es.inserts["u2"] = Insert(Anchor("after", "u1.0"), parse.nodes("enter 0 1 0\n"))
    bf = brainfile.new(75)
    bf.put(es)
    before = build.compile(bf, game, 75, lift=lf)
    brainfile.save(bf, tmp_path / "em75.toml")
    after = build.compile(brainfile.load(tmp_path / "em75.toml"), game, 75, lift=lf)

    def ids(built):
        return [n.id for r in built.program.routines() for n in ir.walk(r.body)]

    assert before.ok and after.ok
    assert ids(before) == ids(after)
    assert {"u1.0", "u1.1", "u2.0"} <= set(ids(after))
    assert before.container == after.container and before.id == after.id
