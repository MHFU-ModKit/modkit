# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The one zip writer: fixed times and modes, sorted entries, so equal input gives equal bytes."""

from __future__ import annotations

import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
MODE: Final = 0o644


def write_zip(archive: Path, files: Mapping[str, Path]) -> None:
    """Write `archive` holding the files of `files` (archive name -> source), sorted by name."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.external_attr = MODE << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name].read_bytes())
