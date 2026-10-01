# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The section's visible mesh, textured and lit by its vertex colour.

One interleaved buffer for the section (position, UV, colour, group ordinal) and one index
batch per (texture slot, backdrop). The backdrop is a skybox pass: first, depth test off, so
it can never stand in front of the section. Slots whose image carries alpha are drawn last
with depth writes off. Per-group state is an R8 texture, the per-vertex highlight a small
dynamic buffer; neither touches the static geometry.
"""

from __future__ import annotations

from collections.abc import Iterable

import moderngl
import numpy as np

from mhfu_studio.shell.camera import Bounds, Mat, gl_bytes
from mhfu_studio.shell.shaders import uniform

from ..core.scene import Array, Key, MapScene
from .shaders import mesh_program

MODES = ("textured", "vertex colour", "texture only", "by group", "flat")
FLAG_HIDDEN, FLAG_BACKDROP, FLAG_SELECTED = 1, 2, 4
HL_NONE, HL_HOVER, HL_SELECTED = 0.0, 1.0, 2.0
FOCUS = (3, 97)
"""Percentiles of the near-field vertex cloud the camera frames: where the detail is."""
Batch = tuple[int | None, bool]
"""(texture slot, backdrop)."""
_STRIDE = 10


class StageMesh:
    def __init__(self, ctx: moderngl.Context, scene: MapScene) -> None:
        self.ctx = ctx
        self.scene = scene
        self.prog = mesh_program(ctx)
        self.mode = 0
        #: texture x vertex colour; 1.0 a plain modulate, the PSP's "double" would be 2.0
        self.gain = 1.0
        self.alpha = 1.0
        self.backdrop_alpha = 1.0
        self.alpha_cut = 0.5
        self.show_backdrop = True
        groups = [g for g in scene.groups if g.n_vertices]
        self._ordinal = {g.key: i for i, g in enumerate(groups)}
        self._range: dict[Key, tuple[int, int]] = {}
        at = 0
        for g in groups:
            self._range[g.key] = (at, at + g.n_vertices)
            at += g.n_vertices
        self.n_vertices = at
        cpu = np.zeros((max(at, 1), _STRIDE), np.float32)
        for g in groups:
            self._fill(cpu, g.key)
            lo, hi = self._range[g.key]
            cpu[lo:hi, 9] = float(self._ordinal[g.key])
        self._cpu = cpu
        self._static = ctx.buffer(cpu.tobytes())
        self._hl = np.zeros(max(at, 1), np.float32)
        self._flags = ctx.buffer(self._hl.tobytes(), dynamic=True)
        self._textures = _upload(ctx, scene)
        self._alpha_slots = _alpha_slots(scene)
        self._vaos: dict[Batch, moderngl.VertexArray] = {}
        self._ibos: dict[Batch, moderngl.Buffer] = {}
        self._build_batches()
        self._gflags = np.zeros(max(len(groups), 1), np.uint8)
        for g in groups:
            if g.backdrop:
                self._gflags[self._ordinal[g.key]] |= FLAG_BACKDROP
        self._gtex = ctx.texture((len(self._gflags), 1), 1, self._gflags.tobytes())
        self._gtex.filter = (ctx.NEAREST, ctx.NEAREST)
        self._gtex.repeat_x = self._gtex.repeat_y = False
        near = [g for g in groups if not g.backdrop] or groups
        zero = Bounds.of(np.zeros((1, 3)))
        self.bounds = Bounds.union(*[Bounds.of(g.positions) for g in near]) if near else zero
        self.full_bounds = (
            Bounds.union(*[Bounds.of(g.positions) for g in groups]) if groups else self.bounds
        )
        if near:
            cloud = np.concatenate([g.positions for g in near])
            lo, hi = (np.percentile(cloud, q, axis=0) for q in FOCUS)
            self.focus = Bounds(lo, hi)
        else:
            self.focus = self.bounds

    def _fill(self, cpu: Array, key: Key) -> None:
        lo, hi = self._range[key]
        g = self.scene.group(*key)
        cpu[lo:hi, 0:3] = g.positions
        if g.uvs is not None:  # no v-flip: the bank is uploaded top row first, as the game's v
            cpu[lo:hi, 3:5] = g.uvs
        cpu[lo:hi, 5:9] = 1.0 if g.colours is None else g.colours.astype(np.float32) / 255.0

    def _build_batches(self) -> None:
        self._release_batches()
        parts: dict[Batch, list[Array]] = {}
        for key, (lo, _) in self._range.items():
            g = self.scene.group(*key)
            if g.n_faces:
                parts.setdefault((g.texture, g.backdrop), []).append(
                    g.triangles.astype(np.int64) + lo
                )
        for batch, ps in parts.items():
            ibo = self.ctx.buffer(np.concatenate(ps).astype("i4").tobytes())
            self._ibos[batch] = ibo
            self._vaos[batch] = self.ctx.vertex_array(
                self.prog,
                [
                    (self._static, "3f 2f 4f 1f", "in_pos", "in_uv", "in_color", "in_group"),
                    (self._flags, "1f", "in_flag"),
                ],
                ibo,
            )

    def _release_batches(self) -> None:
        for vao in self._vaos.values():
            vao.release()
        for ibo in self._ibos.values():
            ibo.release()
        self._vaos, self._ibos = {}, {}

    # per-group state

    def range_of(self, key: Key) -> tuple[int, int]:
        return self._range[key]

    def _set_gflag(self, key: Key, bit: int, on: bool) -> None:
        i = self._ordinal.get(key)
        if i is None:
            return
        v = int(self._gflags[i])
        v = (v | bit) if on else (v & ~bit & 0xFF)
        if v != self._gflags[i]:
            self._gflags[i] = v
            self._gtex.write(self._gflags.tobytes())

    def hide_group(self, key: Key, hidden: bool) -> None:
        self._set_gflag(key, FLAG_HIDDEN, hidden)

    def is_hidden(self, key: Key) -> bool:
        i = self._ordinal.get(key)
        return bool(i is not None and self._gflags[i] & FLAG_HIDDEN)

    def select_group(self, key: Key | None) -> None:
        """Tints one whole group, or none."""
        for k in self._ordinal:
            self._set_gflag(k, FLAG_SELECTED, k == key)

    # live edits

    def _upload(self, key: Key) -> None:
        lo, hi = self._range[key]
        self._static.write(self._cpu[lo:hi].tobytes(), offset=lo * _STRIDE * 4)

    def update_positions(self, key: Key) -> None:
        """One group's positions from the scene, after a preview; a resized group waits for
        `rebuild`."""
        lo, hi = self._range[key]
        g = self.scene.group(*key)
        if g.n_vertices == hi - lo:
            self._cpu[lo:hi, 0:3] = g.positions
            self._upload(key)

    def update_group(self, key: Key) -> None:
        """Positions, UVs and colours: a `pack` may rewrite any of them."""
        lo, hi = self._range[key]
        if self.scene.group(*key).n_vertices == hi - lo:
            self._fill(self._cpu, key)
            self._upload(key)

    def sync(self, dirty: set[Key]) -> None:
        """Uploads every group in `dirty` (a session's) and clears it."""
        for key in list(dirty):
            if key in self._range:
                self.update_positions(key)
        dirty.clear()

    def rebuild(self, subs: Iterable[int]) -> None:
        """After a re-decode: those subs' groups whole, and the batches (a `pack` changes the
        faces, a `material` op the texture that draws them)."""
        subs = set(subs)
        for key in self._range:
            if key[0] in subs:
                self.update_group(key)
        self._build_batches()

    def reload_textures(self) -> None:
        for tex in self._textures.values():
            tex.release()
        self._textures = _upload(self.ctx, self.scene)
        self._alpha_slots = _alpha_slots(self.scene)
        self._build_batches()

    # per-vertex highlight

    def clear_highlight(self, kind: float | None = None) -> None:
        if kind is None:
            self._hl[:] = HL_NONE
        else:
            self._hl[self._hl == kind] = HL_NONE
        self._flags.write(self._hl.tobytes())

    def highlight(
        self, key: Key, vertices: Iterable[int], kind: float, *, replace: bool = True
    ) -> None:
        """Marks vertices of a group HL_HOVER or HL_SELECTED; a selection outranks a hover."""
        if replace:
            self._hl[self._hl == kind] = HL_NONE
        lo, _ = self._range[key]
        ids = lo + np.asarray(list(vertices), dtype=np.int64)
        if len(ids):
            cur = self._hl[ids]
            self._hl[ids] = np.maximum(cur, kind) if kind == HL_HOVER else kind
        self._flags.write(self._hl.tobytes())

    # drawing

    def render(self, mvp: Mat, *, wireframe: bool = False) -> None:
        p, ctx = self.prog, self.ctx
        uniform(p, "u_mvp").write(gl_bytes(mvp))
        uniform(p, "u_mode").value = int(self.mode)
        uniform(p, "u_gain").value = float(self.gain)
        uniform(p, "u_alpha").value = float(self.alpha)
        uniform(p, "u_backdrop_alpha").value = self.backdrop_alpha if self.show_backdrop else 0.0
        uniform(p, "u_alpha_cut").value = float(self.alpha_cut)
        uniform(p, "u_n_groups").value = len(self._gflags)
        uniform(p, "u_tex").value = 0
        uniform(p, "u_group_flags").value = 1
        self._gtex.use(1)
        was = ctx.wireframe
        ctx.wireframe = wireframe
        try:
            keys = list(self._vaos)
            opaque = [k for k in keys if not k[1] and k[0] not in self._alpha_slots]
            blended = [k for k in keys if not k[1] and k[0] in self._alpha_slots]
            backdrop = [k for k in keys if k[1]] if self.show_backdrop else []
            if backdrop:
                ctx.disable(ctx.DEPTH_TEST)
                ctx.depth_mask = False  # type: ignore[attr-defined]  # not in the stubs
                for k in backdrop:
                    self._draw(k)
                ctx.depth_mask = True  # type: ignore[attr-defined]
                ctx.enable(ctx.DEPTH_TEST)
            for k in opaque:
                self._draw(k)
            if blended:
                ctx.depth_mask = False  # type: ignore[attr-defined]
                for k in blended:
                    self._draw(k)
                ctx.depth_mask = True  # type: ignore[attr-defined]
        finally:
            ctx.wireframe = was

    def _draw(self, batch: Batch) -> None:
        image = self._textures.get(batch[0]) if batch[0] is not None else None
        uniform(self.prog, "u_has_tex").value = int(image is not None)
        if image is not None:
            image.use(0)
        self._vaos[batch].render()

    def gl_texture(self, slot: int) -> moderngl.Texture | None:
        """The uploaded texture of a bank slot, for a thumbnail."""
        return self._textures.get(slot)

    def release(self) -> None:
        self._release_batches()
        for tex in self._textures.values():
            tex.release()
        for o in (self._gtex, self._static, self._flags):
            o.release()
        self._textures = {}

    def __repr__(self) -> str:
        n, batches = self.n_vertices, len(self._vaos)
        return f"<StageMesh st{self.scene.stage:03d}: {n} verts, {batches} batches>"


def _upload(ctx: moderngl.Context, scene: MapScene) -> dict[int, moderngl.Texture]:
    out = {}
    for t in scene.textures:
        tex = ctx.texture((t.width, t.height), 4, np.ascontiguousarray(t.rgba, np.uint8).tobytes())
        tex.build_mipmaps()
        tex.repeat_x = tex.repeat_y = True
        tex.anisotropy = 4.0
        out[t.index] = tex
    return out


def _alpha_slots(scene: MapScene) -> set[int]:
    """Slots whose image has a transparent pixel."""
    return {t.index for t in scene.textures if (t.rgba[:, :, 3] < 250).any()}
