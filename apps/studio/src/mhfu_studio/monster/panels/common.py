# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the monster panels share, toolkit-free: data colours and the bone-span format; and the
imgui helpers the panels not yet on Qt still use."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import plain

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

RGBA = tuple[float, float, float, float]
AMBER: RGBA = (0.98, 0.70, 0.20, 1.0)
RED: RGBA = (0.98, 0.35, 0.35, 1.0)
WARM: RGBA = (0.95, 0.75, 0.35, 1.0)
GREY: RGBA = (0.60, 0.62, 0.68, 1.0)
#: FILLER is the loud one: a successful override onto idle looks exactly like a failed one
COVERAGE: dict[str, RGBA] = {
    "CARRIED": (0.55, 0.82, 0.55, 1.0),
    "FILLER": AMBER,
    "HOST": (0.60, 0.72, 0.98, 1.0),
    "ALTERED": (0.90, 0.55, 0.95, 1.0),
    "UNKNOWN": (0.55, 0.58, 0.64, 1.0),
}
MARKERS: dict[str, RGBA] = {
    "gate": (0.98, 0.70, 0.20, 0.95),
    "window": (0.55, 0.82, 0.98, 0.95),
    "effect": (0.85, 0.55, 0.98, 0.95),
    "ours": (0.55, 0.90, 0.60, 0.95),
    "impact": (0.98, 0.35, 0.38, 1.0),
}
LEVELS: dict[str, RGBA] = {
    "error": (0.98, 0.42, 0.42, 1.0),
    "warning": (0.98, 0.75, 0.30, 1.0),
    "info": (0.62, 0.68, 0.78, 1.0),
}
SHAPES = ("sphere", "capsule")


def colored(text: str, rgba: RGBA, wrapped: bool = False) -> None:
    from imgui_bundle import imgui

    imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*rgba))
    if wrapped:
        imgui.text_wrapped(plain(text))
    else:
        imgui.text(plain(text))
    imgui.pop_style_color()


def tooltip(text: str) -> None:
    """On the last item, when it is hovered."""
    from imgui_bundle import imgui

    if imgui.is_item_hovered():
        imgui.set_tooltip(plain(text))


def table_flags(scroll: bool = False, borders: bool = False, fit: bool = False) -> int:
    from imgui_bundle import imgui

    f = imgui.TableFlags_
    flags = f.row_bg.value | (f.borders.value if borders else f.borders_inner_h.value)
    flags |= f.sizing_fixed_fit.value | f.scroll_x.value if fit else f.sizing_stretch_prop.value
    return int(flags | (f.scroll_y.value if scroll else 0))


def columns(spec: Sequence[tuple[str, float]], freeze: bool = True) -> None:
    """Stretch columns `(name, weight)`, the header row frozen."""
    from imgui_bundle import imgui

    for name, w in spec:
        imgui.table_setup_column(name, imgui.TableColumnFlags_.width_stretch.value, w)
    if freeze:
        imgui.table_setup_scroll_freeze(0, 1)
    imgui.table_headers_row()


def span_all() -> int:
    from imgui_bundle import imgui

    return int(imgui.SelectableFlags_.span_all_columns.value)


def swatch(key: str, rgb: Sequence[float]) -> None:
    """The gizmo's own colour, so a table row and the viewport name the same thing."""
    from imgui_bundle import imgui

    flags = imgui.ColorEditFlags_.no_tooltip.value
    imgui.color_button(key, imgui.ImVec4(rgb[0], rgb[1], rgb[2], 1.0), flags, imgui.ImVec2(12, 12))


def bone_span(bones: Sequence[int]) -> str:
    """`10-14, 18`: a bone list at a table column's width."""
    if not bones:
        return "·"
    out, start, prev = [], bones[0], bones[0]
    for b in [*bones[1:], None]:
        if b is not None and b == prev + 1:
            prev = b
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if b is not None:
            start = prev = b
    return ", ".join(out)


def no_scene(ws: MonsterWorkspace) -> bool:
    """Says so and is True when there is nothing to show yet."""
    from imgui_bundle import imgui

    if ws.vp is None or ws.vp.scene is None:
        imgui.text_disabled("no scene yet: open a port manifest or a monster PAC")
        return True
    return False


def save_row(ws: MonsterWorkspace) -> None:
    """Save or revert the manifest; edits are already undo steps of the document."""
    from imgui_bundle import imgui

    doc = ws.doc
    if doc is None or not doc.dirty:
        imgui.text_disabled("nothing staged")
        return
    where = doc.path.name if doc.path is not None else "the manifest"
    if imgui.button(f"save to {where}"):
        ws.save()
    imgui.same_line()
    if imgui.button("discard"):
        ws.revert()


def export_buttons(ws: MonsterWorkspace) -> None:
    """The imgui export and deploy buttons."""
    from imgui_bundle import imgui

    m, doc = ws.manifest, ws.doc
    if m is None or doc is None or doc.path is None:
        return
    if not ws.exportable():
        imgui.text_disabled(
            "nothing to export yet: save volumes, a grid, a hitbox set or an attack record first"
        )
        return
    if imgui.button("export runtime table"):
        try:
            ws.export_hit()
        except (OSError, ValueError) as e:
            ws.message = f"{type(e).__name__}: {e}"
    tooltip(
        f"./{doc.saved_manifest.port.name}_hit.lua: the SAVED [[hurtbox]], [[hitzone]], "
        "[[hitbox]] and [[attack]] as the one P.hit() call mhfu_port.lua writes into the game"
    )
    mods = ws.mods_dir()
    if mods is not None:
        imgui.same_line()
        if imgui.button("deploy to memstick"):
            try:
                ws.deploy_hit(mods)
            except (OSError, ValueError) as e:
                ws.message = f"{type(e).__name__}: {e}"
    if doc.dirty:
        imgui.text_disabled("(exports the SAVED file: save first)")
