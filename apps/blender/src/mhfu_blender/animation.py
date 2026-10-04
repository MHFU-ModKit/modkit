# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Actions on Blender's two APIs: slotted (4.4 on, the only one from 5.0) and the legacy
`Action.fcurves` (4.2, 4.3). An imported clip's Action has one slot, named after its armature."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import bpy
import numpy as np
from mhfu_port.model import Model
from mhp_formats.anim import Clip

from . import curves, stored

_FREE = "FREE"
_BEZIER = "BEZIER"


def slotted(action: Any) -> bool:
    return hasattr(action, "layers")


def fcurves(action: Any, owner: str | None = None) -> Any:
    """The Action's F-curves: on a slotted Action its first slot's, made for `owner` (an object
    name) when given and missing; None when there are none."""
    if not slotted(action):
        return action.fcurves
    a: Any = action
    if not a.slots:
        if owner is None:
            return None
        a.slots.new(id_type="OBJECT", name=owner)
    if not a.layers or not a.layers[0].strips:
        if owner is None:
            return None
        layer = a.layers[0] if a.layers else a.layers.new("Layer")
        layer.strips.new(type="KEYFRAME")
    bag = a.layers[0].strips[0].channelbag(a.slots[0], ensure=owner is not None)
    return None if bag is None else bag.fcurves


def write(action: Any, owner: Any, items: Iterable[curves.Curve]) -> None:
    """New F-curves on `action`, grouped by bone; keys in the order given. No `FCurve.update()`:
    it merges keys on one frame, which the engine's clips have."""
    out = fcurves(action, owner.name)
    groups: dict[str, Any] = {}
    container: Any = _channelbag(action) if slotted(action) else action
    for c in items:
        fc = out.new(c.data_path, index=c.index)
        if c.bone not in groups:
            groups[c.bone] = container.groups.get(c.bone) or container.groups.new(c.bone)
        fc.group = groups[c.bone]
        n = len(c.co)
        points = fc.keyframe_points
        points.add(n)
        for name, xy in (("co", c.co), ("handle_left", c.left), ("handle_right", c.right)):
            points.foreach_set(name, xy.astype(np.float32).ravel())
        points.foreach_set("interpolation", [_enum("interpolation", _BEZIER)] * n)
        points.foreach_set("handle_left_type", [_enum("handle_left_type", _FREE)] * n)
        points.foreach_set("handle_right_type", [_enum("handle_right_type", _FREE)] * n)


def read(action: Any) -> list[curves.Curve]:
    """The pose-bone F-curves of `action`'s first slot."""
    found = fcurves(action)
    out = []
    linear = _enum("interpolation", curves.LINEAR)
    for fc in found or ():
        parsed = curves.parse_path(fc.data_path)
        if parsed is None:
            continue
        points = fc.keyframe_points
        n = len(points)
        co, left, right = (_get(points, name, n) for name in ("co", "handle_left", "handle_right"))
        modes = np.zeros(n, dtype=np.int32)
        points.foreach_get("interpolation", modes)
        interpolation = [curves.LINEAR if m == linear else _BEZIER for m in modes]
        out.append(curves.Curve(*parsed, fc.array_index, co, left, right, interpolation))
    return out


def to_clip(owner: Any, action: Any, model: Model, slot: int) -> Clip:
    """The clip `action` plays in `slot` of `owner`'s model (`stored.model(owner)`).

    Channels and tracks the Action does not reach come from the model's own clip in `slot`."""
    native = next((c for c in model.clips if c.slot == slot), None)
    joints = {b.name: int(b[stored.JOINT]) for b in owner.data.bones if stored.JOINT in b}
    return curves.to_clip(
        read(action),
        joints,
        model.rig.bind_local,
        int(action.get(stored.LOOP, 0)),
        float(action.get(stored.LOOP_START, 0.0)),
        None if native is None else native.source,
        None if native is None else native.joint_tracks,
    )


def play(owner: Any, action: Any) -> None:
    """Show `action` on `owner` from the bind pose, the scene's range set to its keys.

    Blender keeps a channel the Action does not key where the last one left it; the engine
    plays it at bind, hence the reset."""
    for bone in owner.pose.bones:
        bone.location = (0.0, 0.0, 0.0)
        bone.rotation_euler = (0.0, 0.0, 0.0)
        bone.scale = (1.0, 1.0, 1.0)
    data = owner.animation_data or owner.animation_data_create()
    data.action = action
    a: Any = action
    if slotted(action) and a.slots and data.action_slot != a.slots[0]:
        data.action_slot = a.slots[0]
    scene: Any = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = max(1, round(action.frame_range[1]))


def _get(points: Any, name: str, n: int) -> curves.Floats:
    flat = np.zeros(2 * n, dtype=np.float32)
    points.foreach_get(name, flat)
    out: curves.Floats = flat.astype(np.float64).reshape(n, 2)
    return out


def _channelbag(action: Any) -> Any:
    a: Any = action
    return a.layers[0].strips[0].channelbag(a.slots[0])


def _enum(prop: str, name: str) -> int:
    rna: Any = bpy.types.Keyframe.bl_rna
    value: int = rna.properties[prop].enum_items[name].value
    return value
