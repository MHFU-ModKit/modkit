# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Registry loading, validation and ownership."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from modkit_ci import cli, registry
from modkit_ci.registry import CONFIG, MANIFEST, REPO, Module, RegistryError

Edit = Callable[[dict[str, Any]], None]


def edited(root: Path, edit: Edit) -> Path:
    cfg = json.loads((root / CONFIG).read_text())
    edit(cfg["packages"])
    (root / CONFIG).write_text(json.dumps(cfg))
    return root


def test_load(registry_root: Path) -> None:
    reg = registry.load(registry_root)
    assert [m.name for m in reg.modules] == ["framework", "alpha", "sub", "beta"]
    alpha = reg.get("alpha")
    assert alpha.paths == ("packages/alpha", "data")
    assert alpha.version == "0.1.0"
    assert alpha.api == ("python",)
    assert reg.get("beta").qt
    assert reg.get("framework").depends == ("alpha",)
    assert reg.scopes() == ("framework", "alpha", "sub", "beta", REPO)


def test_get_unknown(registry_root: Path) -> None:
    with pytest.raises(KeyError):
        registry.load(registry_root).get("nope")


def test_tests_and_packages(registry_root: Path) -> None:
    alpha = registry.load(registry_root).get("alpha")
    assert alpha.tests(registry_root) == registry_root / "packages/alpha/tests"
    assert alpha.packages(registry_root) == ()
    for name in ("zeta", "alpha_pkg"):
        init = registry_root / "packages/alpha/src" / name / "__init__.py"
        init.parent.mkdir(parents=True)
        init.write_text("")
    assert alpha.packages(registry_root) == ("alpha_pkg", "zeta")


def test_find_root(registry_root: Path) -> None:
    deep = registry_root / "packages/alpha"
    assert registry.find_root(deep) == registry_root.resolve()
    with pytest.raises(RegistryError):
        registry.find_root(registry_root.parent.parent)


def test_load_uses_find_root(registry_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(registry_root / "apps")
    assert registry.load().root == registry_root.resolve()


def test_real_repo_loads() -> None:
    reg = registry.load(registry.find_root(Path(__file__)))
    assert [m.name for m in reg.modules] == [
        "framework",
        "mhfu",
        "mhfu-port",
        "mhp-formats",
        "ppsspp-debug",
        "studio",
        "hud",
        "blender",
        "ppsspp",
    ]
    assert reg.get("framework").depends == ("mhfu", "mhfu-port")
    assert {s.name for s in reg.get("mhfu").schemas} == {"brain-file"}


def who(module: Module | None) -> str | None:
    return module.name if module else None


def test_owner_longest_prefix(registry_root: Path) -> None:
    reg = registry.load(registry_root)
    assert who(reg.owner("packages/alpha/src/x.py")) == "alpha"
    assert who(reg.owner("packages/alpha/sub/x.py")) == "sub"
    assert who(reg.owner("data/file.toml")) == "alpha"
    assert who(reg.owner("framework")) == "framework"


def test_owner_needs_a_whole_segment(registry_root: Path) -> None:
    reg = registry.load(registry_root)
    assert reg.owner("packages/alphabet/x.py") is None
    assert reg.owner("database/x") is None
    assert reg.owner("README.md") is None


def test_release_owner(registry_root: Path) -> None:
    reg = registry.load(registry_root)
    assert reg.release_owner("data/file.toml") is None  # extra paths are not release paths
    assert who(reg.release_owner("packages/alpha/sub/x.py")) == "sub"
    assert who(reg.release_owner("packages/alpha/x.py")) == "alpha"
    assert reg.release_owner("README.md") is None
    assert reg.release_owner("pyproject.toml") is None


def _set(path: str, key: str, value: Any) -> Edit:
    def edit(pk: dict[str, Any]) -> None:
        pk[path][key] = value

    return edit


def _x(path: str, key: str, value: Any) -> Edit:
    def edit(pk: dict[str, Any]) -> None:
        pk[path].setdefault("x-modkit", {})[key] = value

    return edit


def _drop(path: str, key: str) -> Edit:
    def edit(pk: dict[str, Any]) -> None:
        del pk[path][key]

    return edit


def _rm_manifest(root: Path) -> None:
    manifest = json.loads((root / MANIFEST).read_text())
    del manifest["apps/beta"]
    (root / MANIFEST).write_text(json.dumps(manifest))


CASES = {
    "missing component": _drop("apps/beta", "component"),
    "duplicate component": _set("apps/beta", "component", "alpha"),
    "component repo": _set("apps/beta", "component", REPO),
    "bad release type": _set("apps/beta", "release-type", "node"),
    "unknown key": _x("apps/beta", "colour", "red"),
    "unknown dependency": _x("apps/beta", "depends", ["ghost"]),
    "api lua without lua": _x("apps/beta", "api", ["lua"]),
    "unknown api": _x("apps/beta", "api", ["rust"]),
    "path not a dir": _x("apps/beta", "paths", ["nowhere"]),
    "lua dir missing": _x("apps/beta", "lua", ["nowhere"]),
    "schema without load": _x("apps/beta", "schemas", [{"name": "s", "corpus": []}]),
    "qt not a bool": _x("apps/beta", "qt", "yes"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_invalid(registry_root: Path, name: str) -> None:
    with pytest.raises(RegistryError):
        registry.load(edited(registry_root, CASES[name]))


def test_python_without_pyproject(registry_root: Path) -> None:
    (registry_root / "apps/beta/pyproject.toml").unlink()
    with pytest.raises(RegistryError, match="pyproject"):
        registry.load(registry_root)


def test_key_path_not_a_dir(registry_root: Path) -> None:
    shutil.rmtree(registry_root / "apps/beta")
    with pytest.raises(RegistryError, match="not a directory"):
        registry.load(registry_root)


def test_manifest_keys_differ(registry_root: Path) -> None:
    _rm_manifest(registry_root)
    with pytest.raises(RegistryError, match="disagree"):
        registry.load(registry_root)


def test_missing_config(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        registry.load(tmp_path)


def test_cli_json(registry_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.chdir(registry_root)
    assert cli.main(["registry", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in rows] == ["framework", "alpha", "sub", "beta"]


def test_cli_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["registry"]) == 1
