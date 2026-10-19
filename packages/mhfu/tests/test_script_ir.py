# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em.script import opcodes
from mhfu.em.script.opcodes import BY_NAME, LISTS, OPCODES, Ref, Shape


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
