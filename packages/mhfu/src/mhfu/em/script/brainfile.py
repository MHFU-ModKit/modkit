# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A species overlay's brain file: the edits to its rows' native scripts, as TOML.

    bf = brainfile.load("brains/em75.toml")     # brainfile.new(75, "Tigrex") for an empty one
    es = bf.edits(76)                           # the EditSet that covers row 76, or None
    bf.put(es)                                  # a row's edits, kept by `es.row`
    brainfile.save(bf, "brains/em75.toml")

One file per em overlay, `brains/emNN.toml`: `schema`, `overlay`, `label`, a `[pairs]` table of
display names keyed `"main,sub"`, and a `[row.N]` section per species row, each one `EditSet`
(`editset.to_data`). Code a section inserts is source text (`source.render_nodes`, read back by
`parse.nodes`); it carries no ids, because `apply` names new code `u<k>.<i>` in source order, so
they are the same after every load. A file never holds the game's code, only what differs.

`save` writes the normal form: tables and keys sorted, nothing that changes nothing (a section
with no edits, names or notes is left out), `also` sorted. Reading and saving a file in normal
form gives its bytes back; comments in a file are not kept.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from ... import inject as sticks
from ...files import EM_SPECIES, SPECIES_ROWS
from . import ir, parse, source
from .editset import DocError, EditSet

__all__ = [
    "SCHEMA",
    "BrainFile",
    "BrainFileError",
    "code",
    "dumps",
    "filename",
    "load",
    "loads",
    "new",
    "save",
    "twins",
]

SCHEMA = 1
Pair = tuple[int, int]
Plain = dict[str, Any]
_PAIR = re.compile(r"(\d+),(\d+)")
_TOP = ("schema", "overlay", "label", "pairs", "row")


class BrainFileError(ValueError):
    """A file that is no brain file."""


def code(nodes: Sequence[ir.Node]) -> str:
    """New code as the text a file keeps it in: one line a row, each ended."""
    return "".join(f"{line.text}\n" for line in source.render_nodes(nodes))


@dataclass
class BrainFile:
    overlay: int
    label: str = ""
    pairs: dict[Pair, str] = field(default_factory=dict)
    """Display names of move pairs, for the graph and its findings; no bytes depend on them."""
    rows: dict[int, EditSet] = field(default_factory=dict)
    """The edits by species row, each under its own `es.row`."""

    def edits(self, row: int) -> EditSet | None:
        """The edit set that applies to `row`: its own section, or the one listing it in `also`;
        None when the file has no edits for it."""
        if row in self.rows:
            return self.rows[row]
        return next((es for es in self.rows.values() if row in es.also), None)

    def put(self, es: EditSet) -> None:
        """Keep `es` as its row's edits, replacing any."""
        self.rows[es.row] = es

    def copy(self) -> BrainFile:
        return BrainFile(
            self.overlay,
            self.label,
            dict(self.pairs),
            {r: es.copy() for r, es in self.rows.items()},
        )

    def to_data(self) -> Plain:
        """The file as plain tables, in normal form."""
        self.check()
        out: Plain = {"schema": SCHEMA, "overlay": self.overlay}
        if self.label:
            out["label"] = self.label
        if names := {f"{m},{s}": n for (m, s), n in sorted(self.pairs.items()) if n}:
            out["pairs"] = names
        rows: Plain = {}
        for n, es in sorted(self.rows.items()):
            kept = es.copy().prune()
            kept.also = sorted(set(kept.also))
            kept.build = kept.build.lower()
            data = kept.to_data(code)
            if data.keys() - {"build", "also"}:
                rows[str(n)] = data
        if rows:
            out["row"] = rows
        return out

    def check(self) -> None:
        """Refuse what no file can say: an unknown overlay, a row out of range or a row in two
        sections."""
        if self.overlay not in EM_SPECIES:
            raise BrainFileError(f"{self.overlay} is not one of the em overlays {EM_SPECIES}")
        seen: dict[int, int] = {}
        for n, es in sorted(self.rows.items()):
            if es.row != n:
                raise BrainFileError(f"the edits under row {n} are row {es.row}'s")
            if n in es.also:
                raise BrainFileError(f"row {n} lists itself in also")
            for r in (n, *sorted(set(es.also))):
                if not 0 < r < SPECIES_ROWS:
                    raise BrainFileError(f"row {r} is not a species row (1..{SPECIES_ROWS - 1})")
                if r in seen:
                    raise BrainFileError(f"row {r} is in the sections of row {seen[r]} and {n}")
                seen[r] = n


def filename(overlay: int) -> str:
    """The file of an em overlay in `brains/`: `em75.toml`, `em01.toml`."""
    return f"em{overlay:02d}.toml"


def new(overlay: int, label: str = "") -> BrainFile:
    """A file with no edits for an em overlay."""
    bf = BrainFile(overlay, label)
    bf.check()
    return bf


def dumps(bf: BrainFile) -> str:
    return tomli_w.dumps(bf.to_data(), multiline_strings=True)


def save(bf: BrainFile, path: str | Path) -> Path:
    """`bf` in normal form at `path`, through a temp file: a reader never sees half of it."""
    dst = Path(path)
    data = dumps(bf).encode()
    dst.parent.mkdir(parents=True, exist_ok=True)
    return sticks.write_file(dst.parent, dst.name, data)


# ---- reading


def _table(what: str, v: Any) -> Plain:
    if not isinstance(v, dict):
        raise BrainFileError(f"{what} is no table")
    return v


def _pairs(v: Any) -> dict[Pair, str]:
    out: dict[Pair, str] = {}
    for key, name in _table("pairs", v).items():
        m = _PAIR.fullmatch(key)
        if m is None:
            raise BrainFileError(f'pairs: {key!r} is no pair: "main,sub"')
        if not isinstance(name, str):
            raise BrainFileError(f"pairs: the name of {key} is no text")
        out[int(m[1]), int(m[2])] = name
    return out


def _codes(data: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """The code texts of a row's data, each with where it is: `insert u2`, `routine spin`."""
    inserts, routines = data.get("insert"), data.get("routine")
    if isinstance(inserts, Mapping):
        for key, v in inserts.items():
            if isinstance(v, Mapping):
                yield f"insert {key}", v.get("code")
    if isinstance(routines, Mapping):
        for key, v in routines.items():
            yield f"routine {key}", v


def _culprit(data: Any) -> str:
    """Which code of a row's data does not parse, as `insert u2: line 3: ...`."""
    for where, text in _codes(data) if isinstance(data, Mapping) else ():
        if isinstance(text, str):
            try:
                parse.nodes(text)
            except parse.ParseError as e:
                return f"{where}: {e}"
    return ""


def _row(key: str, data: Any) -> EditSet:
    if not key.isdecimal():
        raise BrainFileError(f"row {key!r} is no species row number")
    try:
        return EditSet.from_data(int(key), data, parse=parse.nodes)
    except parse.ParseError as e:
        where = _culprit(data)
        raise BrainFileError(f"row {key} {where}" if where else f"row {key}: {e}") from None
    except DocError as e:
        raise BrainFileError(f"row {key}: {e}") from None


def loads(text: str) -> BrainFile:
    """The file `text` is; a BrainFileError says what is wrong."""
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise BrainFileError(f"not TOML: {e}") from None
    if extra := sorted(set(d) - set(_TOP)):
        raise BrainFileError(f"a brain file has no {extra[0]}")
    if d.get("schema") != SCHEMA:
        raise BrainFileError(f"schema is {d.get('schema')!r}, this reads {SCHEMA}")
    overlay, label = d.get("overlay"), d.get("label", "")
    if isinstance(overlay, bool) or not isinstance(overlay, int):
        raise BrainFileError("overlay is no number")
    if not isinstance(label, str):
        raise BrainFileError("label is no text")
    bf = BrainFile(overlay, label, _pairs(d.get("pairs", {})))
    for key, data in _table("row", d.get("row", {})).items():
        es = _row(key, data)
        bf.rows[es.row] = es
    bf.check()
    return bf


def load(path: str | Path) -> BrainFile:
    """The brain file at `path`."""
    p = Path(path)
    try:
        return loads(p.read_text(encoding="utf-8"))
    except BrainFileError as e:
        raise BrainFileError(f"{p}: {e}") from None


# ---- twins


def twins(lifts: Mapping[int, ir.Program]) -> list[list[int]]:
    """The rows whose lifts are the same code at the same places, sorted: the groups whose edits
    can name each other in `also`. The same bytes are not enough: em01's rows 1, 37 and 42 and
    its rows 11, 41 and 49 read as one region and lift as two."""
    groups: dict[tuple[str, int, tuple[tuple[str, int | None], ...]], list[int]] = {}
    for row, p in sorted(lifts.items()):
        text = tuple((ln.text, ln.pc) for ln in source.render(p)[1:])
        groups.setdefault((p.sha1, p.base, text), []).append(row)
    return [rows for rows in groups.values() if len(rows) > 1]
