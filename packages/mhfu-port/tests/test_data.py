# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port.data import Data


def test_find(tmp_path, monkeypatch):
    for game in ("fu", "p3rd"):
        (tmp_path / game / "data_files").mkdir(parents=True)
    monkeypatch.setenv("MHFU_DATA", str(tmp_path / "fu"))
    monkeypatch.setenv("MHP3RD_DATA", str(tmp_path / "p3rd" / "data_files"))
    d = Data.find()
    assert (d.fu.root, d.p3rd.root) == (tmp_path / "fu", tmp_path / "p3rd")
    monkeypatch.delenv("MHP3RD_DATA")
    with pytest.raises(FileNotFoundError, match="MHP3RD_DATA"):
        Data.find()
