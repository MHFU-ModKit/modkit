# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio import cli
from mhfu_studio.harness import flags, selftest
from mhfu_studio.harness.stats import compare_all, load_golden, measure

#: written in the container (apps/studio/docker): there the hash must match exactly
GOLDEN = Path(__file__).parent / "golden" / "selftest.json"


def test_selftest_matches_its_golden(gl: Any) -> None:
    shots = selftest.render(gl)
    stats = {k: measure(v, shots.clear, shots.renderer) for k, v in shots.images.items()}
    errors = [f for f in compare_all(stats, load_golden(GOLDEN)) if f.level != "info"]
    assert errors == []


def test_render_command(
    gl: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    assert cli.main(["render", "selftest", "--golden", str(GOLDEN), "-o", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.png"))) == len(selftest.VIEWS)
    assert "match" in capsys.readouterr().out
