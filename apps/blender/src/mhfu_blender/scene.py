# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A `Model` as Blender data: an armature, one mesh object per PMO group, a material per texture
and an Action per clip.

Everything inside the armature is in the engine's space and units; the armature object's own
transform (Y-up turned Z-up, and a scale) is for show and is not part of the model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bpy
import numpy as np
from mhfu_port.model import MeshGroup, Model, TextureImage

from . import animation, curves, stored

BONE_LENGTH = 0.02
"""A bone's display length as a share of the bind pose's largest extent."""


def bone_names(model: Model) -> list[str]:
    """`j07`, or `j07 AL-00` where the skeleton names the bone."""
    out = []
    for j, bone in enumerate(model.skeleton.bones):
        label = (bone.name or b"").split(b"\0")[0].decode("ascii", "replace").strip()
        out.append(f"j{j:02d} {label}" if label else f"j{j:02d}")
    return out


def build(context: Any, model: Model, scale: float = 0.01, clips: bool = True) -> Any:
    """The armature with its meshes and Actions, linked in a new collection of the scene."""
    collection = bpy.data.collections.new(model.name)
    context.scene.collection.children.link(collection)
    owner = bpy.data.objects.new(model.name, bpy.data.armatures.new(model.name))
    collection.objects.link(owner)
    owner.rotation_euler = (math.pi / 2, 0.0, 0.0)
    owner.scale = (scale, scale, scale)
    for key, value in stored.props(model).items():
        owner[key] = value
    names = bone_names(model)
    _bones(context, owner, model, names)
    materials = [_material(model.name, t) for t in model.textures]
    for group in model.groups:
        _mesh(collection, owner, group, names, materials, model.name)
    if clips and model.clips:
        _actions(owner, model, names)
    for obj in context.selected_objects:
        obj.select_set(False)
    owner.select_set(True)
    context.view_layer.objects.active = owner
    return owner


def _bones(context: Any, owner: Any, model: Model, names: Sequence[str]) -> None:
    data: Any = owner.data
    joints = model.rig.bind_joints
    length = BONE_LENGTH * float(np.ptp(joints, axis=0).max(initial=0.0)) or 1.0
    layer = context.view_layer
    previous = layer.objects.active
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    layer.objects.active = owner
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        made = []
        for name, head in zip(names, joints, strict=True):
            bone = data.edit_bones.new(name)
            bone.head = head
            bone.tail = head + (0.0, length, 0.0)
            bone.roll = 0.0
            made.append(bone)
        for bone, parent in zip(made, model.rig.parents, strict=True):
            if parent >= 0:
                bone.parent = made[parent]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        layer.objects.active = previous
    for j, name in enumerate(names):
        data.bones[name][stored.JOINT] = j
        owner.pose.bones[name].rotation_mode = "XYZ"


def _material(prefix: str, texture: TextureImage) -> Any:
    """An image material sampled through an explicit UV Map node; without one the image node
    falls back to generated coordinates and smears the texture over the bounding box."""
    image = bpy.data.images.new(
        f"{prefix} tex{texture.index:02d}", texture.width, texture.height, alpha=True
    )
    pixels: Any = image.pixels
    pixels.foreach_set((texture.rgba[::-1].astype(np.float32) / 255.0).ravel())
    image.pack()
    material = bpy.data.materials.new(f"{prefix} tex{texture.index:02d}")
    if material.node_tree is None:  # before 5.0 a new material has no node tree
        material.use_nodes = True
    tree: Any = material.node_tree
    nodes = tree.nodes
    shader = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    sample = nodes.new("ShaderNodeTexImage")
    sample.image = image
    sample.interpolation = "Closest"
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UV"
    uv.location = (sample.location[0] - 250, sample.location[1])
    tree.links.new(uv.outputs["UV"], sample.inputs["Vector"])
    tree.links.new(sample.outputs["Color"], shader.inputs["Base Color"])
    tree.links.new(sample.outputs["Alpha"], shader.inputs["Alpha"])
    return material


def _mesh(
    collection: Any,
    owner: Any,
    group: MeshGroup,
    names: Sequence[str],
    materials: Sequence[Any],
    prefix: str,
) -> None:
    name = f"{prefix} g{group.index:03d}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(group.positions.tolist(), [], group.triangles.tolist())
    if group.uvs is not None:
        layer = mesh.uv_layers.new(name="UV")
        corner = group.uvs[group.triangles.ravel()]
        corner[:, 1] = 1.0 - corner[:, 1]
        layer.data.foreach_set("uv", corner.astype(np.float32).ravel())
    if group.texture is not None:
        mesh.materials.append(materials[group.texture])
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    if group.normals is not None:
        mesh.normals_split_custom_set_from_vertices(group.normals.tolist())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = owner
    obj[stored.GROUP] = group.index
    skin = group.skin
    for joint in np.unique(skin.joints[skin.weights > 0]):
        vertices, slot = np.nonzero((skin.joints == joint) & (skin.weights > 0))
        weights = skin.weights[vertices, slot]
        target = obj.vertex_groups.new(name=names[joint])
        for w in np.unique(weights):
            target.add(vertices[weights == w].tolist(), float(w), "REPLACE")
    modifier: Any = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = owner


def _actions(owner: Any, model: Model, names: Sequence[str]) -> None:
    extra: dict[tuple[str, str], float] = {}
    slots: dict[str, Any] = {}
    for clip in model.clips:
        action = bpy.data.actions.new(clip.name)
        action[stored.LOOP] = int(clip.source.loop)
        action[stored.LOOP_START] = float(clip.source.loop_start)
        found = curves.to_curves(clip.source, names, model.rig.bind_local, clip.joint_tracks)
        for c in found:
            if c.prop not in (curves.ROTATION, curves.LOCATION):
                extra.setdefault((c.bone, c.prop), float(c.co[0, 1]) if len(c.co) else 0.0)
        animation.write(action, owner, found)
        slots[str(clip.slot)] = action
    for (bone, prop), value in extra.items():
        owner.pose.bones[bone][prop] = value
    owner[stored.CLIPS] = slots
    animation.play(owner, slots[str(model.clips[0].slot)])
