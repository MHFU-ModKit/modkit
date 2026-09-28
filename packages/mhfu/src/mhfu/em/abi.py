"""The interface the engine calls a big-monster overlay through. Every em overlay loads at the same
address and has no constructors: BOOT.BIN holds one entity vtable per species whose slots point
into that species' overlay, and the engine only ever calls a species through it.

A vptr points at an (offset-to-top, typeinfo) pair, both zero here, so virtual function k sits
at vptr + 8 + 4k. Slots are numbered k; `offset(k)` is what the `lw` off the vptr reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from rabbitizer import InstrId

from .. import addresses as a
from ..eboot import Eboot
from ..files import EM_SPECIES, GAME_SUB, GAME_TASK, Extracted
from ..memory import Unmapped
from ..mips import Call, Code, is_prologue
from ..overlay import Overlay

ENTITY_SLOTS = 61
"""Virtual functions of the big-monster entity class."""
MIN_SLOTS = 8
"""A shorter run of code pointers after a zero pair is not taken for a vtable."""
_PAIR = 8


def offset(slot: int) -> int:
    return _PAIR + 4 * slot


@dataclass(frozen=True)
class Vtable:
    va: int
    slots: tuple[int, ...]


@dataclass(frozen=True)
class Owner:
    """The species whose overlay a vtable points into, and how sure: the share of its overlay
    pointers that land on a function start there, for the best and the runner-up species."""

    vtable: Vtable
    species: int
    score: float
    second: float
    pointers: int


@dataclass(frozen=True)
class Slot:
    index: int
    impl: Mapping[int, int]
    """Species -> the function the slot holds."""
    overrides: tuple[int, ...]
    """Species whose slot points into their own overlay."""

    @property
    def inherited(self) -> tuple[int, ...]:
        """The base-class functions the other species keep."""
        return tuple(sorted({v for s, v in self.impl.items() if s not in self.overrides}))

    @property
    def kind(self) -> str:
        """`mandatory` (all species override it), `optional` or `base` (none does)."""
        if len(self.overrides) == len(self.impl):
            return "mandatory"
        return "optional" if self.overrides else "base"


class Engine:
    """BOOT.BIN, game_task, game_sub and the em overlays, and which code each pointer reaches."""

    def __init__(
        self, eboot: Eboot, game_task: Overlay, game_sub: Overlay, ems: Mapping[int, Overlay]
    ) -> None:
        self.eboot, self.game_task, self.game_sub = eboot, game_task, game_sub
        self.ems = dict(ems)
        em = range(min(o.text.start for o in ems.values()), max(o.text.stop for o in ems.values()))
        self.zones = {
            "em": em,
            "game_sub": game_sub.text,
            "game_task": game_task.text,
            "eboot": eboot.text,
        }

    @classmethod
    def load(cls, game: Extracted) -> Engine:
        ems = {s: game.em(s) for s in EM_SPECIES}
        return cls(game.eboot(), game.overlay(GAME_TASK), game.overlay(GAME_SUB), ems)

    def zone(self, va: int) -> str | None:
        """Where a code pointer lands; `em` is any species overlay's text."""
        return next((name for name, r in self.zones.items() if va in r), None)

    def in_em(self, va: int) -> bool:
        return va in self.zones["em"]

    @cached_property
    def codes(self) -> dict[int, Code]:
        return {s: Code(o, o.text) for s, o in self.ems.items()}

    @cached_property
    def task_code(self) -> Code:
        return Code(self.game_task, self.game_task.text)

    @cached_property
    def _starts(self) -> dict[int, frozenset[int]]:
        return {s: frozenset(c.entries) for s, c in self.codes.items()}

    def is_start(self, species: int, va: int) -> bool:
        """Whether `va` looks like a function start in that species' overlay."""
        code = self.codes[species]
        if va not in code.text:
            return False
        if va in self._starts[species] or is_prologue(code.at(va)):
            return True
        return va - 8 in code.text and code.at(va - 8).isReturn()

    # --- vtables ---

    def vtable(self, va: int) -> Vtable:
        """The run of code pointers after the pair at `va`."""
        slots: list[int] = []
        while True:
            try:
                word = self.eboot.u32(va + offset(len(slots)))
            except Unmapped:
                break
            if self.zone(word) is None:
                break
            slots.append(word)
        return Vtable(va, tuple(slots))

    @cached_property
    def vtables(self) -> tuple[Vtable, ...]:
        """Every zero pair in BOOT.BIN's data followed by at least MIN_SLOTS code pointers."""
        data = self.eboot.sections[".data"]
        out, va = [], data.start
        while va + _PAIR < data.stop:
            if self.eboot.u32(va) == 0 and self.eboot.u32(va + 4) == 0:
                vt = self.vtable(va)
                if len(vt.slots) >= MIN_SLOTS:
                    out.append(vt)
                    va += offset(len(vt.slots))
                    continue
            va += 4
        return tuple(out)

    @cached_property
    def owners(self) -> dict[int, Owner]:
        """Species -> its entity vtable, by where the vtable's overlay pointers land."""
        out = {}
        for vt in self.vtables:
            em = [p for p in vt.slots if self.in_em(p)]
            if len(vt.slots) != ENTITY_SLOTS or not em:
                continue
            scores = sorted(
                ((sum(self.is_start(s, p) for p in em) / len(em), s) for s in self.ems),
                reverse=True,
            )
            (best, species), (second, _) = scores[0], scores[1]
            if species in out:
                raise ValueError(f"em{species:02d} owns two entity vtables")
            out[species] = Owner(vt, species, best, second, len(em))
        return dict(sorted(out.items()))

    def interface(self) -> list[Slot]:
        """Each entity-vtable slot across the species."""
        out = []
        for k in range(ENTITY_SLOTS):
            impl = {s: o.vtable.slots[k] for s, o in self.owners.items()}
            overrides = tuple(s for s, v in impl.items() if self.in_em(v))
            out.append(Slot(k, impl, overrides))
        return out

    # --- who installs which vtable ---

    def factory(self) -> dict[int, int]:
        """emId -> species overlay, from the cases of EM_FACTORY that install an entity vtable.

        A case runs until the next case starts."""
        code = self.task_code
        fn = code.function(a.EM_FACTORY)
        switch = next(s for jr, s in code.switches.items() if jr in fn)
        vt_species = {o.vtable.va: s for s, o in self.owners.items()}
        starts = sorted(set(switch.targets))
        out = {}
        for n, case in enumerate(switch.targets):
            stop = next((b for b in starts if b > case), fn.stop)
            for va, _ in _vptr_stores(code, range(case, stop)):
                if va in vt_species:
                    out[n + switch.first] = vt_species[va]
                    break
        return out

    def classes(self, species: int) -> dict[int, list[int]]:
        """BOOT.BIN vtables the species' overlay installs itself -> the constructor sites."""
        code = self.codes[species]
        out: dict[int, list[int]] = {}
        for va, site in _vptr_stores(code, code.text):
            if va in self.eboot:
                out.setdefault(va, []).append(site)
        return dict(sorted(out.items()))

    def dispatches(self, slot: int) -> list[int]:
        """game_task's virtual calls through `slot`. Other classes share the offset, so a site
        need not call a monster."""
        return [c.site for c in self.task_code.calls if _slot(self.task_code, c) == offset(slot)]


def _slot(code: Code, call: Call) -> int | None:
    """`call.slot`, or the offset of the `lw` the target comes from when the vptr it reads was
    loaded on another path (at a label, where `Call.slot` gives up)."""
    if call.slot is not None or call.target is not None:
        return call.slot
    load = code.source(call.site, code.at(call.site).rs)
    if load is None or load.uniqueId != InstrId.cpu_lw or code.source(load.vram, load.rs):
        return None
    return int(load.getProcessedImmediate())


def _vptr_stores(code: Code, where: range) -> list[tuple[int, int]]:
    """(literal, site) of each `sw R, 0(obj)` that stores a literal address."""
    out = []
    for ins in code.span(where):
        if ins.uniqueId == InstrId.cpu_sw and ins.getProcessedImmediate() == 0:
            value = code.constant(ins.vram, ins.rt)
            if value is not None:
                out.append((value, ins.vram))
    return out
