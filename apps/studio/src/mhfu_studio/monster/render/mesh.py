# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster: skinned on the CPU through `fk.Skin`, its own TMH textures, one draw per image.

Skinning stays on the CPU so the picture and every headless check run the same maths as
`mhfu_port.fk`; a GLSL skinner would be a second implementation of it.
"""

from __future__ import annotations

from collections.abc import Iterable

import moderngl
import numpy as np
import numpy.typing as npt

from mhfu_studio.monster.core.pose import Pose
from mhfu_studio.monster.core.scene import Clip, Scene
from mhfu_studio.shell.camera import Bounds, Mat, gl_bytes
from mhfu_studio.shell.shaders import HUE, program, uniform

MODE_TEXTURED, MODE_FLAT, MODE_VGROUP = 0, 1, 2
MODES = ("textured", "flat", "vgroup")
#: `isolate`: everything, only the tagged joints' geometry, everything but it
ISOLATE_OFF, ISOLATE_ONLY, ISOLATE_HIDE = 0, 1, 2
#: key light over the left shoulder, a little in front, so a profile is not lit flat
LIGHT = (-0.45, 0.80, 0.40)

MESH_VS = """
#version 330 core
uniform mat4 u_mvp;
in vec3 in_pos;
in vec3 in_normal;
in vec2 in_uv;
in float in_group;
in float in_bone;
out vec3 v_normal;
out vec2 v_uv;
out float v_group;
out float v_bone;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_normal = in_normal;
    v_uv = in_uv;
    v_group = in_group;
    v_bone = in_bone;
}
"""

MESH_FS = (
    """
#version 330 core
uniform sampler2D u_tex;
uniform sampler2D u_bone_tag;
uniform int   u_mode;
uniform int   u_isolate;
uniform int   u_n_bones;
uniform int   u_has_tex;
uniform vec3  u_light;
uniform vec3  u_tint;
uniform float u_alpha;
in vec3 v_normal;
in vec2 v_uv;
in float v_group;
in float v_bone;
out vec4 f_color;
"""
    + HUE
    + """
float bone_tag(float bone) {
    if (bone < 0.0 || u_n_bones <= 0) return 0.0;
    // the texel centre: bone / n sits on a boundary
    return texture(u_bone_tag, vec2((bone + 0.5) / float(u_n_bones), 0.5)).r;
}

void main() {
    float tag = bone_tag(v_bone);
    if (u_isolate == 1 && tag < 0.5) discard;
    if (u_isolate == 2 && tag >= 0.5) discard;
    vec3 base;
    if (u_mode == 2) base = hue(v_group);
    else if (u_mode == 1 || u_has_tex == 0) base = vec3(0.72, 0.72, 0.74);
    else {
        vec4 t = texture(u_tex, v_uv);
        if (t.a < 0.35) discard;  // TMH alpha punches out fins and membranes
        base = t.rgb;
    }
    base *= u_tint;
    if (tag >= 0.5 && u_isolate == 0) base = mix(base, vec3(0.90, 0.13, 0.13), 0.65);
    // two-sided: a monster PAC's winding is not consistent
    float d = abs(dot(normalize(v_normal), normalize(u_light)));
    f_color = vec4(base * (0.35 + 0.65 * d), u_alpha);
}
"""
)


def mesh_program(ctx: moderngl.Context) -> moderngl.Program:
    return program(ctx, MESH_VS, MESH_FS)


def default_pose(scene: Scene) -> tuple[Clip | None, float]:
    """A posed frame to open on, never bind (at bind any skinning looks right): of the looping
    whole-rig clips, those driving the most joints, the median length (the longest is a sleep
    loop, the shortest a twitch), at mid-clip. Native Tigrex: slot 3."""
    pool = (
        [c for c in scene.clips if c.loop and c.whole_rig]
        or [c for c in scene.clips if c.whole_rig]
        or list(scene.clips)
    )
    if not pool:
        return None, 0.0
    most = max(len(c.driven) for c in pool)
    widest = sorted((c for c in pool if len(c.driven) == most), key=lambda c: c.frames)
    clip = widest[(len(widest) - 1) // 2]
    return clip, clip.frames * 0.5


class SkinnedMesh:
    """One vertex buffer for the whole animal (`Scene.merged` order), one index buffer per
    texture: a group averages 19 vertices, so a draw per group would be call overhead."""

    def __init__(self, ctx: moderngl.Context, scene: Scene) -> None:
        self.ctx = ctx
        self.scene = scene
        self.prog = mesh_program(ctx)
        self.mode = MODE_TEXTURED
        self.isolate = ISOLATE_OFF
        self.alpha = 1.0
        self.tint = (1.0, 1.0, 1.0)
        self.light = LIGHT
        #: the deformed extent of the last pose: what the camera frames
        self.bounds = Bounds.of(np.zeros((1, 3)))
        self._skin = scene.merged
        n = len(self._skin.positions)
        self._normals = merged_normals(scene)
        self._textures = _upload_textures(ctx, scene)
        self._batches = _index_batches(ctx, scene)
        self._tags = BoneTags(ctx, scene.rig.n)
        self._dyn = ctx.buffer(reserve=max(n * 6 * 4, 4), dynamic=True)
        self._static = ctx.buffer(static_attributes(scene).tobytes())
        self._vaos = {
            tex: ctx.vertex_array(
                self.prog,
                [
                    (self._dyn, "3f 3f", "in_pos", "in_normal"),
                    (self._static, "2f 1f 1f", "in_uv", "in_group", "in_bone"),
                ],
                ibo,
            )
            for tex, ibo in self._batches.items()
        }
        self.set_pose(None)

    def set_pose(self, pose: Pose | None) -> None:
        """Deform to `pose`, or to bind."""
        if pose is None:
            pos, nrm = self._skin.positions, self._normals
        else:
            pos = self._skin.apply(pose.deform)
            nrm = self._skin.apply_normals(pose.deform, self._normals)
        data = np.concatenate([np.asarray(pos, "f4"), np.asarray(nrm, "f4")], axis=1)
        self._dyn.write(np.ascontiguousarray(data, "f4").tobytes())
        self.bounds = Bounds.of(pos if len(pos) else np.zeros((1, 3)))

    def tag_joints(self, joints: Iterable[int]) -> None:
        """Paint the geometry these joints dominate red; what `isolate` acts on."""
        self._tags.set(joints)

    @property
    def tagged(self) -> tuple[int, ...]:
        return self._tags.joints

    def render(self, mvp: Mat, *, wireframe: bool = False) -> None:
        p = self.prog
        uniform(p, "u_mvp").write(gl_bytes(mvp))
        uniform(p, "u_mode").value = int(self.mode)
        uniform(p, "u_isolate").value = int(self.isolate)
        uniform(p, "u_n_bones").value = int(self.scene.rig.n)
        uniform(p, "u_light").value = tuple(self.light)
        uniform(p, "u_tint").value = tuple(self.tint)
        uniform(p, "u_alpha").value = float(self.alpha)
        uniform(p, "u_tex").value = 0
        uniform(p, "u_bone_tag").value = 1
        self._tags.tex.use(1)
        was = self.ctx.wireframe
        self.ctx.wireframe = bool(wireframe)
        try:
            for tex, vao in self._vaos.items():
                image = None if tex is None else self._textures.get(tex)
                uniform(p, "u_has_tex").value = int(image is not None)
                if image is not None:
                    image.use(0)
                vao.render()
        finally:
            self.ctx.wireframe = was

    def release(self) -> None:
        for vao in self._vaos.values():
            vao.release()
        for ibo in self._batches.values():
            ibo.release()
        for tex in self._textures.values():
            tex.release()
        self._tags.release()
        self._dyn.release()
        self._static.release()
        self._vaos, self._batches, self._textures = {}, {}, {}

    def __repr__(self) -> str:
        return (
            f"<SkinnedMesh {self.scene.name}: {len(self._skin.positions)} verts, "
            f"{len(self._batches)} batches, {len(self._textures)} textures>"
        )


def merged_normals(scene: Scene) -> npt.NDArray[np.float64]:
    """Bind normals in `Scene.merged` order, unit length; +Y for a group with none."""
    out = np.zeros((scene.n_vertices, 3))
    out[:, 1] = 1.0
    for i, g in enumerate(scene.groups):
        if g.normals is None or not g.n_vertices:
            continue
        lo, hi = scene.group_range(i)
        length = np.linalg.norm(g.normals, axis=1, keepdims=True)
        out[lo:hi] = np.divide(
            g.normals, length, out=np.zeros_like(g.normals), where=length > 1e-12
        )
    return out


def static_attributes(scene: Scene) -> npt.NDArray[np.float32]:
    """`(vertices, 4)`: u, v, group, dominant joint. No v-flip: the decoded TMH is top-down
    and uploaded row for row, so GL's t = 0 is the image's top row, as the game's v."""
    out = np.zeros((scene.n_vertices, 4), "f4")
    out[:, 3] = -1.0
    for i, g in enumerate(scene.groups):
        if not g.n_vertices:
            continue
        lo, hi = scene.group_range(i)
        if g.uvs is not None:
            out[lo:hi, :2] = g.uvs
        out[lo:hi, 2] = float(i)
        out[lo:hi, 3] = g.skin.dominant()
    return out


def _upload_textures(ctx: moderngl.Context, scene: Scene) -> dict[int, moderngl.Texture]:
    out = {}
    for i, t in enumerate(scene.textures):
        tex = ctx.texture((t.width, t.height), 4, np.ascontiguousarray(t.rgba, np.uint8).tobytes())
        tex.build_mipmaps()
        tex.repeat_x = tex.repeat_y = True
        tex.anisotropy = 4.0
        out[i] = tex
    return out


def _index_batches(ctx: moderngl.Context, scene: Scene) -> dict[int | None, moderngl.Buffer]:
    """One index buffer per texture position; None holds the untextured groups."""
    parts: dict[int | None, list[npt.NDArray[np.int64]]] = {}
    for i, g in enumerate(scene.groups):
        if g.n_faces:
            lo, _ = scene.group_range(i)
            parts.setdefault(g.texture, []).append(g.triangles.astype(np.int64) + lo)
    return {t: ctx.buffer(np.concatenate(p).astype("i4").tobytes()) for t, p in parts.items()}


class BoneTags:
    """A `(joints, 1)` R8 texture, 255 where a joint is tagged; sampled nearest."""

    def __init__(self, ctx: moderngl.Context, n: int) -> None:
        self.n = max(int(n), 1)
        self.joints: tuple[int, ...] = ()
        self.tex = ctx.texture((self.n, 1), 1, bytes(self.n))
        self.tex.filter = (ctx.NEAREST, ctx.NEAREST)
        self.tex.repeat_x = self.tex.repeat_y = False

    def set(self, joints: Iterable[int]) -> None:
        js = tuple(sorted({int(j) for j in joints if 0 <= int(j) < self.n}))
        if js == self.joints:
            return
        self.joints = js
        buf = np.zeros(self.n, np.uint8)
        buf[list(js)] = 255
        self.tex.write(buf.tobytes())

    def release(self) -> None:
        self.tex.release()
