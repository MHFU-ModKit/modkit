# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where the game keeps things in DATA.BIN, and a game extracted by `mhp-formats extract`.

File ids here are the extracted ones, `file_NNNNN.bin`; the engine asks for NNNNN + 1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .eboot import Eboot
from .overlay import Overlay

ENGINE_SKEW = 1
"""Engine file id minus extracted file id."""

GAME_TASK = 70
"""game_task.ovl: the in-quest engine, species table and hit volumes included."""
GAME_SUB = 75
"""game_sub.ovl: the map table, among others."""
LOBBY_TASK = 69
"""lobby_task.ovl: the hub, which picks a st046 variant."""

EM_SPECIES = (1, 2, 7, 14, 15, 17, 20, 21, 33, 40, 54, 55, 58, 59, 75, 82, 83)
"""Species with a big-monster overlay, in file order from EM_FIRST."""
EM_FIRST = 6094
MONSTER_PAC = 6110
"""A species' model PAC is MONSTER_PAC + species (Tigrex 75: 6185)."""

STAGES = range(0, 267)
"""Stage numbers, which are area_index values; each has an overlay, all but stage 0 a PAC."""
STAGE_OVERLAY = 5541
"""stage<NNN>.ovl is STAGE_OVERLAY + NNN."""
STAGE_PAC = 5807
"""st<NNN>.pac is STAGE_PAC + NNN; there is no st000.pac."""
STAGE_VARIANT = 46
STAGE_VARIANT_PAC = 6074
STAGE_VARIANTS = range(16)
"""The st046_<n><x>.pac the lobby loads in place of st046.pac (addresses.STAGE_VARIANT_FILE),
variant (n - 1) + 4 * (x - 'a'), file STAGE_VARIANT_PAC + variant."""
STAGE_PACS = range(STAGE_PAC + 1, STAGE_VARIANT_PAC + len(STAGE_VARIANTS))
"""Every stage PAC, the variants included."""


def engine_id(file_id: int) -> int:
    return file_id + ENGINE_SKEW


def em_overlay(species: int) -> int:
    return EM_FIRST + EM_SPECIES.index(species)


def monster_pac(species: int) -> int:
    return MONSTER_PAC + species


def stage_pac(stage: int) -> int:
    if stage not in STAGES or not stage:
        raise ValueError(f"no st{stage:03d}.pac")
    return STAGE_PAC + stage


def stage_variant_pac(variant: int) -> int:
    if variant not in STAGE_VARIANTS:
        raise ValueError(f"no st{STAGE_VARIANT:03d} variant {variant}")
    return STAGE_VARIANT_PAC + variant


def stage_overlay(stage: int) -> int:
    if stage not in STAGES:
        raise ValueError(f"no stage{stage:03d}.ovl")
    return STAGE_OVERLAY + stage


@dataclass(frozen=True)
class Extracted:
    """The output directory of `mhp-formats extract` for MHFU EU."""

    root: Path

    @classmethod
    def find(cls, path: str | Path | None = None, env: str = "MHFU_DATA") -> Extracted:
        """From that directory or its `data_files`; by default from the variable `env`, which
        can name another game's extraction for `path` and `read`."""
        if path is None:
            path = os.environ.get(env)
            if not path:
                raise FileNotFoundError(f"no extracted game: pass its directory or set {env}")
        root = Path(path).expanduser()
        if root.name == "data_files":
            root = root.parent
        if not (root / "data_files").is_dir():
            raise FileNotFoundError(f"{root} has no data_files: run `mhp-formats extract` first")
        return cls(root)

    def path(self, file_id: int) -> Path:
        return self.root / "data_files" / f"file_{file_id:05d}.bin"

    def read(self, file_id: int) -> bytes:
        return self.path(file_id).read_bytes()

    def overlay(self, file_id: int) -> Overlay:
        return Overlay(self.read(file_id), self.path(file_id).name)

    def em(self, species: int) -> Overlay:
        return self.overlay(em_overlay(species))

    def eboot(self) -> Eboot:
        return Eboot.from_path(self.root / "PSP_GAME" / "SYSDIR" / "BOOT.BIN")
