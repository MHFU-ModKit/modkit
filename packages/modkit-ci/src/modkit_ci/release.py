# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which releases `release.yml` builds assets for, each with the tag it checks out."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Final

from modkit_ci import bundle, registry
from modkit_ci.registry import Registry

PATHS_KEY: Final = "paths_released"
TAG_SUFFIX: Final = "--tag_name"

# the names of the release's assets, or None when no such release exists
ListAssets = Callable[[str], list[str] | None]


class ReleaseError(RuntimeError):
    """The release-please outputs or the release listing are unusable."""


def leg(module: str, tag: str) -> dict[str, str]:
    """One matrix leg of the asset jobs."""
    return {"module": module, "tag": tag}


def from_outputs(reg: Registry, outputs: Mapping[str, str]) -> list[dict[str, str]]:
    """The legs for the modules release-please released, at its own tags."""
    try:
        paths = json.loads(outputs.get(PATHS_KEY) or "[]")
    except json.JSONDecodeError as err:
        raise ReleaseError(f"{PATHS_KEY}: {err}") from err
    legs = []
    for path in paths:
        module = bundle.resolve(reg, path)
        name = outputs.get(f"{path}{TAG_SUFFIX}")
        if not name:
            raise ReleaseError(f"release-please gave no {path}{TAG_SUFFIX}")
        legs.append(leg(module.name, name))
    return legs


def unfinished(reg: Registry, assets_of: ListAssets) -> list[dict[str, str]]:
    """The legs of modules whose release at the manifest version lacks assets."""
    legs = []
    for module in reg.modules:
        names = assets_of(bundle.tag(module))
        if names is not None and not bundle.complete(module, names):
            legs.append(leg(module.name, bundle.tag(module)))
    return legs


def dispatched(reg: Registry, name: str, tag: str | None) -> list[dict[str, str]]:
    """The one leg of a manual re-run: the manifest's tag unless `tag` is given."""
    module = bundle.resolve(reg, name)
    chosen = tag or bundle.tag(module)
    if not chosen.startswith(f"{module.name}-v"):
        raise ReleaseError(f"tag {chosen!r} is not a {module.name} release")
    return [leg(module.name, chosen)]


def plan(reg: Registry, outputs: Mapping[str, str], assets_of: ListAssets) -> list[dict[str, str]]:
    """Released modules plus the unfinished ones, one leg per module, released tags first."""
    legs: dict[str, dict[str, str]] = {}
    for entry in [*from_outputs(reg, outputs), *unfinished(reg, assets_of)]:
        legs.setdefault(entry["module"], entry)
    return list(legs.values())


def gh_assets(tag: str) -> list[str] | None:
    """The asset names of the GitHub release `tag`; None when there is no such release."""
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets", "--jq", "[.assets[].name]"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return list(json.loads(proc.stdout))
    if "release not found" in proc.stderr:
        return None
    raise ReleaseError(f"gh release view {tag}: {proc.stderr.strip()}")


def _cmd(args: argparse.Namespace) -> int:
    try:
        reg = registry.load(registry.find_root())
        if args.module:
            legs = dispatched(reg, args.module, args.tag)
        else:
            outputs = json.loads(args.outputs or "{}") or {}
            legs = plan(reg, outputs, gh_assets)
    except (ReleaseError, bundle.BundleError, registry.RegistryError, json.JSONDecodeError) as err:
        print(f"release: {err}", file=sys.stderr)
        return 1
    bundle.write_output("modules", json.dumps(legs, separators=(",", ":")))
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `release-plan` command."""
    p = sub.add_parser("release-plan", help="the asset legs of a release run, as JSON")
    p.add_argument("--outputs", help="release-please-action's outputs as JSON (push)")
    p.add_argument("--module", help="module name or path to re-run (dispatch)")
    p.add_argument("--tag", help="tag of the dispatched module, default the manifest's")
    p.set_defaults(func=_cmd)
