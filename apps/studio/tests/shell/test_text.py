# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import os

from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.text import camera_line, plain


def test_plain() -> None:
    home = os.path.expanduser("~")
    assert plain("\u26a0\ufe0f a \u2192 b") == "[!] a -> b"
    assert plain(f"{home}/x.toml") == "~/x.toml"
    assert plain(f"{os.getcwd()}/y") == "y"


def test_camera_line() -> None:
    assert camera_line(OrbitCamera()).startswith("yaw 40")
