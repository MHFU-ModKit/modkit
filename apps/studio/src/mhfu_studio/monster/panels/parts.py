# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Parts panel: where he can be hit and for how much, the host's tables or the port's.

`part` (the accumulator that breaks) and `hitzone_row` (the grid row a hit is scaled by) are
different fields of one record: a Tigrex wing is part 6 and row 5."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

from mhfu import hitzone
from mhfu.em.intel import HitSphere, PartIntel
from mhfu_port.manifest import Hurtbox

from mhfu_studio.monster.render.hitboxes import PART_COLORS

from .common import (
    AMBER,
    RED,
    SHAPES,
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

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

Vol = HitSphere | Hurtbox
HOST_BONES = (
    "! the bone indices are the HOST's. This port ships its own rig, so a copied sphere lands "
    "on whatever joint sits at that index: a starting point you can SEE, not a correct answer."
)


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    vp = ws.vp
    if no_scene(ws) or vp is None:
        return
    host = ws.host_parts()
    changed = False
    for key, label in (("host", f"host em{ws.host_species or 0:02d}"), ("port", "this port")):
        if imgui.radio_button(label, ws.parts_source == key):
            ws.parts_source, changed = key, True
        imgui.same_line()
    imgui.new_line()
    ch, ws.show_parts = imgui.checkbox("show", ws.show_parts)
    imgui.same_line()
    _, vp.hitboxes_xray = imgui.checkbox("x-ray", vp.hitboxes_xray)
    if ch or changed or (vp.hitboxes is None and ws.show_parts):
        ws.sync_hitboxes()
    if ws.parts_source == "host" and host is None:
        imgui.text_disabled(f"no part intel for em{ws.host_species or 0:02d}")
        return
    part_table(ws, host)
    imgui.separator()
    if ws.parts_source == "port":
        volume_editor(ws)
        imgui.separator()
    grid_view(ws, host)
    imgui.separator()
    actions(ws, host)


def volumes_now(ws: MonsterWorkspace) -> Sequence[Vol]:
    """Whichever source the panel shows."""
    if ws.parts_source == "port":
        sess = ws.part_session
        return [] if sess is None else sess.volumes()
    host = ws.host_parts()
    return [] if host is None else host.spheres()


def _part(v: Vol) -> int:
    return (v.part or 0) & hitzone.PART_MASK


def _row(v: Vol) -> int:
    return v.hitzone_row or 0


def part_table(ws: MonsterWorkspace, host: PartIntel | None) -> None:
    """One row per accumulator: colour, name, what is attached, and the select that isolates."""
    from imgui_bundle import imgui

    vols = volumes_now(ws)
    nb = 0 if ws.scene is None else ws.scene.rig.n
    real = [v for v in vols if not v.is_marker]
    markers = "" if len(real) == len(vols) else f", +{len(vols) - len(real)} walker marker(s)"
    imgui.text_disabled(
        f"{len(real)} volume(s) on {len({v.bone for v in real})} bone(s) of {nb}{markers}"
    )
    if ws.parts_source == "host" and host is not None:
        if host.active is not None:
            sp = ",".join(map(str, host.active.species)) or "?"
            imgui.text_disabled(
                f"the set species {sp} walks: 0x{host.active.va:08X} ({host.capacity} records). "
                f"The overlay holds {len(host.hurtboxes)}; the rest are other species ids'."
            )
        else:
            imgui.text_disabled(
                "every hurtbox set in the overlay: this intel does not say which one the "
                "species walks"
            )
    if ws.part_orphans:
        lost = ", ".join(str(getattr(o, "bone", "?")) for o in ws.part_orphans[:6])
        colored(
            f"! {len(ws.part_orphans)} volume(s) name a bone this rig does not have ({lost}) and "
            "are drawn NOWHERE. Bone indices belong to the rig that ships them.",
            RED,
            wrapped=True,
        )
    if imgui.begin_table("##parts", 6, table_flags(), imgui.ImVec2(0.0, 170.0)):
        columns(
            (("", 0.22), ("#", 0.22), ("name", 1.0), ("vols", 0.35), ("bones", 0.9), ("row", 0.45))
        )
        by_part: dict[int, list[Vol]] = {}
        for v in vols:
            by_part.setdefault(_part(v), []).append(v)
        sess = ws.part_session
        for i in range(hitzone.PART_MASK + 1):
            mine = by_part.get(i, [])
            imgui.table_next_row()
            imgui.table_next_column()
            swatch(f"##sw{i}", PART_COLORS[i])
            imgui.table_next_column()
            sel = ws.selected_part == i
            if imgui.selectable(f"{i}##p{i}", sel, span_all())[0]:
                ws.select_part(None if sel else i)
            imgui.table_next_column()
            name = "" if sess is None else sess.name_of(i)
            if name:
                imgui.text(name)
            else:
                imgui.text_disabled("unassigned" if i == 0 else "-")
            imgui.table_next_column()
            imgui.text(str(len(mine)) if mine else "·")
            imgui.table_next_column()
            bones = sorted({v.bone for v in mine})
            imgui.text(bone_span(bones))
            if bones:
                tooltip(", ".join(map(str, bones)))
            imgui.table_next_column()
            rows = sorted({_row(v) for v in mine})
            imgui.text(",".join(map(str, rows)) if rows else "·")
            if len(rows) > 1:
                tooltip(
                    "this part's volumes use DIFFERENT hitzone rows, so they take different "
                    "percentages. Not an error: the Tigrex's wings do it."
                )
        imgui.end_table()
    sess = ws.part_session
    if ws.selected_part is not None and sess is not None:
        imgui.set_next_item_width(-90)
        _, ws.part_name_buf = imgui.input_text("##pname", ws.part_name_buf)
        imgui.same_line()
        part = ws.selected_part
        if imgui.button(f"name {part}"):
            name = ws.part_name_buf
            ws.edit(f"part {part} = {name} (unsaved)", lambda: sess.name_part(part, name))


def volume_editor(ws: MonsterWorkspace) -> None:
    """The port's volumes, a row each, and the selected one's fields: pick a sphere, make it
    obviously different, keep only it, ship it."""
    from imgui_bundle import imgui

    sess = ws.part_session
    if sess is None:
        imgui.text_disabled("no manifest, so no volumes to edit")
        return
    vols = sess.volumes()
    if not vols:
        imgui.text_disabled("no [[hurtbox]] authored: adopt the host's below, or add one")
        if imgui.button("add a sphere"):
            new = Hurtbox(bone=1, radius=150.0, part=1, hitzone_row=0, offset=[0.0, 0.0, 0.0])
            if ws.edit("added a sphere (unsaved)", lambda: sess.add_volume(new)):
                ws.select_volume(len(sess.volumes()) - 1)
        return
    head = f"{len(vols)} volume(s)"
    if sess.capacity is not None:
        head += f", {sess.capacity} fit in place"
    imgui.text_disabled(head)
    if sess.over_capacity:
        imgui.same_line()
        colored(f"! {sess.over_capacity} over: the runtime truncates", RED)
    if ws.selected_part is not None:
        imgui.same_line()
        _, ws.only_selected_part = imgui.checkbox(
            f"only part {ws.selected_part}", ws.only_selected_part
        )
    if imgui.begin_table("##vols", 7, table_flags(scroll=True), imgui.ImVec2(0.0, 150.0)):
        columns(
            (
                ("#", 0.3),
                ("bone", 0.4),
                ("shape", 0.55),
                ("r", 0.5),
                ("part", 0.4),
                ("row", 0.4),
                ("offset", 1.2),
            )
        )
        for i, v in enumerate(vols):
            part = _part(v)
            if ws.only_selected_part and ws.selected_part not in (None, part):
                continue
            imgui.table_next_row()
            imgui.table_next_column()
            sel = ws.selected_volume == i
            if imgui.selectable(f"{i}##v{i}", sel, span_all())[0]:
                ws.select_volume(None if sel else i)
            if sess.volume_changed(i):
                tooltip("changed: not saved yet")
            imgui.table_next_column()
            if v.is_marker:
                imgui.text_disabled(f"0x{v.bone:X}")
                tooltip("a walker MARKER, not a joint: 0x7D is the tail-sever skip. Drawn nowhere.")
            else:
                imgui.text(str(v.bone))
            imgui.table_next_column()
            imgui.text("mark" if v.is_marker else v.shape[:4])
            imgui.table_next_column()
            imgui.text(f"{v.radius:g}")
            imgui.table_next_column()
            swatch(f"##vs{i}", PART_COLORS[part])
            imgui.same_line()
            imgui.text(str(part))
            imgui.table_next_column()
            imgui.text(str(_row(v)))
            imgui.table_next_column()
            o = v.offset or (0.0, 0.0, 0.0)
            imgui.text(f"{o[0]:g} {o[1]:g} {o[2]:g}")
        imgui.end_table()
    pick = ws.selected_volume
    if pick is None or not 0 <= pick < len(vols):
        imgui.text_disabled("select a volume (here, or click its gizmo) to edit it")
        return
    _fields(ws, pick, vols[pick])


def _fields(ws: MonsterWorkspace, i: int, v: Hurtbox) -> None:
    from imgui_bundle import imgui

    sess = ws.part_session
    if sess is None:
        return

    def stage(**fields: Any) -> None:
        what = ", ".join(f"{k}={val}" for k, val in fields.items())
        ws.edit(f"volume {i}: {what} (unsaved)", lambda: sess.edit_volume(i, **fields))

    imgui.text(f"volume {i}" + (f": {v.label}" if v.label else ""))
    imgui.set_next_item_width(70)
    ch, bone = imgui.input_int("bone##vb", int(v.bone), 1, 5)
    if ch:
        stage(bone=max(0, bone))
    n = 0 if ws.scene is None else ws.scene.rig.n
    tooltip(
        f"an index into THIS port's rig ({n} joints). Click a joint in the viewport to read "
        "its number."
    )
    imgui.same_line()
    imgui.set_next_item_width(60)
    ch, part = imgui.input_int("part##vp", int(v.part or 0), 1, 1)
    if ch:
        stage(part=max(0, min(hitzone.PART_MASK, part)))
    imgui.same_line()
    imgui.set_next_item_width(60)
    ch, row = imgui.input_int("row##vr", int(v.hitzone_row or 0), 1, 1)
    if ch:
        stage(hitzone_row=max(0, min(hitzone.MAX_ROW, row)))
    tooltip("hitzone_row: which grid row's percentages this volume takes. NOT the part.")
    imgui.same_line()
    imgui.set_next_item_width(90)
    ch, si = imgui.combo("##vshape", SHAPES.index(v.shape), list(SHAPES))
    if ch:
        capsule = SHAPES[si] == "capsule"
        stage(shape=SHAPES[si], to=(list(v.to) if v.to else [0.0, 0.0, 200.0]) if capsule else v.to)
    imgui.set_next_item_width(140)
    ch, r = imgui.drag_float("radius##vrad", float(v.radius), 1.0, 0.0, 5000.0, "%.1f")
    if ch:
        stage(radius=max(0.0, r))
    for k, f in (("x2", 2.0), ("x3", 3.0), ("x0.5", 0.5)):
        imgui.same_line()
        if imgui.button(k):
            stage(radius=v.radius * f)
    imgui.set_next_item_width(230)
    ch, off = imgui.input_float3("offset##voff", list(v.offset or (0.0, 0.0, 0.0)), "%.1f")
    if ch:
        stage(offset=[float(x) for x in off])
    if v.is_capsule:
        imgui.set_next_item_width(230)
        ch, to = imgui.input_float3("to##vto", list(v.to or (0.0, 0.0, 0.0)), "%.1f")
        if ch:
            stage(to=[float(x) for x in to])
    imgui.text_disabled(f"flags 0x{v.flags:X}" + ("  (shipped as-is)" if v.flags else ""))
    if imgui.button("keep only this"):
        gone = len(sess.volumes()) - 1
        msg = f"kept volume {i}, dropped {gone}: one sphere, a hit lands there or nowhere (unsaved)"
        if ws.edit(msg, lambda: sess.keep_only(i)):
            ws.select_volume(0)
    tooltip("with ONE volume left, where a hit registers is the whole answer")
    imgui.same_line()
    if imgui.button("delete") and ws.edit(
        f"deleted volume {i} (unsaved)", lambda: sess.remove_volume(i)
    ):
        ws.select_volume(None)
    imgui.same_line()
    if imgui.button("duplicate") and ws.edit(
        f"volume {i} duplicated (unsaved)", lambda: sess.add_volume(v)
    ):
        ws.select_volume(len(sess.volumes()) - 1)


def grid_view(ws: MonsterWorkspace, host: PartIntel | None) -> None:
    """The damage grid: seven rows of ten per state, editable once the port owns one."""
    from imgui_bundle import imgui

    sess = ws.part_session
    own = [] if sess is None else sess.states()
    states: Sequence[Any] = own if own else (host.states if host is not None else ())
    if not states:
        imgui.text_disabled("no damage grid: adopt the host's below")
        return
    editable = bool(own)
    tail = "" if editable else ", the HOST's (read only)"
    imgui.text_disabled(f"damage grid: {len(states)} state(s){tail}")
    if imgui.begin_tab_bar("##hzstates"):
        for i, st in enumerate(states):
            name = getattr(st, "name", None) or f"state {i}"
            front = imgui.TabItemFlags_.set_selected.value if ws.show_state == i else 0
            if imgui.begin_tab_item(f" {name} ##hz{i}", None, front)[0]:
                ws.grid_state = i
                grid_table(ws, st.rows, name, i, editable)
                imgui.end_tab_item()
        imgui.end_tab_bar()
        ws.show_state = None
    if host is not None and host.grid_note:
        colored("! " + host.grid_note, AMBER, wrapped=True)
    imgui.text_wrapped(
        "grid writes are proven live (every byte 0xFF gave 411-damage hits); the volumes are "
        "still being tested. A * marks a column whose NAME is inferred."
    )


def grid_table(
    ws: MonsterWorkspace, rows: Sequence[Sequence[int]], name: str, index: int, editable: bool
) -> None:
    from imgui_bundle import imgui

    host = ws.host_parts()
    inferred = set() if host is None else set(host.inferred_columns())
    flags = table_flags(borders=True, fit=True)
    if not imgui.begin_table(
        f"##grid{index}", 1 + len(hitzone.COLUMNS), flags, imgui.ImVec2(0.0, 190.0)
    ):
        return
    imgui.table_setup_column("row")
    for col in hitzone.COLUMNS:
        imgui.table_setup_column(col + ("*" if col in inferred else ""))
    imgui.table_setup_scroll_freeze(1, 1)
    imgui.table_headers_row()
    sess = ws.part_session
    for r, row in enumerate(rows):
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.text(str(r))
        owner = row_owner(ws, r)
        if owner:
            tooltip(f"used by {owner}")
        if editable and sess is not None:
            imgui.same_line()
            if imgui.small_button(f"255##max{index}_{r}"):
                msg = f"{name} row {r}: cut/impact/shot = 255, the most a byte can say (unsaved)"
                ws.edit(msg, partial(sess.fill_row, index, r, 255))
            tooltip(
                "cut, impact and shot to 255%: every weapon class does the most the grid "
                "can express against this row"
            )
        for c, value in enumerate(row):
            imgui.table_next_column()
            if not editable or sess is None:
                if value:
                    imgui.text(str(value))
                else:
                    imgui.text_disabled("0")
                continue
            imgui.set_next_item_width(38)
            ch, v = imgui.input_int(f"##g{index}_{r}_{c}", int(value), 0, 0)
            if ch:
                v = max(0, min(255, int(v)))
                msg = f"{name} row {r} {hitzone.COLUMNS[c]} = {v} (unsaved)"
                ws.edit(msg, partial(sess.set_hitzone, index, r, c, v))
    imgui.end_table()


def row_owner(ws: MonsterWorkspace, row: int) -> str:
    """Which named parts read this grid row: the join that makes the table legible."""
    sess = ws.part_session
    if sess is None:
        return ""
    names: list[str] = []
    for v in volumes_now(ws):
        if _row(v) == row:
            n = sess.name_of(_part(v))
            if n and n not in names:
                names.append(n)
    return ", ".join(names)


def actions(ws: MonsterWorkspace, host: PartIntel | None) -> None:
    """Adopt, save, revert, export, and what adopting costs."""
    from imgui_bundle import imgui

    sess = ws.part_session
    if sess is None or ws.manifest is None:
        imgui.text_disabled("this scene has no manifest, so there is nothing to write parts into")
        return
    if host is not None and host.has_grid:
        if imgui.button("adopt the host's grid"):
            states = host.states
            msg = "adopted the host's grid: the port inherits it at runtime either way (unsaved)"
            if ws.edit(msg, lambda: sess.adopt_grid(states)):
                ws.parts_source = "port"
        imgui.same_line()
    if host is not None and host.spheres():
        if imgui.button("adopt the host's volumes"):
            spheres, src = host.spheres(), f"em{ws.host_species or 0:02d}"
            got = None
            try:
                got = sess.adopt_volumes(spheres, source=src)
            except ValueError as e:
                ws.message = str(e)
            if got is not None:
                ws.parts_source = "port"
                ws.sync()
                ws.message = got.describe()
        tooltip(HOST_BONES)
    export_buttons(ws)
    save_row(ws)
