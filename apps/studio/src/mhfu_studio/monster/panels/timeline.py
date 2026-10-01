# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Timeline: the transport at the engine's rate, and the host action's frames on the clip.

The RATE is the action's (2.0 and 2.4 both measured on one monster); the clip owns only its
SPAN. A gate past the clip's last frame never runs, and is drawn hollow, not clamped."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.monster.align import EFFECT, GATE, IMPACT, OURS, WINDOW, Marker
from mhfu_studio.monster.render.playback import GAME_HZ, OBSERVED_SPEEDS, Playback
from mhfu_studio.shell.widgets import plain

from .common import MARKERS

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

LEGEND = (
    (GATE, "cursor >= F"),
    (WINDOW, "window edge"),
    (EFFECT, "host effect"),
    (OURS, "our effect"),
    (IMPACT, "impact"),
)


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    vp = ws.vp
    if vp is None or vp.clip is None:
        imgui.text_disabled("no clip: pick one in Clips")
        return
    pb, clip = vp.playback, vp.clip
    if imgui.button("|<"):
        pb.rewind()
        vp.set_pose(clip, pb.phase)
    imgui.same_line()
    if imgui.button("<|"):
        pb.step(-1)
        vp.set_pose(clip, pb.phase)
    imgui.same_line()
    if imgui.button("pause" if pb.playing else "play "):
        pb.toggle()
    imgui.same_line()
    if imgui.button("|>"):
        pb.step(1)
        vp.set_pose(clip, pb.phase)
    imgui.same_line()
    _, pb.loop = imgui.checkbox("loop", pb.loop)
    imgui.same_line()
    _, strip = imgui.checkbox("in place", vp.strip_root)
    if strip != vp.strip_root:
        vp.strip_root = strip
    moved, frame = imgui.slider_float(
        "##scrub", pb.phase, 0.0, max(pb.end, 1.0), f"frame %.1f / {int(pb.end)}"
    )
    if moved:
        pb.seek(frame)
        vp.set_pose(clip, pb.phase)
    changed, speed = imgui.slider_float("speed", pb.speed, 0.25, 4.0, "%.2f f/frame")
    if changed:
        pb.speed = max(0.05, speed)
    for s in OBSERVED_SPEEDS:
        imgui.same_line()
        if imgui.small_button(f"{s:.1f}"):
            pb.speed = s
    imgui.text_disabled(
        f"{pb.duration:.2f} s at {pb.speed:.2f}  (end {pb.end:.0f} / speed / {GAME_HZ:g} Hz): the "
        "SPAN is the clip's, the RATE comes from the action dispatch"
    )
    net, peak = ws.travel(clip.slot)
    imgui.text_disabled(
        f"root travel  net {net:.0f}  peak {peak:.0f}" + ("" if net >= 1.0 else "   (in place)")
    )
    if ws.manifest is not None:
        # the one number no file can give: where this clip's own contact is
        found = ws.manifest_clip(clip.slot)
        if imgui.small_button(f"set impact = frame {int(round(pb.phase))}"):
            ws.set_impact_here()
        imgui.same_line()
        if found is not None and found[1].impact_frame is not None:
            imgui.text_disabled(f"impact {found[1].impact_frame}")
        else:
            imgui.text_disabled("no impact frame recorded for this clip")
    marker_strip(ws.markers, pb)


def marker_strip(markers: list[Marker], pb: Playback) -> None:
    """The handler's frames in the clip's own frame space, the playhead over them."""
    from imgui_bundle import imgui

    h = 22.0
    w = max(imgui.get_content_region_avail().x, 1.0)
    pos = imgui.get_cursor_screen_pos()
    draw = imgui.get_window_draw_list()
    bg = imgui.get_color_u32(imgui.ImVec4(0.18, 0.19, 0.22, 1.0))
    draw.add_rect_filled(pos, imgui.ImVec2(pos.x + w, pos.y + h), bg, 3.0)
    hovered, mouse = None, imgui.get_io().mouse_pos
    if pb.end > 0:

        def at(mk: Marker) -> float:
            return float(pos.x + w * max(0.0, min(mk.frame / pb.end, 1.0)))

        for mk in markers:
            x = at(mk)
            col = imgui.get_color_u32(imgui.ImVec4(*MARKERS[mk.kind]))
            if mk.unreachable:
                draw.add_rect(
                    imgui.ImVec2(x - 2, pos.y + 3),
                    imgui.ImVec2(x + 2, pos.y + h - 3),
                    col,
                    thickness=1.5,  # the binding's order is rounding, thickness, flags
                )
            else:
                draw.add_line(imgui.ImVec2(x, pos.y + 1), imgui.ImVec2(x, pos.y + h - 1), col, 2.0)
            if abs(mouse.x - x) < 6 and pos.y <= mouse.y <= pos.y + h:
                hovered = mk
        # labels are placed, not just drawn: close gates overprint into a smear. The impact's
        # goes first, it is the answer the strip exists to give
        taken: list[tuple[float, float]] = []
        for mk in sorted(markers, key=lambda k: (k.kind != IMPACT, k.frame)):
            x = at(mk) + 3.0
            tw = imgui.calc_text_size(mk.label).x
            if any(x < b and x + tw > a for a, b in taken):
                continue
            taken.append((x - 2.0, x + tw + 2.0))
            col = imgui.get_color_u32(imgui.ImVec4(*MARKERS[mk.kind]))
            draw.add_text(imgui.ImVec2(x, pos.y + 3), col, mk.label)
        x = pos.x + w * pb.progress
        head = imgui.get_color_u32(imgui.ImVec4(0.95, 0.25, 0.28, 1.0))
        draw.add_line(imgui.ImVec2(x, pos.y), imgui.ImVec2(x, pos.y + h), head, 2.0)
    imgui.dummy(imgui.ImVec2(w, h))
    if hovered is not None:
        never = "\n! past the clip's last frame: never reached" if hovered.unreachable else ""
        imgui.set_tooltip(
            plain(f"frame {hovered.frame:g}: {hovered.detail or hovered.label}{never}")
        )
    if not markers:
        imgui.text_disabled(
            "no frame markers: pick a (main,sub) in Action and the handler's own frames appear here"
        )
        return
    first = True
    for kind, name in LEGEND:
        if not any(mk.kind == kind for mk in markers):
            continue
        if not first:
            imgui.same_line()
        first = False
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*MARKERS[kind]))
        imgui.text("| " + name)
        imgui.pop_style_color()
