"""Staged edits: authored in QUEST_PREP before a quest, written to each matching monster once
when it spawns.

    size_species  species id X: Entity.resize(value)
    size_slot     registry slot N: Entity.resize(value)
    type_swap     species id X: ENTITY.SPECIES = value
    hp_slot       registry slot N: ENTITY.HP = value

Each edit remembers the entities it wrote (`applied_to`), so a value changed afterwards is not
written back.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from mhfu.memory import Live
from mhfu.structs import Entity
from ppsspp_debug import Client

from .state import MonsterHUD


@dataclass
class StagedEdit:
    kind: str
    """size_species, size_slot, type_swap or hp_slot."""
    selector_value: int
    """Species id for size_species and type_swap, registry slot for the *_slot kinds."""
    new_value: float
    enabled: bool = True
    label: str = ""
    applied_to: set[int] = field(default_factory=set)

    def reset_applied(self) -> None:
        self.applied_to.clear()

    def matches(self, m: MonsterHUD) -> bool:
        key = m.slot if self.kind in ("size_slot", "hp_slot") else m.species
        return key == self.selector_value

    def write(self, e: Entity) -> None:
        if self.kind in ("size_species", "size_slot"):
            e.resize(float(self.new_value))
        elif self.kind == "type_swap":
            e.species = int(self.new_value) & 0xFF
        elif self.kind == "hp_slot":
            e.hp = int(self.new_value) & 0xFFFF


class EditBank:
    """Every staged edit, with a master switch."""

    def __init__(self) -> None:
        self.edits: list[StagedEdit] = []
        self.enabled = True

    def add(self, edit: StagedEdit) -> None:
        self.edits.append(edit)

    def remove(self, idx: int) -> None:
        if 0 <= idx < len(self.edits):
            self.edits.pop(idx)

    def toggle(self, idx: int) -> None:
        if 0 <= idx < len(self.edits):
            self.edits[idx].enabled = not self.edits[idx].enabled

    def toggle_master(self) -> None:
        self.enabled = not self.enabled

    def reset_applied(self) -> None:
        """Forget the entities written, so the edits fire again on a new quest's monsters."""
        for e in self.edits:
            e.reset_applied()

    def apply_to_monsters(self, client: Client | None, monsters: Iterable[MonsterHUD]) -> int:
        """Write every enabled edit to the monsters it matches and has not written; the count."""
        if not self.enabled or client is None or not self.edits:
            return 0
        mem = Live(client)
        writes = 0
        for m in monsters:
            for e in list(self.edits):
                if not e.enabled or m.ptr in e.applied_to or not e.matches(m):
                    continue
                try:
                    e.write(Entity(mem, m.ptr))
                except Exception:
                    continue  # the next poll retries it
                e.applied_to.add(m.ptr)
                writes += 1
        return writes
