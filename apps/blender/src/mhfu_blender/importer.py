# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""File > Import > MHFU Model, and the MHFU tab of the 3D view's sidebar."""

# no `from __future__ import annotations`: Blender reads the property annotations as objects

from pathlib import Path
from typing import TYPE_CHECKING, Any

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mhfu_port.model import Model, ModelError

from . import animation, scene, stored

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

    Done = set[OperatorReturnItems]


def monster(context: Any) -> Any:
    """The imported armature the active object is, or belongs to; None for none."""
    obj = context.active_object
    while obj is not None and stored.PAC not in obj:
        obj = obj.parent
    return obj


def clips(owner: Any) -> dict[str, Any]:
    """`stored.CLIPS` of `owner`: slot (as text) to Action."""
    found = owner.get(stored.CLIPS)
    return {} if found is None else {k: v for k, v in found.items() if v is not None}


class IMPORT_SCENE_OT_mhfu_model(bpy.types.Operator, ImportHelper):  # type: ignore[misc]
    """Import an MHFU monster model PAC, a built port, or an MHP3rd model PAC"""

    bl_idname = "import_scene.mhfu_model"
    bl_label = "Import MHFU Model"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    if TYPE_CHECKING:
        filepath: str
    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin;*.pac", options={"HIDDEN"})  # type: ignore[valid-type]
    geometry: StringProperty(  # type: ignore[valid-type]
        name="Geometry",
        description="An MHP3rd model's geometry file; empty: the next file_NNNNN",
        subtype="FILE_PATH",
    )
    moveset: StringProperty(  # type: ignore[valid-type]
        name="Moveset",
        description="An MHP3rd model's emNNN animation file; empty: file_NNNNN two on",
        subtype="FILE_PATH",
    )
    em_id: IntProperty(  # type: ignore[valid-type]
        name="em id",
        description="An MHP3rd monster's em number, which picks its record map; -1: from the file",
        default=-1,
        min=-1,
    )
    scale: FloatProperty(  # type: ignore[valid-type]
        name="Scale",
        description="The armature object's scale; the data inside stays in game units",
        default=0.01,
        min=1e-6,
        soft_max=1.0,
    )
    clips: BoolProperty(  # type: ignore[valid-type]
        name="Animations", description="An Action per animation slot", default=True
    )

    def execute(self, context: bpy.types.Context) -> "Done":
        try:
            model = Model.from_path(
                Path(self.filepath),
                None if self.em_id < 0 else self.em_id,
                Path(self.geometry) if self.geometry else None,
                Path(self.moveset) if self.moveset else None,
            )
        except (ModelError, OSError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        scene.build(context, model, self.scale, self.clips)
        for note in model.notes:
            self.report({"WARNING"}, note)
        played = len(model.clips) if self.clips else 0
        counts = f"{model.rig.n} bones, {len(model.groups)} groups, {played} clips"
        self.report({"INFO"}, f"{model.name}: {counts}")
        return {"FINISHED"}


_items: list[tuple[str, str, str]] = []
"""The clip menu's items: Blender keeps no reference to a dynamic enum's strings."""


def _clip_items(_: Any, context: Any) -> list[tuple[str, str, str]]:
    owner = None if context is None else monster(context)
    found = {} if owner is None else clips(owner)
    _items[:] = [
        (slot, f"{action.name} (slot {slot})", "")
        for slot, action in sorted(found.items(), key=lambda kv: int(kv[0]))
    ]
    return _items


class MHFU_OT_play_clip(bpy.types.Operator):
    """Show a clip from the bind pose, the scene's frame range set to it"""

    bl_idname = "mhfu.play_clip"
    bl_label = "Play Clip"
    bl_options = {"REGISTER", "UNDO"}

    clip: EnumProperty(name="Clip", items=_clip_items)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return monster(context) is not None

    def execute(self, context: bpy.types.Context) -> "Done":
        owner = monster(context)
        action = None if owner is None else clips(owner).get(self.clip)
        if action is None:
            self.report({"ERROR"}, "no such clip")
            return {"CANCELLED"}
        animation.play(owner, action)
        return {"FINISHED"}


class VIEW3D_PT_mhfu(bpy.types.Panel):
    bl_label = "MHFU"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MHFU"

    def draw(self, context: bpy.types.Context) -> None:
        layout: Any = self.layout
        layout.operator(IMPORT_SCENE_OT_mhfu_model.bl_idname, icon="IMPORT")
        owner = monster(context)
        if owner is None:
            return
        box = layout.box()
        box.label(text=f"{owner.get(stored.NAME, owner.name)} ({owner.get(stored.GAME)})")
        if stored.EM_ID in owner:
            box.label(text=f"em id {owner[stored.EM_ID]}")
        data = owner.animation_data
        current = data.action.name if data is not None and data.action is not None else "Clip"
        box.operator_menu_enum(MHFU_OT_play_clip.bl_idname, "clip", text=current)
        row = box.row(align=True)
        row.operator("export_scene.mhfu_pac", text="Export PAC", icon="EXPORT")
        row.operator("mhfu.push", text="Push to Game", icon="PLAY")


def _menu(self: Any, context: Any) -> None:
    self.layout.operator(IMPORT_SCENE_OT_mhfu_model.bl_idname, text="MHFU Model (.bin)")


CLASSES: tuple[type, ...] = (IMPORT_SCENE_OT_mhfu_model, MHFU_OT_play_clip, VIEW3D_PT_mhfu)
MENUS: tuple[tuple[str, Any], ...] = (("TOPBAR_MT_file_import", _menu),)
