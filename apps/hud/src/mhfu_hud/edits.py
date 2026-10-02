"""Staged edits as frozen values: what QUEST_PREP authors and the writer's status it draws.

Every kind keys on the species id (ENTITY.SPECIES), the one thing known before a quest spawns
its monsters; `writer.GameWriter` writes them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .monster_db import species_name
from .state import MonsterHUD

SIZE_STEP = 0.05
SIZE_MIN = 0.1
HP_STEP = 10
HP_DEFAULT = 100


class Kind(Enum):
    SIZE = "size"
    """Entity.resize(value)."""
    SPECIES = "species"
    """ENTITY.SPECIES = value."""
    HP = "hp"
    """ENTITY.HP = value."""


@dataclass(frozen=True)
class Edit:
    kind: Kind
    species: int
    """The species id it applies to."""
    value: float
    enabled: bool = True

    @classmethod
    def of(cls, kind: Kind, species: int) -> Edit:
        """An edit of `kind` with a value that changes nothing yet where one exists."""
        value = {Kind.SIZE: 1.0, Kind.SPECIES: float(species), Kind.HP: float(HP_DEFAULT)}
        return cls(kind, species, value[kind])

    def matches(self, m: MonsterHUD) -> bool:
        return m.species == self.species

    def stepped(self, sign: int) -> Edit:
        """One step up (`sign` 1) or down (-1)."""
        if self.kind is Kind.SIZE:
            value = max(SIZE_MIN, round(self.value + sign * SIZE_STEP, 3))
        elif self.kind is Kind.HP:
            value = max(1.0, self.value + sign * HP_STEP)
        else:
            value = float((int(self.value) + sign) & 0xFF)
        return replace(self, value=value)

    def next_kind(self) -> Edit:
        kinds = list(Kind)
        kind = kinds[(kinds.index(self.kind) + 1) % len(kinds)]
        return replace(Edit.of(kind, self.species), enabled=self.enabled)

    @property
    def label(self) -> str:
        who = f"{species_name(self.species)} (0x{self.species:02X})"
        if self.kind is Kind.SIZE:
            return f"size {who} -> {self.value:.2f}"
        if self.kind is Kind.HP:
            return f"hp {who} -> {int(self.value)}"
        to = int(self.value) & 0xFF
        return f"species {who} -> {species_name(to)} (0x{to:02X})"


@dataclass(frozen=True)
class Staged:
    edit: Edit
    applied: int = 0
    """Monsters it has written since the last quest area was entered."""


@dataclass(frozen=True)
class WriterStatus:
    staged: tuple[Staged, ...] = ()
    enabled: bool = True
    """The master switch."""
    writes: int = 0
    error: str = ""
    """The last failed write; the next snapshot retries it."""

    @property
    def active(self) -> bool:
        """Something would be written: the master switch and an edit are on."""
        return self.enabled and any(s.edit.enabled for s in self.staged)
