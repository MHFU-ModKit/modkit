# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The real repo: every workspace member is a module or private; real affected cases."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from modkit_ci import affected, registry

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / registry.CONFIG).is_file(), reason="not inside the modkit repo"
)


def members() -> list[Path]:
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["uv"]["workspace"]
    return sorted(
        p for g in globs["members"] for p in ROOT.glob(g) if (p / "pyproject.toml").is_file()
    )


def test_members_are_modules_or_private() -> None:
    reg = registry.load(ROOT)
    module_paths = {m.path for m in reg.modules}
    for path in members():
        rel = path.relative_to(ROOT).as_posix()
        project = tomllib.loads((path / "pyproject.toml").read_text("utf-8"))["project"]
        private = "Private :: Do Not Upload" in project.get("classifiers", [])
        assert (rel in module_paths) != private, f"{rel}: neither module nor private (or both)"


def test_lock_members_match_directories() -> None:
    reg = registry.load(ROOT)
    ws = affected.workspace(reg, ROOT / "uv.lock")
    assert {p for p, _ in ws.values()} == {p.relative_to(ROOT).as_posix() for p in members()}


def test_mhp_formats_dependents() -> None:
    reg = registry.load(ROOT)
    result = affected.compute(
        reg, ["packages/mhp-formats/src/mhp_formats/pac.py"], ROOT / "uv.lock"
    )
    assert not result.all
    assert result.modules == {
        "mhp-formats",
        "mhfu-port",
        "studio",
        "blender",
        "framework",
        "mhfu",
        "hud",
    }


def test_ports_dir_belongs_to_mhfu_port() -> None:
    reg = registry.load(ROOT)
    result = affected.compute(reg, ["ports/x.toml"], ROOT / "uv.lock")
    assert "mhfu-port" in result.modules
    assert result.changed == {"ports/x.toml"}


def test_ci_package_means_all() -> None:
    reg = registry.load(ROOT)
    result = affected.compute(reg, ["packages/modkit-ci/src/modkit_ci/cli.py"], ROOT / "uv.lock")
    assert result.all
    assert {"modkit-ci", "modkit-testing"} <= {u.name for u in result.units}


def test_port_lua_affects_its_users() -> None:
    reg = registry.load(ROOT)
    result = affected.compute(reg, ["framework/lua/lib/mhfu_port.lua"], ROOT / "uv.lock")
    assert not result.all
    assert {"framework", "mhfu", "studio", "mhfu-port", "blender", "hud"} <= result.modules
