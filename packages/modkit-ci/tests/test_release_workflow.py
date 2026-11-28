# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""release.yml keeps its tokens narrow, builds from the released tag and skips without the App."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from modkit_ci import registry

ROOT = registry.find_root(Path(__file__))
FLOW: dict[str, Any] = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
JOBS: dict[str, Any] = FLOW["jobs"]


def _steps(*names: str, uses: str) -> list[dict[str, Any]]:
    return [s for n in names for s in JOBS[n]["steps"] if s.get("uses", "").startswith(uses)]


def test_every_checkout_drops_the_token() -> None:
    checkouts = _steps(*JOBS, uses="actions/checkout")
    assert len(checkouts) == 4
    assert all(c["with"]["persist-credentials"] is False for c in checkouts)


def test_app_tokens_are_narrow_and_use_the_client_id() -> None:
    tokens = _steps(*JOBS, uses="actions/create-github-app-token")
    assert len(tokens) == 2
    for step in tokens:
        assert "app-id" not in step["with"]
        assert step["with"]["client-id"] == "${{ secrets.RELEASE_APP_CLIENT_ID }}"
        assert step["with"]["private-key"] == "${{ secrets.RELEASE_APP_KEY }}"
    perms = {
        n: {k for k in s["with"] if k.startswith("permission-")}
        for n in ("please", "lock")
        for s in _steps(n, uses="actions/create-github-app-token")
    }
    assert perms["lock"] == {"permission-contents"}
    assert perms["please"] == {
        "permission-contents",
        "permission-pull-requests",
        "permission-issues",
    }


def test_please_holds_no_workflow_permissions() -> None:
    assert JOBS["please"]["permissions"] == {}
    assert "prs" not in JOBS["please"]["outputs"]


def test_skips_without_the_app() -> None:
    for name in ("please",):
        assert "needs.config.outputs.app == 'true'" in JOBS[name]["if"]
    assert "::notice::" in JOBS["config"]["steps"][0]["run"]
    assert JOBS["branches"]["needs"] == "please"


def test_asset_jobs_run_after_a_skipped_please() -> None:
    assert JOBS["plan"]["if"] == "${{ !cancelled() }}"
    for name in ("docs", "assets"):
        cond = JOBS[name]["if"]
        assert "!cancelled()" in cond and "needs.plan.result == 'success'" in cond
        assert "always()" not in cond


def test_legs_build_from_their_tag() -> None:
    for name in ("docs", "assets"):
        assert "include" in JOBS[name]["strategy"]["matrix"]
        (checkout,) = _steps(name, uses="actions/checkout")
        assert checkout["with"]["ref"] == "refs/tags/${{ matrix.tag }}"


def test_docs_leg_syncs_the_docs_group() -> None:
    (setup,) = _steps("docs", uses="./.github/actions/setup")
    assert setup["with"]["sync-args"] == "--group docs"


def test_lock_pushes_with_the_token_in_the_push_step_only() -> None:
    steps = JOBS["lock"]["steps"]
    holders = [s for s in steps if "TOKEN" in s.get("env", {})]
    assert [s["name"] for s in holders] == ["Commit the refreshed lock"]


def test_branch_list_is_deduplicated_and_same_repo() -> None:
    run = JOBS["branches"]["steps"][0]["run"]
    assert "isCrossRepository" in run
    assert "unique" in run


def test_dispatch_has_its_own_group() -> None:
    group = FLOW["concurrency"]["group"]
    assert "inputs.module" in group
    assert FLOW["concurrency"]["cancel-in-progress"] is False


@pytest.mark.parametrize("name", ["docs", "assets"])
def test_upload_token_only_in_the_upload_step(name: str) -> None:
    holders = [s for s in JOBS[name]["steps"] if "GH_TOKEN" in s.get("env", {})]
    assert len(holders) == 1


def test_uploads_use_the_leg_tag() -> None:
    for name in ("docs", "assets"):
        uploads = [s for s in JOBS[name]["steps"] if "gh release upload" in s.get("run", "")]
        assert [u["env"]["TAG"] for u in uploads] == ["${{ matrix.tag }}"]


def test_build_steps_come_from_the_plan() -> None:
    conds = {s.get("if") for s in JOBS["assets"]["steps"]}
    assert {"${{ matrix.make }}", "${{ matrix.gl }}"} <= conds
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "matrix.module ==" not in text and "contains(matrix.module" not in text
