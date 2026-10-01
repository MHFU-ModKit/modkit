# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_studio import cli


@pytest.mark.parametrize("group", ["render", "port", "map"])
def test_groups_exist(group: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main([group, "--help"])
    assert e.value.code == 0
    assert "ACTION" in capsys.readouterr().out
