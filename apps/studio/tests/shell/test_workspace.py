# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

from mhfu_studio.cli import AREAS
from mhfu_studio.shell import workspace
from mhfu_studio.shell.testing import FakeWorkspace
from mhfu_studio.shell.workspace import Gesture, View, discover, pick, register


def test_registry() -> None:
    register("fake", lambda: FakeWorkspace("fake"))
    try:
        found = discover(AREAS)
        assert found["fake"]().name == "fake" and {"map", "monster"} <= set(found)
    finally:
        workspace.unregister("fake")
    assert "fake" not in workspace.registered()


def test_pick_and_defaults() -> None:
    a, b = FakeWorkspace("a", ".pac"), FakeWorkspace("b", ".toml")
    assert pick([a, b], Path("x.toml")) is b and pick([a, b], Path("x.png")) is None
    view = View((10.0, 20.0), (100, 50), hovered=False, active=False)
    assert a.input(view) is Gesture.NONE and not a.wants_mouse() and not a.animating()
    assert view.mouse(15.0, 25.0) == (5.0, 5.0)
    assert [s.name for s in a.layout()] == ["Left", "Right", "Bottom"]
