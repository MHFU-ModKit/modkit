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

EM_SPECIES = (1, 2, 7, 14, 15, 17, 20, 21, 33, 40, 54, 55, 58, 59, 75, 82, 83)
"""Species with a big-monster overlay, in file order from EM_FIRST."""
EM_FIRST = 6094
MONSTER_PAC = 6110
"""A species' model PAC is MONSTER_PAC + species (Tigrex 75: 6185)."""

STAGE_PAC = 5807
STAGE_OVERLAY = 5541
STAGES = range(0, 267)


def engine_id(file_id: int) -> int:
    return file_id + ENGINE_SKEW


def em_overlay(species: int) -> int:
    return EM_FIRST + EM_SPECIES.index(species)


def monster_pac(species: int) -> int:
    return MONSTER_PAC + species


def stage_pac(stage: int) -> int:
    return STAGE_PAC + stage


def stage_overlay(stage: int) -> int:
    return STAGE_OVERLAY + stage


@dataclass(frozen=True)
class Extracted:
    """The output directory of `mhp-formats extract` for MHFU EU."""

    root: Path

    @classmethod
    def find(cls, path: str | Path | None = None) -> Extracted:
        """From that directory or its `data_files`; by default from the MHFU_DATA variable."""
        if path is None:
            path = os.environ.get("MHFU_DATA")
            if not path:
                raise FileNotFoundError("no extracted game: pass its directory or set MHFU_DATA")
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
