# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import numpy as np
import pytest
from mhfu_studio.shell import gizmo
from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.testing import fake_imgui
from mhfu_studio.shell.workspace import View

M = np.arange(16, dtype=np.float64).reshape(4, 4)


def test_manipulate_round_trips_the_matrix() -> None:
    with fake_imgui() as fake:
        fake.imguizmo.using = True
        got = gizmo.manipulate(OrbitCamera(), View((0.0, 0.0), (64, 32), True, True), M, "rotate")
        assert np.array_equal(got.matrix, M) and got.using and not got.over
        assert gizmo.m16(M).values == list(M.T.ravel())
        assert gizmo.hot()


def test_real_matrix16_is_column_major() -> None:
    pytest.importorskip("imgui_bundle.imguizmo")
    assert np.array_equal(gizmo.np16(gizmo.m16(M)), M)
