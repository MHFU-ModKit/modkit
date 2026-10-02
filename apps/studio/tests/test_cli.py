# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio import cli


@pytest.mark.parametrize("group", ["render", "port", "map"])
def test_groups_exist(group: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main([group, "--help"])
    assert e.value.code == 0
    assert "ACTION" in capsys.readouterr().out


def test_open_args() -> None:
    args = cli.parser().parse_args(["open", "a.toml", "--workspace", "map", "--size", "800x600"])
    assert (args.path, args.workspace, args.size) == (Path("a.toml"), "map", (800, 600))
    assert cli.parser().parse_args(["open"]).size is None


def test_bare_studio_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[dict[str, Any]] = []
    app = types.ModuleType("mhfu_studio.ui.app")
    app.main = lambda **kw: got.append(kw) or 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mhfu_studio.ui.app", app)
    assert cli.main([]) == 0
    assert got == [{"path": None, "workspace": None, "size": None}]


def test_open_without_qt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setitem(sys.modules, "mhfu_studio.ui.app", None)
    assert cli.main(["open"]) == 1
    assert "studio open: the window cannot load" in capsys.readouterr().err
