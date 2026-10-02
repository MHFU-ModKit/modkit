"""Monster names and icon slugs: by vtable through mhfu, else by species id (ENTITY.SPECIES).

The species ids here are the ones seen live; QUEST_PREP stages edits by species id before any
monster has spawned, so its picker needs them.
"""

from __future__ import annotations

from mhfu.structs import SPECIES_NAMES

TYPE_NAMES = {
    0x05: "Bullfango",
    0x13: "Vespoid",
    0x23: "Giaprey",
    0x3D: "Blango",
    0x45: "Anteka",
    0x46: "Popo",
    0x48: "Popo",
    0x4B: "Tigrex",
    0x4D: "Giadrome",
}
"""Species id -> name, as observed live."""

PICKER_SPECIES: list[tuple[str, int | None]] = [
    ("Bullfango", 0x05),
    ("Vespoid", 0x13),
    ("Giaprey", 0x23),
    ("Blango", 0x3D),
    ("Anteka", 0x45),
    ("Popo", 0x46),
    ("Tigrex", 0x4B),
    ("Giadrome", 0x4D),
    # species ids not observed yet; the picker binds one by hand
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
    return TYPE_NAMES.get(species, f"em{species}")


def identify(vtable: int, species: int) -> tuple[str, str | None]:
    """(name, icon slug); no slug for a monster known by neither key."""
    name = SPECIES_NAMES.get(vtable) or TYPE_NAMES.get(species)
    if name is None:
        return f"em{species}", None
    return name, slugify(name)
