# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json

import pytest
from mhfu import addresses as a
from mhfu import symbols


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "names.json"
    monkeypatch.setenv("MHFU_NAMES", str(path))
    symbols.generated.cache_clear()
    yield path
    symbols.generated.cache_clear()


def test_own_names_win(table):
    fn = next(x for x in a.table().addresses.values() if x.type == "fn")
    table.write_text(
        json.dumps({"source": "t", "names": {f"0x{fn:08X}": {"name": "other", "symbol": "s"}}})
    )
    assert symbols.name(fn) == fn.name


FN = 0x08800010  # noaddr: any address the table names


def test_generated_names(table):
    table.write_text(
        json.dumps({"source": "t", "names": {f"0x{FN:08X}": {"name": "f()", "symbol": "f__Fv"}}})
    )
    assert symbols.name(FN) == "f()"
    assert symbols.name(FN + 4) is None


def test_no_table(table):
    assert symbols.generated() == {}
