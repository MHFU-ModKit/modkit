# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The asset legs of a release run: released tags, unfinished releases, dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from modkit_ci import bundle, cli, registry, release
from modkit_ci.registry import Registry


def _reg(root: Path) -> Registry:
    return registry.load(root)


def _outputs(**tags: str) -> dict[str, str]:
    paths = [p.replace("_", "/") for p in tags]
    return {"paths_released": json.dumps(paths)} | {
        f"{p}--tag_name": tag for p, tag in zip(paths, tags.values(), strict=True)
    }


def _complete(reg: Registry) -> release.ListAssets:
    def assets_of(tag: str) -> list[str]:
        module = next(m for m in reg.modules if bundle.tag(m) == tag)
        return [bundle.docs_name(module), "x.whl", "mhfu_framework-0.1.0.zip"]

    return assets_of


def test_released_modules_at_release_please_tags(registry_root: Path) -> None:
    reg = _reg(registry_root)
    outputs = _outputs(framework="framework-v0.2.0", packages_alpha="alpha-v0.3.1")
    assert release.from_outputs(reg, outputs) == [
        {"module": "framework", "tag": "framework-v0.2.0"},
        {"module": "alpha", "tag": "alpha-v0.3.1"},
    ]


def test_missing_tag_output_fails(registry_root: Path) -> None:
    outputs = {"paths_released": '["framework"]'}
    with pytest.raises(release.ReleaseError, match="framework--tag_name"):
        release.from_outputs(_reg(registry_root), outputs)


def test_no_outputs_no_legs(registry_root: Path) -> None:
    assert release.from_outputs(_reg(registry_root), {}) == []


def test_unfinished_release_is_planned(registry_root: Path) -> None:
    reg = _reg(registry_root)

    def assets_of(tag: str) -> list[str] | None:
        return {"alpha-v0.1.0": ["modkit-docs-alpha-v0.1.0.zip"], "beta-v0.1.0": None}.get(
            tag, _complete(reg)(tag)
        )

    assert release.unfinished(reg, assets_of) == [{"module": "alpha", "tag": "alpha-v0.1.0"}]


def test_plan_unions_without_duplicates(registry_root: Path) -> None:
    reg = _reg(registry_root)
    outputs = _outputs(packages_alpha="alpha-v0.1.0")
    legs = release.plan(reg, outputs, lambda tag: ["modkit-docs-" + tag + ".zip"])
    assert [leg["module"] for leg in legs].count("alpha") == 1
    assert legs[0] == {"module": "alpha", "tag": "alpha-v0.1.0"}
    assert {leg["module"] for leg in legs} >= {"framework", "alpha", "beta"}


def test_plan_of_finished_releases_is_empty(registry_root: Path) -> None:
    reg = _reg(registry_root)
    assert release.plan(reg, {}, _complete(reg)) == []


def test_dispatch_uses_manifest_tag_or_given(registry_root: Path) -> None:
    reg = _reg(registry_root)
    assert release.dispatched(reg, "alpha", None) == [{"module": "alpha", "tag": "alpha-v0.1.0"}]
    assert release.dispatched(reg, "packages/alpha", "alpha-v0.0.9")[0]["tag"] == "alpha-v0.0.9"
    with pytest.raises(release.ReleaseError):
        release.dispatched(reg, "alpha", "beta-v0.1.0")


def test_cli_dispatch_writes_modules(
    registry_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv(bundle.ENV_OUTPUT, str(out))
    monkeypatch.chdir(registry_root)
    assert cli.main(["release-plan", "--module", "alpha"]) == 0
    assert out.read_text() == 'modules=[{"module":"alpha","tag":"alpha-v0.1.0"}]\n'


def test_cli_unknown_module(registry_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(registry_root)
    assert cli.main(["release-plan", "--module", "nope"]) == 1


def test_cli_push_without_outputs(
    registry_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv(bundle.ENV_OUTPUT, str(out))
    monkeypatch.chdir(registry_root)
    monkeypatch.setattr(release, "gh_assets", lambda tag: None)
    assert cli.main(["release-plan", "--outputs", "null"]) == 0
    assert out.read_text() == "modules=[]\n"
