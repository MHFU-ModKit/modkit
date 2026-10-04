# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster viewport: the skinned animal, its bones, its volumes, and a host beside it.

It opens posed, never at bind (`mesh.default_pose`), and frames the posed extent, not the bind
box: a bind pose is a splayed T centred nowhere near the standing animal. The bind extent is
kept as the stable scale the ground and the joint size come from.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import moderngl
import numpy as np
from mhfu_port.model import Clip

from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.shell.camera import Bounds, Lens, Mat
from mhfu_studio.shell.lines import Lines, axes_geometry, bounds_geometry
from mhfu_studio.shell.target import RGBA, SAMPLES
from mhfu_studio.shell.viewport import Viewport, depth_write_off

from .ground import Ground, point_cloud_geometry
from .hitboxes import HitboxOverlay, Palette, Volume
from .mesh import SkinnedMesh, default_pose
from .playback import Playback, pose_at
from .skeleton import SkeletonOverlay

OVERLAYS: tuple[tuple[str, Palette], ...] = (("hitboxes", "part"), ("attacks", "set"))
#: low-chroma dark: a grey-brown monster and a saturated bone overlay both read on it
BACKGROUND: RGBA = (0.102, 0.110, 0.129, 1.0)


def scene_bounds(scene: Scene) -> Bounds:
    """The bind extent: the geometry, or the joints when there is none."""
    parts = [Bounds.of(g.positions) for g in scene.groups if g.n_vertices]
    return Bounds.union(*parts) if parts else Bounds.of(scene.rig.bind_joints)


def joint_size(bind: Bounds) -> float:
    return max(4.0, min(9.0, bind.radius / 160.0))


class Actor:
    """One posed monster: mesh, skeleton, its two volume overlays and its own transport.

    The port and the host reference pose through this one class, so the only difference on
    screen between them is the data."""

    def __init__(self, ctx: moderngl.Context, scene: Scene) -> None:
        self.ctx = ctx
        self.scene = scene
        self.bind_bounds = scene_bounds(scene)
        self.mesh = SkinnedMesh(ctx, scene)
        self.skeleton = SkeletonOverlay(ctx, scene)
        self.skeleton.joint_size = joint_size(self.bind_bounds)
        self.hitboxes: HitboxOverlay | None = None
        self.attacks: HitboxOverlay | None = None
        self.playback = Playback()
        #: the clip the mesh is deformed to; None is bind
        self.clip: Clip | None = None
        self.frame = 0.0
        self.strip_root = False

    def pose(self, clip: Clip | None, frame: float = 0.0) -> None:
        self.clip, self.frame = clip, float(frame)
        p = pose_at(self.scene, clip, frame, strip_root=self.strip_root)
        self.mesh.set_pose(None if clip is None else p)
        self.skeleton.set_positions(p.joints)
        for ov in (self.hitboxes, self.attacks):
            if ov is not None:
                ov.set_pose(p.world)
        if self.hitboxes is not None or self.attacks is not None:
            self.skeleton.set_driven(None if clip is None else clip.driven)

    def repose(self) -> None:
        self.pose(self.clip, self.frame)

    def play(self, clip: Clip | None, frame: float = 0.0) -> None:
        """Binds `clip` to the transport, posed at `frame`."""
        self.playback.set_clip(clip)
        if clip is None:
            self.pose(None)
            return
        self.playback.seek(frame)
        self.pose(clip, self.playback.phase)

    def overlay(
        self, which: str, vols: Sequence[Volume] | None, palette: Palette
    ) -> HitboxOverlay | None:
        """Replaces the `hitboxes` or `attacks` overlay; empty clears it."""
        old = getattr(self, which)
        if old is not None:
            old.release()
        ov = HitboxOverlay(self.ctx, vols, self.scene.rig.n, palette) if vols else None
        setattr(self, which, ov)
        if ov is not None:
            self.repose()
        return ov

    def tick(self, dt: float) -> bool:
        if self.clip is None:
            return False
        before = self.playback.phase
        self.playback.advance(dt)
        if self.playback.phase == before:
            return False
        self.pose(self.clip, self.playback.phase)
        return True

    @property
    def bounds(self) -> Bounds:
        return self.mesh.bounds if self.mesh.bounds.radius > 0 else self.bind_bounds

    def release(self) -> None:
        for o in (self.mesh, self.skeleton, self.hitboxes, self.attacks):
            if o is not None:
                o.release()
        self.hitboxes = self.attacks = None


class Reference(Actor):
    """A second monster beside the port, along the flank axis: the host playing the action's
    own clip. Same rate as the port (the action's), each looping at its own end."""

    GAP = 0.45

    def __init__(self, ctx: moderngl.Context, scene: Scene, beside: Bounds, speed: float) -> None:
        super().__init__(ctx, scene)
        self.playback.speed = speed
        self.visible = True
        span = beside.radius + self.bind_bounds.radius
        self.offset = np.array([span * (1.0 + self.GAP), 0.0, 0.0])
        self.play(*default_pose(scene))

    @property
    def bounds(self) -> Bounds:
        b = super().bounds
        return Bounds(b.lo + self.offset, b.hi + self.offset)

    def model(self) -> Mat:
        m = np.eye(4)
        m[:3, 3] = self.offset
        return m


class MonsterViewport(Viewport):
    """Draws the port (`actor`), the host reference and the scene furniture; every `show_*`
    is a checkbox."""

    def __init__(
        self, ctx: moderngl.Context, size: tuple[int, int] = (1280, 800), samples: int = SAMPLES
    ) -> None:
        super().__init__(ctx, size, samples, Lens())
        self.background = BACKGROUND
        self.show_mesh = True
        self.show_skeleton = True
        self.show_hitboxes = False
        self.show_attacks = False
        #: volumes and bones live inside the animal: drawn through it by default
        self.hitboxes_xray = True
        self.skeleton_xray = True
        self.show_ground = True
        self.show_axes = False
        self.show_bounds = False
        self.show_points = False
        self.wireframe = False
        self.point_size = 2.0
        self.actor: Actor | None = None
        self.reference: Reference | None = None
        self.bind_bounds = Bounds.of(np.zeros((1, 3)))
        #: the host's own volumes for the reference, kept so a reference attached later gets
        #: them: the panels that set them and the one that attaches it run in either order
        self._ref_volumes: dict[str, list[Volume]] = {"hitboxes": [], "attacks": []}
        self._ground = Ground(ctx)
        self._axes = Lines(ctx)
        self._box = Lines(ctx)
        self._points = Lines(ctx, ctx.POINTS)

    # the port

    @property
    def scene(self) -> Scene | None:
        return None if self.actor is None else self.actor.scene

    @property
    def playback(self) -> Playback:
        if self.actor is None:
            raise RuntimeError("no scene in the viewport")
        return self.actor.playback

    @property
    def clip(self) -> Clip | None:
        return None if self.actor is None else self.actor.clip

    @property
    def frame(self) -> float:
        return 0.0 if self.actor is None else self.actor.frame

    @property
    def mesh(self) -> SkinnedMesh | None:
        return None if self.actor is None else self.actor.mesh

    @property
    def skeleton(self) -> SkeletonOverlay | None:
        return None if self.actor is None else self.actor.skeleton

    @property
    def hitboxes(self) -> HitboxOverlay | None:
        return None if self.actor is None else self.actor.hitboxes

    @property
    def attacks(self) -> HitboxOverlay | None:
        return None if self.actor is None else self.actor.attacks

    @property
    def strip_root(self) -> bool:
        return self.actor is not None and self.actor.strip_root

    @strip_root.setter
    def strip_root(self, on: bool) -> None:
        for a in (self.actor, self.reference):
            if a is not None:
                a.strip_root = on
        self.repose()

    def set_scene(self, scene: Scene, *, frame_camera: bool = True, posed: bool = True) -> None:
        """Binds `scene`, posed on `default_pose` unless `posed` is False."""
        self._release_scene()
        self.actor = Actor(self.ctx, scene)
        self.bind_bounds = self.actor.bind_bounds
        self._ground.fit(self.bind_bounds)
        self._axes.set(*axes_geometry(self.bind_bounds.radius * 0.6))
        pts = [g.positions for g in scene.groups if g.n_vertices]
        cloud = np.concatenate(pts) if pts else scene.rig.bind_joints
        self._points.set(*point_cloud_geometry(cloud))
        if posed:
            self.actor.play(*default_pose(scene))
        else:
            self.actor.pose(None)
        if frame_camera:
            self.camera.frame(self.bounds()).look("three")

    def set_pose(self, clip: Clip | None, frame: float = 0.0) -> None:
        if self.actor is not None:
            self.actor.pose(clip, frame)

    def repose(self) -> None:
        for a in (self.actor, self.reference):
            if a is not None:
                a.repose()

    def play_clip(self, clip: Clip | None, frame: float = 0.0) -> None:
        """The port on `clip` at `frame`; the host beside restarts its clip there, in step."""
        if self.actor is not None:
            self.actor.play(clip, frame)
        if self.reference is not None:
            self.reference.play(self.reference.clip, frame)

    def restart(self) -> None:
        """Both animals back to frame 0."""
        for a in (self.actor, self.reference):
            if a is not None:
                a.playback.rewind()
                a.pose(a.clip, 0.0)

    def play_pause(self) -> None:
        """The port's transport; a play from the end of a one-shot restarts both."""
        pb = self.playback
        if not pb.playing and pb.at_end:
            self.restart()
        pb.toggle()

    def tick(self, dt: float) -> bool:
        """Advances both transports `dt` real seconds; whether anything re-posed. The host
        advances whatever the port does: it is most worth watching when the port has no clip."""
        moved = False
        if self.reference is not None and self.actor is not None:
            pb, rp = self.actor.playback, self.reference.playback
            rp.speed, rp.loop, rp.playing = pb.speed, pb.loop, pb.playing
            moved = self.reference.tick(dt)
        if self.actor is not None and self.actor.tick(dt):
            moved = True
        return moved

    # volumes

    def set_hitboxes(self, vols: Iterable[Volume]) -> HitboxOverlay | None:
        """The port's hurtboxes; returns the overlay, whose `orphans` must be shown."""
        return self._set("hitboxes", list(vols), "part")

    def set_attacks(self, vols: Iterable[Volume]) -> HitboxOverlay | None:
        return self._set("attacks", list(vols), "set")

    def _set(self, which: str, vols: list[Volume], palette: Palette) -> HitboxOverlay | None:
        if self.actor is None:
            return None
        ov = self.actor.overlay(which, vols, palette)
        setattr(self, f"show_{which}", ov is not None)
        return ov

    def clear_hitboxes(self) -> None:
        self._clear("hitboxes")

    def clear_attacks(self) -> None:
        self._clear("attacks")

    def _clear(self, which: str) -> None:
        if self.actor is not None:
            self.actor.overlay(which, None, "part")
        setattr(self, f"show_{which}", False)
        self._ref_volumes[which] = []
        if self.reference is not None:
            self.reference.overlay(which, None, "part")

    def set_reference_hitboxes(self, vols: Iterable[Volume] | None) -> HitboxOverlay | None:
        """The host's own hurtboxes on the reference's rig, whatever the port authored."""
        return self._set_reference("hitboxes", vols, "part")

    def set_reference_attacks(self, vols: Iterable[Volume] | None) -> HitboxOverlay | None:
        return self._set_reference("attacks", vols, "set")

    def _set_reference(
        self, which: str, vols: Iterable[Volume] | None, palette: Palette
    ) -> HitboxOverlay | None:
        self._ref_volumes[which] = list(vols or [])
        if self.reference is None:
            return None
        ov = self.reference.overlay(which, self._ref_volumes[which], palette)
        self.sync_focus(which)
        return ov

    def sync_focus(self, which: str = "hitboxes") -> None:
        """The reference shows the same group filter and selection as the port: isolating a
        part on only one of the two animals is worse than not isolating."""
        own = getattr(self.actor, which, None)
        ref = getattr(self.reference, which, None)
        if own is None or ref is None:
            return
        ref.set_selected_group(own.selected_group)
        ref.set_visible(own.visible)

    # the host reference

    def set_reference(self, scene: Scene, *, frame_camera: bool = True) -> Reference:
        """A second monster beside this one; framed on the union so both are on screen."""
        self.clear_reference()
        speed = self.actor.playback.speed if self.actor is not None else Playback().speed
        self.reference = Reference(self.ctx, scene, self.bind_bounds, speed)
        self.reference.strip_root = self.strip_root
        self.reference.repose()
        for which, palette in OVERLAYS:
            if self._ref_volumes[which]:
                self._set_reference(which, self._ref_volumes[which], palette)
        if frame_camera:
            self.camera.frame(self.bounds())
        return self.reference

    def clear_reference(self, *, frame_camera: bool = False) -> None:
        if self.reference is not None:
            self.reference.release()
            self.reference = None
        if frame_camera:
            self.camera.frame(self.bounds())

    def play_reference_clip(self, clip: Clip | None, frame: float = 0.0) -> None:
        if self.reference is not None:
            self.reference.play(clip, frame)

    # selection

    def tag_joints(self, joints: Iterable[int]) -> None:
        if self.actor is not None:
            self.actor.mesh.tag_joints(joints)

    def select_joint(self, joint: int | None) -> None:
        if self.actor is not None:
            self.actor.skeleton.set_selected(joint)

    @property
    def selected_joint(self) -> int | None:
        return None if self.actor is None else self.actor.skeleton.selected

    # the frame

    def bounds(self) -> Bounds:
        """What is on screen now: the posed port, with the reference the union of both."""
        if self.actor is None:
            return self.bind_bounds
        own = self.actor.bounds
        if self.reference is None or not self.reference.visible:
            return own
        return Bounds.union(own, self.reference.bounds)

    def draw_scene(self, mvp: Mat) -> None:
        ctx, a = self.ctx, self.actor
        ref = self.reference if self.reference is not None and self.reference.visible else None
        actors: list[tuple[Actor, Mat]] = [] if a is None else [(a, mvp)]
        if ref is not None:
            actors.append((ref, mvp @ ref.model()))
        if self.show_mesh:
            for act, m in actors:
                act.mesh.render(m, wireframe=self.wireframe)
        if self.show_skeleton:
            with _xray(ctx, self.skeleton_xray):
                for act, m in actors:
                    act.skeleton.render(m)
        if self.show_hitboxes or self.show_attacks:
            with _xray(ctx, self.hitboxes_xray):
                for which in ("hitboxes", "attacks"):
                    if getattr(self, f"show_{which}"):
                        for act, m in actors:
                            ov = getattr(act, which)
                            if ov is not None:
                                ov.render(m)
        if self.show_points:
            self._points.point_size = self.point_size
            self._points.render(mvp)
        if self.show_bounds:
            self._box.set(*bounds_geometry(self.bounds()))
            self._box.render(mvp)
        if self.show_axes:
            self._axes.render(mvp)
        if self.show_ground:
            with depth_write_off(ctx):  # translucent: last, and writing no depth
                self._ground.render(mvp)

    def _release_scene(self) -> None:
        self.clear_reference()
        if self.actor is not None:
            self.actor.release()
            self.actor = None
        self._ref_volumes = {"hitboxes": [], "attacks": []}

    def release(self) -> None:
        self._release_scene()
        for o in (self._ground, self._axes, self._box, self._points):
            o.release()
        super().release()

    def __repr__(self) -> str:
        name = self.scene.name if self.scene is not None else "no scene"
        w, h = self.target.size
        return f"<MonsterViewport {name} {w}x{h} {self.camera!r}>"


class _xray:
    """Depth testing off inside, when `on`."""

    def __init__(self, ctx: moderngl.Context, on: bool) -> None:
        self.ctx, self.on = ctx, on

    def __enter__(self) -> None:
        if self.on:
            self.ctx.disable(self.ctx.DEPTH_TEST)

    def __exit__(self, *exc: object) -> None:
        if self.on:
            self.ctx.enable(self.ctx.DEPTH_TEST)
