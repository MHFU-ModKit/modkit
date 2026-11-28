# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from modkit_ci import archive, docs


def _tree(root: Path, stamp: int) -> None:
    for name, mode in (("b.txt", 0o600), ("a/c.txt", 0o755)):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
        path.chmod(mode)
        os.utime(path, (stamp, stamp))


def test_same_input_same_bytes(tmp_path: Path) -> None:
    _tree(tmp_path / "x", 1_000_000_000)
    _tree(tmp_path / "y", 1_700_000_000)
    docs.zip_site(tmp_path / "x", tmp_path / "x.zip")
    docs.zip_site(tmp_path / "y", tmp_path / "y.zip")
    assert (tmp_path / "x.zip").read_bytes() == (tmp_path / "y.zip").read_bytes()


def test_sorted_with_fixed_stamps(tmp_path: Path) -> None:
    _tree(tmp_path / "x", 1_700_000_000)
    docs.zip_site(tmp_path / "x", tmp_path / "x.zip")
    with zipfile.ZipFile(tmp_path / "x.zip") as zf:
        assert zf.namelist() == ["a/c.txt", "b.txt"]
        assert {i.date_time for i in zf.infolist()} == {archive.ZIP_TIME}
        assert {i.external_attr >> 16 for i in zf.infolist()} == {archive.MODE}
