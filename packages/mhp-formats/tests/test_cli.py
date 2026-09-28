# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import subprocess
import sys
from pathlib import Path

import pytest
from mhp_formats import FormatError, cli
from mhp_formats.databin import Game


def test_extract(monkeypatch, capsys):
    calls = []

    def extract(iso, out, progress):
        calls.append((iso, out))
        progress("PSP_GAME/SYSDIR/BOOT.BIN")
        return Game.MHP3RD

    monkeypatch.setattr(cli.iso, "extract", extract)
    assert cli.main(["extract", "game.iso", "out"]) == 0
    assert calls == [(Path("game.iso"), Path("out"))]
    out, err = capsys.readouterr()
    assert "MHP3RD (ULJM-05800)" in out
    assert "BOOT.BIN" in err


def test_error(monkeypatch, capsys):
    def extract(iso, out, progress):
        raise FormatError("no UMD_DATA.BIN")

    monkeypatch.setattr(cli.iso, "extract", extract)
    with pytest.raises(SystemExit) as raised:
        cli.main(["extract", "game.iso", "out"])
    assert raised.value.code == 1
    assert capsys.readouterr().err == "mhp-formats: no UMD_DATA.BIN\n"


@pytest.mark.parametrize(("argv", "code"), [(["--help"], 0), ([], 2), (["extract", "x.iso"], 2)])
def test_usage(argv, code):
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == code


def test_without_iso_extra(tmp_path):
    # the command line and the modules load without the extra; the command says what is missing
    code = (
        "import sys; sys.modules['mhef'] = sys.modules['pycdlib'] = None; "
        "from mhp_formats import cli; cli.main(['extract', 'x.iso', 'out'])"
    )
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 1
    assert "install mhp-formats[iso]" in run.stderr
