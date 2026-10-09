# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port import layout, manifest, slots
from mhfu_port.manifest import ManifestError
from mhfu_studio.monster import clips as C
from mhfu_studio.monster.inputs import donor_clips, host_anim
from mhp_formats import fu

LABELS = """
[clips.charge]
slot = 61
frames = 382
loop = false
label = "charge"
labelled_build = "b.bin@1"

[moves.charge]
main = 2
sub = 8
clip = "charge"
"""


def pack(**slots):
    """`slotN=clip` keyword arguments -> one stream."""
    n = max(int(k[4:]) for k in slots) + 1
    stream = [slots.get(f"slot{i}") for i in range(n)]
    return fu.Anim([stream])


@pytest.fixture
def packs(make_clip):
    idle, own, host4, d5, p5, extra = (
        make_clip(f, lp) for f, lp in ((100, 1), (50, 0), (70, 0), (30, 0), (31, 0), (90, 0))
    )
    donor = {1: idle, 2: own, 5: d5, 6: make_clip(100, 1), 7: extra}
    port = pack(slot1=idle, slot2=own, slot3=idle, slot4=host4, slot5=p5, slot6=idle)
    host = pack(
        slot1=make_clip(20),
        slot2=make_clip(20),
        slot3=make_clip(20),
        slot4=host4,
        slot5=make_clip(20),
        slot6=make_clip(20),
    )
    return port, host, donor


def test_coverage(packs):
    port, host, donor = packs
    cov = C.coverage(port, host, donor, layout.Layout({1: 1, 2: 2, 5: 5, 6: 6}))
    kinds = {s: c.kind for s, c in cov.slots.items()}
    assert kinds == {1: C.CARRIED, 2: C.CARRIED, 3: C.FILLER, 4: C.HOST, 5: C.ALTERED, 6: C.CARRIED}
    assert cov.dropped == {7: (90, False)} and cov.slots[2].scriptable
    assert cov.counts()[C.DROPPED] == 1 and "1 donor clip(s) DROPPED" in cov.summary()
    assert "idle copy" in cov.slots[3].why().lower()
    assert cov.sources() == {1: 1, 2: 2, 5: 5, 6: 6}
    moved = C.coverage(port, host, donor, layout.Layout({2: 6, 6: 2}))
    assert (moved.kind(2), moved.kind(6), moved.slots[2].clip) == (C.ALTERED, C.FILLER, 6)


def test_coverage_without_evidence(packs):
    port, _, donor = packs
    cov = C.coverage(port)
    assert {c.kind for c in cov.slots.values()} == {C.UNKNOWN} and "all UNKNOWN" in cov.summary()
    no_host = C.coverage(port, donor=donor)
    assert no_host.kind(4) == C.ALTERED and not no_host.dropped
    assert "no host pack" in no_host.summary()


def test_tables(packs):
    port, _, _ = packs
    assert C.clip_table(port)[1] == (100, True) and C.clip_table(port)[2] == (50, False)
    assert C.build_id("z.bin", b"x") == "z.bin@11f6ad8e"


def test_tracks(make):
    m = make(LABELS + "\n[clips.walk]\nslot = 5\nframes = 40\n\n[clips.raw]\nslot = 9\n")
    table = {61: (382, False), 5: (12, False), 6: (40, True), 9: (1, False)}
    t = {x.name: x for x in C.track_labels(m, table, "b.bin@1")}
    assert t["charge"].status == C.CURRENT and t["charge"].trusted
    assert t["walk"].status == C.MOVED and t["walk"].now_at == 6
    assert t["raw"].status == C.UNCHECKABLE
    assert C.track_labels(m, table, "other")[0].status == C.STILL_VALID
    table[7] = (40, False)
    walk = next(x for x in C.track_labels(m, table) if x.name == "walk")
    assert walk.status == C.AMBIGUOUS and walk.candidates == (6, 7)
    lost = next(x for x in C.track_labels(m, {61: (1, False)}) if x.name == "charge")
    assert lost.status == C.LOST and not lost.trusted
    assert C.unlabelled_slots(m, table) == [6, 7]


def test_label(doc):
    d = doc(LABELS)
    s = C.LabelSession(d, {61: (382, False), 5: (120, True)}, "new.bin@2", {61: 61, 5: 205})
    assert s.default_name(5) == "clip_05" and s.default_name(61) == "charge"
    s.label(5, "walk", "slow walk", impact_frame=40)
    c = d.manifest.clips["walk"]
    assert (c.frames, c.loop, c.labelled_build, c.impact_frame) == (120, True, "new.bin@2", 40)
    assert (c.slot, c.source, d.manifest.clips["charge"].slot) == (None, 205, 61), (
        "names pin nothing"
    )
    s.label(61, "rush")
    assert "charge" not in d.manifest.clips and d.manifest.moves["charge"].clip == "rush"
    assert d.manifest.clips["rush"].label == ""
    d.undo()
    assert d.manifest.moves["charge"].clip == "charge"
    for bad, why in (("a b", "not a name"), ("walk", "already exists"), ("", "not a name")):
        with pytest.raises(ManifestError, match=why):
            s.label(61, bad)
    with pytest.raises(ManifestError, match="not populated"):
        s.label(7, "nothing")
    s.table[8] = (10, False)
    with pytest.raises(ManifestError, match="anim 8 holds none of the original's clips"):
        s.label(8, "host")


REBIND = (
    LABELS
    + """hold_max = 40
latch = 3
label = "the rush"
anim = 9
after = "stop"

[moves.stop]
main = 0
sub = 3
clip = "charge"
"""
)


def test_bind_move(doc):
    d = doc(LABELS)
    s = C.LabelSession(d, {61: (382, False)})
    assert s.bind_move("rush", 1, 4, 61) == "moves.rush = (1,4) on charge"
    assert d.manifest.moves["rush"].main == 1
    with pytest.raises(ManifestError, match="not populated"):
        s.bind_move("x", 1, 4, 7)


def test_rebind_keeps_the_move(doc):
    d = doc(REBIND)
    s = C.LabelSession(d, {61: (382, False), 5: (30, False)})
    assert s.bind_move("charge", 1, 4, 5) == "moves.charge = (1,4) on clip_05"
    mv = d.manifest.moves["charge"]
    assert (mv.main, mv.sub, mv.clip, mv.anim) == (1, 4, "clip_05", None)
    assert (mv.hold_max, mv.latch, mv.label, mv.after) == (40, 3, "the rush", "stop")
    assert d.manifest.clips["clip_05"].frames == 30, "the unnamed slot is named"
    d.undo()
    assert "clip_05" not in d.manifest.clips and d.manifest.moves["charge"].main == 2


def test_unbind_and_rename(doc):
    d = doc(REBIND + '\n[[rule]]\nplay = "stop"\nfrom = "charge"\n')
    s = C.LabelSession(d, {61: (382, False)})
    with pytest.raises(ManifestError, match="stop is still used by moves.charge .after., rule 0"):
        s.unbind_move("stop")
    assert s.rename_move("stop", "skid") == "moves.stop is now moves.skid"
    m = d.manifest
    assert m.moves["charge"].after == "skid" and m.rules[0].play == "skid" and "stop" not in m.moves
    assert s.rename_move("charge", "rush") and m is not d.manifest
    assert d.manifest.rules[0].from_move == "rush"
    with pytest.raises(ManifestError, match="already exists"):
        s.rename_move("rush", "skid")
    d.undo()
    d.undo()
    d.edit(lambda m: setattr(m, "rules", []))
    d.edit(lambda m: setattr(m.moves["charge"], "after", None))
    assert s.unbind_move("stop") == "moves.stop removed" and list(d.manifest.moves) == ["charge"]
    with pytest.raises(ManifestError, match="no move"):
        s.unbind_move("stop")


def test_import(doc, packs):
    port, host, donor = packs
    d = doc(LABELS)
    table = {**C.clip_table(port), 61: (382, False)}
    labels = {2: "bite", 3: "idle again", 61: "rush", 99: "nowhere"}
    done = C.import_labels(
        d, labels, table, C.UNRECORDED.format("f.txt"), C.coverage(port, host, donor)
    )
    assert done == [2]
    clip = d.manifest.clips["clip_02"]
    assert clip.label == "bite" and clip.labelled_build.startswith("unrecorded: f.txt")
    assert C.import_labels(d, labels, table, "b", overwrite=True) == [2, 3, 61]
    assert d.manifest.clips["charge"].label == "rush"


def test_report(make, packs):
    port, host, donor = packs
    m = make("\n[clips.bite]\nslot = 2\nframes = 50\nloop = false\n")
    v = C.survey(m, C.clip_table(port), C.coverage(port, host, donor), "p.bin@1")
    text = C.report("t", v)
    assert "p.bin@1" in text and "1 trustworthy" in text and "CARRIED: 1, 6" in text
    assert v.track(2).name == "bite" and v.kind(3) == C.FILLER and not v.suspect


def test_zinogre(games, built, ports):
    m = manifest.load(ports / "zinogre.toml")
    port = slots.anim_of(built("zinogre"))
    host, donor = host_anim(m, games), donor_clips(m, games)
    cov = C.coverage(port, host, donor, layout.of(m, donor, host))
    n = cov.counts()
    assert len(cov.slots) == n[C.CARRIED] == 103 and n[C.FILLER] == n[C.DROPPED] == 0
    assert all(t.trusted for t in C.track_labels(m, C.clip_table(port), sources=cov.sources()))
    pinned = {name for name, c in m.clips.items() if c.slot is not None}
    got = {t.name: t.status for t in C.track_labels(m, C.clip_table(port))}
    assert {k: v for k, v in got.items() if v != C.UNCHECKABLE} == dict.fromkeys(
        pinned, C.STILL_VALID
    ), "without a layout only a pin is checkable"


def test_brute(built):
    table = C.pac_clip_table(built("brute_tigrex"))
    assert len(table) == 77 and table[61] == (382, False) and 24 not in table
