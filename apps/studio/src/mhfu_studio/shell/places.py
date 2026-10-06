# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where the studio finds the two extracted games and PPSSPP's memory stick.

Every lookup goes through `find`: the lane's own (`MHFU_LANE`, for the memory stick), else the
place's environment variable, else its saved setting (`shell.settings`), else a guess. A job the
window runs gets the answers as environment variables (`environ`), so its command line finds
what the window found.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mhfu import inject
from mhfu.files import Extracted
from mhfu_port.data import Data
from mhp_formats.databin import Game
from ppsspp_debug import Lane

from mhfu_studio.shell import settings

#: where a guess starts looking (its parents too); None is the working directory
GUESS_FROM: Path | None = None
#: how many folders up a guess looks
GUESS_UP = 4


def _game(game: Game) -> Callable[[Path], Path]:
    """The extraction at a path, refused when its UMD_DATA.BIN names another game."""

    def check(path: Path) -> Path:
        root = Extracted.find(path).root
        umd = root / "UMD_DATA.BIN"
        code = (
            umd.read_bytes().partition(b"|")[0].decode("ascii", "replace") if umd.exists() else ""
        )
        if code and code != game.value:
            raise ValueError(f"{root} is {code}, not {game.name} ({game.value})")
        return root

    return check


def _guess(rel: str) -> Callable[[], Path | None]:
    """The first `<folder>/rel` holding `data_files`, from `GUESS_FROM` up."""

    def guess() -> Path | None:
        start = (GUESS_FROM or Path.cwd()).resolve()
        for base in [start, *start.parents][: GUESS_UP + 1]:
            if (base / rel / "data_files").is_dir():
                return base / rel
        return None

    return guess


@dataclass(frozen=True)
class Place:
    """One location the studio needs."""

    #: its setting's name, under "places/"
    key: str
    name: str
    env: str
    #: who needs it, for a checklist line without it
    needs: str
    #: what to choose, for the folder dialog and the tooltip
    what: str
    #: the usable path for a chosen one; FileNotFoundError or ValueError says why not
    check: Callable[[Path], Path]
    guess: Callable[[], Path | None]
    #: where a lane keeps its own; with `MHFU_LANE` set it wins, as its PPSSPP reads only that
    in_lane: Callable[[Lane], Path] | None = None


MHFU = Place(
    "mhfu",
    "MHFU extraction",
    "MHFU_DATA",
    "Maps and monsters need the MHFU extraction",
    "The folder `mhp-formats extract` wrote for MHFU (EU): it holds data_files",
    _game(Game.MHFU),
    _guess("workspace/extracted"),
)
MHP3RD = Place(
    "mhp3rd",
    "MHP3rd extraction",
    "MHP3RD_DATA",
    "Monsters need the MHP3rd extraction: a port is built from it",
    "The folder `mhp-formats extract` wrote for MHP3rd: it holds data_files",
    _game(Game.MHP3RD),
    _guess("workspace/extracted_mhp3"),
)
MEMSTICK = Place(
    "memstick",
    "PPSSPP memory stick",
    inject.MEMSTICK_ENV,
    "Sending a monster to the game needs PPSSPP's memory stick",
    "PPSSPP's memory stick: its PSP folder, or the folder holding it",
    inject.memstick,
    inject.detect,
    lambda lane: lane.stick,
)
PLACES = (MHFU, MHP3RD, MEMSTICK)

Source = Literal["lane", "env", "saved", "found", ""]


@dataclass(frozen=True)
class Found:
    """Where `place` is, and how that was decided."""

    place: Place
    #: usable; None when nothing was found or `given` is not usable
    path: Path | None
    #: lane (`MHFU_LANE`), env (its variable), saved (the setting), found (a guess), or ""
    source: Source
    #: what the variable or the setting says
    given: str = ""
    #: why `given` is not usable
    problem: str = ""

    def why(self) -> str:
        """Why there is no path, in words; "" when there is one."""
        from mhfu_studio.shell.text import plain  # it brings numpy, which a command line skips

        if self.path is not None:
            return ""
        if self.problem:
            return f"the {self.place.name} is not usable: {plain(self.problem)}"
        return f"no {self.place.name} found"

    def says(self) -> str:
        """The checklist's line: the path and where it came from, or what is wrong."""
        from mhfu_studio.shell.text import plain

        if self.path is None:
            why = self.why()
            return why[:1].upper() + why[1:]
        how = {
            "lane": f"from {inject.LANE_ENV}",
            "env": f"from {self.place.env}",
            "saved": "chosen",
            "found": "found",
        }
        return f"{plain(str(self.path))} ({how.get(self.source, self.source)})"


class Missing(FileNotFoundError):
    """No usable place; `str` advises a command line, `words` the window."""

    def __init__(self, found: Found) -> None:
        self.found = found
        super().__init__(f"{found.why()}: pass its directory or set {found.place.env}")

    @property
    def words(self) -> str:
        return f"{self.found.why()} (choose it under Setup on the start page)"


def setting(place: Place) -> str:
    return f"places/{place.key}"


def find(place: Place) -> Found:
    """The lane's, else the variable, else the setting, else a guess; a bad one is reported,
    not passed over."""
    lane = inject.lane() if place.in_lane is not None else None
    told: tuple[tuple[Source, str | None], ...] = (
        ("lane", str(place.in_lane(lane)) if place.in_lane and lane else None),
        ("env", os.environ.get(place.env)),
        ("saved", settings.store.get(setting(place))),
    )
    for source, given in told:
        if given:
            try:
                return Found(place, place.check(Path(given).expanduser()), source, given)
            except (OSError, ValueError) as e:
                return Found(place, None, source, given, str(e))
    got = place.guess()
    return Found(place, got, "found" if got is not None else "")


def remember(place: Place, path: Path | str | None) -> Found:
    """Saves `path` as the setting (None forgets it) once `check` takes it; the new answer.
    Raises what `check` raises, and saves nothing then."""
    value = None if path is None else str(place.check(Path(path).expanduser()))
    settings.store.put(setting(place), value)
    return find(place)


def path(place: Place) -> Path:
    """`find`'s path; `Missing` when there is none."""
    f = find(place)
    if f.path is None:
        raise Missing(f)
    return f.path


def extracted(place: Place = MHFU, given: str | Path | None = None) -> Extracted:
    """`given` (a command's --data), else `place`'s extraction."""
    return Extracted.find(given) if given is not None else Extracted(path(place))


def games(fu: str | Path | None = None, p3rd: str | Path | None = None) -> Data:
    """Both extractions a port reads, `fu` and `p3rd` over the places."""
    return Data(extracted(MHFU, fu), extracted(MHP3RD, p3rd))


def mods_dir() -> Path:
    """The framework's mods folder on the memory stick."""
    return inject.default_mods_dir(path(MEMSTICK))


def environ() -> dict[str, str]:
    """Each found place as its variable, for a command the window runs."""
    found = (find(p) for p in PLACES)
    return {f.place.env: str(f.path) for f in found if f.path is not None}
