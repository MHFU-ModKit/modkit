# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port import manifest, moves
from mhfu_port.layout import Layout
from mhfu_port.manifest import SEAM_RULES, ManifestError
from mhfu_studio.monster import validate as V

CLIP = "\n[clips.c]\nslot = 61\nframes = 382\nloop = false\n"
MOVE = '\n[moves.m]\nmain = {}\nsub = {}\nclip = "c"\n'
NEXT = {"to": [[0, 3]], "guards": ["phase==3", "budget spent"]}
ATTACKS = {
    "present": True,
    "join": "inferred",
    "tables": [
        {
            "handle": "0x1",
            "records": "0x2",
            "volume_table": "0x3",
            "primary": True,
            "sets": [
                {"index": 0, "va": "0x4", "spheres": [{"bone": 1, "radius": 1.0}]},
                {"index": 1, "va": "0x5", "rigged": False, "spheres": [{"bone": 127, "radius": 1}]},
            ],
            "attacks": [{"id": 1, "power": 9, "volume": 0, "raw": "01"}],
        }
    ],
}


def found(findings, code=None):
    return {f.code: f for f in findings} if code is None else {f.code: f for f in findings}[code]


def test_no_evidence(make):
    out = found(V.validate(make(CLIP + MOVE.format(2, 8))))
    assert out["PAC_ABSENT"].level == "warning" and out["INTEL_ABSENT"].level == "warning"
    assert V.validate(make()) == []
    assert V.report([]) == "OK: no findings."


def test_census(make, species, make_pair):
    m = make(CLIP + MOVE.format(4, 15))
    never = species([make_pair(4, 15, measured={"entered": 0})], census=True)
    f = found(V.validate(m, intel=never), "MOVE_PAIR_NEVER_ENTERED")
    assert f.level == "error" and f.where == "moves.m" and f.target == ("moves", "m")
    allowed = make(CLIP + MOVE.format(4, 15) + "allow_unentered = true\n")
    assert found(V.validate(allowed, intel=never), "MOVE_PAIR_NEVER_ENTERED").level == "warning"
    blind = found(V.validate(m, intel=species([make_pair(4, 15)])))
    assert "MOVE_PAIR_NEVER_ENTERED" not in blind
    assert blind["INTEL_ABSENT"].level == "info", "no census is everyone's, not a problem"
    short = species([make_pair(4, 15, measured={"entered": 3, "dwell_ticks": 1.0})], census=True)
    assert "MOVE_PAIR_SHORT_DWELL" in found(V.validate(m, intel=short))
    unseen = found(V.validate(make(CLIP + MOVE.format(1, 1)), intel=short))
    assert unseen["MOVE_PAIR_NO_HANDLER"].level == "error"
    assert (
        found(V.validate(make(CLIP + MOVE.format(4, 1)), intel=short))["MOVE_PAIR_UNOBSERVED"].level
        == "warning"
    )


def test_wrong_species(make, species, make_pair):
    si = species([make_pair(2, 8)], host_species=76)
    assert set(found(V.validate(make(CLIP + MOVE.format(2, 8)), intel=si))) == {
        "INTEL_WRONG_SPECIES",
        "PAC_ABSENT",
    }


def test_chain(make, species, make_pair):
    m = make(CLIP + MOVE.format(1, 4))
    si = species([make_pair(1, 4, next=[NEXT]), make_pair(0, 3)])
    out = found(V.validate(m, intel=si))
    assert {"MOVE_PAIR_PARKS", "MOVE_BUDGET_ROOT_MOTION"} <= set(out)
    parks = species([make_pair(1, 4, next=[])])
    assert "never ends by itself" in found(V.validate(m, intel=parks), "MOVE_PAIR_PARKS").message
    chained = make(
        CLIP + MOVE.format(1, 4) + 'after = "stop"\n\n[moves.stop]\nmain = 0\nsub = 6\nanim = 3\n'
        '\n[behaviour.blocks.b1]\nkind = "played_for"\nat = [0.0, 0.0]\nframes = 30\n'
        'play = ["stop"]\n'
        '\n[behaviour.moves.m]\nat = [0.0, 0.0]\nduring = ["b1"]\n'
    )
    out = found(V.validate(chained, intel=si))
    assert "MOVE_PAIR_PARKS" not in out and "MOVE_BUDGET_ROOT_MOTION" not in out
    assert out["MOVE_AFTER_ENGINE"].level == "info"
    budget = {"gated": True, "phase0_seeds": [60], "post_hook_owns": False}
    si = species([make_pair(1, 4, ends_on="budget", budget=budget)])
    assert "[60]" in found(V.validate(m, intel=si), "MOVE_PAIR_BUDGET_GATED").message


def test_pac(make, synthetic_pac):
    m = make(
        '\n[clips.idle]\nslot = 1\nframes = 10\nloop = true\nlabel = "x"\n'
        "\n[clips.head]\nslot = 2\nframes = 7\n"
        "\n[clips.gone]\nslot = 9\n"
        "\n[[hurtbox]]\nbone = 3\nradius = 0.0\npart = 1\n"
        "\n[[hurtbox]]\nbone = 125\nradius = 0.0\npart = 1\n"
        '\n[[effect]]\nmove = "m"\nframe = 1\nid = 2\nbone = 5\n'
        "\n[moves.m]\nmain = 0\nsub = 1\nanim = 1\n"
    )
    out = V.validate(m, synthetic_pac)
    by = {(f.code, f.where) for f in out}
    assert ("CLIP_SLOT_MISSING", "clips.gone") in by
    assert ("CLIP_FRAMES_MISMATCH", "clips.head") in by and ("LABEL_UNKEYED", "clips.idle") in by
    assert ("HURTBOX_BONE_RANGE", "hurtbox[0]") in by and ("HURTBOX_RADIUS", "hurtbox[0]") in by
    assert not any(w == "hurtbox[1]" for _, w in by), "a marker is not a joint"
    assert ("EFFECT_BONE_RANGE", "effect[0]") in by
    assert V.bone_count(synthetic_pac) == 3
    with pytest.raises(ValueError):
        V.bone_count(b"\0" * 64)


def test_named_clips(make, synthetic_pac):
    """A clip named without a slot is checked in the anim the layout gives it, skipped without
    a layout, and missing where the layout leaves it out."""
    m = make("\n[clips.walk]\nsource = 205\nframes = 10\n[clips.out]\nsource = 206\n")
    by = {(f.code, f.where) for f in V.validate(m, synthetic_pac, sources={1: 205})}
    assert ("CLIP_SLOT_MISSING", "clips.out") in by and not any(w == "clips.walk" for _, w in by)
    by = {(f.code, f.where) for f in V.validate(m, synthetic_pac, sources={2: 205})}
    assert ("CLIP_FRAMES_MISMATCH", "clips.walk") in by
    assert not any(
        w.startswith("clips.") for _, w in {(f.code, f.where) for f in V.validate(m, synthetic_pac)}
    )


def test_filler(make, synthetic_pac, monkeypatch):
    monkeypatch.setattr(V.clips, "pac_clip_table", lambda pac: {1: (10, True), 5: (10, True)})
    out = found(V.validate(make("\n[clips.f]\nslot = 5\n"), synthetic_pac))
    assert out["CLIP_IS_FILLER"].level == "warning"
    assert "CLIP_IS_FILLER" not in found(V.validate(make("\n[clips.f]\nslot = 1\n"), synthetic_pac))


def test_settings():
    m = manifest.loads(
        "[port]\nname = 't'\nhost_species = 75\npac = 't.bin'\n"
        "[source]\nmodel = 5248\nem_id = 58\ngeo = 9\n"
        "[build]\nskin = 'source'\nbone_offset = 3\n"
    )
    out = found(V.validate(m))
    assert {"SOURCE_FILES_UNEXPECTED", "SKIN_SOURCE_WITHOUT_RIG", "BONE_OFFSET_OVERRIDE"} <= set(
        out
    )


def test_parts(make, species):
    m = make(
        "\n[parts.head]\nindex = 1\n\n[parts.skull]\nindex = 1\n"
        "\n[[hurtbox]]\nbone = 1\nradius = 9.0\n"
        '\n[[hurtbox]]\nbone = 1\nradius = 9.0\npart = 4\nshape = "capsule"\n'
        '\n[[hitzone]]\nstate = "a"\nrows = ' + str([[0] * 10] * 7) + "\n"
        '\n[[hitzone]]\nstate = "a"\nrows = ' + str([[1] * 10] * 7) + "\n"
    )
    parts = {
        "present": True,
        "active_set": "0x1",
        "sets": [{"va": "0x1", "kind": "hurtbox", "spheres": [{"bone": 1, "radius": 1}]}],
        "grid": {"present": True, "states": [{"va": "0x2", "rows": [[0] * 10] * 7}]},
    }
    out = found(V.validate(m, intel=species([], parts=parts)))
    for code, level in (
        ("PART_INDEX_DUPLICATE", "error"),
        ("HURTBOX_NO_PART", "warning"),
        ("HURTBOX_PART_UNNAMED", "warning"),
        ("HURTBOX_CAPSULE_NO_END", "error"),
        ("HITZONE_ALL_ZERO", "warning"),
        ("HITZONE_STATE_DUPLICATE", "error"),
        ("HITZONE_STATE_COUNT", "warning"),
        ("HURTBOX_OVER_CAPACITY", "warning"),
    ):
        assert out[code].level == level, code
    assert "HITZONE_SHARED" not in out, "said where the grid is edited, not as a finding"
    assert out["PART_INDEX_DUPLICATE"].target == ("part", 1)
    assert out["HURTBOX_PART_UNNAMED"].focus == V.PART_NAME
    over = out["HURTBOX_OVER_CAPACITY"]
    assert over.target == ("hurtbox", 1) and over.focus == V.HURTBOXES, "the first that falls off"
    assert all(f.focus or f.fix for f in out.values())
    unnamed = make(
        "\n[[hurtbox]]\nbone = 1\nradius = 9.0\n\n[[hurtbox]]\nbone = 1\nradius = 2\npart = 3\n"
    )
    f = found(V.validate(unnamed), "PARTS_UNNAMED")
    assert f.target == ("part", 3) and f.focus == V.PART_NAME
    head = found(V.validate(make("\n[[hurtbox]]\nbone = 1\nradius = 9.0\npart = 0\n")))
    assert head["PARTS_UNNAMED"].target == ("part", 0), "part 0 is the head, a part"
    assert head["PARTS_UNNAMED"].focus == V.PART_NAME
    partless = found(V.validate(make("\n[[hurtbox]]\nbone = 1\nradius = 9.0\n")))
    assert partless["PARTS_UNNAMED"].target == ("hurtbox", 0)
    assert partless["PARTS_UNNAMED"].focus == V.HURT_PART
    named = make("\n[parts.head]\nindex = 0\n\n[[hurtbox]]\nbone = 1\nradius = 9.0\npart = 0\n")
    assert not any(f.code.startswith(("PARTS_", "HURTBOX_")) for f in V.validate(named))


def test_attacks(make, species):
    m = make(
        "\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 0\n"
        '\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 0\nshape = "capsule"\n'
        "\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 1\n"
        "\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 7\n"
        "\n[[attack]]\nid = 1\n\n[[attack]]\nid = 50\nvolume = 9\n"
    )
    out = found(V.validate(m, intel=species([], attacks=ATTACKS)))
    for code in (
        "HITBOX_CAPSULE_NO_END",
        "ATTACK_EMPTY",
        "HITBOX_SET_UNKNOWN",
        "HITBOX_OVER_CAPACITY",
        "HITBOX_SET_UNRIGGED",
        "HITBOX_SET_UNUSED",
        "ATTACK_RECORD_UNKNOWN",
        "ATTACK_VOLUME_UNKNOWN",
        "ATTACK_JOIN_INFERRED",
    ):
        assert code in out, code
    assert "HITBOX_SHARED" not in out, "said where hitboxes are edited, not as a finding"
    assert out["HITBOX_OVER_CAPACITY"].target == ("set", 0)
    assert out["HITBOX_OVER_CAPACITY"].focus == V.HIT_GROUP
    assert out["ATTACK_JOIN_INFERRED"].level == out["HITBOX_SET_UNRIGGED"].level == "info"
    assert all(f.focus or f.fix for f in out.values())
    pointed = make(
        "\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 1\n\n[[attack]]\nid = 1\nvolume = 1\n"
    )
    assert "HITBOX_SET_UNUSED" not in found(V.validate(pointed, intel=species([], attacks=ATTACKS)))
    assert "HITBOX_UNCHECKED" in found(V.validate(m))
    assert not [f for f in V.validate(make()) if f.code.startswith(("HITBOX", "ATTACK"))]


@pytest.mark.parametrize("name", ["zinogre", "brute_tigrex"])
def test_ports(name, ports, built, em75):
    m = manifest.load(ports / f"{name}.toml")
    out = V.validate(m, built(name), em75)
    assert not [f for f in out if f.level == "error"], [str(f) for f in out]


OWN = (
    '\n[clips.walk]\nslot = 1\n\n[moves.o]\nclip = "walk"\n'
    "[[moves.o.attack]]\nid = 1\nframe = 2\nend = 30\n"
    "[[moves.o.attack]]\nid = 7\nframe = 10\n"
)


def test_own_moves(make, species, synthetic_pac):
    """The moves module's own refusal, one per move, and the studio's past-the-clip warning."""
    intel = species([], attacks=ATTACKS)
    out = [f for f in V.validate(make(OWN), synthetic_pac, intel) if f.code[:4] == "OWN_"]
    assert [(f.code, f.level, f.where) for f in out] == [
        ("OWN_MOVE_REFUSED", "error", "moves.o.attack[0]"),
        ("OWN_WINDOW_PAST_CLIP", "warning", "moves.o"),
    ], [str(f) for f in out]
    assert all(f.target == ("moves", "o") and f.focus == V.MOVE_WINDOWS for f in out)
    with pytest.raises(ManifestError) as refused:
        moves.check(make(OWN), Layout({1: 1}), set())
    assert out[0].message == str(refused.value).partition(": ")[2], "the module's own words"
    unchecked = V.validate(make(OWN), synthetic_pac)
    assert found(unchecked, "OWN_ATTACKS_UNCHECKED").level == "warning"
    f = found(unchecked, "OWN_MOVE_REFUSED")
    assert f.where == "moves.o.attack[1]" and "past the clip's 10" in f.message, "then the frames"


def flinch(i: int, move: str) -> str:
    at = i * 60.0
    return f'\n[behaviour.blocks.b{i}]\nkind = "on_flinch"\nat = [0.0, {at}]\nplay = ["{move}"]\n'


def test_own_rules_refused(make):
    two = "\n[moves.p]\nanim = 1\ncarrier = [0, 1]\n"
    m = make(OWN + two + flinch(1, "o") + flinch(2, "p"))
    f = found(V.validate(m, sources={1: 1}), "OWN_MOVE_REFUSED")
    assert (f.where, f.focus, f.target) == ("rule", V.BEHAVIOUR, ("behaviour", ""))
    assert "carriers" in f.message


def test_a_refused_path_is_a_finding(make):
    two = (
        '\n[behaviour.blocks.b1]\nkind = "on_noticed"\nat = [0.0, 0.0]\nnext = ["b2"]\n'
        '\n[behaviour.blocks.b2]\nkind = "cooldown"\nat = [0.0, 0.0]\nframes = 5\nnext = ["b3"]\n'
        '\n[behaviour.blocks.b3]\nkind = "cooldown"\nat = [0.0, 0.0]\nframes = 9\nplay = ["o"]\n'
    )
    f = found(V.validate(make(OWN + two), sources={1: 1}), "BEHAVIOUR_PATH_REFUSED")
    assert (f.level, f.where) == ("error", "behaviour path b1 > b2 > b3 plays o")
    assert (f.target, f.focus) == (("block", "b1"), V.BEHAVIOUR) and "two" in f.message
    assert "OWN_MOVE_REFUSED" not in found(V.validate(make(OWN + two), sources={1: 1}))


def test_more_paths_than_the_seam_holds(make):
    blocks = "".join(flinch(i, "o").replace("on_flinch", "on_noticed") for i in range(40))
    m = make(OWN + blocks)
    assert len(m.behaviour.blocks) > SEAM_RULES
    f = found(V.validate(m, sources={1: 1}), "BEHAVIOUR_OVER_CAP")
    assert f.level == "error" and f"the seam holds {SEAM_RULES}" in f.message


def test_the_zinogre_graph_passes(ports):
    m = manifest.load(ports / "zinogre.toml")
    assert not [f for f in V.validate(m) if f.code.startswith("BEHAVIOUR")]


def test_own_move_in_no_anim(make, synthetic_pac):
    m = make('\n[clips.gone]\nslot = 9\n\n[moves.o]\nclip = "gone"\n')
    f = found(V.validate(m, synthetic_pac, sources={1: 1}), "OWN_MOVE_REFUSED")
    assert f.level == "error" and f.focus == V.MOVE_CLIP and "no executor entry" in f.message
    named = make('\n[clips.gone]\nsource = 9\n\n[moves.o]\nclip = "gone"\n')
    assert "OWN_MOVE_REFUSED" not in found(V.validate(named)), "no layout: not known"
