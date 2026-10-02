# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Push a stage edit into the running game: no disk write, the resident PAC is written over.

The three halves land differently. The visible mesh is compiled when an area loads, so a
mesh write shows only after the next load; a quest area evicts and re-reads its PAC on that
load, so the write has to land again while the loader puts the file down (`catch`, a write
watchpoint on the tail of the last edited entry). The village never re-reads, and an armed
watchpoint slows the whole game, so there the write alone is enough. Collision and textures
are read live and land at once, but a reload undoes them too (`hold` re-applies); a
climbable material is read at area load, so a climb edit needs a hold across the entry.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mhfu import addresses as a
from mhfu import files
from mhfu import stage as S
from mhfu.files import Extracted
from mhfu.memory import Memory, Unmapped
from mhp_formats.fu.stage import BASE, GRID_POINTER, TERM, TRI_SIZE, TRIS_POINTER
from ppsspp_debug import Client

from mhfu_studio.shell.findings import Finding

from . import ops as O
from .collision import Plan, plan
from .file import PROPS, TERRAIN, TEXTURES, StageFile
from .mesh import MeshEdit, diff_runs, edits
from .textures import TextureEdit, build

Log = Callable[[str], None]

IDENT_AT, IDENT_SIZE = 0x80, 32
"""A slice of the terrain PMO's tables, which differ per stage: whose PAC sits in a slot."""
CATCH_CHUNK = 0x40000
"""The loader copies a PAC in pieces this big, and a watchpoint stops it at the start of the
piece it sits in: whatever the edit puts in that piece is overwritten after the resume."""
GAP = 64
"""Diff runs closer than this are written as one."""
QUEST_CATCH = 120.0
"""Seconds a catch waits by default outside the village."""
CLIMB = (9, 10)
"""Materials that make a near-vertical triangle climbable."""


class NotThisStage(ValueError):
    """The PAC in the stage's slot is not that stage's, or its fixup has not run."""


def pac_address(mem: Memory, stage: int) -> int | None:
    slot = S.resident_files(mem).get(files.stage_pac(stage))
    return slot.data if slot else None


@contextmanager
def connect(port: int | None = None) -> Iterator[Client]:
    """PPSSPP's debugger; none running is a refusal (ValueError), not a traceback."""
    try:
        client = Client.connect(port=port)
    except ConnectionError as e:
        raise ValueError(str(e)) from None
    with client as c:
        yield c


def undo_path(stage: int) -> Path:
    """The stage's collision undo, one per user (`$XDG_STATE_HOME`), whichever route pushed:
    the next push adopts the scratch the last one wrote."""
    root = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(root) / "mhfu-studio" / "push" / f"st{stage:03d}_collision_undo.json"


def in_village(game: Extracted, stage: int) -> bool:
    """Row 0 of the map table: the village and its interiors, whose PAC is never re-read."""
    return stage in S.read_map_table(game)[0]


class Patch(Protocol):
    name: str

    def apply(self, mem: Memory, pac: int) -> None: ...

    def intact(self, mem: Memory, pac: int) -> bool:
        """The patch is still in: a reload has not put the file's bytes back."""
        ...


@dataclass
class Bytes:
    """An entry of the PAC (a PMO, the texture bank) written over where it differs."""

    name: str
    offset: int
    """The entry's offset in the PAC."""
    data: bytes
    original: bytes
    runs: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.runs:
            self.runs = diff_runs(self.data, self.original, GAP)

    @property
    def size(self) -> int:
        return sum(n for _, n in self.runs)

    def apply(self, mem: Memory, pac: int) -> None:
        for o, n in self.runs:
            mem.write(pac + self.offset + o, self.data[o : o + n])

    def verify(self, mem: Memory, pac: int) -> bool:
        return all(self._holds(mem, pac, o, n) for o, n in self.runs)

    def intact(self, mem: Memory, pac: int) -> bool:
        # the loader copies the file whole, so the first run going back is the signal; reading
        # every run each poll costs more than the window a reload leaves
        return not self.runs or self._holds(mem, pac, *self.runs[0])

    def _holds(self, mem: Memory, pac: int, o: int, n: int) -> bool:
        return mem.read(pac + self.offset + o, n) == self.data[o : o + n]


class ScratchError(ValueError):
    """The scratch is not free: the push refuses rather than write memory it does not own."""


@dataclass
class Collision:
    """A collision plan written into the running game: moved records where they are, added
    records and the changed cell lists in `scratch`, one grid word per changed cell.

    The scratch is written only where it is zero or holds what this document's last push
    wrote there (`owned`), never over anything else."""

    stage: StageFile
    plan: Plan
    scratch: int = a.STAGE_SCRATCH
    size: int = a.STAGE_SCRATCH.count or 0
    """Bytes the push may use from `scratch`."""
    name: str = "collision"
    undo: list[tuple[int, bytes, bytes]] = field(default_factory=list)
    """(address, the bytes there before, what the push wrote) of every write into the PAC."""
    owned: bytes = b""
    """What this document last wrote at `scratch`: the one non-zero content it may replace."""
    _probe: tuple[int, int, int, bytes] | None = None
    """(chunk, GRID_POINTER or TRIS_POINTER, offset from that pointer, what we wrote there)."""

    def pointers(self, mem: Memory, pac: int, chunk: int) -> tuple[int, int, int]:
        return chunk_pointers(mem, self.stage, pac, chunk)

    @property
    def scratch_used(self) -> int:
        p = self.plan
        lists = sum(4 * (len(r) + 1) for cells in p.cells.values() for r in cells.values())
        return TRI_SIZE * len(p.added) + lists

    def check_scratch(self, mem: Memory) -> None:
        """Raise ScratchError unless the plan fits the scratch and every byte it would write
        over is zero or this document's own."""
        need = max(self.scratch_used, len(self.owned))
        if self.scratch_used > self.size:
            raise ScratchError(
                f"the collision needs {self.scratch_used} bytes of scratch;"
                f" 0x{self.scratch:08X} has {self.size}"
            )
        here = mem.read(self.scratch, need)
        mine = self.owned.ljust(need, b"\0")
        foreign = [i for i, (x, y) in enumerate(zip(here, mine, strict=True)) if x and x != y]
        if foreign:
            at = foreign[0]
            raise ScratchError(
                f"scratch at 0x{self.scratch + at:08X} holds data this document did not write;"
                " refusing to write over it (give --scratch free memory)"
            )

    def adopt(self, saved: dict[str, Any]) -> None:
        """Take a previous push's undo file as this one's own: the scratch it wrote."""
        at, data = saved.get("scratch", [self.scratch, ""])
        if at == self.scratch:
            self.owned = bytes.fromhex(data)

    def apply(self, mem: Memory, pac: int) -> None:
        p = self.plan
        chunks = sorted({c for c, _, _ in p.moved} | {c for c, _ in p.added} | set(p.cells))
        live = {c: self.pointers(mem, pac, c) for c in chunks}
        self.check_scratch(mem)
        writes: list[tuple[int, bytes]] = []
        for c, t, tri in p.moved:
            writes.append((live[c][2] + TRI_SIZE * t, tri.to_bytes()))
        blob = bytearray()
        added = {}
        for k, (_, tri) in enumerate(p.added):
            added[k] = self.scratch + len(blob)
            blob += tri.to_bytes()
        probe = None
        for c, cells in sorted(p.cells.items()):
            _, grid, tris = live[c]
            for cell, refs in sorted(cells.items()):
                at = self.scratch + len(blob)
                words = [added[r.number] if r.added else tris + TRI_SIZE * r.number for r in refs]
                blob += b"".join(w.to_bytes(4, "little") for w in [*words, TERM])
                writes.append((grid + 4 * cell, at.to_bytes(4, "little")))
                probe = probe or (c, GRID_POINTER, 4 * cell, at.to_bytes(4, "little"))
        if probe is None and p.moved:
            c, t, tri = p.moved[0]
            probe = (c, TRIS_POINTER, TRI_SIZE * t, tri.to_bytes())
        # what the last push left past this one's end is ours and no longer linked: clear it
        mem.write(self.scratch, bytes(blob).ljust(len(self.owned), b"\0"))
        self.owned = bytes(blob)
        # a word still holding what the last apply wrote keeps that apply's "before"
        last = {addr: (old, new) for addr, old, new in self.undo}
        undo = []
        for addr, data in writes:
            here = mem.read(addr, len(data))
            old, new = last.get(addr, (here, b""))
            undo.append((addr, old if here == new else here, data))
            mem.write(addr, data)
        self._probe, self.undo = probe, undo

    def intact(self, mem: Memory, pac: int) -> bool:
        if self._probe is None:
            return True
        chunk, pointer, offset, data = self._probe
        _, grid, tris = self.pointers(mem, pac, chunk)
        at = (grid if pointer == GRID_POINTER else tris) + offset
        return mem.read(at, len(data)) == data

    def save_undo(self, path: Path, pac: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        undo = [[addr, old.hex(), new.hex()] for addr, old, new in self.undo]
        saved = {"stage": self.stage.number, "pac": pac, "undo": undo}
        path.write_text(json.dumps(saved | {"scratch": [self.scratch, self.owned.hex()]}))


def chunk_pointers(mem: Memory, stage: StageFile, pac: int, chunk: int) -> tuple[int, int, int]:
    """(chunk + BASE, grid, triangle array) of a resident chunk, as the loader fixed them up."""
    at = pac + stage.chunk_offset(chunk)
    grid, tris = mem.u32(at + GRID_POINTER), mem.u32(at + TRIS_POINTER)
    want = pac + stage.tri_offset(chunk, 0)
    if tris != want:
        raise NotThisStage(f"chunk {chunk}: triangles at 0x{tris:08X}, the file says 0x{want:08X}")
    return at + BASE, grid, tris


def undo(mem: Memory, path: Path) -> int:
    """Take an undo file's writes back out, newest first, where each still holds what the push
    wrote (a reload or another push has replaced the rest); returns how many."""
    n = 0
    for addr, old, new in reversed(json.loads(path.read_text())["undo"]):
        if mem.read(addr, len(new) // 2) == bytes.fromhex(new):
            mem.write(addr, bytes.fromhex(old))
            n += 1
    return n


@dataclass
class Push:
    """What one edit list writes into one stage."""

    stage: StageFile
    mesh: list[Bytes] = field(default_factory=list)
    collision: Collision | None = None
    textures: Bytes | None = None
    log: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def patches(self) -> list[Patch]:
        out: list[Patch] = [*self.mesh]
        if self.collision:
            out.append(self.collision)
        if self.textures:
            out.append(self.textures)
        return out


def prepare(
    stage: StageFile,
    ops: Sequence[O.Op],
    base_dir: Path,
    *,
    mesh: bool = True,
    collision: bool = True,
    textures: bool = True,
    scratch: int = a.STAGE_SCRATCH,
    scratch_size: int = a.STAGE_SCRATCH.count or 0,
) -> Push:
    """The writes for the halves asked for, built offline."""
    out = Push(stage)
    if mesh:
        for sub, edit in edits(stage, ops, base_dir).items():
            out.log += edit.log
            out.findings += edit.findings
            if not edit.safe:
                out.log.append(f"sub {sub}: not resident-safe, left out")
            elif edit.data == edit.original:
                out.log.append(f"sub {sub}: the same as the file")
            else:
                out.mesh.append(_entry(stage, sub, edit))
    if collision and any(O.touches_collision(op) for op in ops):
        p = plan(stage, ops, base_dir)
        out.log += p.log
        out.findings += p.findings
        if not p.empty:
            out.collision = Collision(stage, p, scratch, scratch_size)
    if textures and any(O.touches_textures(op) for op in ops):
        bank = build(stage, ops, base_dir)
        out.log += bank.log
        out.findings += bank.findings
        if bank.data != bank.original:
            out.textures = _entry(stage, TEXTURES, bank)
    return out


def _entry(stage: StageFile, sub: int, edit: MeshEdit | TextureEdit) -> Bytes:
    return Bytes(f"sub {sub}", stage.table[sub][0], edit.data, edit.original)


def describe(push: Push) -> list[str]:
    """What the push will write, a line per half."""
    out = [f"{b.name}: {len(b.runs)} runs, {b.size} bytes to write" for b in push.mesh]
    if push.textures:
        out.append(f"textures: {len(push.textures.runs)} runs, {push.textures.size} bytes")
    if push.collision:
        p = push.collision.plan
        cells = sum(len(c) for c in p.cells.values())
        out.append(
            f"collision: {len(p.moved)} moved, {len(p.added)} added, {cells} cells relinked,"
            f" {push.collision.scratch_used} bytes of scratch"
        )
    return out


def default_catch(push: Push, log: Log = print) -> float:
    """No catch in the village or without a mesh edit; QUEST_CATCH elsewhere."""
    game = push.stage.game
    if not push.mesh:
        return 0.0
    if game is not None and in_village(game, push.stage.number):
        log(
            "village: no catch (its PAC is never re-read); the mesh shows after an interior"
            " round trip"
        )
        return 0.0
    return QUEST_CATCH


def climbs(plan: Plan) -> int:
    """Triangles the plan makes climbable: what only an area load picks up."""
    tris = [t for _, _, t in plan.moved] + [t for _, t in plan.added]
    return sum(t.flags.material in CLIMB for t in tris)


def run(
    client: Client,
    mem: Memory,
    push: Push,
    *,
    catch_for: float = 0.0,
    hold_for: float = 0.0,
    undo_file: Path | None = None,
    log: Log = print,
) -> None:
    """Write the push in the order the game needs: the mesh (caught on the next reload when
    `catch_for` seconds), then collision and textures, which that reload would have undone;
    `hold_for` seconds re-applies all three across further loads."""
    stage = push.stage
    here = S.map_manager(mem).stage
    if here != stage.number:
        log(f"[!] the game shows st{here:03d}; walk to {stage.label} first")
    pac = pac_address(mem, stage.number)
    if pac is None:
        raise ValueError(f"{stage.label}.pac is not resident: stand in the area first")
    saved = None
    if push.collision:
        if undo_file is not None and undo_file.exists():
            saved = json.loads(undo_file.read_text())
            if saved.get("stage") == stage.number:
                push.collision.adopt(saved)
        push.collision.check_scratch(mem)  # before anything is written
    for b in push.mesh:
        b.apply(mem, pac)
        log(f"{b.name}: written at 0x{pac + b.offset:08X}, verified {b.verify(mem, pac)}")
    if push.mesh and catch_for:
        if lost := shadowed(push):
            log(
                f"[!] {lost} edited bytes share the loader's piece with the watchpoint:"
                " a catch loses them"
            )
        catch(client, mem, push, catch_for, log)
        pac = pac_address(mem, stage.number) or pac
    if push.collision:
        if saved is not None and undo_file is not None and (n := undo(mem, undo_file)):
            log(f"collision: took the last push's {n} writes back out")
        push.collision.apply(mem, pac)
        log(f"collision: applied at 0x{pac:08X}")
        if undo_file is not None:
            push.collision.save_undo(undo_file, pac)
            log(f"collision: undo in {undo_file}")
        if climbs(push.collision.plan) and not hold_for:
            log("[!] a climbable material is read at area load: hold the push and walk out and in")
    if push.textures:
        push.textures.apply(mem, pac)
        log(f"textures: written, verified {push.textures.verify(mem, pac)}")
    if hold_for:
        hold(mem, stage.number, push.patches, hold_for, log)


def shadowed(push: Push) -> int:
    """Edited mesh bytes a catch loses: those in the loader's piece that holds the watchpoint."""
    if not push.mesh:
        return 0
    tail = max(b.offset + len(b.original) for b in push.mesh) - 4
    start = tail & ~(CATCH_CHUNK - 1)
    return sum(
        max(0, b.offset + o + n - max(b.offset + o, start)) for b in push.mesh for o, n in b.runs
    )


def catch(
    client: Client,
    mem: Memory,
    push: Push,
    seconds: float,
    log: Log = print,
    poll: float = 0.25,
) -> bool:
    """Wait up to `seconds` for the stage's PAC to be re-read, and put the mesh edit on top
    while the loader is stopped on it. A slot can be reused by another file: the first write
    to the watched word that is not this stage's ends the watch, so a file that keeps writing
    there never stops the game write by write."""
    if not push.mesh:
        return False
    label = push.stage.label
    deadline = time.monotonic() + seconds
    log(
        f"watching the tail of {label}.pac for {seconds:.0f} s: take the transition now"
        " (the game runs slower while it is armed)"
    )
    while time.monotonic() < deadline:
        addr = pac_address(mem, push.stage.number)
        if addr is None:
            time.sleep(poll)
            continue
        caught = _watch(client, mem, push, addr, deadline, poll)
        if caught == "moved":
            continue
        if caught is not None:
            client.resume()
        if isinstance(caught, tuple):
            here, ok = caught
            log(f"caught the reload at 0x{here:08X}; re-applied, verified: {ok}")
            return True
        if caught == "reused":
            log(
                f"{label}'s slot at 0x{addr:08X} went to another file: nothing caught; push"
                " again with --hold to carry the edit across the next transition"
            )
            return False
        break
    log(f"nothing caught in {seconds:.0f} s: the PAC was not re-read")
    return False


def _watch(
    client: Client, mem: Memory, push: Push, addr: int, deadline: float, poll: float
) -> tuple[int, bool] | str | None:
    """One watch on the tail of the PAC at `addr`, removed before this returns:
    (where, verified) with the CPU left stopped on a reload of this stage; "reused", the CPU
    left stopped, when a write there is another file's; "moved" when the PAC moved; None
    when time ran out."""
    stage = push.stage
    tail = max(b.offset + len(b.original) for b in push.mesh) - 4
    ident_at = stage.table[TERRAIN][0] + IDENT_AT
    ident = stage.data[ident_at : ident_at + IDENT_SIZE]
    with client.watchpoint(addr + tail, 4, write=True) as hits:
        while (left := deadline - time.monotonic()) > 0:
            try:
                hits.next(timeout=min(poll, left))
            except TimeoutError:
                now = pac_address(mem, stage.number)
                if now is not None and now != addr:
                    return "moved"
                continue
            here = pac_address(mem, stage.number) or addr
            if mem.read(here + ident_at, IDENT_SIZE) != ident:
                return "reused"
            for b in push.mesh:
                b.apply(mem, here)
            return here, all(b.verify(mem, here) for b in push.mesh)
    return None


def hold(
    mem: Memory,
    stage: int,
    patches: Sequence[Patch],
    seconds: float,
    log: Log = print,
    period: float = 0.03,
) -> tuple[int, int]:
    """Re-apply every patch a reload undid, for `seconds`. Returns (PAC moves, re-applies)."""
    deadline = time.monotonic() + seconds
    last = pac_address(mem, stage)
    moves = again = 0
    while time.monotonic() < deadline:
        try:
            pac = pac_address(mem, stage)
            if pac is not None:
                if pac != last:
                    moves += 1
                    log(
                        f"the PAC moved to 0x{pac:08X}"
                        if last
                        else f"the PAC is back at 0x{pac:08X}"
                    )
                    last = pac
                for p in patches:
                    if not p.intact(mem, pac):
                        p.apply(mem, pac)
                        again += 1
                        log(f"{p.name}: re-applied at 0x{pac:08X}")
        except (NotThisStage, Unmapped) as e:
            log(f"(waiting: {e})")
        time.sleep(period)
    log(f"held {seconds:.0f} s: {moves} PAC moves, {again} re-applies")
    return moves, again


def restore(mem: Memory, stage: StageFile, log: Log = print) -> None:
    """The file's meshes, bank, grid words and triangle records back, whatever was pushed."""
    pac = pac_address(mem, stage.number)
    if pac is None:
        raise ValueError(f"{stage.label}.pac is not resident")
    for sub in (TERRAIN, TEXTURES, PROPS):
        off, size = stage.table[sub]
        if size:
            mem.write(pac + off, stage.data[off : off + size])
    for c, hits in enumerate(stage.chunks):
        base, grid, tris = chunk_pointers(mem, stage, pac, c)
        heads = b"".join((base + h).to_bytes(4, "little") for h in hits.heads())
        mem.write(grid, heads)
        at = stage.tri_offset(c, 0)
        mem.write(tris, stage.data[at : at + TRI_SIZE * len(hits.tris)])
    log(f"{stage.label}: the file's meshes, textures and collision are back")
