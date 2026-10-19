# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
import struct
from collections import Counter

import pytest
from mhfu.em import script
from mhfu.em.script import asm, ir, lift, opcodes
from mhfu.em.script.asm import AssembleError
from mhfu.em.script.opcodes import BY_NAME, LISTS, OPCODES, Ref, Shape
from mhfu.files import EM_SPECIES
from mhfu.memory import Image, Space, Unmapped
from mhfu.overlay import Overlay

BASE = 0x0010_0000
DATA = BASE + 0x80
MOVED = 0x0020_0000
FAR = 0x0880_0000  # noaddr: where a relocated image is decoded
FLOAT = bytes.fromhex("0000803f")


# ---- the opcode table


def test_ref_tags():
    tagged = {
        o.name: [(t.name, t.ref) for t in opcodes.operands(o.args) if t.ref]
        for o in OPCODES.values()
    }
    assert {k: v for k, v in tagged.items() if v} == {
        "goto": [("routine", "base")],
        "walk": [("stages", "walk"), ("step", "walk"), ("arrive", "walk")],
        "call_ret": [("routine", "ret")],
        "route_arm": [("list", "stages"), ("body", "loop")],
        "call": [("routine", "call")],
        "call_deep": [("routine", "deep")],
    }
    for o in OPCODES.values():
        for t in opcodes.operands(o.args):
            assert t.ref in (None, "deep", *LISTS)
            assert t.ref != "deep" or "table" in o.args


def test_refs():
    assert BY_NAME["goto_base"].refs({}) == (Ref("goto", 0, 0),)
    assert BY_NAME["call_deep"].refs({"table": 2, "routine": 7}) == (Ref("call", 17, 7),)
    arm = BY_NAME["route_arm"].refs({"list": 3, "count": 1, "body": 4, "loop": 0})
    assert arm == (Ref("route", 5, 3), Ref("route", 6, 4))
    walk = BY_NAME["walk"].refs({"mode": 0, "limit": 1, "stages": 2, "step": 3, "arrive": 4})
    assert [r.kind for r in walk] == ["stages", "walk", "walk"]
    assert BY_NAME["if_latch"].refs({}, 1) == ()
    assert BY_NAME["call"].refs({"routine": ir.Sym("x")}) == ()


def test_set_target_layouts():
    op = BY_NAME["set_target"]
    assert op.layout(3) == "kind a section:u16" and op.layout(1) == "kind a b"
    assert op.length_of({"kind": 3}) == 5 and op.length_of({"kind": 2}) == 4
    assert op.lengths == {3: 5}


def test_flags():
    assert {o.code for o in OPCODES.values() if o.resume} == {0x05, 0x0F}
    ordered = [o for o in OPCODES.values() if o.ordered]
    assert len(ordered) == 11 and all(o.shape is Shape.SWITCH for o in ordered)
    assert not BY_NAME["switch_hp"].ordered and not BY_NAME["switch_section"].ordered
    for o in OPCODES.values():
        if o.resume:
            assert o.length_of({}) == o.size


@pytest.mark.parametrize(
    ("shape", "subs", "n"),
    [
        (Shape.SWITCH, [1, 1, 2], 2),
        (Shape.SWITCH, [], 0),
        (Shape.RANDOM, [1, 1, 1], 3),
        (Shape.BUCKET, [1, 1, 1], 2),
        (Shape.IF, [1], None),
        (Shape.ZONE, [1, 1], None),
    ],
)
def test_derived_count(shape, subs, n):
    assert opcodes.derived_count(shape, subs) == n


def test_marker_sub():
    assert [opcodes.marker_sub(Shape.RANDOM, 1, k) for k in range(3)] == [1, 2, 3]
    assert opcodes.marker_sub(Shape.SWITCH, 2, 5) == 2


# ---- trees by hand


def ins(name, **args):
    return ir.Insn(BY_NAME[name], args)


def blk(name, head=(), arms=(), **args):
    return ir.Block(BY_NAME[name], args, list(head), list(arms))


def arm(sub, *body, **args):
    return ir.Arm(sub, args, list(body))


def enter(sub):
    return ins("enter", main=0, sub=sub, gate=0)


def encode(*nodes, tail=b""):
    return asm.assemble(ir.Program(1, BASE, [ir.Routine("r", list(nodes), tail)])).data


def hexed(*parts):
    return bytes.fromhex("".join(parts))


def reader(raw):
    def read(pc):
        if not 0 <= pc - BASE < len(raw):
            raise Unmapped(f"0x{pc:08X}")
        return raw[pc - BASE]

    return read


def count(nodes):
    """Instructions the bytes of these nodes hold: a block is its opener, markers and close."""
    found = [n for n in ir.walk(nodes) if not isinstance(n, ir.Arm)]
    return sum(1 if isinstance(n, ir.Insn) else 2 + len(n.arms) for n in found)


SHAPES = {
    "if_else": (
        [blk("if_latch", [enter(1)], [arm(1, enter(2))]), ins("end", kind=0)],
        ("3900", "05000100", "3901", "05000200", "3902", "ff00"),
    ),
    "if": ([blk("if_latch", [enter(1)])], ("3900", "05000100", "3902")),
    "if_u16": ([blk("if_in_section", section=0x1234)], ("0e001234", "0e02")),
    "switch": (
        [
            blk(
                "switch_hp",
                arms=[arm(1, enter(1), case=5), arm(1, enter(2), case=9), arm(2, enter(3))],
            )
        ],
        ("1c0002", "1c0105", "05000100", "1c0109", "05000200", "1c02", "05000300", "1c03"),
    ),
    "switch_no_default": (
        [blk("switch_hp", arms=[arm(1, enter(1), case=5)])],
        ("1c0001", "1c0105", "05000100", "1c03"),
    ),
    "switch_section": (
        [blk("switch_section", arms=[arm(1, section=0x74)])],
        ("150001", "15010074", "1503"),
    ),
    "switch_extra": (
        [blk("switch_query", arms=[arm(1, case=1)], arg=7)],
        ("79000107", "790101", "7903"),
    ),
    "random": (
        [blk("random", arms=[arm(1, enter(1), weight=16), arm(1, enter(2), weight=16)])],
        ("800002", "800110", "05000100", "800210", "05000200", "80ff"),
    ),
    "random_shifted": (
        [blk("random_shifted", arms=[arm(1, enter(1), weight=16), arm(1, weight=16)], shift=5)],
        ("85000205", "800110", "05000100", "800210", "80ff"),
    ),
    "bucket": (
        [blk("switch_range", arms=[arm(1, enter(1)), arm(1, enter(2))])],
        ("830001", "8301", "05000100", "8302", "05000200", "83ff"),
    ),
    "zone": (
        [blk("switch_zone_relation", arms=[arm(1, enter(1)), arm(1, enter(2))])],
        ("6200", "6201", "05000100", "6202", "05000200", "62ff"),
    ),
    "repeat": ([blk("repeat", [enter(1)], count=3)], ("240003", "05000100", "2401")),
    "nested": (
        [blk("if_latch", [blk("if_latch", [enter(1)], [arm(1, enter(2))])], [arm(1, enter(3))])],
        ("3900", "3900", "05000100", "3901", "05000200", "3902", "3901", "05000300", "3902"),
    ),
}


@pytest.mark.parametrize("shape", SHAPES)
def test_shape(shape):
    nodes, parts = SHAPES[shape]
    assert encode(*nodes) == hexed(*parts)
    ends = isinstance(nodes[-1], ir.Insn) and nodes[-1].op.name == "end"
    closed = nodes if ends else [*nodes, ins("end", kind=0)]
    got = script.decode(reader(encode(*closed)), BASE)
    assert got.clean and len(got.insns) == count(closed)


def test_count_follows_arms():
    sw = blk("switch_hp", arms=[arm(1, case=5)])
    assert encode(sw)[2] == 1 and sw.count == 1
    sw.arms += [arm(1, case=9), arm(2)]
    assert encode(sw)[2] == 2 and sw.count == 2  # a default is no case
    rnd = blk("random", arms=[arm(1, weight=8)] * 3)
    assert encode(rnd)[:3] == b"\x80\x00\x03"
    buckets = blk("switch_range", arms=[arm(1)] * 4)
    assert encode(buckets)[2] == 3 and buckets.count == 3
    assert blk("if_latch").count is None


def test_tail():
    assert encode(ins("end", kind=0), tail=b"\x12\x34") == bytes([0xFF, 0, 0x12, 0x34])


def test_encode_errors():
    with pytest.raises(AssembleError, match="label 'x'"):
        encode(ins("call", routine=ir.Sym("x")))
    with pytest.raises(AssembleError, match="takes"):
        encode(ins("enter", main=0))
    with pytest.raises(AssembleError, match="does not fit"):
        encode(ins("enter", main=256, sub=0, gate=0))
    with pytest.raises(AssembleError, match="takes"):
        encode(blk("switch_hp", arms=[arm(1)]))  # a case needs its value
    with pytest.raises(AssembleError, match="no marker"):
        encode(blk("if_latch", arms=[arm(2)]))
    with pytest.raises(AssembleError, match="opens a block"):
        encode(ins("if_latch"))
    with pytest.raises(AssembleError, match="nothing is named nowhere"):
        asm.assemble(ir.Program(1, BASE, [ir.Header("h", ["nowhere"])]))


def test_signed_and_wide_operands():
    assert encode(blk("if_pursuit_off_facing", deg=-128)) == hexed("4200", "80", "4202")
    assert encode(ins("set_target", kind=3, a=1, section=0xBBCC)) == hexed("0603", "01", "bbcc")
    assert encode(ins("set_target", kind=2, a=1, b=2)) == hexed("0602", "0102")


def test_duplicate_names():
    end = [ins("end", kind=0)]
    p = ir.Program(1, BASE, [ir.Routine("a", end), ir.Table("a", [])])
    with pytest.raises(AssembleError, match="names two things"):
        asm.assemble(p)
    d = ir.Program(1, BASE, [ir.Data("a", b"12", {"a": 0, "b": 1}), ir.Table("b", [])])
    with pytest.raises(AssembleError, match="names two things"):
        asm.assemble(d)


def test_edit_that_outgrows():
    p = ir.Program(
        1, BASE, [ir.Routine("a", [ins("end", kind=0)], va=BASE), ir.Table("t", [], BASE + 2)]
    )
    assert asm.assemble(p).data == b"\xff\x00"
    p.items[0].body.append(ins("wait", frames=1))
    with pytest.raises(AssembleError, match="outgrew"):
        asm.assemble(p)
    p.items[0].body.pop()
    p.items.append(ir.Routine("fresh", [ins("end", kind=1)]))
    img = asm.assemble(p)
    off = img.items["fresh"][0]
    assert off % 8 == 0 and off > 0 and img.data[off : off + 2] == b"\xff\x01"


# ---- an overlay by hand


def overlay(data):
    text = bytes(0x40)
    head = struct.pack("<4sIIIIIII", b"MWo3", 7, BASE, len(text), len(data), 0x100, 0, 0)
    return Overlay(head.ljust(0x40, b"\0") + text + data)


class Synth:
    """Chunks laid out 8-aligned from the start of an overlay's data. A list holds words: an
    int, a chunk's name (`name+4` for an offset), or `bss+8` for a word in the overlay's bss."""

    def __init__(self):
        self.chunks = []

    def add(self, name, payload):
        self.chunks.append((name, payload))
        return self

    def build(self):
        at, pos = {}, 0
        for name, payload in self.chunks:
            pos = -(-pos // 8) * 8
            at[name] = DATA + pos
            pos += len(payload) if isinstance(payload, bytes) else 4 * len(payload)
        size = -(-pos // 8) * 8
        data = bytearray(size)

        def word(w):
            if isinstance(w, int):
                return w
            name, _, off = w.partition("+")
            return (DATA + size if name == "bss" else at[name]) + int(off or "0", 0)

        for name, payload in self.chunks:
            if not isinstance(payload, bytes):
                payload = b"".join(struct.pack("<I", word(w)) for w in payload)
            data[at[name] - DATA : at[name] - DATA + len(payload)] = payload
        return overlay(bytes(data)), at


def header(**fields):
    """The 15 pointer fields of a header, by slot name."""
    return [fields.get(s.name, 0) for s in opcodes.SLOTS]


class Fake(script.Scripts):
    """Scripts over a synthetic overlay: each row maps its 17 map types to headers."""

    def __init__(self, ovl, rows):
        self.species = next(iter(rows))
        self.ovl = self.task = ovl
        self.mem = Space([ovl])
        self._listings = {}
        self.table = {
            r: v if isinstance(v, dict) else dict.fromkeys(range(17), v) for r, v in rows.items()
        }
        self.__dict__["rows"] = tuple(rows)

    def header_at(self, variant, row=None):
        return self.table[self.species if row is None else row][variant]


ROUTINES = {
    # if/else, a call, a walk (stages 0, step 1, arrive 2), a goto
    "main": hexed("3900", "05000100", "3901", "8100", "3902", "0f0001000102", "0701"),
    "other": hexed("05000200", "ff00", "1234"),  # two dead bytes after the end
    "callee": hexed("ff01"),
    "step": hexed("10", "fffe"),
    "arrive": hexed("ffff"),
    "intr": hexed("fffd"),
}


def world():
    s = Synth()
    for name, code in ROUTINES.items():
        s.add(name, code)
    s.add("arr", bytes([1, 2, 3, 0, 0, 0, 0, 0]))
    s.add("arr5a", struct.pack(">4H", 1, 2, 0, 0))
    s.add("arr5b", struct.pack(">4H", 3, 0, 0, 0))
    s.add("rec", bytes(range(16)))
    s.add("base_l", ["main", "other"])
    s.add("call_l", ["callee", 0])
    s.add("walk_l", ["arr", "step", "arrive", "bss+8"])
    s.add("int10_l", ["intr", 0])
    s.add("stages_l", ["arr5a", "arr5b"])
    s.add("pick_l", ["rec", 0])
    fields = header(
        base="base_l",
        call="call_l",
        walk="walk_l",
        int_0x10="int10_l",
        stages="stages_l",
        pick="pick_l",
    )
    s.add("header", fields).add("floats", FLOAT * 4)
    ovl, at = s.build()
    return Fake(ovl, {1: at["header"]}), at


def test_lift_items():
    sc, at = world()
    p = lift.lift(sc)
    kinds = [type(i).__name__ for i in p.items]
    assert kinds == ["Routine"] * 6 + ["Data"] + ["Table"] * 6 + ["Header"]
    assert (p.base, p.end) == (at["main"], at["header"] + 60)
    assert p.sha1 == hashlib.sha1(sc.ovl.read(p.base, p.end - p.base)).hexdigest()
    names = [i.name for i in p.items if not isinstance(i, ir.Blob)]
    assert names[:6] == ["base_0", "base_1", "call_0", "walk_1", "walk_2", "int_0x10_0"]
    data = p.item("walk_0")
    assert isinstance(data, ir.Data) and data.va == at["arr"] and len(data.data) == 40
    assert data.labels == {"walk_0": 0, "stages_0": 8, "stages_1": 16, "pick_0": 24}
    walk = p.item("walk_l")
    assert isinstance(walk, ir.Table)
    assert walk.slots == ["walk_0", "walk_1", "walk_2", ir.External(sc.ovl.bss.start + 8)]
    assert p.item("call_l").slots == ["call_0", None]
    h = p.headers()[0]
    assert h.variants == tuple(range(17)) and h.va == at["header"]
    assert h.fields[:3] == ["base_l", "call_l", "walk_l"] and h.fields[5] == "stages_l"
    assert h.fields[4] is None and len(h.fields) == 15


def test_lift_tail_and_ids():
    sc, at = world()
    p = lift.lift(sc)
    main, other = p.routines()[:2]
    assert main.tail == bytes(4) and main.va == at["main"]
    assert other.tail == b"\x12\x34"
    first = main.body[0]
    assert isinstance(first, ir.Block) and first.id == f"1:{at['main']:08X}"
    assert first.close == at["main"] + 2 + 4 + 2 + 2 and first.pc == at["main"]
    assert [a.id for a in first.arms] == [f"1:{at['main'] + 6:08X}"]
    ids = [n.id for n in p.nodes()]
    assert len(ids) == len(set(ids))
    assert [n.id for n in lift.lift(sc).nodes()] == ids


def test_assemble_identity():
    sc, at = world()
    p = lift.lift(sc)
    img = asm.assemble(p)
    assert img.data == sc.ovl.read(p.base, p.end - p.base)
    assert img.headers == {at["header"]: at["header"] - p.base}
    assert img.items["h0"] == (at["header"] - p.base, 60)
    walk = img.items["walk_l"][0]
    assert walk in img.relocs and walk + 12 not in img.relocs  # the bss word is no relocation
    assert len(img.relocs) == 10 + 6  # the lists' names, the header's fields
    moved = img.at(MOVED)
    delta = MOVED - p.base
    for off in img.relocs:
        assert (
            struct.unpack_from("<I", moved, off)[0]
            == struct.unpack_from("<I", img.data, off)[0] + delta
        )
    assert moved[walk + 12 : walk + 16] == img.data[walk + 12 : walk + 16]
    again = asm.assemble(p, MOVED)
    assert again.data == moved and again.base == MOVED and again.at(p.base) == img.data


def test_image_decodes_where_it_moves():
    sc, at = world()
    img = asm.assemble(lift.lift(sc))
    mem = Image(img.at(MOVED), MOVED)
    window = range(sc.ovl.text.stop, MOVED + len(img.data))
    h = script.Header.read(
        mem, MOVED + img.headers[at["header"]], window, range(MOVED, MOVED + len(img.data))
    )
    lst = script.disassemble(Space([mem, sc.ovl]).u8, h)
    want = sc.listing(at["header"])
    assert len(lst.routines) == len(want.routines) == 6
    assert all(r.clean for r in lst.routines.values())
    assert lst.stats().instructions == want.stats().instructions
    assert h.fields[0] == MOVED + img.items["base_l"][0]
    with pytest.raises(ValueError, match="window"):
        script.Header.read(mem, MOVED)


def test_origin_and_nodes():
    sc, at = world()
    p = lift.lift(sc)
    img = asm.assemble(p)
    assert all(off == pc - p.base for pc, off in img.origin.items())
    for n in p.nodes():
        off, length = img.nodes[n.id]
        assert off == n.pc - p.base
        if isinstance(n, ir.Insn):
            assert length == n.length
    closes = {
        n.close - p.base for r in p.routines() for n in ir.walk(r.body) if isinstance(n, ir.Block)
    }
    assert closes <= set(img.origin.values())


def test_lookups():
    sc, at = world()
    p = lift.lift(sc)
    main = at["main"]
    (found,) = p.at(main + 2)
    assert isinstance(found, ir.Insn) and found.op.name == "enter"
    assert p.ending_at(main + 2 + 4) is found
    (marker,) = p.at(main + 6)
    assert isinstance(marker, ir.Arm) and marker.sub == 1
    close = p.routines()[0].body[0].close
    (block,) = p.at(close)
    assert isinstance(block, ir.Block) and block.close == close
    walk_pc = main + 6 + 2 + 2 + 2
    (walk,) = p.at(walk_pc)
    assert walk.op.name == "walk" and p.ending_at(walk_pc + 6) is walk
    assert p.ending_at(walk_pc + 5) is None and p.at(main + 1) == ()
    assert p.fresh() == "n0"
    p.routines()[0].body.append(ir.Insn(BY_NAME["wait"], {"frames": 1}, "n7"))
    assert p.fresh() == "n8"


def test_reach_and_entry():
    sc, at = world()
    p = lift.lift(sc)
    h = p.headers()[0]
    assert p.entry(h, Ref("call", 1, 0)) == "call_0"
    assert p.entry(h, Ref("call", 1, 1)) is None and p.entry(h, Ref("call", 1, 9)) is None
    assert p.entry(h, Ref("call", 40, 0)) is None and p.entry(h, Ref("walk", 2, 3)) is None
    reach = p.reach()
    assert reach["base_0"] == ("h0",) and reach["walk_2"] == ("h0",)
    assert set(reach) == {r.name for r in p.routines()}


def test_nested_random():
    code = hexed(
        "800002", "800110", "85000205", "800110", "05000100", "800210", "05000200", "80ff",
        "800210", "05000300", "80ff", "ff00",
    )  # fmt: skip
    s = Synth().add("main", code).add("base_l", ["main"]).add("header", header(base="base_l"))
    ovl, at = s.build()
    p = lift.lift(Fake(ovl, {1: at["header"]}))
    outer, end = p.routines()[0].body
    assert isinstance(outer, ir.Block) and outer.op.name == "random" and len(outer.arms) == 2
    inner = outer.arms[0].body[0]
    assert isinstance(inner, ir.Block) and inner.op.name == "random_shifted"
    assert inner.args == {"shift": 5} and len(inner.arms) == 2
    assert inner.arms[1].args == {"weight": 16} and outer.arms[1].body[0].args["sub"] == 3
    assert end.op.name == "end"
    assert asm.assemble(p).data == ovl.read(p.base, p.end - p.base)


@pytest.mark.parametrize(
    "code",
    [
        hexed("3900", "05000100", "ff00"),  # an if that is never closed
        hexed("3901", "ff00"),  # a marker of no block
        hexed("3900", "800001", "3902", "ff00"),  # markers that cross
    ],
)
def test_lift_refuses_unbalanced(code):
    s = Synth().add("main", code).add("base_l", ["main"]).add("header", header(base="base_l"))
    ovl, at = s.build()
    with pytest.raises(ValueError):
        lift.lift(Fake(ovl, {1: at["header"]}))


def test_lift_count_mismatch():
    code = hexed("800005", "800110", "05000100", "80ff", "ff00")  # n says 5, one branch
    s = Synth().add("main", code).add("base_l", ["main"]).add("header", header(base="base_l"))
    ovl, at = s.build()
    with pytest.raises(lift.LiftError, match="counts 5, not 1"):
        lift.lift(Fake(ovl, {1: at["header"]}))


def test_siblings_fence_each_other():
    s = Synth().add("r1", hexed("ff00"))
    s.add("r2", hexed("ff00")).add("l2", ["r2"]).add("h2", header(base="l2")).add("f2", FLOAT)
    s.add("l1", ["r1"]).add("h1", header(base="l1")).add("f1", FLOAT)
    ovl, at = s.build()
    sc = Fake(ovl, {1: at["h1"], 2: at["h2"]})
    one, two = lift.lift(sc, 1), lift.lift(sc, 2)
    assert [type(i).__name__ for i in one.items] == ["Routine", "Blob", "Table", "Header"]
    assert [type(i).__name__ for i in two.items] == ["Routine", "Table", "Header"]
    blob = one.items[1]
    assert isinstance(blob, ir.Blob) and blob.va == at["r2"]
    assert len(blob.data) == at["l1"] - at["r2"]  # the sibling's items, kept as they are
    assert one.routines()[0].tail == bytes(6)  # and bounding the routine before them
    for prog in (one, two):
        assert asm.assemble(prog).data == ovl.read(prog.base, prog.end - prog.base)
    assert set(lift.lift_all(sc)) == {1, 2}
    with pytest.raises(ValueError, match="does not serve"):
        lift.lift(sc, 3)


def test_floats_after_inner_header():
    s = Synth()
    s.add("ra", hexed("ff00")).add("la", ["ra"]).add("ha", header(base="la")).add(
        "floats", FLOAT * 3
    )
    s.add("rb", hexed("ff01")).add("lb", ["rb"]).add("hb", header(base="lb")).add("last", FLOAT * 4)
    ovl, at = s.build()
    rows = {1: {**dict.fromkeys(range(5), at["ha"]), **dict.fromkeys(range(5, 17), at["hb"])}}
    p = lift.lift(Fake(ovl, rows))
    blobs = [i for i in p.items if isinstance(i, ir.Blob)]
    assert [b.data for b in blobs] == [bytes(4) + FLOAT * 3 + bytes(4)]
    assert p.end == at["hb"] + 60  # the floats after the last header are not carried
    assert [h.variants for h in p.headers()] == [tuple(range(5)), tuple(range(5, 17))]
    assert [h.name for h in p.headers()] == ["h0", "h1"]
    assert p.item("h1_base_0").va == at["rb"] and p.item("h1_base_l").va == at["lb"]
    assert asm.assemble(p).data == ovl.read(p.base, p.end - p.base)


def test_shared_lists_and_externals():
    s = Synth().add("main", hexed("ff00")).add("base_l", ["main", "bss+0x10"])
    s.add("header", header(base="base_l", int_0x10="base_l", node="bss+0x20")).add("f", FLOAT)
    ovl, at = s.build()
    p = lift.lift(Fake(ovl, {1: at["header"]}))
    (table,) = [i for i in p.items if isinstance(i, ir.Table)]
    assert table.slots == ["base_0", ir.External(ovl.bss.start + 0x10)]
    h = p.headers()[0]
    assert h.fields[0] == h.fields[3] == "base_l"
    assert h.fields[12] == ir.External(ovl.bss.start + 0x20)
    img = asm.assemble(p)
    assert len(img.relocs) == 1 + 2  # the list's one name, the header's two list fields
    ext = img.items["h0"][0] + 4 * 12
    assert img.at(MOVED)[ext : ext + 4] == img.data[ext : ext + 4]


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    """Every species row of every overlay, lifted: {em: (Scripts, {row: Program})}."""
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def region(sc, p):
    return sc.ovl.read(p.base, p.end - p.base)


def test_game_rows(rows):
    progs = [(sc, p) for sc, ps in rows.values() for p in ps.values()]
    assert len(progs) == 42 and Counter(p.row for _, p in progs)[75] == 1
    assert sum(len(rows[em][1][em].headers()) for em in EM_SPECIES) == 39
    for sc, p in progs:
        img = asm.assemble(p)
        assert img.data == region(sc, p), (sc.species, p.row)
        assert hashlib.sha256(img.data).digest() == hashlib.sha256(region(sc, p)).digest()
        assert p.sha1 == hashlib.sha1(img.data).hexdigest()


def test_game_externals(rows):
    fields = words = 0
    for _, ps in rows.values():
        for p in ps.values():
            fields += sum(isinstance(e, ir.External) for h in p.headers() for e in h.fields)
            tables = [t for t in p.items if isinstance(t, ir.Table)]
            words += sum(isinstance(e, ir.External) for t in tables for e in t.slots)
    assert (fields, words) == (263, 11)


def test_game_relocs(rows):
    for sc, ps in rows.values():
        for p in ps.values():
            img = asm.assemble(p)
            inside = range(p.base, p.end)
            want = set()
            for h in p.headers():
                dh = sc.header_in(h.va)
                want |= {h.va - p.base + 4 * f for f, w in enumerate(dh.fields) if w in inside}
                for f, words in dh.pointers.items():
                    start = dh.fields[f] - p.base
                    want |= {start + 4 * i for i, w in enumerate(words) if w in inside}
            assert set(img.relocs) == want, (sc.species, p.row)
            expect = bytearray(img.data)
            for off in img.relocs:
                word = struct.unpack_from("<I", img.data, off)[0] - p.base + FAR
                struct.pack_into("<I", expect, off, word)
            assert img.at(FAR) == bytes(expect)  # every other word, an External too, is as it was
            assert img.at(p.base) == img.data


def test_game_moved_image_decodes(rows):
    headers = routines = 0
    for em in EM_SPECIES:
        sc, ps = rows[em]
        p = ps[em]
        img = asm.assemble(p)
        space = Space([Image(img.at(FAR), FAR), sc.ovl])
        window = range(FAR, sc.ovl.bss.stop)
        for h in p.headers():
            moved = script.Header.read(
                space, FAR + img.headers[h.va], window, range(FAR, FAR + len(img.data))
            )
            got, want = script.disassemble(space.u8, moved), sc.listing(h.va)
            assert len(got.routines) == len(want.routines)
            assert all(r.clean for r in got.routines.values())
            assert got.stats().instructions == want.stats().instructions
            sizes = [len(r.insns) for r in got.routines.values()]
            assert sorted(sizes) == sorted(len(r.insns) for r in want.routines.values())
            assert len(got.unresolved) == len(want.unresolved)
            headers += 1
            routines += len(got.routines)
    assert headers == 39 and routines > 1000


def test_game_origin(rows):
    for em in EM_SPECIES:
        sc, ps = rows[em]
        p = ps[em]
        img = asm.assemble(p)
        bounds = set()
        for r in p.routines():
            off, size = img.items[r.name]
            pc = p.base + off
            while pc < p.base + off + size - len(r.tail):
                length = script.length(sc.mem.u8, pc)
                bounds |= {pc, pc + length}
                pc += length
        assert set(img.origin) == bounds
        assert all(pc - p.base == off for pc, off in img.origin.items())
        for n in p.nodes():
            off, length = img.nodes[n.id]
            assert off == n.pc - p.base
            if isinstance(n, ir.Insn):
                assert length == script.length(sc.mem.u8, n.pc)


def test_game_tree(rows):
    nested = ordered = 0
    for em in EM_SPECIES:
        sc, ps = rows[em]
        p = ps[em]
        assert sum(count(r.body) for r in p.routines()) >= sc.stats().instructions
        for r in p.routines():
            for n in ir.walk(r.body):
                if isinstance(n, ir.Block) and n.op.shape in opcodes.NUMBERED:
                    assert all(a.sub == 1 for a in n.arms)
                if isinstance(n, ir.Block) and n.op.ordered:
                    cases = [next(iter(a.args.values())) for a in n.arms if a.sub == 1]
                    assert cases == sorted(cases)
                    ordered += 1
            nested += len(inner_random(r.body, False))
    assert nested == 1 and ordered > 100  # em83 puts a random_shifted in a random


def inner_random(nodes, inside):
    """The random_shifted blocks nested in a random's branch, whose markers they share."""
    out = []
    for n in nodes:
        if isinstance(n, ir.Block):
            random = n.op.marker == 0x80
            if n.op.name == "random_shifted" and inside:
                out.append(n)
            for part in [n.head, *(a.body for a in n.arms)]:
                out += inner_random(part, inside or random)
    return out


def test_game_row_split(rows):
    sc, ps = rows[1]
    assert sorted(ps) == [1, 11, 37, 41, 42, 49]
    assert len(ps[1].headers()) == 7 and len(ps[11].headers()) == 8
    assert (ps[1].base, ps[1].end) == (ps[11].base, ps[11].end)
    assert ps[1].items != ps[11].items and ps[1].sha1 == ps[11].sha1
    blobs = [i for i in ps[1].items if isinstance(i, ir.Blob)]
    assert any(len(b.data) > 3000 for b in blobs)  # row 11's private items, inside row 1's span


def test_game_reach(rows):
    sc, ps = rows[1]
    p = ps[1]
    reach = p.reach()
    assert set(reach) == {r.name for r in p.routines()} and all(reach.values())
