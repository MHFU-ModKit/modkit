# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Hitboxes panel: where he hits YOU. The Parts panel's mirror, with volume sets for parts:
a handler spawns an attack by id, the record names a set, the set is the same sphere record."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from mhfu import hitzone
from mhfu.em.intel import AttackIntel, AttackRecord
from mhfu_port.manifest import Attack, Hitbox

from mhfu_studio.monster.render.hitboxes import set_color

from .common import (
    RED,
    SHAPES,
    WARM,
    bone_span,
    colored,
    columns,
    export_buttons,
    no_scene,
    save_row,
    span_all,
    swatch,
    table_flags,
    tooltip,
)
from .parts import HOST_BONES

if TYPE_CHECKING:
    from mhfu_studio.monster.attacks import AttackSession
    from mhfu_studio.monster.workspace import MonsterWorkspace


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    vp = ws.vp
    if no_scene(ws) or vp is None:
        return
    host = ws.host_attacks()
    changed = False
    for key, label in (("host", f"host em{ws.host_species or 0:02d}"), ("port", "this port")):
        if imgui.radio_button(f"{label}##atk", ws.attacks_source == key):
            ws.attacks_source, changed = key, True
            ws.selected_attack_volume = None
        imgui.same_line()
    imgui.new_line()
    ch, ws.show_attacks = imgui.checkbox("show##atk", ws.show_attacks)
    imgui.same_line()
    _, vp.hitboxes_xray = imgui.checkbox("x-ray##atk", vp.hitboxes_xray)
    if ch or changed or (vp.attacks is None and ws.show_attacks):
        ws.sync_attacks()
    if host is None:
        imgui.text_disabled(f"no attack intel for em{ws.host_species or 0:02d}")
        return
    provenance(ws, host)
    sets_table(ws, host)
    imgui.separator()
    if ws.attacks_source == "port":
        volume_editor(ws, host)
        imgui.separator()
    levers(ws, host)
    imgui.separator()
    actions(ws, host)


def provenance(ws: MonsterWorkspace, host: AttackIntel) -> None:
    """Where the id -> record join came from: em75's was walked live, the rest are an id range
    fitted to a table, and the two must not look alike."""
    from imgui_bundle import imgui

    sp = f"0x{host.spawner:08X}" if host.spawner else "?"
    if host.join == "measured":
        n = len(host.primary.attacks) if host.primary else 0
        imgui.text_disabled(f"spawner {sp} -> the {n}-record table: MEASURED live")
    else:
        colored(
            f"spawner {sp} -> table: INFERRED ({host.join}). Only em75's join was walked to the "
            "HP write; here the id range was matched to the biggest table.",
            WARM,
            wrapped=True,
        )
    if host.join_provenance:
        tooltip(host.join_provenance)
    hs = ws.host_species
    off = None if hs is None else host.id_offset(hs)
    if off is None:
        colored(
            f"! species {hs} shares this overlay but its id offset is unknown: a move's attack "
            "ids cannot be resolved to records",
            RED,
            wrapped=True,
        )
    elif off:
        imgui.text_disabled(f"species {hs} uses record = handler id + {off}")


def moves_hitting(ws: MonsterWorkspace, set_index: int) -> tuple[list[str], int]:
    """`(declared moves, other pairs)` whose handler hits with this set."""
    si = ws.host_intel()
    if si is None:
        return [], 0
    by_pair: dict[tuple[int, int], list[str]] = {}
    m = ws.manifest
    for name, mv in ({} if m is None else m.moves).items():
        by_pair.setdefault((mv.main, mv.sub), []).append(name)
    names, others = [], 0
    for p in si.pairs_hitting_with(set_index, ws.host_species):
        got = by_pair.get((p.main, p.sub))
        if got:
            names += got
        else:
            others += 1
    return names, others


def sets_table(ws: MonsterWorkspace, host: AttackIntel) -> None:
    """A row per set: colour, the attacks on it, the moves that spawn them, volumes, bones."""
    from imgui_bundle import imgui

    sess = ws.attack_session if ws.attacks_source == "port" else None
    pair_sets = ws.pair_sets()
    if pair_sets and ws.pair is not None:
        main, sub = ws.pair
        _, ws.sets_of_move_only = imgui.checkbox(
            f"only the sets ({main},{sub}) hits with: " + ", ".join(map(str, pair_sets)),
            ws.sets_of_move_only,
        )
    elif ws.pair is not None:
        imgui.text_disabled(
            f"({ws.pair[0]},{ws.pair[1]}) spawns no attack the static scan can see: a turn, a "
            "roar, a walk; or a computed id"
        )
    only = bool(pair_sets) and ws.sets_of_move_only
    listed = sorted(pair_sets) if only else [st.index for st in host.sets]
    if ws.attacks_source == "port" and not only and ws.attack_session is not None:
        listed = sorted(set(listed) | set(ws.attack_session.sets()))
    if ws.attack_orphans:
        lost = ", ".join(str(getattr(o, "bone", "?")) for o in ws.attack_orphans[:6])
        colored(
            f"! {len(ws.attack_orphans)} volume(s) name a bone this rig does not have ({lost}) "
            "and are drawn NOWHERE. Bone indices belong to the rig that ships them.",
            RED,
            wrapped=True,
        )
    imgui.text_disabled(
        f"{len(listed)} set(s) listed of {len(host.sets)}; {len(host.attacks)} attack record(s)"
    )
    if not imgui.begin_table("##sets", 6, table_flags(scroll=True), imgui.ImVec2(0.0, 160.0)):
        return
    columns(
        (("", 0.2), ("set", 0.3), ("attacks", 0.9), ("moves", 0.9), ("vols", 0.45), ("bones", 0.9))
    )
    for idx in listed:
        st = host.set(idx)
        imgui.table_next_row()
        imgui.table_next_column()
        swatch(f"##ss{idx}", set_color(idx))
        imgui.table_next_column()
        sel = ws.selected_set == idx
        if imgui.selectable(f"{idx}##s{idx}", sel, span_all())[0]:
            ws.select_set(None if sel else idx)
        if st is not None and not st.rigged:
            tooltip(
                "un-rigged: every record hangs on bone 126/127, the NODE's own position, "
                "projectile-shaped. Nothing here to re-align to a joint."
            )
        imgui.table_next_column()
        atks = host.attacks_using(idx)
        more = f" +{len(atks) - 4}" if len(atks) > 4 else ""
        imgui.text(", ".join(f"{a.id}(p{a.power})" for a in atks[:4]) + more if atks else "·")
        if atks:
            tooltip("\n".join(f"attack {a.id}: {a.describe()}" for a in atks))
        imgui.table_next_column()
        names, others = moves_hitting(ws, idx)
        txt = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        if others:
            txt += (" " if txt else "") + f"({others} pair{'' if others == 1 else 's'})"
        imgui.text(txt or "·")
        imgui.table_next_column()
        n_host = 0 if st is None else st.capacity
        if sess is not None:
            n_port, over = len(sess.volumes_of(idx)), sess.over_capacity(idx)
            text = f"{n_port}/{n_host}" if n_port else f"·/{n_host}"
            if over:
                colored(text, RED)
                tooltip(f"{over} more than fit in place: the runtime truncates")
            else:
                imgui.text(text)
        else:
            imgui.text(str(n_host))
        imgui.table_next_column()
        if sess is not None and sess.volumes_of(idx):
            mine = {h.bone for _, h in sess.volumes_of(idx)}
            bones = sorted(b for b in mine if b not in hitzone.MARKER_BONES)
        else:
            bones = [] if st is None else st.bones
        unrigged = st is not None and not st.rigged
        imgui.text(bone_span(bones) if bones else ("node" if unrigged else "·"))
        if bones:
            tooltip(", ".join(map(str, bones)))
    imgui.end_table()


def volume_editor(ws: MonsterWorkspace, host: AttackIntel) -> None:
    """The port's attack volumes, the selected set's or all, and the selected one's fields."""
    from imgui_bundle import imgui

    sess = ws.attack_session
    if sess is None:
        imgui.text_disabled("no manifest, so no hitboxes to edit")
        return
    vols, st = sess.volumes(), ws.selected_set
    if st is not None and not sess.volumes_of(st):
        hs = host.set(st)
        imgui.text_disabled(
            f"set {st}: nothing authored, the host's {0 if hs is None else hs.capacity} "
            "record(s) stand"
        )
        if hs is not None and hs.spheres:
            if imgui.button(f"adopt the host's set {st}"):
                adopt(ws, sess, host, st)
            tooltip(HOST_BONES)
        imgui.same_line()
        add_sphere(ws, sess, st)
        return
    if not vols:
        imgui.text_disabled(
            "no [[hitbox]] authored: select a set above to adopt the host's or add a sphere"
        )
        return
    rows = sess.volumes_of(st) if st is not None else list(enumerate(vols))
    over = sess.over_capacity(st) if st is not None else sum(sess.over_capacity_all().values())
    if st is not None:
        head = f"set {st}: {len(rows)} volume(s)"
        cap = sess.capacities.get(st)
        head += "" if cap is None else f", {cap} fit in place"
    else:
        head = f"{len(vols)} volume(s) over {len(sess.sets())} set(s)"
    imgui.text_disabled(head)
    if over:
        imgui.same_line()
        colored(f"! {over} over: the runtime truncates", RED)
    if imgui.begin_table("##avols", 6, table_flags(scroll=True), imgui.ImVec2(0.0, 140.0)):
        columns(
            (("#", 0.3), ("set", 0.35), ("bone", 0.4), ("shape", 0.55), ("r", 0.5), ("offset", 1.2))
        )
        for i, v in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            sel = ws.selected_attack_volume == i
            if imgui.selectable(f"{i}##av{i}", sel, span_all())[0]:
                ws.select_attack_volume(None if sel else i)
            if sess.volume_changed(i):
                tooltip("changed: not saved yet")
            imgui.table_next_column()
            swatch(f"##avs{i}", set_color(v.set))
            imgui.same_line()
            imgui.text(str(v.set))
            imgui.table_next_column()
            if v.is_node_space:
                imgui.text_disabled("node")
                tooltip(
                    f"bone {v.bone}: at the NODE's own position, the attacker's origin for a body "
                    "attack, a projectile's for a thrown one. Drawn at the origin."
                )
            elif v.is_marker:
                imgui.text_disabled("0x7D")
                tooltip("a JOINER the walker hands on, with no geometry of its own. Drawn nowhere.")
            else:
                imgui.text(str(v.bone))
            imgui.table_next_column()
            imgui.text(v.shape[:4])
            imgui.table_next_column()
            imgui.text(f"{v.radius:g}")
            imgui.table_next_column()
            o = v.offset or (0.0, 0.0, 0.0)
            imgui.text(f"{o[0]:g} {o[1]:g} {o[2]:g}")
        imgui.end_table()
    pick = ws.selected_attack_volume
    if pick is None or not 0 <= pick < len(vols):
        imgui.text_disabled("select a volume (here, or click its gizmo) to edit it")
        if st is not None:
            add_sphere(ws, sess, st)
            imgui.same_line()
            hs = host.set(st)
            if hs is not None and hs.spheres and imgui.button(f"re-adopt the host's set {st}"):
                adopt(ws, sess, host, st)
        return
    _fields(ws, sess, host, pick, vols[pick])


def add_sphere(ws: MonsterWorkspace, sess: AttackSession, st: int) -> None:
    from imgui_bundle import imgui

    if imgui.button(f"add a sphere to set {st}"):
        new = Hitbox(bone=1, radius=150.0, set=st, offset=[0.0, 0.0, 0.0])
        if ws.edit(f"added a sphere to set {st} (unsaved)", lambda: sess.add_volume(new)):
            ws.select_attack_volume(len(sess.volumes()) - 1)


def adopt(ws: MonsterWorkspace, sess: AttackSession, host: AttackIntel, st: int) -> None:
    hs = host.set(st)
    if hs is None:
        return
    try:
        got = sess.adopt_set(st, hs.spheres, source=f"em{ws.host_species or 0:02d} set {st}")
    except ValueError as e:
        ws.message = str(e)
        return
    ws.sync()
    ws.message = got.describe()


def _fields(
    ws: MonsterWorkspace, sess: AttackSession, host: AttackIntel, i: int, v: Hitbox
) -> None:
    from imgui_bundle import imgui

    def stage(**fields: Any) -> None:
        what = ", ".join(f"{k}={val}" for k, val in fields.items())
        ws.edit(f"hitbox {i}: {what} (unsaved)", lambda: sess.edit_volume(i, **fields))

    imgui.text(f"hitbox {i}" + (f": {v.label}" if v.label else ""))
    imgui.set_next_item_width(70)
    ch, bone = imgui.input_int("bone##ab", int(v.bone), 1, 5)
    if ch:
        stage(bone=max(0, bone))
    n = 0 if ws.scene is None else ws.scene.rig.n
    tooltip(
        f"an index into THIS port's rig ({n} joints); 126/127 = the node's own position. "
        "Click a joint in the viewport to read its number."
    )
    imgui.same_line()
    imgui.set_next_item_width(60)
    ch, sv = imgui.input_int("set##as", int(v.set), 1, 1)
    if ch:
        stage(set=max(0, min(len(host.sets) - 1, sv)))
    tooltip(
        f"which of the host's {len(host.sets)} volume sets this record ships in: the attack "
        "record picks the set"
    )
    imgui.same_line()
    imgui.set_next_item_width(90)
    ch, si = imgui.combo("##ashape", SHAPES.index(v.shape), list(SHAPES))
    if ch:
        capsule = SHAPES[si] == "capsule"
        stage(shape=SHAPES[si], to=(list(v.to) if v.to else [0.0, 0.0, 200.0]) if capsule else v.to)
    imgui.set_next_item_width(140)
    ch, r = imgui.drag_float("radius##arad", float(v.radius), 1.0, 0.0, 5000.0, "%.1f")
    if ch:
        stage(radius=max(0.0, r))
    for k, f in (("x2", 2.0), ("x3", 3.0), ("x0.5", 0.5)):
        imgui.same_line()
        if imgui.button(f"{k}##a"):
            stage(radius=v.radius * f)
    imgui.set_next_item_width(230)
    ch, off = imgui.input_float3("offset##aoff", list(v.offset or (0.0, 0.0, 0.0)), "%.1f")
    if ch:
        stage(offset=[float(x) for x in off])
    if v.is_capsule:
        imgui.set_next_item_width(230)
        ch, to = imgui.input_float3("to##ato", list(v.to or (0.0, 0.0, 0.0)), "%.1f")
        if ch:
            stage(to=[float(x) for x in to])
    imgui.set_next_item_width(-120)
    ch, ws.attack_label_buf = imgui.input_text("##alabel", v.label or "")
    if ch:
        stage(label=ws.attack_label_buf)
    imgui.same_line()
    imgui.text_disabled(f"flags 0x{v.flags:X}")
    if imgui.button(f"keep only this in set {v.set}"):
        gone = len(sess.volumes_of(v.set)) - 1
        msg = (
            f"kept hitbox {i}, dropped {gone} from set {v.set}: one sphere, the attack lands "
            "there or nowhere (unsaved)"
        )
        if ws.edit(msg, lambda: sess.keep_only(i)):
            ws.select_attack_volume(sess.volumes_of(v.set)[0][0])
    tooltip(
        "with ONE volume left in the set, where the blow lands is the whole answer. The "
        "other sets are other attacks and stay."
    )
    imgui.same_line()
    if imgui.button("delete##a") and ws.edit(
        f"deleted hitbox {i} (unsaved)", lambda: sess.remove_volume(i)
    ):
        ws.select_attack_volume(None)
    imgui.same_line()
    if imgui.button("duplicate##a") and ws.edit(
        f"hitbox {i} duplicated (unsaved)", lambda: sess.add_volume(v)
    ):
        ws.select_attack_volume(len(sess.volumes()) - 1)


def levers(ws: MonsterWorkspace, host: AttackIntel) -> None:
    """The records on the selected set (or the selected pair's): power, element and volume,
    the three measured levers, editable on the port; the host's byte where the port is silent."""
    from imgui_bundle import imgui

    hp = ws.host_pair()
    if ws.selected_set is not None:
        recs = host.attacks_using(ws.selected_set)
        title = f"attack records using set {ws.selected_set}"
    elif hp is not None and ws.pair is not None:
        recs = host.records_for(hp.attack_ids, ws.host_species)
        title = f"attack records ({ws.pair[0]},{ws.pair[1]}) spawns"
    else:
        imgui.text_disabled("select a set or a pair to see its attack records")
        return
    if not recs:
        imgui.text_disabled(title + ": none")
        return
    imgui.text_disabled(title + ": power / element gate / set. Only these three are decoded.")
    sess = ws.attack_session if ws.attacks_source == "port" else None
    if not imgui.begin_table("##levers", 5, table_flags()):
        return
    columns((("id", 0.3), ("power", 0.6), ("element", 0.6), ("set", 0.6), ("", 0.5)), False)
    for a in recs:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.text(str(a.id))
        tooltip(
            f"record 0x{a.va:08X}: kind {a.kind}, angle {a.angle}, tag 0x{a.tag:02X}, located "
            "not decoded"
        )
        if sess is None:
            for text in (str(a.power), f"0x{a.element:02X}", str(a.volume)):
                imgui.table_next_column()
                imgui.text(text)
            imgui.table_next_column()
            continue
        mine = sess.attack(a.id)
        for key, hex_ in (("power", False), ("element", True), ("volume", False)):
            imgui.table_next_column()
            lever(ws, sess, a, mine, key, hex_)
        imgui.table_next_column()
        if mine is not None and not mine.is_empty:
            if imgui.small_button(f"host##r{a.id}"):
                ws.edit(
                    f"attack {a.id}: back to the host's bytes (unsaved)",
                    partial(sess.clear_attack, a.id),
                )
            tooltip("drop the port's block; the host's record stands")
        else:
            imgui.text_disabled("host")
    imgui.end_table()


def lever(
    ws: MonsterWorkspace,
    sess: AttackSession,
    a: AttackRecord,
    mine: Attack | None,
    key: str,
    hex_: bool,
) -> None:
    from imgui_bundle import imgui

    host_value = int(getattr(a, key))
    val = None if mine is None else getattr(mine, key)
    shown = host_value if val is None else int(val)
    imgui.set_next_item_width(-1)
    if hex_:
        ch, txt = imgui.input_text(f"##{key}{a.id}", f"0x{shown:02X}")
        if not ch:
            return
        try:
            nv = int(txt, 0)
        except ValueError:
            return
    else:
        ch, nv = imgui.input_int(f"##{key}{a.id}", shown, 1, 10)
        if not ch:
            return
    nv = max(0, min(255, int(nv)))
    same = " = the host's, so the block says nothing" if nv == host_value else ""
    levers_ = {key: None if nv == host_value else nv}
    ws.edit(f"attack {a.id} {key} = {nv}{same} (unsaved)", lambda: sess.set_attack(a.id, **levers_))


def actions(ws: MonsterWorkspace, host: AttackIntel) -> None:
    """Adopt the move's sets, export, save, revert, and the shared-data caveat."""
    from imgui_bundle import imgui

    sess = ws.attack_session
    if sess is None or ws.manifest is None:
        imgui.text_disabled(
            "this scene has no manifest, so there is nothing to write hitboxes into"
        )
        return
    ps = ws.pair_sets()
    if ps and ws.pair is not None:
        if imgui.button(f"adopt the sets ({ws.pair[0]},{ws.pair[1]}) hits with"):
            done = 0
            for st in ps:
                hs = host.set(st)
                if hs is not None and hs.spheres:
                    src = f"em{ws.host_species or 0:02d} set {st}"
                    if ws.edit("", partial(sess.adopt_set, st, hs.spheres, src)):
                        done += 1
            ws.attacks_source = "port"
            ws.sync()
            ws.message = (
                f"adopted {done} set(s) from em{ws.host_species or 0:02d}: the HOST's bone "
                "indices, on this rig, a starting point you can see, not a correct answer "
                "(unsaved)"
            )
        tooltip(
            "copy the host's records for every set this pair's handler spawns, so you can "
            "move them onto the right joints of THIS rig"
        )
    colored(
        f"the sets are SPECIES data in the overlay: with the port REPLACING its host they are his "
        f"alone; beside a native em{ws.host_species or 0:02d} they re-arm the native too. The "
        "in-place write and the P.hit() path are proven live. Deploy syncs mhfu_port.lua too: a "
        "stale library silently drops these tables.",
        WARM,
        wrapped=True,
    )
    export_buttons(ws)
    save_row(ws)
