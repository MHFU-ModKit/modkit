# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port import records
from mhp_formats.anim import channel_kind
from mhp_formats.detect import detect
from mhp_formats.p3rd.anim import Anim
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

# a hip (1) forking into a front (2..) and a rear (5..) branch
FORKED = [-1, 0, 1, 2, 3, 1, 5]


def test_record_to_bone():
    assert records.record_to_bone(4, 8, 1, {2, 4}) == {0: 1, 1: 3, 2: 5, 3: 6}
    assert records.record_to_bone(9, 5, 2, ()) == {0: 2, 1: 3, 2: 4}
    assert records.bone_to_record(3, 8, 0, {1}) == {0: 0, 2: 1, 3: 2}


def test_for_monster():
    assert records.for_monster(40, 3, 20) == {0: 0, 1: 1, 2: 2}
    assert records.for_monster(40, 12, 20)[13] == 11
    assert records.for_monster(None, 2, 20) == {2: 0, 3: 1}
    assert records.for_monster(40, 2, 20, offset=5, skip=[5]) == {6: 0, 7: 1}


def test_em_for_model():
    assert records.em_for_model(5339) == 40
    assert records.em_for_model(1) is None


def test_fork():
    assert records.body_fork(FORKED) == 1
    assert records.body_fork([-1, 0, 1]) == 0
    assert records.loc_below_fork(FORKED, [0, 1]) == []
    assert records.loc_below_fork(FORKED, [1, 2, 6, -1]) == [2, 6]


@pytest.mark.parametrize(("model", "records_", "driven"), [(5248, 43, 43), (5339, 37, 46)])
def test_donor(data, model, records_, driven):
    """Every record maps to a bone, the driven range is the donor's animated count, and every
    location record lands at or above the fork."""
    entries = Pac.from_bytes(data.p3rd.read(model)).entries
    skeleton = next(Skeleton.from_bytes(e) for e in entries if detect(e) is Skeleton)
    clips = [c for c in Anim.from_bytes(data.p3rd.read(model + 2)).streams[0] if c]
    assert {len(c.tracks) for c in clips} == {records_}
    bones = records.for_monster(records.em_for_model(model), records_, len(skeleton.bones))
    mapping = {r: b for b, r in bones.items()}
    assert len(mapping) == records_
    assert max(mapping.values()) + 1 == driven == skeleton.params[1]
    loc = {
        mapping[r]
        for c in clips
        for r, t in enumerate(c.tracks)
        if any((channel_kind(ch.bit) or ("",))[0] == "loc" for ch in t.channels)
    }
    assert loc
    assert records.loc_below_fork([b.parent for b in skeleton.bones], loc) == []
