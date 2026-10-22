# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_studio.monster import align as AL

MOVE = """
[clips.lunge]
slot = 6
frames = 100
loop = false
impact_frame = 41

[moves.lunge]
main = 1
sub = 4
clip = "lunge"

[[effect]]
move = "lunge"
frame = 120
id = 60
bone = 33
"""
EFFECTS = [{"id": 24, "bone": 37, "frame": 56}, {"id": 79, "bone": 3, "frame": None}]


def events(entry: int, *attacks: list) -> dict:
    """An events block with one entry spawning `[id, frame]` attacks."""
    return {"present": True, "entries": [{"entry": entry, "gates": [], "attacks": list(attacks)}]}


def codes(a):
    return {f.code: f.level for f in a.findings}


def test_headline(make, species, make_pair):
    m = make(MOVE)
    a = AL.align(m, "lunge", species([make_pair(1, 4, event_frames=[40.0, 60.0])]))
    assert "checks frames 40, 60" in a.headline and "on the 40 check (+1.0)" in a.headline
    a = AL.align(m, "lunge", species([make_pair(1, 4, event_frames=[50.0])]))
    assert "9.0 frame(s) early" in a.headline and codes(a)["IMPACT_OFF_GATE"] == "warning"
    a = AL.align(m, "lunge", species([make_pair(1, 4)]))
    assert "only the clip's length" not in a.headline and "nothing has to line up" in a.headline
    assert codes(a)["NO_FIXED_FRAMES"] == "info" and codes(a)["ENDS_ON_CLIP"] == "info"


def test_timing_words():
    assert AL.timing([], 30, 100).text == "–" and AL.timing([], 30, 100).level is None
    assert AL.timing([40], None, 100).text == "check 40"
    assert AL.timing([40, 70, 90], None, 100).text == "check 40…"
    assert "checks frame 40, 70, 90." in AL.timing([40, 70, 90], None, 100).detail
    assert AL.timing([40], 41, 100).text == "on time"
    assert AL.timing([40], 47, 100).text == "7 late"
    early = AL.timing([40, 70], 32, 100)
    assert early.level == "warning" and "8 frames early for the 40 check" in early.detail
    late = AL.timing([40, 120], 41, 100)
    assert (late.text, late.level) == ("clip too short", "error")


def test_no_impact(make, species, make_pair):
    m = make(MOVE.replace("impact_frame = 41\n", ""))
    a = AL.align(m, "lunge", species([make_pair(1, 4, event_frames=[40.0])]))
    assert "has no impact frame yet" in a.headline
    a = AL.align(m, "lunge", species([make_pair(1, 4)]))
    assert "only the clip's length matters" in a.headline


def test_gates(make, species, make_pair):
    si = species([make_pair(1, 4, event_frames=[40.0, 110.0], window_frames=[-5.0, 130.0])])
    a = AL.align(make(MOVE), "lunge", si)
    gate = next(f for f in a.findings if f.code == "GATE_BEYOND_CLIP")
    assert gate.level == "error" and "frames 110, 130 but the clip ends at 100" in gate.message
    assert sorted(mk.frame for mk in a.markers if mk.unreachable) == [110.0, 120.0, 130.0]
    assert a.gates == [-5.0, 40.0, 110.0, 130.0]
    assert a.marker_map()[110.0] == "110" and a.marker_map()[41.0] == "impact"
    assert not AL.align(make(MOVE), "lunge", si, clip_frames=200).errors


def test_entry_events(make, species, make_pair):
    m, pair = make(MOVE), make_pair(1, 4)
    si = species([pair], events=events(6, [7, 120.0], [8, 120.0], [9, 40.0]))
    a = AL.align(m, "lunge", si)  # the clip is pinned at entry 6 and ends at 100
    late = next(f for f in a.findings if f.code == "GATE_BEYOND_CLIP")
    assert late.level == "error"
    assert (
        "animation events spawn attacks 7, 8 at frame 120 but the clip ends at 100" in late.message
    )
    assert "those attacks never spawn" in late.message
    info = next(f for f in a.findings if f.code == "EVENT_ATTACKS")
    assert "attacks 7, 8, 9 at frames 40, 120, for whatever clip plays there" in info.message
    gates = {mk.frame: mk for mk in a.markers if mk.kind == AL.GATE}
    assert (gates[40.0].label, gates[40.0].unreachable) == ("atk9", False)
    assert (gates[120.0].label, gates[120.0].unreachable) == ("atk7,8", True)
    assert a.gates == [40.0, 120.0]
    assert not AL.align(m, "lunge", si, clip_frames=200).errors


def test_entry_events_are_keyed_by_the_entry(make, species, make_pair):
    m, pair = make(MOVE), make_pair(1, 4, event_frames=[40.0])
    elsewhere = species([pair], events=events(5, [7, 120.0]))
    assert "EVENT_ATTACKS" not in codes(AL.align(m, "lunge", elsewhere))
    one = species([pair], events=events(6, [7, 120.0]))
    late = next(f for f in AL.align(m, "lunge", one).findings if f.code == "GATE_BEYOND_CLIP")
    assert "attack 7 at frame 120 but" in late.message
    assert "that attack never spawns" in late.message
    unpinned = AL.align_pair(m, 1, 4, one, clip_frames=100)
    assert "EVENT_ATTACKS" not in codes(unpinned)
    pinned = AL.align_pair(m, 1, 4, one, slot=6, clip_frames=100)
    assert codes(pinned)["GATE_BEYOND_CLIP"] == "error"


def test_entry_events_without_a_handler(make, species, make_pair):
    m, ev = make(MOVE), events(6, [7, 120.0])
    inline = species([make_pair(1, 4, handler=None)], events=ev)
    assert codes(AL.align(m, "lunge", inline))["GATE_BEYOND_CLIP"] == "error"
    unknown = AL.align_pair(m, 3, 3, species([], events=ev), slot=6, clip_frames=100)
    assert codes(unknown)["GATE_BEYOND_CLIP"] == "error" and "neither" in unknown.headline


def test_entry_events_the_code_does_not_frame(make, species, make_pair):
    m, pair = make(MOVE), make_pair(1, 4)
    a = AL.align(m, "lunge", species([pair], events=events(6, [7, None], [8, 20.0])))
    info = next(f for f in a.findings if f.code == "EVENT_ATTACKS")
    assert "attack 8 at frame 20; 1 behind a frame read from data" in info.message
    a = AL.align(m, "lunge", species([pair], events=events(6, [7, None])))
    info = next(f for f in a.findings if f.code == "EVENT_ATTACKS")
    assert "spawn no attack at a known frame, 1 behind a frame read from data" in info.message
    assert not a.errors and not [mk for mk in a.markers if mk.kind == AL.GATE]


def test_events_absent_from_an_old_document(make, species, make_pair):
    a = AL.align(make(MOVE), "lunge", species([make_pair(1, 4)]))
    assert codes(a)["EVENTS_ABSENT"] == "info"
    empty = species([make_pair(1, 4)], events={"present": False, "entries": []})
    assert "EVENTS_ABSENT" not in codes(AL.align(make(MOVE), "lunge", empty))


def test_ends_on(make, species, make_pair):
    m = make(MOVE)
    budget = {"gated": True, "phase0_seeds": [150], "post_hook_owns": False}
    a = AL.align(m, "lunge", species([make_pair(1, 4, ends_on="budget", budget=budget)]))
    ends = next(f for f in a.findings if f.code == "ENDS_ON_BUDGET")
    assert (
        ends.level == "warning"
        and "RE-SEEDS the budget in its phase-0 block with 150" in ends.message
    )
    a = AL.align(m, "lunge", species([make_pair(1, 4, ends_on="cursor")]))
    assert codes(a)["ENDS_ON_CURSOR"] == "warning"


def test_census(make, species, make_pair):
    m = make(MOVE)
    never = species([make_pair(1, 4, measured={"entered": 0})], census=True)
    assert codes(AL.align(m, "lunge", never))["NEVER_ENTERED"] == "error"
    allowed = make(MOVE.replace('clip = "lunge"\n', 'clip = "lunge"\nallow_unentered = true\n'))
    assert codes(AL.align(allowed, "lunge", never))["NEVER_ENTERED"] == "warning"
    absent = codes(AL.align(m, "lunge", species([make_pair(1, 4)])))
    assert absent["UNMEASURED"] == "warning" and "NEVER_ENTERED" not in absent
    used = species(
        [make_pair(1, 4, measured={"entered": 9, "dwell_ticks": 2.0, "move_per_tick": 300.0})],
        census=True,
    )
    c = codes(AL.align(m, "lunge", used))
    assert c["DWELL"] == "info" and c["SHORT_DWELL"] == "warning"


def test_effects(make, species, make_pair):
    m = make(MOVE)
    si = species([make_pair(1, 4, effects=EFFECTS)])
    rig = AL.PortRig(36, driven={37}, vertices={37: 12})
    a = AL.align(m, "lunge", si, clip_frames=50, rig=rig)
    c = codes(a)
    assert c["HOST_EFFECTS"] == "info" and c["EFFECT_BEYOND_CLIP"] == "warning"
    assert c["EFFECT_BONE_RANGE"] == "error" and c["EFFECT_BONE_UNDRIVEN"] == "warning"
    assert c["OUR_EFFECT_BEYOND_CLIP"] == "warning"
    hosts = next(f for f in a.findings if f.code == "EFFECT_BONES_ARE_THE_HOSTS")
    assert "joint 3, no geometry, NOT ANIMATED" in hosts.message
    kinds = {mk.kind for mk in a.markers}
    assert {AL.EFFECT, AL.OURS, AL.IMPACT} <= kinds
    assert codes(AL.align(m, "lunge", si, rig=AL.PortRig(30)))["OUR_EFFECT_BONE_RANGE"] == "error"


def test_without_intel(make, species):
    m = make(MOVE)
    a = AL.align(m, "lunge")
    assert codes(a) == {"INTEL_ABSENT": "warning"} and "em75" in a.headline
    a = AL.align_pair(m, 3, 3, species([]))
    assert "neither the overlay's jump tables nor the census" in a.headline


def test_align_all(make, species, make_pair):
    m = make(MOVE)
    out = AL.align_all(m, species([make_pair(1, 4, event_frames=[90.0])]), clip_frames={6: 80})
    assert [a.move for a in out] == ["lunge"] and out[0].frames == 80 and out[0].errors
    with pytest.raises(KeyError, match="no move named"):
        AL.align(m, "bite")
    assert "lunge -> (1,4)  clip lunge" in out[0].report()


def test_port_rig():
    rig = AL.PortRig(10, driven={1}, vertices={1: 40})
    assert rig.describe(1) == "joint 1, 40 vertices, driven"
    assert rig.describe(12) == "joint 12 does not exist on this rig (10 joints)"
    assert AL.PortRig(10).describe(2) == "joint 2, no geometry"


def test_em75_events(em75, make):
    m = make(MOVE)
    a = AL.align_pair(m, 3, 4, em75, slot=41, clip_frames=100)
    late = next(f for f in a.findings if f.code == "GATE_BEYOND_CLIP" and "events" in f.message)
    assert "attack 3 at frame 120 but the clip ends at 100" in late.message
    assert [mk.frame for mk in a.markers if mk.label.startswith("atk")] == [90.0, 120.0]
    assert not [f for f in AL.align_pair(m, 3, 4, em75, slot=41, clip_frames=260).errors]
    assert "EVENT_ATTACKS" not in codes(AL.align_pair(m, 3, 4, em75, slot=61, clip_frames=100))


def test_em75(em75):
    p = em75.pair(1, 4)
    assert p is not None and p.handler is not None
    timed = [q for q in em75 if q.tested_frames]
    assert len(em75) == 242 and len(timed) > 100
    assert all(e.frame is not None for e in em75.framed_effects())
