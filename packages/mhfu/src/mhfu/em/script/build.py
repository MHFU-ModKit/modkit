# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The one path from a brain file to a stick container: the row's lift, the file's edits applied,
the result vetted, laid out and packed.

    built = build.compile(bf, game, 75)       # `lift=` a cached lift, `intel=` the species' intel
    built.findings, built.ok                  # apply's DOC_* findings, then the checks
    built.container, built.id                 # None while a finding is an error
    stick.ship(built, root)

The CLI and the studio both call `compile`. Per gesture the studio keeps the lift and passes it
back, so only apply, assemble and check run (em75: tens of ms, em01: about two seconds, most of
it the run budget). Nothing here writes: `stick.ship` and `inject.push` take a `Built`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...files import Extracted
from . import asm, check, editset, inject, ir, stick
from . import lift as lifting
from .budget import Budget
from .check import Finding
from .disasm import Scripts
from .editset import Applied, EditSet

if TYPE_CHECKING:
    from ..intel import SpeciesIntel
    from .brainfile import BrainFile

__all__ = ["Built", "compile", "lift_row"]


@dataclass
class Built:
    """What a row's edits come to."""

    row: int
    lift: ir.Program
    """The game's own program of the row."""
    edits: EditSet
    applied: Applied
    findings: list[Finding]
    """Apply's, then the checks'; each level `error`, `warning` or `info`."""
    image: asm.Image | None = None
    """The program laid out; None when it cannot be (an `ASSEMBLE` finding)."""
    container: bytes | None = None
    """The stick container; None while a finding is an error."""
    id: int | None = None
    """The container's id, the first 4 bytes of the image's SHA-1: what the installer carries."""

    @property
    def program(self) -> ir.Program:
        """Resolved, so it assembles as it is; the lift itself when nothing was edited."""
        return self.applied.program

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    runs: Budget | None = field(default=None, repr=False)

    @property
    def budget(self) -> Budget:
        """The run budget of the program, the one the checks used, else built when asked for: it
        is the slow part of a check."""
        if self.runs is None:
            self.runs = Budget(self.program)
        return self.runs

    @classmethod
    def of(cls, lift: ir.Program, es: EditSet, intel: SpeciesIntel | None = None) -> Built:
        """`es` applied to `lift`, vetted and packed. `es` is for `lift`'s row, or lists it in
        `also`; anything else is a DOC_BUILD finding."""
        applied = editset.apply(lift, es)
        findings = list(applied.findings)
        program = applied.program
        try:
            image = asm.assemble(program)
        except asm.AssembleError as e:
            findings.append(Finding("error", "ASSEMBLE", str(e)))
            return cls(lift.row, lift, es, applied, findings)
        runs = None
        if program is not lift:  # nothing edited, nothing for the checks to hold to the rules
            runs = Budget(program)
            findings += check.check(program, image, runs)
            if intel is not None:
                findings += _pairs(program, applied.touched, intel)
        built = cls(lift.row, lift, es, applied, findings, image, runs=runs)
        if built.ok:
            built.container = stick.build(program, image)
            built.id = inject.Head.parse(built.container).id
        return built


def _pairs(p: ir.Program, touched: Sequence[str], intel: SpeciesIntel) -> list[Finding]:
    """PAIR_UNKNOWN: a new or edited `enter` of a pair the species' intel has no record of, or
    that its census measured as never entered. An unmeasured pair says nothing."""
    if not (intel.has_static or intel.has_census):
        return []
    out = []
    for r in (r for r in p.routines() if r.name in touched):
        for n in ir.walk(r.body):
            if not (isinstance(n, ir.Insn) and n.pc is None and n.op.name == "enter"):
                continue
            main, sub = n.args.get("main"), n.args.get("sub")
            if not (isinstance(main, int) and isinstance(sub, int)):
                continue
            known = intel.pair(main, sub)
            if known is None:
                why = "neither the overlay's tables nor the census have it"
            elif known.entered == 0:
                why = "the census saw the engine never enter it, and a forced pair bounces back"
            else:
                continue
            text = f"enter ({main}, {sub}): {why}"
            out.append(Finding("warning", "PAIR_UNKNOWN", text, n.id, r.name))
    return out


def lift_row(game: Extracted, overlay: int, row: int) -> ir.Program:
    """The game's own program of species row `row`, served by `overlay`. Seconds for a large
    overlay (em01's): the caller keeps it."""
    return lifting.lift(Scripts(game, overlay), row)


def compile(  # noqa: A001
    bf: BrainFile,
    game: Extracted | None,
    row: int,
    *,
    lift: ir.Program | None = None,
    intel: SpeciesIntel | None = None,
) -> Built:
    """Row `row` of the file's overlay as the file edits it. A row the file has no edits for
    builds the game's own bytes. `lift` is the row's lift when the caller has it (then `game`
    is not read), and `intel` the overlay's species intel, which adds PAIR_UNKNOWN; ValueError
    if the overlay does not serve the row, or the intel is another species'."""
    if intel is not None and intel.host_species != bf.overlay:
        raise ValueError(
            f"the intel is em{intel.host_species:02d}'s, the file em{bf.overlay:02d}'s"
        )
    if lift is None:
        if game is None:
            raise ValueError("name the game, or give the row's lift")
        lift = lift_row(game, bf.overlay, row)
    elif lift.row != row:
        raise ValueError(f"the lift is row {lift.row}'s, not row {row}'s")
    return Built.of(lift, bf.edits(row) or EditSet.of(lift), intel)
