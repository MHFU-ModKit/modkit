# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The gizmo on hitboxes and hurtboxes, driven with synthetic pointers over the synthetic port."""

from __future__ import annotations

import numpy as np
import pytest
from mhfu_studio.monster.render.hitboxes import HitboxOverlay
from mhfu_studio.monster.tools import HIT, HURT, SIZE, End
from mhfu_studio.monster.workspace import HOST, PORT, MonsterWorkspace
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.manipulator import ARM_PX, Handle, hit, world_per_px
from mhfu_studio.shell.overlay import Recorder
from mhfu_studio.shell.workspace import Gesture

VIEW = (320, 240)
Pt = tuple[float, float]


def ev(kind: str, at: Pt, mods: Mod = Mod.NONE) -> Pointer:
    held = Button.LEFT if kind in ("press", "move") else Button.NONE
    changed = Button.NONE if kind == "move" else Button.LEFT
    return Pointer(kind, at[0], at[1], VIEW, button=changed, buttons=held, mods=mods)  # type: ignore[arg-type]


def picked(ws: MonsterWorkspace, which: str = HIT, index: int = 0) -> HitboxOverlay:
    """`index` of `which`, yours, shown and picked; the clip paused where it is."""
    assert ws.vp is not None
    if which == HIT:
        ws.show_attacks, ws.attacks_source = True, PORT
        ws.sync()
        ws.select_attack_volume(index)
    else:
        ws.show_parts, ws.parts_source = True, PORT
        ws.sync()
        ws.select_volume(index)
    ws.vp.playback.pause()
    ov = ws.tools.overlay(which)
    assert ov is not None
    return ov


def screen(ws: MonsterWorkspace, p: np.ndarray) -> Pt:
    assert ws.vp is not None
    x, y, _ = ws.vp.camera.project(np.asarray(p, np.float64), VIEW)[0]
    return float(x), float(y)


def grip(ws: MonsterWorkspace, h: Handle, end: End = "near") -> tuple[Pt, np.ndarray]:
    """A point on handle `h` of the gizmo at `end`, and the handle's world direction."""
    t = ws.tools.target()
    assert t is not None and ws.vp is not None
    pose = ws.tools._poses(t[3], t[2])[end]
    cam = ws.vp.camera
    pivot = pose[:3, 3]
    d = pose[:3, h.axis or 0] / np.linalg.norm(pose[:3, h.axis or 0])
    arm = ARM_PX * world_per_px(cam, pivot, VIEW[1])
    op = "scale" if ws.tools.tool == SIZE else "translate"
    for f in np.linspace(0.95, 0.4, 12):
        at = screen(ws, pivot + d * arm * f)
        if hit(cam, VIEW, pose, op, *at, local=True) == h:
            return at, d
    raise AssertionError(f"no {h} on screen")


def drag(ws: MonsterWorkspace, a: Pt, b: Pt, mods: Mod = Mod.NONE) -> list[Gesture]:
    got = [ws.pointer(ev("press", a, mods))]
    got.append(ws.pointer(ev("move", ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), mods)))
    got.append(ws.pointer(ev("move", b, mods)))
    got.append(ws.pointer(ev("release", b, mods)))
    return got


def along(at: Pt, ws: MonsterWorkspace, d: np.ndarray, px: float, origin: np.ndarray) -> Pt:
    """`at` moved `px` points along the screen image of world direction `d` at `origin`."""
    a, b = np.array(screen(ws, origin)), np.array(screen(ws, origin + d))
    u = (b - a) / np.linalg.norm(b - a)
    return at[0] + u[0] * px, at[1] + u[1] * px


@pytest.mark.parametrize("which", [HIT, HURT])
def test_move_drags_the_offset_in_bone_space(workspace: MonsterWorkspace, which: str) -> None:
    ws = workspace
    ov = picked(ws, which)
    assert ws.doc is not None and not ws.doc.can_undo()
    v0 = ov.volumes[0]
    frame = ov.frame(v0)
    at, d = grip(ws, Handle("axis", 0))
    to = along(at, ws, d, 40.0, ov.place(v0)[0])
    ws.pointer(ev("press", at))
    ws.pointer(ev("move", to))
    moved = ov.volumes[0]
    assert moved.a != v0.a, "the overlay previews the drag"
    vols = ws.manifest.hurtboxes if which == HURT else ws.manifest.hitboxes  # type: ignore[union-attr]
    assert list(vols[0].offset or (0, 0, 0)) == list(v0.a), "nothing is written mid-drag"
    assert ws.pointer(ev("release", to)) == Gesture.ALL
    vols = ws.manifest.hurtboxes if which == HURT else ws.manifest.hitboxes  # type: ignore[union-attr]
    new = np.array(vols[0].offset)
    step = new - np.array(v0.a)
    assert step[0] > 1.0 and np.allclose(step[1:], 0.0), "the X arrow is the bone's x"
    world = frame[:3, :3] @ step
    assert np.allclose(world / np.linalg.norm(world), d, atol=1e-3)
    ws.doc.undo()
    assert not ws.doc.can_undo(), "one drag, one undo step"
    ws.frame(0.0)
    vols = ws.manifest.hurtboxes if which == HURT else ws.manifest.hitboxes  # type: ignore[union-attr]
    assert list(vols[0].offset or (0, 0, 0)) == list(v0.a)


def test_size_drags_the_radius(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ov = picked(ws)
    assert ws.key(Key("R")) and ws.tools.tool == SIZE
    assert "drag a box to resize it" in ws.hint()
    c = screen(ws, ov.place(ov.volumes[0])[0])
    drag(ws, c, (c[0] + ARM_PX, c[1]))
    assert ws.manifest is not None and ws.manifest.hitboxes[0].radius == pytest.approx(60.0)
    at, _ = grip(ws, Handle("axis", 1))
    a = np.array(at) - np.array(c)
    drag(ws, at, (c[0] + a[0] * 0.5, c[1] + a[1] * 0.5))
    assert ws.manifest.hitboxes[0].radius == pytest.approx(30.0, rel=0.1), "an arm halves it"


def test_capsule_far_end_has_its_own(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.edit_volume(HIT, 0, shape="capsule", to=[0.0, 0.0, 40.0])
    ov = picked(ws)
    rec = Recorder(VIEW)
    ws.paint(rec)
    assert len(ws.tools._poses(ov, ov.volumes[0])) == 2
    a0, b0 = ov.volumes[0].a, ov.volumes[0].b
    at, d = grip(ws, Handle("axis", 1), "far")
    drag(ws, at, along(at, ws, d, 30.0, ov.place(ov.volumes[0])[1]))  # type: ignore[arg-type]
    h = ws.manifest.hitboxes[0]  # type: ignore[union-attr]
    assert list(h.offset or (0, 0, 0)) == list(a0) and h.to != list(b0 or ())
    assert h.to is not None and h.to[1] > 1.0 and h.to[0] == 0.0
    ov = ws.tools.overlay(HIT)
    assert ov is not None
    v = ov.volumes[0]
    at, d = grip(ws, Handle("axis", 2))
    drag(ws, at, along(at, ws, d, 30.0, ov.place(v)[0]), Mod.SHIFT)
    h2 = ws.manifest.hitboxes[0]  # type: ignore[union-attr]
    da = np.subtract(h2.offset or (0, 0, 0), h.offset or (0, 0, 0))
    assert da[2] > 1.0 and np.allclose(np.subtract(h2.to or (), h.to), da), "Shift moves both"


def test_the_base_monsters_are_pickable_not_draggable(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.show_attacks, ws.attacks_source = True, HOST
    ws.select_set(2)
    ws.sync()
    ov = ws.tools.overlay(HIT)
    assert ov is not None
    i = next(i for i in range(len(ov.volumes)) if ov.shows(i))
    c = screen(ws, ov.place(ov.volumes[i])[0])
    ws.pointer(ev("press", c))
    ws.pointer(ev("release", c))
    assert ws.selected_attack_volume == i and ov.selected_volume == i, "picked and lit"
    assert ws.tools.target() is None and "Edit yours in Hitboxes" in ws.hint()
    rec = Recorder(VIEW)
    ws.turn.shown = False
    ws.paint(rec)
    assert not rec.calls, "no gizmo"
    assert ws.key(Key("W")) and "read only" in ws.message
    before = ws.doc.manifest if ws.doc else None
    assert drag(ws, c, (c[0] + 40, c[1])) == [Gesture.NONE] * 4, "the camera keeps it"
    assert ws.doc is not None and ws.doc.manifest is before


def test_escape_drops_the_drag_then_the_pick(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ov = picked(ws)
    v0 = ov.volumes[0]
    at, d = grip(ws, Handle("axis", 0))
    ws.pointer(ev("press", at))
    ws.pointer(ev("move", along(at, ws, d, 40.0, ov.place(v0)[0])))
    assert ws.tools.dragging and ov.volumes[0] != v0
    assert ws.key(Key("Escape")) and not ws.tools.dragging and ov.volumes[0] == v0
    ws.pointer(ev("release", at))
    assert ws.doc is not None and not ws.doc.can_undo()
    assert ws.key(Key("Escape")) and ws.selected_attack_volume is None
    assert not ws.key(Key("Escape"))


def test_hint_names_the_keys(workspace: MonsterWorkspace) -> None:
    ws = workspace
    picked(ws)
    assert "hitbox 0 of hit group 2: drag the arrows to move it" in ws.hint()
    assert "R size" in ws.hint() and "Esc deselect" in ws.hint()
    assert {k.keys[0] for k in ws.shortcuts()} >= {"W", "R", "F", "Escape"}
    assert ws.key(Key("F"))
