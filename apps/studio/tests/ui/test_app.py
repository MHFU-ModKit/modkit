# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_studio import cli
from PySide6.QtGui import QSurfaceFormat


def test_qt_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["qt", "--help"])
    assert e.value.code == 0 and "path" in capsys.readouterr().out


def test_prepared(qapp: object) -> None:
    fmt = QSurfaceFormat.defaultFormat()
    assert fmt.version() == (3, 3) and fmt.depthBufferSize() == 24
