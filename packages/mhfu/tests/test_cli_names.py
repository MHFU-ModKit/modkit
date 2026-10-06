# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections import Counter
from types import SimpleNamespace

import pytest
from mhfu import addresses as a
from mhfu import names, symbols
from mhfu.cli import main
from mhfu.cli import names as cli

FN = 0x0010_0000  # noaddr: any address the table names
TABLE = {
    "names": {
        f"0x{FN:08X}": {
            "name": "ObjBase::draw()",
            "symbol": "draw__7ObjBaseFv",
            "how": "exact",
            "jp": "0x00F00000",
            "module": "eboot",
        }
    },
    "functions": {
        f"0x{FN:08X}": {"jp": "0x00F00000", "module": "eboot", "how": "exact"},
        f"0x{a.ACT_SET:08X}": {"jp": "0x00F00100", "module": "game_task", "how": "exact"},
    },
    "data": {},
    "coverage": {
        "eboot": {
            "jp_functions": 3,
            "eu_functions": 2,
            "matched": {"exact": 2},
            "named": 1,
            "named_mapped": 1,
        },
        "calls": {"agree": 5},
    },
}


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "names.json"
    monkeypatch.setenv("MHFU_NAMES", str(path))
    names.write(TABLE, path)
    return path


def run(capsys, *args, code=0):
    assert main([str(x) for x in args]) == code
    return capsys.readouterr().out


def test_show(table, capsys):
    assert "ObjBase::draw()" in run(capsys, "names", "show", hex(FN))
    out = run(capsys, "names", "show", "ACT_SET")
    assert "ACT_SET  (addresses.toml)" in out and '"jp": "0x00F00100"' in out
    assert run(capsys, "names", "show", "draw__").startswith(f"0x{FN:08X}  ObjBase::draw()")
    run(capsys, "names", "show", "nothing", code=1)


def test_coverage(table, capsys, monkeypatch):
    monkeypatch.setattr(cli.Extracted, "find", lambda path: SimpleNamespace(em=lambda n: n))
    monkeypatch.setattr(cli.names, "outbound", lambda em: Counter({FN: 3, a.ACT_SET: 1, 4: 1}))
    out = run(capsys, "names", "coverage")
    assert "eboot            3       2        2      1       1" in out
    assert "em75 outbound: 3 targets, 5 call sites" in out
    assert "names table        1 targets  60.0% of sites" in out
    assert "matched to JP      2 targets  80.0% of sites" in out


def test_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.Extracted, "find", lambda path, env="": path)
    monkeypatch.setattr(cli.Decomp, "find", lambda path: path)
    monkeypatch.setattr(cli.names, "build", lambda eu, jp, decomp, progress: TABLE)
    out = tmp_path / "t.json"
    assert "1 names, 2 functions" in run(capsys, "names", "build", "-o", out)
    symbols.functions.cache_clear()
    assert out.exists()


def test_no_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MHFU_NAMES", str(tmp_path / "none.json"))
    assert main(["names", "show", "0x0"]) == 1
    assert "mhfu names build" in capsys.readouterr().err
