# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Release tags, bundle layouts, wheel commands, the Blender pin and release titles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from modkit_ci import bundle, cli, commits, docs, licences, registry
from modkit_ci.registry import MANIFEST, Module, Registry

LAYOUT = [
    "LICENSES/BSD-3-Clause.txt",
    "LICENSES/MIT.txt",
    "PSP/PLUGINS/mhfu_framework/mhfu_boot.prx",
    "PSP/PLUGINS/mhfu_framework/mhfu_framework.prx",
    "PSP/PLUGINS/mhfu_framework/mods/lib/mhfu_port.lua",
    "sdk/examples/a.lua",
    "sdk/examples/b.lua",
    "sdk/meta/addresses.gen.lua",
    "sdk/meta/mhfu.d.lua",
    "THIRD_PARTY_NOTICES.txt",
]


NOTICES = """
[[component]]
name = "Lib"
source = "https://example.org"
spdx = "BSD-3-Clause"
text = true
libs = ["lib"]
copyright = ["Copyright (c) 2000 Someone"]
"""


def _load(root: Path) -> Registry:
    return registry.load(root)


def _fake_framework(root: Path) -> Path:
    lua = root / "framework" / "lua"
    for rel in ("lib/_prelude.lua", "lib/mhfu_port.lua", "meta/mhfu.d.lua", "meta/notes.txt"):
        (lua / rel).parent.mkdir(parents=True, exist_ok=True)
        (lua / rel).write_text(rel, encoding="utf-8")
    for name in ("a", "b"):
        (lua / "examples").mkdir(exist_ok=True)
        (lua / "examples" / f"{name}.lua").write_text(name, encoding="utf-8")
    (root / "LICENSES").mkdir()
    for spdx in ("MIT", "BSD-3-Clause", "GPL-3.0-or-later"):
        (root / "LICENSES" / f"{spdx}.txt").write_text(spdx, encoding="utf-8")
    notices = root / licences.NOTICES
    notices.parent.mkdir(parents=True)
    notices.write_text(NOTICES, encoding="utf-8")
    build = root / "framework" / "build"
    build.mkdir(parents=True)
    for prx in bundle.PRXS:
        (build / prx).write_bytes(b"\x7fPRX " + prx.encode())
    return build


def _addresses(cmd: Sequence[str], cwd: Path) -> None:
    assert cmd[1:5] == ["-m", "mhfu.addresses", "lua", "-o"]
    Path(cmd[5]).write_text("-- generated\n", encoding="utf-8")


def test_tag_from_manifest(registry_root: Path) -> None:
    manifest = json.loads((registry_root / MANIFEST).read_text())
    manifest.update({"packages/alpha": "1.2.3", "framework": "0.4.0"})
    (registry_root / MANIFEST).write_text(json.dumps(manifest))
    reg = _load(registry_root)
    assert bundle.tag(reg.get("alpha")) == "alpha-v1.2.3"
    assert bundle.tag(reg.get("framework")) == "framework-v0.4.0"
    assert bundle.tag(reg.get("sub")) == "sub-v0.1.0"


def test_resolve_name_or_path(registry_root: Path) -> None:
    reg = _load(registry_root)
    assert bundle.resolve(reg, "beta").path == "apps/beta"
    assert bundle.resolve(reg, "apps/beta").name == "beta"
    with pytest.raises(bundle.BundleError):
        bundle.resolve(reg, "nope")


def test_framework_layout(registry_root: Path, tmp_path: Path) -> None:
    build = _fake_framework(registry_root)
    module = _load(registry_root).get("framework")
    archive = bundle.framework_zip(registry_root, build, module, tmp_path / "out", _addresses)
    assert archive.name == "mhfu_framework-0.1.0.zip"
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == sorted(LAYOUT)
        assert b"Copyright (c) 2000 Someone" in zf.read("THIRD_PARTY_NOTICES.txt")
        assert zf.read("LICENSES/MIT.txt") == b"MIT"
        assert zf.read("sdk/meta/addresses.gen.lua") == b"-- generated\n"
        assert zf.read("sdk/examples/a.lua") == b"a"


def test_framework_zip_is_reproducible(registry_root: Path, tmp_path: Path) -> None:
    build = _fake_framework(registry_root)
    module = _load(registry_root).get("framework")
    first = bundle.framework_zip(registry_root, build, module, tmp_path / "x", _addresses)
    second = bundle.framework_zip(registry_root, build, module, tmp_path / "y", _addresses)
    assert first.read_bytes() == second.read_bytes()


def test_framework_missing_prx(registry_root: Path, tmp_path: Path) -> None:
    build = _fake_framework(registry_root)
    (build / "mhfu_boot.prx").unlink()
    module = _load(registry_root).get("framework")
    with pytest.raises(bundle.BundleError, match="make -C framework"):
        bundle.framework_zip(registry_root, build, module, tmp_path, _addresses)


def test_framework_missing_licence_text(registry_root: Path, tmp_path: Path) -> None:
    build = _fake_framework(registry_root)
    (registry_root / "LICENSES" / "BSD-3-Clause.txt").unlink()
    module = _load(registry_root).get("framework")
    with pytest.raises(bundle.BundleError, match="BSD-3-Clause"):
        bundle.framework_zip(registry_root, build, module, tmp_path, _addresses)


def test_wheel_command(registry_root: Path, tmp_path: Path) -> None:
    (registry_root / "packages/alpha/pyproject.toml").write_text('[project]\nname = "pkg-alpha"\n')
    reg = _load(registry_root)
    cmd = bundle.wheel_command(registry_root, reg.get("alpha"), tmp_path)
    assert cmd == ["uv", "build", "--package", "pkg-alpha", "--wheel", "--out-dir", str(tmp_path)]


def test_assets_by_kind(registry_root: Path, tmp_path: Path) -> None:
    (registry_root / "apps/beta/pyproject.toml").write_text('[project]\nname = "pkg-beta"\n')
    reg = _load(registry_root)
    calls: list[list[str]] = []

    def run(cmd: Sequence[str], cwd: Path) -> None:
        calls.append(list(cmd))
        (tmp_path / "pkg_beta-0.1.0-py3-none-any.whl").write_bytes(b"w")

    made = bundle.assets(registry_root, reg.get("beta"), tmp_path, run=run)
    assert [p.name for p in made] == ["pkg_beta-0.1.0-py3-none-any.whl"]
    assert calls[0][:4] == ["uv", "build", "--package", "pkg-beta"]


def test_notes_only_module_has_no_assets(registry_root: Path, tmp_path: Path) -> None:
    ppsspp = {"ppsspp": {"component": "ppsspp", "release-type": "simple"}}
    config = json.loads((registry_root / registry.CONFIG).read_text())
    config["packages"].update(ppsspp)
    (registry_root / registry.CONFIG).write_text(json.dumps(config))
    manifest = json.loads((registry_root / MANIFEST).read_text()) | {"ppsspp": "0.1.0"}
    (registry_root / MANIFEST).write_text(json.dumps(manifest))
    (registry_root / "ppsspp").mkdir()
    assert bundle.assets(registry_root, _load(registry_root).get("ppsspp"), tmp_path) == []


def test_blender_pin_format() -> None:
    pin = bundle.BLENDER_PIN
    assert re.fullmatch(r"4\.2\.\d+", pin.version)
    assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)
    assert pin.url == (
        f"https://download.blender.org/release/Blender4.2/blender-{pin.version}-linux-x64.tar.xz"
    )


def _tarball(stem: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        body = b"#!/bin/sh\n"
        info = tarfile.TarInfo(f"{stem}/blender")
        info.size, info.mode = len(body), 0o755
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture
def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bundle.ENV_BLENDER, raising=False)
    monkeypatch.setattr(bundle.sys, "platform", "linux")
    monkeypatch.setattr(bundle.os, "uname", lambda: type("U", (), {"machine": "x86_64"})())


@pytest.mark.usefixtures("_linux")
def test_blender_download_verified(tmp_path: Path) -> None:
    data = _tarball("blender-4.2.0-linux-x64")
    pin = bundle.Pin("4.2.0", hashlib.sha256(data).hexdigest())
    urls: list[str] = []

    def fetch(url: str, dest: Path) -> None:
        urls.append(url)
        dest.write_bytes(data)

    binary = bundle.blender_binary(pin, tmp_path, fetch)
    assert binary == tmp_path / "4.2.0" / "blender-4.2.0-linux-x64" / "blender"
    assert binary.is_file()
    assert urls == [pin.url]
    bundle.blender_binary(pin, tmp_path, fetch)  # cached
    assert len(urls) == 1


@pytest.mark.usefixtures("_linux")
def test_blender_hash_mismatch(tmp_path: Path) -> None:
    pin = bundle.Pin("4.2.0", "0" * 64)
    with pytest.raises(bundle.BundleError, match="sha256"):
        bundle.blender_binary(pin, tmp_path, lambda url, dest: dest.write_bytes(b"x"))
    assert not (tmp_path / "4.2.0").exists()


@pytest.mark.usefixtures("_linux")
def test_blender_pin_rejects_other_bytes(tmp_path: Path) -> None:
    fake = _tarball(bundle.BLENDER_PIN.stem)
    with pytest.raises(bundle.BundleError, match=bundle.BLENDER_PIN.sha256):
        bundle.blender_binary(cache=tmp_path, fetch=lambda url, dest: dest.write_bytes(fake))
    assert not (tmp_path / bundle.BLENDER_PIN.version).exists()


def test_blender_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(bundle.ENV_BLENDER, "/opt/blender")
    assert bundle.blender_binary(cache=tmp_path) == Path("/opt/blender")


def test_blender_zip(registry_root: Path, tmp_path: Path) -> None:
    reg = _load(registry_root)
    module = reg.get("beta")
    calls: list[list[str]] = []

    def run(cmd: Sequence[str], cwd: Path) -> None:
        calls.append(list(cmd))
        dist = registry_root / module.path / "dist"
        dist.mkdir()
        (dist / "ext-0.1.0.zip").write_bytes(b"z")

    out = tmp_path / "out"
    made = bundle.blender_zip(registry_root, module, out, run, Path("/x/blender"))
    assert made == out / "ext-0.1.0.zip"
    assert calls[0][1:] == [
        str(registry_root / "apps/beta/build.py"),
        "build",
        "--blender",
        "/x/blender",
    ]


def test_docs_zip_builds_in_process(
    registry_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load(registry_root).get("alpha")
    seen: list[tuple[Path, Path]] = []

    def build(root: Path, out: Path, zip_path: Path | None = None) -> int:
        assert zip_path is not None
        seen.append((root, zip_path))
        zip_path.write_bytes(b"z")
        return 0

    monkeypatch.setattr(docs, "build", build)
    made = bundle.docs_zip(registry_root, module, tmp_path)
    assert made.name == "modkit-docs-alpha-v0.1.0.zip"
    assert seen == [(registry_root, made)]


def test_docs_zip_failure(
    registry_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load(registry_root).get("alpha")
    monkeypatch.setattr(docs, "build", lambda root, out, zip_path=None: 1)
    with pytest.raises(bundle.BundleError, match="docs build failed"):
        bundle.docs_zip(registry_root, module, tmp_path)


def test_complete_by_kind(registry_root: Path) -> None:
    reg = _load(registry_root)
    alpha, framework, beta = reg.get("alpha"), reg.get("framework"), reg.get("beta")
    docs_alpha = bundle.docs_name(alpha)
    assert not bundle.complete(alpha, [docs_alpha])
    assert bundle.complete(alpha, [docs_alpha, "alpha-0.1.0-py3-none-any.whl"])
    assert not bundle.complete(alpha, ["alpha-0.1.0-py3-none-any.whl"])
    assert not bundle.complete(framework, [bundle.docs_name(framework)])
    assert bundle.complete(framework, [bundle.docs_name(framework), "mhfu_framework-0.1.0.zip"])
    assert not bundle.complete(framework, [bundle.docs_name(framework), "mhfu_framework-0.0.9.zip"])
    assert bundle.complete(beta, [bundle.docs_name(beta), "x.whl"])


def test_cli_writes_github_output(
    registry_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "gh_output"
    monkeypatch.setenv(bundle.ENV_OUTPUT, str(output))
    monkeypatch.chdir(registry_root)
    ppsspp = {"component": "ppsspp", "release-type": "simple"}
    config = json.loads((registry_root / registry.CONFIG).read_text())
    config["packages"]["ppsspp"] = ppsspp
    (registry_root / registry.CONFIG).write_text(json.dumps(config))
    manifest = json.loads((registry_root / MANIFEST).read_text()) | {"ppsspp": "2.0.1"}
    (registry_root / MANIFEST).write_text(json.dumps(manifest))
    (registry_root / "ppsspp").mkdir()
    assert cli.main(["bundle", "ppsspp", "--out", str(tmp_path / "out")]) == 0
    assert output.read_text() == "tag=ppsspp-v2.0.1\n"


def test_cli_unknown_module(
    registry_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(registry_root)
    monkeypatch.delenv(bundle.ENV_OUTPUT, raising=False)
    assert cli.main(["bundle", "nope", "--out", str(tmp_path)]) == 1


def _title(config: dict[str, object], module: Module) -> str:
    """The title release-please renders: pattern with component (space first) and version."""
    pattern = str(config["pull-request-title-pattern"])
    return pattern.replace("${component}", f" {module.name}").replace("${version}", "0.2.0")


def test_release_titles_pass_the_commit_check() -> None:
    root = registry.find_root(Path(__file__))
    reg = registry.load(root)
    config = json.loads((root / registry.CONFIG).read_text())
    assert config["separate-pull-requests"] is True
    for module in reg.modules:
        title = _title(config, module)
        assert title == f"chore(repo): release {module.name} 0.2.0"
        assert commits.check(title, reg.scopes(), ci=True, registry=reg) == []
