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


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "mhfu-studio" / "species"


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


def find(
    species: int, root: Path | None = None, data: str | Path | None = None
) -> SpeciesIntel | None:
    """From `root` when given (None when it has no file for the species), else the cache over
    `data` or `MHFU_DATA`; None when neither is there or the species has no overlay."""
    if root is not None:
        path = root / _name(species)
        return SpeciesIntel.load(path) if path.is_file() else None
    if species not in files.EM_SPECIES:
        return None
    try:
        game = Extracted.find(data)
    except FileNotFoundError:
        return None
    return cached(species, game)


def survey(root: Path | None = None, data: str | Path | None = None) -> list[HostSummary]:
    """Every host overlay summarised; builds the whole cache on first use."""
    ids = available(root) if root is not None else list(files.EM_SPECIES)
    found = (find(s, root, data) for s in ids)
    return [HostSummary.of(si) for si in found if si is not None]
