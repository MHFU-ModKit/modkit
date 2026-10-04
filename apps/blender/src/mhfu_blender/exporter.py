# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""File > Export > MHFU PAC and Push to Game: an imported monster read back out of Blender into
`write`'s plain data."""

# no `from __future__ import annotations`: Blender reads the property annotations as objects

from pathlib import Path
from typing import TYPE_CHECKING, Any

import bpy
import numpy as np
from bpy.props import IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mhfu_port.model import MHFU, Model, ModelError

from . import animation, curves, scene, stored, write
from .importer import clips, monster

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

    Done = set[OperatorReturnItems]

PACKAGE = __package__ or ""
"""The extension's module, which keys its preferences."""
IDENTITY_EPS = 1e-5
"""A mesh object this close to the armature's own transform has none of its own: its vertices
are read as stored, so an untouched one writes back exactly."""
NORMAL_EPS = 1e-5
"""How far a normal may differ from the one the import showed and still be unedited."""
ROTATED_EPS = 1e-4
"""How far a bone's rest axes may turn from upright before the export says so."""


class MHFU_preferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    memstick: StringProperty(  # type: ignore[valid-type]
        name="Memory stick",
        description="PPSSPP's memory stick folder (the one holding PSP); empty: $MHFU_MEMSTICK, "
        "else PPSSPP's default",
        subtype="DIR_PATH",
    )

    def draw(self, context: bpy.types.Context) -> None:
        layout: Any = self.layout
        layout.prop(self, "memstick")


def memstick(context: Any) -> str | None:
    """The memory stick the preferences name; None for `inject.memstick`'s own search."""
    addon = context.preferences.addons.get(PACKAGE)
    value = addon.preferences.memstick if addon is not None else ""
    return bpy.path.abspath(value) if value else None


def export(context: Any, owner: Any) -> tuple[Model, write.Written]:
    """`owner`'s monster as Blender holds it, written; the source model alongside."""
    for obj in context.objects_in_mode:
        obj.update_from_editmode()
    context.view_layer.update()
    model = stored.model(owner)
    skeleton = write.skeleton(model, joints(owner, model))
    bind = np.array([b.position for b in skeleton.bones], dtype=np.float64)
    groups, notes = geometry(owner)
    found = clips(owner) if model.game == MHFU else {}
    slots = {
        int(slot): animation.to_clip(owner, action, model, int(slot), bind)
        for slot, action in found.items()
    }
    out = write.write(model, skeleton, groups, slots)
    out.notes[:0] = notes + turned(owner)
    return model, out


def joints(owner: Any, model: Model) -> curves.Floats:
    """Each joint's bind position, from its bone's rest head; the bones must be the model's."""
    n = model.rig.n
    by_joint: dict[int, Any] = {}
    for bone in owner.data.bones:
        if stored.JOINT not in bone:
            raise write.ExportError(f"bone {bone.name!r} is new: the skeleton keeps its joints")
        j = int(bone[stored.JOINT])
        if j in by_joint or not 0 <= j < n:
            raise write.ExportError(f"bone {bone.name!r} claims joint {j} of {n}")
        by_joint[j] = bone
    if len(by_joint) != n:
        missing = sorted(set(range(n)) - set(by_joint))
        raise write.ExportError(f"joints {missing} have no bone: the skeleton keeps its joints")
    for j, bone in sorted(by_joint.items()):
        parent = -1 if bone.parent is None else int(bone.parent[stored.JOINT])
        if parent != model.rig.parents[j]:
            raise write.ExportError(
                f"bone {bone.name!r} hangs off joint {parent}, the model's off "
                f"{model.rig.parents[j]}: the hierarchy is fixed"
            )
    return np.array([by_joint[j].head_local for j in range(n)], dtype=np.float64)


def turned(owner: Any) -> list[str]:
    """A note when a bone's rest orientation was turned: the engine has none, so its poses
    differ from Blender's."""
    names = [
        b.name
        for b in owner.data.bones
        if np.abs(np.array(b.matrix_local)[:3, :3] - np.eye(3)).max() > ROTATED_EPS
    ]
    if not names:
        return []
    return [f"bones {', '.join(names[:5])} rest turned: the game poses them from upright"]


def geometry(owner: Any) -> tuple[dict[int, write.Geometry], list[str]]:
    """Each group's object under `owner`, read in the armature's space."""
    joint_of = {b.name: int(b[stored.JOINT]) for b in owner.data.bones if stored.JOINT in b}
    inverse = np.linalg.inv(np.array(owner.matrix_world, dtype=np.float64))
    out: dict[int, write.Geometry] = {}
    names: dict[int, str] = {}
    notes = []
    for obj in owner.children_recursive:
        if obj.type != "MESH" or stored.GROUP not in obj:
            continue
        g = int(obj[stored.GROUP])
        if g in out:
            raise write.ExportError(f"{names[g]!r} and {obj.name!r} are both group {g}")
        names[g] = obj.name
        matrix = inverse @ np.array(obj.matrix_world, dtype=np.float64)
        out[g], note = _group(obj, matrix, joint_of)
        notes += [f"{obj.name}: {n}" for n in note]
    return out, notes


def _group(
    obj: Any, matrix: curves.Floats, joint_of: dict[str, int]
) -> tuple[write.Geometry, list[str]]:
    mesh = obj.data
    n = len(mesh.vertices)
    positions = _floats(mesh.vertices, "co", n, 3)
    loops = _ints(mesh.loops, "vertex_index", len(mesh.loops))
    normals = scene.vertex_normals(mesh)
    if np.abs(matrix - np.eye(4)).max() > IDENTITY_EPS:
        positions = positions @ matrix[:3, :3].T + matrix[:3, 3]
        normals = normals @ np.linalg.inv(matrix[:3, :3])
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    shown = mesh.attributes.get(stored.NORMAL)
    turned = np.ones(n, dtype=bool)
    if shown is not None and shown.domain == "POINT" and shown.data_type == "FLOAT_VECTOR":
        before = _floats(shown.data, "vector", n, 3)
        turned = np.abs(np.nan_to_num(normals) - before).max(axis=1, initial=0.0) > NORMAL_EPS
    notes = []
    corners = _corners(mesh)
    layer = mesh.uv_layers.get("UV") or mesh.uv_layers.active
    uvs = None
    if layer is not None:
        uvs = _floats(layer.data, "uv", len(loops), 2)[corners]
        uvs[..., 1] = 1.0 - uvs[..., 1]
    groups = [joint_of.get(vg.name, -1) for vg in obj.vertex_groups]
    stray = sorted(vg.name for vg, j in zip(obj.vertex_groups, groups, strict=True) if j < 0)
    if stray:
        notes.append(f"vertex groups {', '.join(stray[:5])} name no bone: left out")
    influences = [
        [(groups[e.group], e.weight) for e in v.groups if groups[e.group] >= 0]
        for v in mesh.vertices
    ]
    triangles = loops[corners].astype(np.int32)
    return write.Geometry(positions, triangles, normals, turned, uvs, influences), notes


def _corners(mesh: Any) -> Any:
    """`(faces, 3)` loop numbers of the faces as triangles: as stored where every face is one,
    else Blender's triangulation."""
    totals = _ints(mesh.polygons, "loop_total", len(mesh.polygons))
    if (totals == 3).all():
        starts = _ints(mesh.polygons, "loop_start", len(mesh.polygons))
        return starts[:, None] + np.arange(3)
    mesh.calc_loop_triangles()
    return _ints(mesh.loop_triangles, "loops", 3 * len(mesh.loop_triangles)).reshape(-1, 3)


def _floats(items: Any, name: str, n: int, width: int) -> curves.Floats:
    flat = np.zeros(n * width, dtype=np.float32)
    items.foreach_get(name, flat)
    out: curves.Floats = flat.astype(np.float64).reshape(n, width)
    return out


def _ints(items: Any, name: str, n: int) -> Any:
    out = np.zeros(n, dtype=np.int32)
    items.foreach_get(name, out)
    return out


def report(op: Any, out: write.Written) -> None:
    for note in out.notes:
        op.report({"WARNING"}, note)
    for result in out.results:
        op.report({"WARNING"}, str(result))


class EXPORT_SCENE_OT_mhfu_pac(bpy.types.Operator, ExportHelper):  # type: ignore[misc]
    """Write the selected monster as an MHFU model PAC"""

    bl_idname = "export_scene.mhfu_pac"
    bl_label = "Export MHFU PAC"
    bl_options = {"REGISTER"}

    if TYPE_CHECKING:
        filepath: str
    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin;*.pac", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return monster(context) is not None

    def invoke(self, context: Any, event: Any) -> "Done":  # type: ignore[override]
        """The file browser on the source's name, next to the .blend once it is saved."""
        owner = monster(context)
        if owner is not None and not self.filepath:
            name = f"{owner.get(stored.NAME, owner.name)}{self.filename_ext}"
            blend = bpy.data.filepath
            self.filepath = str(Path(blend).with_name(name)) if blend else name
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> "Done":
        owner = monster(context)
        try:
            model, out = export(context, owner)
            Path(self.filepath).write_bytes(out.pac)
        except (write.ExportError, ModelError, OSError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        report(self, out)
        size = f"{len(out.pac)} bytes"
        if model.game == MHFU:
            size += f", {len(out.pac) - len(model.pac or b''):+d}"
            if out.rebuilt:
                size += f", groups {write.few(out.rebuilt)} rebuilt"
        self.report({"INFO"}, f"{self.filepath}: {size}")
        return {"FINISHED"}


class MHFU_OT_push(bpy.types.Operator):
    """Export the selected monster into the memory stick's inject folder, for the running game"""

    bl_idname = "mhfu.push"
    bl_label = "Push to Game"
    bl_options = {"REGISTER"}

    file_id: IntProperty(  # type: ignore[valid-type]
        name="File id",
        description="The extracted file it replaces; -1: from the source's file_NNNNN name",
        default=-1,
        min=-1,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return monster(context) is not None

    def execute(self, context: bpy.types.Context) -> "Done":
        owner = monster(context)
        try:
            if owner.get(stored.GAME) != MHFU:
                raise write.ExportError(
                    "an MHP3rd model reaches the game as a port: build it from a manifest"
                )
            model, out = export(context, owner)
            fid = None if self.file_id < 0 else self.file_id
            placed = write.place(out.pac, model, memstick(context), fid)
        except (write.ExportError, ModelError, OSError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        report(self, out)
        how = "relocated: larger than the source" if placed.grown else "in place"
        self.report({"INFO"}, f"{placed.path} ({how}); a Lua mod loads it: {placed.lua}")
        return {"FINISHED"}


def _menu(self: Any, context: Any) -> None:
    self.layout.operator(EXPORT_SCENE_OT_mhfu_pac.bl_idname, text="MHFU PAC (.bin)")


CLASSES: tuple[type, ...] = (MHFU_preferences, EXPORT_SCENE_OT_mhfu_pac, MHFU_OT_push)
MENUS: tuple[tuple[str, Any], ...] = (("TOPBAR_MT_file_export", _menu),)
