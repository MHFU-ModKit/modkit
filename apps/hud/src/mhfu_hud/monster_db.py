"""Monster names and icon slugs: by vtable through mhfu, else by species id (ENTITY.SPECIES).

QUEST_PREP stages edits by species id before any monster has spawned, so its picker lists the
ids mhfu has checked (SPECIES_IDS) first.
"""

from __future__ import annotations

from mhfu.structs import SPECIES_IDS, SPECIES_NAMES


def _checked() -> list[tuple[str, int | None]]:
    """One row per checked name, at its lowest id."""
    rows: dict[str, int | None] = {}
    for sid, name in sorted(SPECIES_IDS.items()):
        rows.setdefault(name, sid)
    return list(rows.items())


PICKER_SPECIES: list[tuple[str, int | None]] = [
    *_checked(),
    # species ids not checked yet; the picker binds one by hand
    ("Velociprey", None),
    ("Velocidrome", None),
    ("Genprey", None),
    ("Gendrome", None),
    ("Ioprey", None),
    ("Iodrome", None),
    ("Hornetaur", None),
    ("Bulldrome", None),
    ("Felyne", None),
    ("Melynx", None),
    ("Conga", None),
    ("Remobra", None),
    ("Cephalos", None),
    ("Yian Kut-Ku", None),
    ("Yian Garuga", None),
    ("Khezu", None),
    ("Rathian", None),
    ("Rathalos", None),
    ("Cephadrome", None),
    ("Diablos", None),
    ("Monoblos", None),
    ("Plesioth", None),
    ("Gravios", None),
    ("Basarios", None),
    ("Daimyo Hermitaur", None),
    ("Shogun Ceanataur", None),
    ("Congalala", None),
    ("Blangonga", None),
    ("Kirin", None),
    ("Gypceros", None),
    ("Lao-Shan Lung", None),
    ("Shen Gaoren", None),
    ("Rajang", None),
    ("Nargacuga", None),
    ("Akantor", None),
    ("Ukanlos", None),
    ("Espinas", None),
    ("Berukyurosu", None),
]
"""QUEST_PREP's species list: name and the species id an edit keys on."""


def slugify(name: str) -> str:
    kept = (ch for ch in name.lower() if ch.isalnum() or ch in " -_'")
    out = "".join(ch if ch.isalnum() else "_" for ch in kept)
    return "_".join(filter(None, out.split("_")))


def species_name(species: int) -> str:
    return SPECIES_IDS.get(species, f"em{species}")


def identify(vtable: int, species: int) -> tuple[str, str | None]:
    """(name, icon slug); no slug for a monster known by neither key."""
    name = SPECIES_NAMES.get(vtable) or SPECIES_IDS.get(species)
    if name is None:
        return f"em{species}", None
    return name, slugify(name)
