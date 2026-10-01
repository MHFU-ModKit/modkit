# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The ImGuizmo transform gizmo over the viewport, in the camera's row-major matrices.

ImGuizmo wants column-major 16-float matrices; `manipulate` transposes both ways, which is the
one place a silent mistake would send the gizmo off screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from mhfu_studio.shell.camera import Mat, OrbitCamera
from mhfu_studio.shell.workspace import View

if TYPE_CHECKING:
    from imgui_bundle import imguizmo

Operation = Literal["translate", "rotate", "scale"]


@dataclass(frozen=True)
class Manipulation:
    """`matrix` after this frame's drag; `using` while dragging, `over` while hovered."""

    matrix: Mat
    using: bool
    over: bool


def m16(m: Mat) -> imguizmo.im_guizmo.Matrix16:
    """Row-major numpy to ImGuizmo's column-major Matrix16."""
    from imgui_bundle import imguizmo

    flat = np.asarray(m, dtype=np.float64).T.reshape(-1)
    return imguizmo.im_guizmo.Matrix16([float(v) for v in flat])


def np16(m: Any) -> Mat:
    """ImGuizmo's Matrix16 back to row-major numpy."""
    return np.array(m.values, dtype=np.float64).reshape(4, 4).T


def manipulate(
    camera: OrbitCamera,
    view: View,
    matrix: Mat,
    operation: Operation,
    *,
    local: bool = False,
    snap: float | None = None,
) -> Manipulation:
    """Draws the gizmo for `matrix` (world, row-major) and applies this frame's drag to it."""
    from imgui_bundle import imgui, imguizmo

    im = imguizmo.im_guizmo
    w, h = float(view.size[0]), float(view.size[1])
    im.set_orthographic(False)
    im.set_drawlist(imgui.get_window_draw_list())
    im.set_rect(view.origin[0], view.origin[1], w, h)
    op = getattr(im.OPERATION, operation)
    mode = im.MODE.local if local else im.MODE.world
    obj = m16(matrix)
    snap3 = im.Matrix3([snap] * 3) if snap else None
    im.manipulate(m16(camera.view), m16(camera.projection(w / h)), op, mode, obj, None, snap3)
    return Manipulation(np16(obj), bool(im.is_using()), bool(im.is_over()))


def hot() -> bool:
    """Last frame's answer: the gizmo is hovered or dragging."""
    from imgui_bundle import imguizmo

    im = imguizmo.im_guizmo
    return bool(im.is_over() or im.is_using())
