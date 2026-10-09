# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.files import monster_pac
from mhfu_port import constraints, mesh, motion
from mhfu_port.constraints import ConstraintError
from mhfu_port.mesh import Part, Skinned
from mhfu_port.model import ANIMATION, MODEL, SKELETON
from mhp_formats import Channel, Clip, Keyframe, Track, fu, pmo
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

ROT_X, SCL_X = 0x008, 0x200
FRAMES = 8
# a body of three joints and a head of two hanging off joint 1; a joint past the animated ones
PARENTS = [-1, 0, 1, 1, 3, 0]
STREAMS = [0, 0, 0, 1, 1, 0]
ANIMATED = 5
BIG_MONSTERS = range(6111, 6160)


def skeleton(parents=PARENTS, streams=STREAMS, animated=ANIMATED):
    bones = [Bone(parent=p, stream=s) for p, s in zip(parents, streams, strict=True)]
    return Skeleton(bones, [0, animated])


def clip(tracks):
    return Clip([motion.rest(FRAMES) for _ in range(tracks)])


def anim(body=3, head=2):
    """Parts 0 and 1 in streams 0 and 2; the odd streams a second set, as many natives have."""
    return fu.Anim([[clip(body)], [clip(body)], [clip(head)], [None]])


def model(joints=(0, 1, 3), vertices=3):
    part = Part([(0.0, 0.0, 0.0)] * vertices, [], [(0.0, 0.0)] * vertices, [], [], [], 0)
    rows = [[(joints[i % len(joints)], 1.0)] for i in range(vertices)]
    return mesh.build([Skinned(part, rows)], (1.0, 1.0, 1.0), tip=None)


def codes(**parts):
    given = {"model": model(), "skeleton": skeleton(), "anim": anim(), **parts}
    return sorted({r.code for r in constraints.validate(**given)})


def test_clean():
    assert codes() == []
    assert constraints.runs(skeleton()) == [(0, 3), (1, 2)]
    assert constraints.animated(Skeleton([Bone()] * 4)) == 4


@pytest.mark.parametrize(
    ("parts", "code"),
    [
        ({"skeleton": skeleton(parents=[-1, 0, 1, 1, 3, 5])}, "PARENT_ORDER"),
        ({"skeleton": skeleton(animated=9)}, "ANIMATED_COUNT"),
        (
            {"skeleton": skeleton(streams=[0, 1, 0, 1, 1, 0]), "anim": anim(body=2, head=3)},
            "STREAM_RUNS",
        ),
        ({"skeleton": skeleton(parents=[-1, 0, 1, 1, 0, 0])}, "STREAM_SUBTREE"),
        ({"anim": anim(head=3)}, "PARTITION"),
        ({"anim": fu.Anim([[clip(3)], []])}, "PARTITION"),
        ({"anim": fu.Anim([[clip(3), Clip([])], [], [clip(2)]])}, "NO_TRACKS"),
        ({"model": model(joints=(0, 7))}, "PALETTE_JOINT"),
    ],
)
def test_errors(parts, code):
    assert codes(**parts) == [code]


def _keyed(*channels):
    c = clip(3)
    c.tracks[1] = Track(list(channels))
    return fu.Anim([[c], [], [clip(2)]])


@pytest.mark.parametrize(
    ("channels", "code"),
    [
        ([Channel(SCL_X, [Keyframe(256, 0)])], "SCALE_CHANNEL"),
        ([Channel(ROT_X, [Keyframe(0, 0)]), Channel(ROT_X, [Keyframe(0, 0)])], "DUPLICATE_CHANNEL"),
        ([Channel(ROT_X, [Keyframe(0, -1)])], "NEGATIVE_FRAME"),
        ([Channel(ROT_X, [Keyframe(0, 4), Keyframe(0, 2)])], "FRAME_ORDER"),
        ([Channel(ROT_X, [])], "EMPTY_CHANNEL"),
    ],
)
def test_channels(channels, code):
    assert codes(anim=_keyed(*channels)) == [code]


def test_empty_group_and_template():
    assert codes(model=model(vertices=0)) == ["EMPTY_GROUP"]
    other = skeleton(parents=[-1, 0, 1, 1, 3, 1])
    assert [r.code for r in constraints.validate(skeleton=skeleton(), template=other)] == [
        "TEMPLATE"
    ]


def test_check():
    warned = constraints.check(model(), skeleton(), _keyed(Channel(ROT_X, [])))
    assert [r.level for r in warned] == ["warn"]
    with pytest.raises(ConstraintError, match="PARTITION: stream 2 carries") as e:
        constraints.check(anim=anim(head=4), skeleton=skeleton())
    assert [r.code for r in e.value.errors] == ["PARTITION"]


def test_big_monsters(data):
    """Every big monster of the game, the Tigrex included, keeps every rule."""
    for n in [*BIG_MONSTERS, monster_pac(75)]:
        e = Pac.from_bytes(data.fu.read(n)).entries
        model = pmo.Pmo.from_bytes(e[MODEL])
        skeleton = Skeleton.from_bytes(e[SKELETON])
        anim = fu.Anim.from_bytes(e[ANIMATION])
        assert constraints.validate(model, skeleton, anim) == [], n
