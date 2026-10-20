# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An assembled AI script in the running game, and the container ('MHAI') it travels in.

    img = asm.assemble(prog)
    inject.push(session, prog, img)      # container to SCRIPT_PUSH, the row and live monsters on it
    inject.status(session.mem)
    inject.restore(session, prog.row)    # the game's own script back
    inject.around(session.mem, header)   # the container a monster's header is in: pushed or stick

The container's layout is in docs/formats/ai_script.md; the framework loads the same bytes from the
memory stick. A push lasts until the next quest load re-reads the species' overlay.
"""

from __future__ import annotations

import hashlib
import struct
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property

from ppsspp_debug import Client, Disconnected

from ... import addresses as a
from ...files import SPECIES_ROWS
from ...live import script as live_script
from ...live.script import ScriptState
from ...live.session import Session
from ...memory import Memory, Unmapped
from ...writes import Plan, Pushed, Refused, Write, apply
from . import asm, ir
from .disasm import VARIANTS

MAGIC = b"MHAI"
VERSION = 1
HEAD = 0x40
IMAGE_OFF = HEAD
"""Where the image starts in a container."""
GUARD = 16
"""Bytes at SCRIPT_PUSH a plan guards: a container's first fields, or zeros."""
PUSH_SIZE = a.CLI_BRIDGE_BLOCK - a.SCRIPT_PUSH
TIMEOUT = 5.0
"""Seconds `push` waits for a monster of the species to tick before it pauses the CPU."""
NEEDS_64 = (
    "SCRIPT_PUSH is outside the emulator's memory: a push needs PPSSPP's 64 MB mode (memory=64)"
)

SCAN = 0x10000
"""Bytes below a header that `around` looks for its container in."""

_ALIGN = 8
_WORD = 4
_CHUNK = 0x1000
_HEAD = struct.Struct("<4sHH12I8x")
_ENTRY = (4, 8, 8, 16)
"""Bytes of a reloc, patch, origin and item entry."""

_E = a.ENTITY
_PLAIN = (
    _E.SCRIPT_HEADER,
    *live_script.INTERRUPT_STARTS.values(),
    _E.SCRIPT_CALL_RETURN,
    _E.SCRIPT_RET_RETURN,
    _E.SCRIPT_ROUTE,
    _E.SCRIPT_DEEP_RETURN,
    _E.SCRIPT_LOOP_RETURN,
    _E.SCRIPT_SPOT_RETURN,
)
_NAMES = {
    **{int(f): str(f.name) for f in _PLAIN},
    **{int(_E.SCRIPT_PCS) + 4 * i: f"SCRIPT_PCS[{i}]" for i in range(len(live_script.CONTEXTS))},
}
"""The entity's script words by offset."""


class ContainerError(ValueError):
    """Bytes that are no container this version reads."""


class Kind(IntEnum):
    """What an item of the container is."""

    ROUTINE = 0
    TABLE = 1
    DATA = 2
    HEADER = 3
    BLOB = 4


@dataclass(frozen=True)
class Item:
    """An item of the native region: where it was, where it is in the image and its size there."""

    native: int
    offset: int
    size: int
    kind: Kind


Span = tuple[int, int]


@dataclass(frozen=True)
class Head:
    """The container's first 0x40 bytes: who, where it loads and where each section is."""

    row: int
    id: int
    load: int
    image: Span
    """Offset and size."""
    relocs: Span
    """Offset and count; the same for each section after it."""
    patches: Span
    origin: Span
    items: Span

    @classmethod
    def parse(cls, data: bytes) -> Head:
        if len(data) < HEAD:
            raise ContainerError(f"{len(data)} bytes are shorter than the {HEAD}-byte header")
        magic, version, row, id_, load, *flat = _HEAD.unpack_from(data)
        if magic != MAGIC:
            raise ContainerError(f"it starts with {magic!r}, not {MAGIC!r}")
        if version != VERSION:
            raise ContainerError(f"it is version {version}, this reads {VERSION}")
        image, relocs, patches, origin, items = zip(flat[::2], flat[1::2], strict=True)
        return cls(row, id_, load, image, relocs, patches, origin, items)

    @property
    def extent(self) -> int:
        """The bytes the container spans, whole sections and 8-aligned."""
        spans = (self.relocs, self.patches, self.origin, self.items)
        ends = [off + n * size for (off, n), size in zip(spans, _ENTRY, strict=True)]
        end = max(HEAD, self.image[0] + self.image[1], *ends)
        return -(-end // _ALIGN) * _ALIGN

    @property
    def addresses(self) -> range:
        """Where the image sits."""
        return range(self.load, self.load + self.image[1])


@dataclass(frozen=True)
class Container:
    """A container read back; `rebase` and `native` map addresses across it. Addresses of the
    image are `load` plus an offset, so a stick container (load 0) answers in offsets."""

    head: Head
    image: bytes
    relocs: tuple[int, ...]
    patches: tuple[tuple[int, int], ...]
    """Each header's native address and image offset."""
    origin: tuple[tuple[int, int], ...]
    """Native addresses of instructions, markers and block ends, with their image offsets."""
    items: tuple[Item, ...]

    @classmethod
    def read(cls, data: bytes) -> Container:
        """Parse and check `data`: header, bounds, and that the id is the image's."""
        head = Head.parse(data)
        if head.extent > len(data):
            raise ContainerError(f"it needs {head.extent} bytes and {len(data)} are there")
        off, size = head.image
        image = bytes(data[off : off + size])

        def table(span: Span, fmt: str) -> list[tuple[int, ...]]:
            step = struct.Struct("<" + fmt)
            return [step.unpack_from(data, span[0] + k * step.size) for k in range(span[1])]

        relocs = tuple(w for (w,) in table(head.relocs, "I"))
        patches = tuple((n, o) for n, o in table(head.patches, "2I"))
        origin = tuple((n, o) for n, o in table(head.origin, "2I"))
        try:
            items = tuple(Item(n, o, s, Kind(k)) for n, o, s, k in table(head.items, "4I"))
        except ValueError as e:
            raise ContainerError(f"an item has an unknown kind ({e})") from None
        box = Container(head, image, relocs, patches, origin, items)
        box._check()
        return box

    def _check(self) -> None:
        size = len(self.image)
        if any(r + 4 > size for r in self.relocs):
            raise ContainerError("a relocation lies outside the image")
        if any(o > size for _, o in (*self.patches, *self.origin)):
            raise ContainerError("an offset lies outside the image")
        if any(i.offset + i.size > size for i in self.items):
            raise ContainerError("an item lies outside the image")
        if _id(self._moved(0)) != self.head.id:
            raise ContainerError("its id is not the image's")

    def _moved(self, load: int) -> bytes:
        """The image with its pointer words written for `load`."""
        img = asm.Image(self.image, self.head.load, list(self.relocs), {}, {}, {})
        return img.at(load)

    @property
    def row(self) -> int:
        return self.head.row

    @property
    def load(self) -> int:
        return self.head.load

    @cached_property
    def headers(self) -> dict[int, int]:
        """Each header's native address and its address in the image."""
        return {n: self.load + o for n, o in self.patches}

    @cached_property
    def _forward(self) -> dict[int, int]:
        return {**dict(self.patches), **dict(self.origin)}

    @cached_property
    def _backward(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for native, off in sorted((*self.patches, *self.origin)):
            out.setdefault(off, native)
        return out

    @cached_property
    def _spans(self) -> tuple[list[Item], list[int], list[Item], list[int]]:
        """The tables and data by native address and by image offset, each with its keys."""
        kept = [i for i in self.items if i.kind in (Kind.TABLE, Kind.DATA)]
        by_native = sorted(kept, key=lambda i: i.native)
        by_offset = sorted(kept, key=lambda i: i.offset)
        return by_native, [i.native for i in by_native], by_offset, [i.offset for i in by_offset]

    def rebase(self, addr: int) -> int | None:
        """The image address of the native `addr`: an instruction, marker or block end, a header,
        or a byte inside a table or data; None for anything else."""
        off = self._forward.get(addr)
        if off is None:
            held, keys, _, _ = self._spans
            k = bisect_right(keys, addr) - 1
            if k >= 0 and addr - held[k].native < held[k].size:
                off = held[k].offset + addr - held[k].native
        return None if off is None else self.load + off

    def native(self, addr: int) -> int | None:
        """The native address of the image address `addr`, the inverse of `rebase`; the lowest
        when several map to one place."""
        off = addr - self.load
        if not 0 <= off <= len(self.image):
            return None
        if off in self._backward:
            return self._backward[off]
        _, _, held, keys = self._spans
        k = bisect_right(keys, off) - 1
        if k >= 0 and off - held[k].offset < held[k].size:
            return held[k].native + off - held[k].offset
        return None

    def entry(self, header: int) -> int | None:
        """The routine in slot 0 of the base list of the image header at `header`."""
        lst = self._pointer(header)
        return None if lst is None else self._pointer(lst)

    def _pointer(self, addr: int) -> int | None:
        """The image address in the word at `addr`; None for an empty or external word."""
        off = addr - self.load
        if off not in self._moving:
            return None
        return int.from_bytes(self.image[off : off + 4], "little")

    @cached_property
    def _moving(self) -> frozenset[int]:
        return frozenset(self.relocs)

    def place(self, load: int) -> bytes:
        """These bytes written for an image at `load`."""
        return _pack(
            self.row,
            self.head.id,
            load,
            self._moved(load),
            self.relocs,
            self.patches,
            self.origin,
            self.items,
        )


def _id(image: bytes) -> int:
    """The first 4 bytes of the SHA-1 of an image written for base 0."""
    return int.from_bytes(hashlib.sha1(image).digest()[:4], "little")


def _pack(
    row: int,
    id_: int,
    load: int,
    image: bytes,
    relocs: Sequence[int],
    patches: Sequence[tuple[int, int]],
    origin: Sequence[tuple[int, int]],
    items: Sequence[Item],
) -> bytes:
    out = bytearray(HEAD)
    spans = [HEAD, len(image)]
    out += image
    sections = (
        (len(relocs), struct.pack(f"<{len(relocs)}I", *relocs)),
        (len(patches), b"".join(struct.pack("<2I", *e) for e in patches)),
        (len(origin), b"".join(struct.pack("<2I", *e) for e in origin)),
        (
            len(items),
            b"".join(struct.pack("<4I", i.native, i.offset, i.size, i.kind) for i in items),
        ),
    )
    for count, raw in sections:
        out += bytes(-len(out) % _ALIGN)
        spans += [len(out), count]
        out += raw
    out += bytes(-len(out) % _ALIGN)
    _HEAD.pack_into(out, 0, MAGIC, VERSION, row, id_, load, *spans)
    return bytes(out)


def _items(p: ir.Program, img: asm.Image) -> list[Item]:
    """The native items of `p`, by native address."""
    out = []
    for it in p.items:
        if it.va is None:
            continue
        if isinstance(it, ir.Blob):  # unnamed and never moved: it stays where it was lifted
            off, size, kind = it.va - p.base, len(it.data), Kind.BLOB
            if img.data[off : off + size] != it.data:
                raise ValueError(f"the blob at 0x{it.va:08X} is not where it was lifted")
        else:
            off, size = img.items[it.name]
            kind = {
                ir.Routine: Kind.ROUTINE,
                ir.Table: Kind.TABLE,
                ir.Data: Kind.DATA,
                ir.Header: Kind.HEADER,
            }[type(it)]
        out.append(Item(it.va, off, size, kind))
    return sorted(out, key=lambda i: i.native)


def container(p: ir.Program, img: asm.Image, load: int = 0, origin: bool = True) -> bytes:
    """The container of an assembled program, its image written for `load`. A stick container
    (`load` 0, `origin` False) leaves out the maps a live push rebinds monsters with."""
    return _pack(
        p.row,
        _id(img.at(0)),
        load,
        img.at(load),
        img.relocs,
        sorted(img.headers.items()),
        sorted(img.origin.items()) if origin else (),
        _items(p, img) if origin else (),
    )


# --- the plan


@dataclass(frozen=True)
class _Route:
    """One move of script words: which are the script's, where each goes."""

    headers: frozenset[int]
    mine: Callable[[int], bool]
    remap: Callable[[int], int | None]
    entry: Callable[[int], int | None]
    """The routine a restarted entity begins at, by its new header."""


def _words(s: ScriptState) -> dict[int, int]:
    """The entity's script words by offset."""
    out = {
        int(_E.SCRIPT_HEADER): s.header,
        int(_E.SCRIPT_CALL_RETURN): s.call_return,
        int(_E.SCRIPT_RET_RETURN): s.ret_return,
        int(_E.SCRIPT_ROUTE): s.route,
        int(_E.SCRIPT_DEEP_RETURN): s.deep_return,
        int(_E.SCRIPT_LOOP_RETURN): s.loop_return,
        int(_E.SCRIPT_SPOT_RETURN): s.spot_return,
    }
    out |= {int(f): s.interrupts[bit] for bit, f in live_script.INTERRUPT_STARTS.items()}
    out |= {int(_E.SCRIPT_PCS) + 4 * i: pc for i, pc in enumerate(s.pcs)}
    return dict(sorted(out.items()))


def _runs(
    parts: Sequence[tuple[int, bytes, str]],
) -> list[tuple[int, tuple[tuple[bytes, str], ...]]]:
    """Adjacent parts as one run each, with the label of every part."""
    out: list[tuple[int, list[tuple[bytes, str]]]] = []
    for at, data, label in sorted(parts):
        if out and out[-1][0] + sum(len(d) for d, _ in out[-1][1]) == at:
            out[-1][1].append((data, label))
        else:
            out.append((at, [(data, label)]))
    return [(at, tuple(pieces)) for at, pieces in out]


def _bind(route: _Route, s: ScriptState) -> tuple[list[Write], str]:
    """The writes that move one entity's script onto `route`, and a note on how."""
    old = _words(s)
    new = dict(old)
    stuck = []
    for off, w in old.items():
        if w == 0 or not route.mine(w):
            continue
        if (moved := route.remap(w)) is None:
            stuck.append(off)
        else:
            new[off] = moved
    context, base = s.context, s.base
    who = f"entity 0x{s.entity:08X}"
    if not stuck:
        note = f"{who} rebased"
    else:
        header = route.remap(s.header)
        entry = None if header is None else route.entry(header)
        if header is None or entry is None:
            raise Refused(f"{who} needs a restart and its script has no base routine to start at")
        new[int(_E.SCRIPT_HEADER)] = header
        new |= {int(_E.SCRIPT_PCS) + 4 * i: entry for i in range(len(s.pcs))}
        context = base = 0
        bad = stuck[0]
        more = f" and {len(stuck) - 1} more" if len(stuck) > 1 else ""
        note = (
            f"{who} restarted: {_NAMES[bad]} 0x{old[bad]:08X}{more} has no place in the new script"
        )
    at = s.entity
    guards = [(at + off, w.to_bytes(4, "little"), _NAMES[off]) for off, w in old.items()] + [
        (at + int(_E.SCRIPT_CONTEXT), bytes([s.context]), "SCRIPT_CONTEXT"),
        (at + int(_E.SCRIPT_BASE), s.base.to_bytes(2, "little"), "SCRIPT_BASE"),
    ]
    changes = [
        (at + off, w.to_bytes(4, "little"), f"{_NAMES[off]} 0x{old[off]:08X} -> 0x{w:08X}")
        for off, w in new.items()
        if w != old[off]
    ]
    if context != s.context:
        label = f"SCRIPT_CONTEXT 0x{s.context:02X} -> 0x{context:02X}"
        changes.append((at + int(_E.SCRIPT_CONTEXT), bytes([context]), label))
    if base != s.base:
        label = f"SCRIPT_BASE {s.base} -> {base}"
        changes.append((at + int(_E.SCRIPT_BASE), base.to_bytes(2, "little"), label))
    if not changes:
        return [], f"{who} unchanged"
    held = tuple((run_at, b"".join(d for d, _ in pieces)) for run_at, pieces in _runs(guards))
    runs = _runs(changes)
    guarded = Write(f"{who}: script words", *runs[0], held)
    return [guarded, *(Write(f"{who}: script words", *run) for run in runs[1:])], note


def _old(home: bytes, row: int) -> Container | None:
    """The container `home` holds, None for zeros; a refusal for anything else, or for another
    row's, whose words and monsters a second script would strand."""
    if len(home) < GUARD:
        raise ValueError(f"home is the bytes at SCRIPT_PUSH, at least {GUARD} of them")
    if not any(home[:GUARD]):
        return None
    if home[:4] != MAGIC:
        raise Refused("SCRIPT_PUSH holds something that is no script container; it is not free")
    try:
        old = Container.read(home)
    except ContainerError as e:
        raise Refused(f"the container at SCRIPT_PUSH does not read ({e})") from None
    if old.row != row:
        raise Refused(
            f"SCRIPT_PUSH has row {old.row}'s script, not row {row}'s; restore row {old.row} first"
        )
    return old


def _placed(p: ir.Program, source: asm.Image | Container) -> bytes:
    """The container written for an image at SCRIPT_PUSH."""
    load = int(a.SCRIPT_PUSH) + IMAGE_OFF
    if isinstance(source, Container):
        if source.row != p.row:
            raise ValueError(f"the container is row {source.row}'s, the program row {p.row}'s")
        return source.place(load)
    return container(p, source, load)


def _row_write(row: int, words: Sequence[int], moved: Sequence[int], what: str) -> Write:
    at = int(a.SCRIPT_HEADERS) + row * a.SPECIES.stride
    pieces = tuple(
        (struct.pack("<I", new), f"map type {k}: 0x{old:08X} -> 0x{new:08X}")
        for k, (old, new) in enumerate(zip(words, moved, strict=True))
    )
    return Write(what, at, pieces, ((at, struct.pack(f"<{len(words)}I", *words)),))


def _check_row(row: int, words: Sequence[int]) -> None:
    if len(words) != VARIANTS:
        raise ValueError(f"a row has {VARIANTS} header words, not {len(words)}")


def plan(
    p: ir.Program,
    source: asm.Image | Container,
    live: Sequence[ScriptState],
    row_words: Sequence[int],
    home: bytes,
    staged: Sequence[Container] = (),
) -> Plan:
    """The writes that put `source` at SCRIPT_PUSH, point the row at it and rebind the live
    monsters running it. `row_words` are the row's header words and `home` the bytes at
    SCRIPT_PUSH (the whole container if there is one) as the game holds them; `staged` the stick
    containers they point into (`around`), whose monsters restart on `source` (no maps)."""
    _check_row(p.row, row_words)
    old = _old(home, p.row)
    data = _placed(p, source)
    if len(data) > PUSH_SIZE:
        raise Refused(f"the container is {len(data)} bytes; SCRIPT_PUSH holds {PUSH_SIZE}")
    new = Container.read(data)
    region = range(p.base, p.end or p.base + len(new.image))
    boxes = [b for b in (old, *staged) if b is not None and b.row == p.row]

    def owner(w: int) -> Container | None:
        return next((b for b in boxes if w in b.head.addresses), None)

    def mine(w: int) -> bool:
        return w in region or owner(w) is not None

    def remap(w: int) -> int | None:
        box = owner(w)
        native = w if box is None else box.native(w)
        return None if native is None else new.rebase(native)

    headers = frozenset(new.headers) | {h for b in boxes for h in b.headers.values()}
    hits = [w in headers for w in row_words]
    if not any(hits):
        raise Refused(
            f"none of row {p.row}'s header words is this script's (not in a quest, or another game)"
        )
    moved = [remap(w) if hit else w for w, hit in zip(row_words, hits, strict=True)]
    if None in moved:
        raise Refused(f"a header word of row {p.row} has no place in the new script")
    route = _Route(headers, mine, remap, new.entry)
    at = int(a.SCRIPT_PUSH)
    label = f"container 'MHAI' row {p.row} id {new.head.id:08X}"
    writes = [
        Write("container", at, ((data, label),), ((at, bytes(home[:GUARD])),)),
        _row_write(p.row, row_words, [m for m in moved if m is not None], f"row {p.row} headers"),
    ]
    notes = []
    targets = [s for s in live if s.header in headers]
    for s in targets:
        more, note = _bind(route, s)
        writes += more
        notes.append(note)
    if not targets:
        notes.append(f"no live monster runs row {p.row}'s script")
    return Plan(tuple(writes), tuple(notes))


def plan_restore(
    row: int,
    live: Sequence[ScriptState],
    row_words: Sequence[int],
    home: bytes,
    entry: Callable[[int], int | None],
) -> Plan:
    """The writes that put the game's own script back: the row's header words and the monsters
    running the container at SCRIPT_PUSH, which is then cleared. `entry(header)` is the routine
    in slot 0 of the base list of a native header, where a restarted monster begins."""
    _check_row(row, row_words)
    old = _old(home, row)
    if old is None:
        raise Refused("nothing is pushed: SCRIPT_PUSH holds no container")
    headers = frozenset(old.headers.values())
    route = _Route(headers, old.head.addresses.__contains__, old.native, entry)
    hits = [w in headers for w in row_words]
    moved = [old.native(w) if hit else w for w, hit in zip(row_words, hits, strict=True)]
    if None in moved:
        raise Refused(f"a header word of row {row} has no native header")
    writes: list[Write] = []
    notes = []
    if any(hits):
        writes.append(
            _row_write(row, row_words, [m for m in moved if m is not None], f"row {row} headers")
        )
    else:
        notes.append(f"no header word of row {row} pointed into the container")
    for s in (s for s in live if s.header in headers):
        more, note = _bind(route, s)
        writes += more
        notes.append(note)
    at = int(a.SCRIPT_PUSH)
    writes.append(
        Write("container", at, ((bytes(old.head.extent), "cleared"),), ((at, bytes(home[:GUARD])),))
    )
    return Plan(tuple(writes), tuple(notes))


# --- the running game


@dataclass(frozen=True)
class _Snapshot:
    live: list[ScriptState]
    row_words: tuple[int, ...]
    home: bytes


def _row_at(row: int) -> int:
    return int(a.SCRIPT_HEADERS) + row * int(a.SPECIES.stride)


def _home(mem: Memory) -> bytes:
    """The container at SCRIPT_PUSH if there is one, else its first bytes."""
    head = mem.read(a.SCRIPT_PUSH, HEAD)
    if head[:4] != MAGIC:
        return head
    try:
        size = Head.parse(head).extent
    except ContainerError:
        return head
    return mem.read(a.SCRIPT_PUSH, size) if HEAD < size <= PUSH_SIZE else head


def pushed(mem: Memory, known: Container | None = None) -> Container | None:
    """The container at SCRIPT_PUSH, `known` while its header reads the same; None when there is
    none, it does not read, or the address is outside the emulator's memory."""
    try:
        head = mem.read(a.SCRIPT_PUSH, HEAD)
        if head[:4] != MAGIC:
            return None
        if known is not None and Head.parse(head) == known.head:
            return known
        return Container.read(_home(mem))
    except (Unmapped, ContainerError):
        return None


def _at(mem: Memory, base: int, header: int, known: Container | None) -> Container | None:
    """The container at `base` whose image holds `header`; `known` while its head reads the same."""
    if base not in a.RAM:
        return None
    try:
        raw = mem.read(base, HEAD)
        if raw[:4] != MAGIC:
            return None
        head = Head.parse(raw)
        if not head.load <= header < head.load + head.image[1]:
            return None
        if known is not None and head == known.head:
            return known
        if head.extent > PUSH_SIZE:  # a head that is no container's: do not read what it claims
            return None
        return Container.read(mem.read(base, head.extent))
    except (Unmapped, ContainerError):
        return None


def around(mem: Memory, header: int, known: Container | None = None) -> Container | None:
    """The container whose image holds the monster script header `header`, pushed or staged from
    the stick; None for the game's own script.

    Callers keep the result per header value and pass it back as `known`: it is tried first, its
    header offsets placing the container, and returned itself while its head reads the same.
    Otherwise the `SCAN` bytes below `header` are searched for a word-aligned magic: a few
    requests, and as many as the span has pages for a header that is the game's.
    """
    if known is not None:
        for off in sorted({off for _, off in known.patches}):
            box = _at(mem, header - off - known.head.image[0], header, known)
            if box is not None:
                return box
    low = -(-max(header - SCAN, a.RAM.start) // _WORD) * _WORD
    end = header - header % _WORD + _WORD  # the bases are words, up to the header
    while end > low:
        start = max((end - 1) // _CHUNK * _CHUNK, low)  # a chunk never spans a page
        try:
            data = mem.read(start, end - start)
        except Unmapped:
            return None
        i = len(data)
        while (i := data.rfind(MAGIC, 0, i)) >= 0:
            if (start + i) % _WORD == 0:
                box = _at(mem, start + i, header, known)
                if box is not None:
                    return box
        end = start
    return None


def _species(mem: Memory, row: int) -> list[ScriptState]:
    return [s for s in live_script.monsters(mem).values() if s.species == row]


def _snapshot(mem: Memory, row: int) -> _Snapshot:
    live = _species(mem, row)
    words = struct.unpack(f"<{VARIANTS}I", mem.read(_row_at(row), 4 * VARIANTS))
    return _Snapshot(live, words, _home(mem))


def _stop(client: Client, entities: Sequence[int], timeout: float) -> str:
    """Stop the game where no entity is inside the script: at SCRIPT_TICK of one of `entities`,
    else after `timeout` s wherever it is (a monster out of the hunter's section may not tick)."""
    if entities:
        condition = " || ".join(f"a0 == {e:#x}" for e in entities)
        with client.breakpoint(a.SCRIPT_TICK, condition=condition) as hits:
            try:
                hits.next(timeout)
                return "the game, stopped at SCRIPT_TICK"
            except TimeoutError:
                pass
    client.pause()
    return "the game, paused"


def _act(s: Session, row: int, timeout: float, build: Callable[[_Snapshot], Plan]) -> Pushed:
    """Stop the game, read what a plan needs, apply it and let the game run."""
    mem, client = s.mem, s.client
    try:
        mem.read(a.SCRIPT_PUSH, GUARD)
    except Unmapped:
        raise Refused(NEEDS_64) from None
    entities = [m.entity for m in _species(mem, row)]
    try:
        where = _stop(client, entities, timeout)
        return apply(mem, build(_snapshot(mem, row)), where)
    finally:
        with suppress(Disconnected):
            if client.status().stepping:
                client.resume()


def push(s: Session, p: ir.Program, img: asm.Image, *, timeout: float = TIMEOUT) -> Pushed:
    """Put `img` into the running game: the container at SCRIPT_PUSH, the row's header words and
    every monster of the species on it. Written in one stop at SCRIPT_TICK, which no entity is
    inside; with no monster to wait for, or none ticking within `timeout`, with the CPU paused."""
    return _act(
        s,
        p.row,
        timeout,
        lambda st: plan(p, img, st.live, st.row_words, st.home, _staged(s.mem, p, st)),
    )


def _staged(mem: Memory, p: ir.Program, st: _Snapshot) -> list[Container]:
    """The stick containers of `p`'s row that its header words and monsters point into."""
    known = range(p.base, p.end or p.base)
    home = range(int(a.SCRIPT_PUSH), int(a.SCRIPT_PUSH) + PUSH_SIZE)
    found: list[Container] = []
    for w in dict.fromkeys((*st.row_words, *(m.header for m in st.live))):
        if not w or w in known or w in home or any(w in b.head.addresses for b in found):
            continue
        box = around(mem, w)
        if box is not None and box.row == p.row:
            found.append(box)
    return found


def restore(s: Session, row: int, *, timeout: float = TIMEOUT) -> Pushed:
    """Put the game's own script back: the row's header words and the monsters on the container,
    which is cleared. A restarted monster begins at the first base routine of the game's header."""
    mem = s.mem

    def first(header: int) -> int | None:
        try:
            return mem.u32(mem.u32(header)) or None
        except Unmapped:
            return None

    return _act(
        s,
        row,
        timeout,
        lambda st: plan_restore(row, st.live, st.row_words, st.home, first),
    )


@dataclass(frozen=True)
class Monster:
    slot: int
    entity: int
    species: int
    pushed: bool
    """Whether its header is in the container, not the game's own."""
    context: int
    pc: int | None
    """Where the running context resumes."""
    container: Head | None = None
    """The container its header is in: the pushed one, or a stick one when `status` was given a
    `find`."""


@dataclass(frozen=True)
class Status:
    mapped: bool
    """False when SCRIPT_PUSH is outside the emulator's memory."""
    container: Head | None
    foreign: bool
    """SCRIPT_PUSH holds something that is no container."""
    rows: dict[int, tuple[int, ...]]
    """The rows whose header words point into the container, with their map types."""
    monsters: tuple[Monster, ...]


def status(mem: Memory, find: Callable[[int], Container | None] | None = None) -> Status:
    """What is pushed: the container, the rows and monsters that run it. With `find`, such as
    `around` with the caller's cache, each other monster's `container` is `find(its header)`."""
    found = live_script.monsters(mem)
    try:
        head = mem.read(a.SCRIPT_PUSH, HEAD)
    except Unmapped:
        return Status(False, None, False, {}, _seen(found, None, find))
    box: Head | None = None
    if any(head[:GUARD]):
        try:
            box = Head.parse(head)
        except ContainerError:
            pass
    rows: dict[int, tuple[int, ...]] = {}
    if box:
        stride = a.SPECIES.stride
        raw = mem.read(a.SCRIPT_HEADERS, (SPECIES_ROWS - 1) * stride + 4 * VARIANTS)
        for row in range(SPECIES_ROWS):
            words = struct.unpack_from(f"<{VARIANTS}I", raw, row * stride)
            if hit := tuple(k for k, w in enumerate(words) if w in box.addresses):
                rows[row] = hit
    foreign = box is None and any(head[:GUARD])
    return Status(True, box, foreign, rows, _seen(found, box, find))


def _seen(
    found: Mapping[int, ScriptState],
    box: Head | None,
    find: Callable[[int], Container | None] | None,
) -> tuple[Monster, ...]:
    out = []
    for slot, s in found.items():
        held = box if box is not None and s.header in box.addresses else None
        pushed = held is not None
        if not pushed and find is not None and (other := find(s.header)) is not None:
            held = other.head
        out.append(Monster(slot, s.entity, s.species, pushed, s.context, s.pc, held))
    return tuple(out)
