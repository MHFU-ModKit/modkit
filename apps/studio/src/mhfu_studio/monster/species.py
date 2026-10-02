# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where the studio finds a host species' intel: a directory given, else a cache built on demand
from the extracted game (`MHFU_DATA`) through `mhfu.em.intel.build`.

The cache is `$XDG_CACHE_HOME/mhfu-studio/species/emNN.json` (`~/.cache` without the variable);
a cached document is rebuilt when its overlay's sha1 is not the extracted overlay's. Intel with a
census attached comes from `mhfu intel --log` into a directory you name.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mhfu import files
from mhfu.em.intel import HostSummary, SpeciesIntel
from mhfu.files import Extracted


def cache_root() -> Path:
    """The studio's per-user cache."""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "mhfu-studio"


def cache_dir() -> Path:
    return cache_root() / "species"


def _name(species: int) -> str:
    return f"em{species:02d}.json"


def available(root: Path) -> list[int]:
    """Species with an `emNN.json` in `root`."""
    stems = (p.stem[2:] for p in root.glob("em*.json"))
    return sorted(int(s) for s in stems if s.isdigit())


def cached(species: int, game: Extracted, root: Path | None = None) -> SpeciesIntel:
    """The species' intel from the cache, built first when missing or stale."""
    path = (root or cache_dir()) / _name(species)
    sha1 = hashlib.sha1(game.read(files.em_overlay(species))).hexdigest()
    if path.is_file():
        si = SpeciesIntel.load(path)
        if si.overlay.get("sha1") == sha1:
            return si
    from mhfu.em import intel

    doc = intel.build(intel.Game(game), species)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return SpeciesIntel(doc, str(path))


def load(species: int, root: Path | None = None, data: str | Path | None = None) -> SpeciesIntel:
    """From `root` when given, else the cache over `data` or `MHFU_DATA`; `LookupError` says
    why there is none and what to do."""
    if root is not None:
        path = root / _name(species)
        if not path.is_file():
            raise LookupError(f"{root} has no {path.name}: write it with `mhfu intel`")
        return SpeciesIntel.load(path)
    if species not in files.EM_SPECIES:
        raise LookupError(
            f"em{species:02d} has no overlay in the game: check host_species in the manifest"
        )
    try:
        game = Extracted.find(data)
    except FileNotFoundError as e:
        raise LookupError(
            f"it is built from the extracted game, and there is none ({e}): set MHFU_DATA and"
            " restart the studio"
        ) from e
    return cached(species, game)


def find(
    species: int, root: Path | None = None, data: str | Path | None = None
) -> SpeciesIntel | None:
    """`load`, None where it would say why."""
    try:
        return load(species, root, data)
    except LookupError:
        return None


def survey(root: Path | None = None, data: str | Path | None = None) -> list[HostSummary]:
    """Every host overlay summarised; builds the whole cache on first use."""
    ids = available(root) if root is not None else list(files.EM_SPECIES)
    found = (find(s, root, data) for s in ids)
    return [HostSummary.of(si) for si in found if si is not None]
