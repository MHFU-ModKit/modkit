# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The two extracted games a port reads: MHFU EU, the host, and MHP3rd, the donor."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from mhfu.files import Extracted


@dataclass(frozen=True)
class Data:
    fu: Extracted
    p3rd: Extracted

    @classmethod
    def find(cls, fu: str | Path | None = None, p3rd: str | Path | None = None) -> Data:
        """From those directories; by default from MHFU_DATA and MHP3RD_DATA."""
        return cls(Extracted.find(fu), Extracted.find(p3rd, env="MHP3RD_DATA"))


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--data", type=Path, help="extracted MHFU (default: $MHFU_DATA)")
    ap.add_argument("--p3rd-data", type=Path, help="extracted MHP3rd (default: $MHP3RD_DATA)")


def from_arguments(args: argparse.Namespace) -> Data:
    return Data.find(args.data, args.p3rd_data)
