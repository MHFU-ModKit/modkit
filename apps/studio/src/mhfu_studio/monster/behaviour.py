# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's behaviour graph (`mhfu_port.behaviour`) as document edits, and what the canvas shows
of it.

No toolkit: the Behaviour dock calls these, one undo step per gesture (`Gesture`); a refusal is
the manifest's own `ManifestError`, so its checks are the only ones. Paths, priority, validity
and references are the package's; this module names the canvas's nodes and ports and reads the
graph for them.
"""

from __future__ import annotations

import itertools
import typing
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, NamedTuple, Protocol

from mhfu_port import behaviour, sequence
from mhfu_port.behaviour import KINDS, Block, MoveNode, Param, Path
from mhfu_port.manifest import Manifest, ManifestError

from mhfu_studio.monster.document import PortDocument

Point = tuple[float, float]
#: a move's node is `move:<name>`; a block id has no colon
MOVE = "move:"
IN, OUT = "in", "out"
PLAY, WHILE, THEN = "play", "while playing", "then"
"""A move node's ports: the input, then the two outputs."""
ROLE_TITLES = {"state": "State"}
"""A palette group's title where the role in plural is not it."""


class Wire(NamedTuple):
    src: str
    src_port: str
    dst: str
    dst_port: str


class Edits(Protocol):
    """What an edit needs of a document: `PortDocument`, or a `Gesture` gathering its edits."""

    @property
    def manifest(self) -> Manifest: ...

    def edit(self, change: Callable[[Manifest], object]) -> None: ...


class Gesture:
    """The edits of one gesture, run against the document as it was and committed together as
    one undo step. The first refusal refuses all of it."""

    def __init__(self, doc: PortDocument) -> None:
        self._doc = doc
        self._changes: list[Callable[[Manifest], object]] = []
        self._said: list[str] = []
        self._refused: str | None = None

    @property
    def manifest(self) -> Manifest:
        return self._doc.manifest

    def edit(self, change: Callable[[Manifest], object]) -> None:
        self._changes.append(change)

    def run(self, fn: Callable[[Edits], str]) -> None:
        if self._refused is None:
            try:
                self._said.append(fn(self))
            except ManifestError as e:
                self._refused = str(e)

    def commit(self) -> str:
        """The one undo step; the edits' messages in one line."""
        if self._refused is not None:
            raise ManifestError(self._refused)
        if self._changes:
            changes = self._changes
            self._doc.edit(lambda m: [c(m) for c in changes])
        return "; ".join(self._said)


# nodes


def move_id(name: str) -> str:
    return MOVE + name


def move_name(node: str) -> str | None:
    """The move a node stands for; None for a block."""
    return node.removeprefix(MOVE) if node.startswith(MOVE) else None


def _block(m: Manifest, i: str) -> Block:
    blk = m.behaviour.blocks.get(i)
    if blk is None:
        raise ManifestError(f"no block {i}")
    return blk


def _move(m: Manifest, name: str) -> None:
    if name not in m.moves:
        raise ManifestError(f"no move {name!r}")


def _say(m: Manifest, node: str) -> str:
    """A node in words: a block by its title, a move by its name."""
    name = move_name(node)
    return name if name is not None else KINDS[_block(m, node).kind].title


def _node(m: Manifest, name: str) -> MoveNode:
    """The move's node; one at its automatic spot is made when it has none."""
    return m.behaviour.moves.setdefault(name, MoveNode(spots(m)[name]))


def _at(p: Point) -> Point:
    return round(float(p[0]), 1), round(float(p[1]), 1)


# reading


def wires(m: Manifest) -> list[Wire]:
    """Every stored link, as the wire it is drawn as."""
    b = m.behaviour
    out = [Wire(i, OUT, n, IN) for i, blk in b.blocks.items() for n in blk.next]
    out += [Wire(i, OUT, move_id(n), PLAY) for i, blk in b.blocks.items() for n in blk.play]
    out += [Wire(move_id(n), WHILE, i, IN) for n, node in b.moves.items() for i in node.during]
    out += [
        Wire(move_id(n), THEN, move_id(mv.after), PLAY) for n, mv in m.moves.items() if mv.after
    ]
    return out


def spots(m: Manifest) -> dict[str, Point]:
    """Where each move sits: its node's place, else a column right of everything placed, in
    sequence order so a chain sits together."""
    b = m.behaviour
    clear = [blk.at[0] + behaviour.COLUMN for blk in b.blocks.values()]
    clear += [n.at[0] + behaviour.LEFT for n in b.moves.values()]
    x = max(clear, default=float(behaviour.LEFT))
    order = [n for steps in sequence.groups(m).values() for n in steps]
    return {
        n: b.moves[n].at if n in b.moves else (x, float(k * behaviour.ROW))
        for k, n in enumerate(order)
    }


class Reading(NamedTuple):
    """What the graph says of itself."""

    paths: list[Path]
    priority: dict[str, list[int]]
    """Block id -> the 1-based places in `paths` of the paths that end at it; a path that plays
    nothing ends at its last block too."""
    loose: list[str]
    refused: dict[str, str]
    """Block id -> why, for the blocks on a path `compile` refuses."""
    capped: str | None
    """Set when the paths outnumber the rules the seam holds."""


def read(m: Manifest) -> Reading:
    ps = behaviour.paths(m)
    priority: dict[str, list[int]] = {}
    for k, p in enumerate(ps, 1):
        priority.setdefault(p.blocks[-1], []).append(k)
    return Reading(ps, priority, behaviour.loose(m), behaviour.refused(m), capped(m, ps))


def capped(m: Manifest, ps: Sequence[Path] | None = None) -> str | None:
    """Why the paths are too many for the rules the seam holds; None when they fit."""
    n = len(behaviour.paths(m) if ps is None else ps)
    return f"{n} paths, the seam holds {behaviour.SEAM_RULES}" if n > behaviour.SEAM_RULES else None


def part_options(m: Manifest, optional: bool) -> list[tuple[str, int | None]]:
    """The parts to pick from: the manifest's named ones, then `part k`; `any` for none."""
    named: dict[int, str] = {}
    for name, part in m.parts.items():
        named.setdefault(part.index, name)
    out: list[tuple[str, int | None]] = [("any", None)] if optional else []
    out += [(name, k) for k, name in named.items()]
    out += [(f"part {k}", k) for k in behaviour.PARTS if k not in named]
    return out


def main_options() -> list[tuple[str, int]]:
    return [(str(k), k) for k in behaviour.MAIN_STATES]


def palette() -> list[tuple[str, list[tuple[str, str, str]]]]:
    """The kinds a block can be, a group per role: (kind, title, tip). All from `KINDS`."""
    roles = typing.get_args(behaviour.Role)
    groups = [
        (
            ROLE_TITLES.get(role, role.capitalize() + "s"),
            [(k.name, k.title, k.tip) for k in KINDS.values() if k.role == role],
        )
        for role in roles
    ]
    return [g for g in groups if g[1]]


def fresh(stem: str, taken: Collection[str]) -> str:
    """The first of `stem1`, `stem2`, ... not in `taken`."""
    return next(f"{stem}{k}" for k in itertools.count(1) if f"{stem}{k}" not in taken)


def start(p: Param, taken: Collection[str] = ()) -> object:
    """A new block's value for `p`: the default, else a name not in `taken`, the first main
    state or side, the least, or the first choice; none for an optional param."""
    if p.optional:
        return None
    if p.default is not None:
        return p.default
    kind: str = p.type
    if kind == "mains":
        return [0]
    if kind == "sides":
        return list(p.choices[:1])
    if kind == "var":
        return fresh(p.name, taken)
    if kind == "signal":
        return fresh(kind, taken)
    if p.choices:
        return p.choices[0]
    lo = 0 if p.lo is None else p.lo
    return float(lo) if kind == "float" else int(lo)


# edits


def add_block(doc: Edits, kind: str, at: Point) -> str:
    """A block of `kind` at `at`, with valid values."""
    if kind not in KINDS:
        raise ManifestError(f"{kind!r} is not a kind of block")
    m = doc.manifest
    taken = {n for p in KINDS[kind].params for n in behaviour.names(m, p.type)}
    params: dict[str, object] = {}
    for p in KINDS[kind].params:
        if (v := start(p, taken)) is not None:
            params[p.name] = v
            if isinstance(v, str):
                taken.add(v)
    i = behaviour.new_id(m.behaviour)

    def add(m: Manifest) -> None:
        m.behaviour.blocks[i] = Block(kind, _at(at), params)

    doc.edit(add)
    return f"{KINDS[kind].title} added"


def delete(doc: Edits, ids: Sequence[str]) -> str:
    """Blocks go with every `next` and `during` that names them. A move node stays: a move is
    deleted in the Moves dock."""
    m = doc.manifest
    gone = list(dict.fromkeys(i for i in ids if move_name(i) is None))
    for i in gone:
        _block(m, i)
    if not gone:
        raise ManifestError("moves are deleted in the Moves dock")

    def drop(m: Manifest) -> None:
        for i in gone:
            del m.behaviour.blocks[i]
        for blk in m.behaviour.blocks.values():
            blk.next = [n for n in blk.next if n not in gone]
        for node in m.behaviour.moves.values():
            node.during = [n for n in node.during if n not in gone]

    doc.edit(drop)
    kept = "; moves stay: delete them in the Moves dock" if len(gone) < len(ids) else ""
    return f"{len(gone)} block{'s' * (len(gone) > 1)} deleted{kept}"


def _ends(m: Manifest, w: Wire) -> tuple[str, str, str]:
    """(kind, from, to) of a wire the graph stores: `next`, `play`, `during` or `then`."""
    s, d = move_name(w.src), move_name(w.dst)
    for name in (s, d):
        if name is not None:
            _move(m, name)
    for i, name in ((w.src, s), (w.dst, d)):
        if name is None:
            _block(m, i)
    ends = {
        (False, OUT, False, IN): "next",
        (False, OUT, True, PLAY): "play",
        (True, WHILE, False, IN): "during",
        (True, THEN, True, PLAY): "then",
    }
    kind = ends.get((s is not None, w.src_port, d is not None, w.dst_port))
    if kind is None:
        a, b = ("move" if s else "block"), ("move" if d else "block")
        raise ManifestError(f"a {a}'s {w.src_port!r} cannot feed a {b}'s {w.dst_port!r}")
    return kind, s or w.src, d or w.dst


def _held(m: Manifest, kind: str, src: str) -> list[str]:
    """The stored list a link of `kind` from `src` lives in (empty for a move with no node)."""
    b = m.behaviour
    if kind == "during":
        node = b.moves.get(src)
        return [] if node is None else node.during
    blk = b.blocks[src]
    return blk.next if kind == "next" else blk.play


def link(doc: Edits, src: str, src_port: str, dst: str, dst_port: str) -> str:
    """Stores the wire: `next`, a block's `play`, a move's `during`, or a move's `after`."""
    w = Wire(src, src_port, dst, dst_port)
    m = doc.manifest
    kind, a, b = _ends(m, w)
    if kind == "then":
        return set_then(doc, a, b)
    if b in _held(m, kind, a):
        raise ManifestError(f"{_say(m, src)} already feeds {_say(m, dst)}")

    def add(m: Manifest) -> None:
        held = _node(m, a).during if kind == "during" else _held(m, kind, a)
        held.append(b)

    doc.edit(add)
    return f"{_say(m, src)} feeds {_say(m, dst)}"


def unlink(doc: Edits, src: str, src_port: str, dst: str, dst_port: str) -> str:
    """Clears the stored wire."""
    w = Wire(src, src_port, dst, dst_port)
    m = doc.manifest
    kind, a, b = _ends(m, w)
    if kind == "then":
        if m.moves[a].after != b:
            raise ManifestError(f"{a} does not hand to {b}")
        return set_then(doc, a, None)
    if b not in _held(m, kind, a):
        raise ManifestError(f"{_say(m, src)} does not feed {_say(m, dst)}")

    def cut(m: Manifest) -> None:
        held = _held(m, kind, a)
        held.remove(b)

    doc.edit(cut)
    return f"{_say(m, src)} no longer feeds {_say(m, dst)}"


def set_then(doc: Edits, move: str, then: str | None) -> str:
    """The move `move` hands to when it ends (`Move.after`); none clears it."""
    m = doc.manifest
    _move(m, move)
    if then is not None:
        _move(m, then)
        if then == move:
            raise ManifestError(f"{move} cannot hand to itself")
    if m.moves[move].after == then:
        return f"{move}: unchanged"

    def change(m: Manifest) -> None:
        m.moves[move].after = then

    doc.edit(change)
    return f"{move} hands to {then}" if then else f"{move} hands to no move"


def _norm(p: Param, value: object) -> object:
    if value is None:
        return None
    kind: str = p.type
    if kind == "mains":
        return sorted({int(k) for k in typing.cast(Sequence[int], value)})
    if kind == "sides":
        picked = set(typing.cast(Sequence[str], value))
        return [c for c in p.choices if c in picked]
    if kind in ("int", "part"):
        return int(typing.cast(int, value))
    if kind == "float":
        return float(typing.cast(float, value))
    return value


def set_param(doc: Edits, i: str, name: str, value: object) -> str:
    """Block `i`'s param `name`; none drops an optional one."""
    blk = _block(doc.manifest, i)
    kind = KINDS[blk.kind]
    p = next((p for p in kind.params if p.name == name), None)
    if p is None:
        raise ManifestError(f"{kind.title} has no {name!r}")
    v = _norm(p, value)
    if v is None and not p.optional:
        raise ManifestError(f"{p.title} cannot be none")
    if blk.params.get(name) == v:
        return f"{kind.title}: unchanged"

    def change(m: Manifest) -> None:
        params: dict[str, Any] = m.behaviour.blocks[i].params
        if v is None:
            params.pop(name, None)
        else:
            params[name] = v

    doc.edit(change)
    return f"{kind.title}: {p.title.lower()} {'none' if v is None else v}"


def _checked(m: Manifest, node: str) -> str | None:
    """The move a node stands for, or None for a block; either must exist."""
    name = move_name(node)
    if name is None:
        _block(m, node)
    else:
        _move(m, name)
    return name


def set_label(doc: Edits, node: str, text: str) -> str:
    """The note on a block, or a move's own label."""
    m = doc.manifest
    text = text.strip()
    name = _checked(m, node)
    held = _block(m, node).label if name is None else m.moves[name].label
    if held == text:
        return f"{_say(m, node)}: unchanged"

    def change(m: Manifest) -> None:
        if name is None:
            m.behaviour.blocks[node].label = text
        else:
            m.moves[name].label = text

    doc.edit(change)
    return f"{_say(m, node)}: note set"


def move_nodes(doc: Edits, at: Mapping[str, Point]) -> str:
    """Nodes to new places; a move gets its node on its first move."""
    m = doc.manifest
    here = spots(m)
    went: dict[str, Point] = {}
    for node, p in at.items():
        name = _checked(m, node)
        now = _block(m, node).at if name is None else here[name]
        if _at(p) != now:
            went[node] = _at(p)
    if not went:
        return "unchanged"

    def place(m: Manifest) -> None:
        for node, p in went.items():
            name = move_name(node)
            if name is None:
                m.behaviour.blocks[node].at = p
            elif name in m.behaviour.moves:
                m.behaviour.moves[name].at = p
            else:
                m.behaviour.moves[name] = MoveNode(p)

    doc.edit(place)
    return f"moved {len(went)} node{'s' * (len(went) > 1)}"


def spread(doc: Edits, kx: float = 1.5, ky: float = 1.7) -> str:
    """Every placed node's place scaled out from the origin, so crowded nodes stop overlapping.
    A scale keeps the order of the canvas rows and columns, so every path's priority holds."""
    b = doc.manifest.behaviour

    def scale(m: Manifest) -> None:
        for blk in m.behaviour.blocks.values():
            blk.at = _at((blk.at[0] * kx, blk.at[1] * ky))
        for node in m.behaviour.moves.values():
            node.at = _at((node.at[0] * kx, node.at[1] * ky))

    doc.edit(scale)
    return f"{len(b.blocks) + len(b.moves)} nodes spread out"
