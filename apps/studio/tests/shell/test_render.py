# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from typing import Any

import moderngl
import numpy as np
import pytest
from mhfu_studio.harness import selftest
from mhfu_studio.shell.camera import Mat
from mhfu_studio.shell.context import standalone
from mhfu_studio.shell.lines import Lines, tinted
from mhfu_studio.shell.shaders import LINE_FS, LINE_VS, line_program, program, uniform
from mhfu_studio.shell.target import Target, write_png
from mhfu_studio.shell.viewport import Viewport, depth_write_off

RED = (1.0, 0.0, 0.0, 1.0)


class Upper(Viewport):
    """A red triangle over the upper half of clip space."""

    def __init__(self, ctx: moderngl.Context, samples: int = 0) -> None:
        super().__init__(ctx, (64, 32), samples)
        self.tri = Lines(ctx, ctx.TRIANGLES)
        self.tri.set([(-1, 0.1, 0), (1, 0.1, 0), (0, 1, 0)], RED)

    def draw_scene(self, mvp: Mat) -> None:
        self.tri.render(np.eye(4))

    def release(self) -> None:
        self.tri.release()
        super().release()


@pytest.mark.parametrize("samples", [0, 4])
def test_read_is_upright(gl: Any, samples: int) -> None:
    with Upper(gl, samples) as vp:
        vp.draw()
        img = vp.target.read()
    assert img.shape == (32, 64, 4)
    assert img[2, 32, 0] == 255 and img[29, 32, 0] < 40


def test_draw_restores_the_bound_framebuffer(gl: Any) -> None:
    other = Target(gl, (8, 8), 0)
    with Upper(gl) as vp:
        other.use()
        bound = gl.fbo
        vp.draw()
        assert gl.fbo is bound
        assert gl.viewport == (0, 0, 8, 8)
    other.release()


def test_resize_and_release(gl: Any) -> None:
    t = Target(gl, (0, 0), 64)
    assert t.size == (1, 1) and t.samples <= 64
    assert t.resize((10, 4)) and not t.resize((10, 4))
    t.release()
    with pytest.raises(RuntimeError):
        _ = t.texture


def test_lines(gl: Any) -> None:
    b = Lines(gl)
    with pytest.raises(ValueError):
        b.set(np.zeros((3, 3)), np.zeros((2, 4)))
    b.set(*tinted(np.zeros((4, 3)), RED))
    assert b.count == 4
    b.clear()
    b.render(np.eye(4))
    b.release()


def test_program_cache(gl: Any) -> None:
    assert line_program(gl) is program(gl, LINE_VS, LINE_FS)
    with pytest.raises(KeyError):
        uniform(line_program(gl), "u_nothing")
    with pytest.raises(RuntimeError, match="compile"):
        program(gl, LINE_VS, "#version 330 core\nvoid main() { nope; }")


def test_depth_write_off(gl: Any) -> None:
    with depth_write_off(gl):
        assert not gl.depth_mask
    assert gl.depth_mask


def test_selftest_draws(gl: Any, tmp_path: Any) -> None:
    shots = selftest.render(gl, (160, 120), 4)
    bg = np.round(np.array(shots.clear[:3]) * 255)
    for name, img in shots.images.items():
        drawn = (np.abs(img[:, :, :3] - bg).max(axis=2) > 12).mean()
        assert 0.05 < drawn < 0.9, name
    assert not np.array_equal(shots.images["front"], shots.images["top"])
    assert write_png(tmp_path / "a" / "b.png", shots.images["top"]).stat().st_size > 100


def test_standalone_hands_the_context_back(gl: Any) -> None:
    with standalone() as other:
        assert other is not gl
    with Upper(gl) as vp:
        vp.draw()
        assert vp.target.read()[2, 32, 0] == 255
