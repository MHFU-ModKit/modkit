# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Scene, View and Joints panels: what is in the file, what is drawn, and the bones."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import plain

from .common import AMBER, colored, no_scene

if TYPE_CHECKING:
    from mhfu_studio.monster.render.skeleton import SkeletonOverlay
    from mhfu_studio.monster.workspace import MonsterWorkspace

ISOLATE = ["off", "only tagged", "hide tagged"]


def scene_panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    sc = ws.scene
    if sc is None:
        imgui.text_disabled("no scene yet: open a port manifest or a monster PAC")
        return
    imgui.text(sc.name)
    imgui.text_disabled(sc.game)
    imgui.separator()
    for label, value in (
        ("bones", sc.rig.n),
        ("groups", len(sc.groups)),
        ("vertices", sc.n_vertices),
        ("textures", len(sc.textures)),
        ("clips", len(sc.clips)),
    ):
        imgui.text(f"{label:<9} {value}")
    if sc.notes:
        imgui.separator()
        imgui.text_disabled("notes")
        for n in sc.notes:
            imgui.text_wrapped(plain(f"* {n}"))


def view_panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    from mhfu_studio.monster.render.mesh import MODES

    vp = ws.vp
    if vp is None or vp.mesh is None:
        imgui.text_disabled("no GL context yet" if vp is None else "no scene yet")
        return
    cam = vp.camera
    for i, name in enumerate(cam.lens.views):
        if i % 3:
            imgui.same_line()
        if imgui.button(name, imgui.ImVec2(72, 0)):
            cam.look(name)
    if imgui.button("frame", imgui.ImVec2(72, 0)):
        cam.frame(vp.bounds())
    imgui.separator()
    imgui.text_disabled("shading")
    changed, mode = imgui.combo("##mode", vp.mesh.mode, list(MODES))
    if changed:
        vp.mesh.mode = mode
    _, vp.show_mesh = imgui.checkbox("mesh", vp.show_mesh)
    imgui.same_line()
    _, vp.wireframe = imgui.checkbox("wire", vp.wireframe)
    imgui.separator()
    imgui.text_disabled("skeleton")
    _, vp.show_skeleton = imgui.checkbox("bones", vp.show_skeleton)
    imgui.same_line()
    _, vp.skeleton_xray = imgui.checkbox("x-ray", vp.skeleton_xray)
    _, ws.show_joint_ids = imgui.checkbox("indices", ws.show_joint_ids)
    imgui.separator()
    imgui.text_disabled("reference")
    _, vp.show_ground = imgui.checkbox("ground", vp.show_ground)
    imgui.same_line()
    _, vp.show_axes = imgui.checkbox("axes", vp.show_axes)
    _, vp.show_bounds = imgui.checkbox("bounds", vp.show_bounds)
    imgui.same_line()
    _, vp.show_points = imgui.checkbox("bind pts", vp.show_points)
    _, cam.fov = imgui.slider_float("fov", cam.fov, 15.0, 90.0)


def joints_panel(ws: MonsterWorkspace) -> None:
    """Select, tag and isolate joints: what `--hilite` and `--only` do on the command line."""
    from imgui_bundle import imgui

    vp = ws.vp
    if no_scene(ws) or vp is None or vp.skeleton is None or vp.mesh is None:
        return
    sk = vp.skeleton
    imgui.text(f"fork {sk.fork}")
    imgui.same_line()
    imgui.text_disabled(f"lead {list(sk.lead) or '-'}")
    if ws.undriven:
        joints = ", ".join(map(str, sorted(ws.undriven)))
        colored(
            f"! {sum(ws.undriven.values())} vertices hang on joints no clip drives ({joints}): "
            "they stay at bind, which is why they sit apart from the animal.",
            AMBER,
            wrapped=True,
        )
    changed, iso = imgui.combo("isolate", vp.mesh.isolate, ISOLATE)
    if changed:
        vp.mesh.isolate = iso
    if imgui.button("clear tags"):
        vp.tag_joints(())
    imgui.same_line()
    if imgui.button("tag lead+fork"):
        vp.tag_joints(sk.lead)
    imgui.separator()
    tagged = set(vp.mesh.tagged)
    counts = ws.joint_counts()
    imgui.begin_child("##joints")  # EndChild is owed whatever BeginChild returned
    for j in range(len(sk.positions)):
        hit, on = imgui.checkbox(f"##t{j}", j in tagged)
        if hit:
            vp.tag_joints((tagged | {j}) if on else (tagged - {j}))
        imgui.same_line()
        if imgui.selectable(f"{j:2d}  {note(sk, j, counts.get(j, 0))}", sk.selected == j)[0]:
            vp.select_joint(None if sk.selected == j else j)
    imgui.end_child()


def note(sk: SkeletonOverlay, j: int, verts: int) -> str:
    """A joint's role in one line: fork, lead chain, undriven, its vertices."""
    bits = []
    if j == sk.fork:
        bits.append("FORK")
    elif j in sk.lead:
        bits.append("lead")
    if sk.driven is not None and j not in sk.driven:
        bits.append("undriven")
    if verts:
        bits.append(f"{verts}v")
    return " ".join(bits)
