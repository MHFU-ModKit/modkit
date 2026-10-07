# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's moves and rules as the game runs them: `<port>_moves.lua`, written with the clips
module from the same build. `mhfu_port.lua`'s `P.define` reads it when a mod gives no `moves`
or `rules`.

A pair move goes as the runtime paints it on its host pair. An own move goes as the move player
takes it (`mhfu.move_play`): its clip's executor entry and, for `turn = "clip"`, its turn keys,
both from the build's layout; the attack windows, the carrier (`hub` when it names none), the
steer and `after`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from mhfu import addresses, hitbox
from mhfu.files import Extracted, em_overlay

from .layout import Layout
from .manifest import Manifest, ManifestError, Move, Rule

Pair = tuple[int, int]

HUBS: dict[int, Pair] = {75: (0, 2)}
"""Host species -> the pair an own move rides when it names no carrier: em75's alert hub, which
takes one clip and hands back to the brain once it ends."""
KEYS: int = addresses.STEER_SPEC.KEYS.count or 0
"""Turn keys the move player holds for one move."""
POOL: int = addresses.EM_MOVES.KEYS.count or 0
"""Turn keys the framework holds for all of a port's own moves."""


def hub(species: int) -> Pair | None:
    """The carrier an own move on `species` rides by default; None where none is known."""
    return HUBS.get(species)


def module_name(m: Manifest) -> str:
    """The Lua module `P.define` requires for the port's moves and rules."""
    return f"{m.port.name}_moves.lua"


def carrier(m: Manifest, name: str) -> Pair:
    mv = m.moves[name]
    pair = mv.carrier if mv.carrier is not None else hub(m.port.host_species)
    if pair is None:
        raise ManifestError(
            f"moves.{name}: no carrier, and none is known for host species "
            f"{m.port.host_species}: give one"
        )
    return pair


def entry(m: Manifest, name: str, layout: Layout) -> int:
    """An own move's executor entry: its clip's in `layout`, else its `anim`."""
    mv = m.moves[name]
    if mv.clip is None:
        assert mv.anim is not None
        return mv.anim
    e = layout.ids.get(m.clips[mv.clip].id)
    if e is None:
        raise ManifestError(f"moves.{name}: clip {mv.clip!r} has no executor entry in this build")
    return e


def curve(m: Manifest, name: str, layout: Layout) -> str | None:
    """The turn keys YAW follows, for `turn = "clip"` on a clip that turns."""
    mv = m.moves[name]
    if mv.steer.turn != "clip":
        return None
    t = layout.turns.get(entry(m, name, layout))
    return None if t is None else t.lua()


def records(game: Extracted, species: int) -> frozenset[int]:
    """The attack ids `species` has a record for: its overlay's moveset table, blanks left out."""
    t = hitbox.primary_table(hitbox.tables(game.overlay(em_overlay(species))))
    off = hitbox.id_offset(species, species) or 0
    return frozenset(r.index - off for r in ([] if t is None else t.attacks) if any(r.raw))


def check(m: Manifest, layout: Layout, known: Collection[int] | None = None) -> None:
    """Raise `ManifestError` for an own move the module cannot carry: a clip with no entry, an
    attack past its clip or with no record in `known` (the host's, when given), more turn keys
    than the framework holds, no carrier; or rules on the flinch whose moves ride different
    carriers."""
    pool = 0
    for name, mv in m.moves.items():
        if not mv.own:
            continue
        w = f"moves.{name}"
        e = entry(m, name, layout)
        carrier(m, name)
        frames = layout.frames.get(e)
        for i, a in enumerate(mv.attacks):
            if known is not None and a.id not in known:
                raise ManifestError(
                    f"{w}.attack[{i}]: host species {m.port.host_species} has no "
                    f"attack record {a.id}"
                )
            if frames is not None and a.frame >= frames:
                raise ManifestError(f"{w}.attack[{i}]: frame {a.frame} is past the clip's {frames}")
        keys = len(curve(m, name, layout) or "") // 4
        if keys > KEYS:
            raise ManifestError(f"{w}: the clip's turn has {keys} keys, a move holds {KEYS}")
        pool += keys
    if pool > POOL:
        raise ManifestError(
            f"moves: the own moves' turns need {pool} keys, the framework holds {POOL}"
        )
    flinch = {carrier(m, r.play) for r in m.rules if r.on == "flinch"}
    if len(flinch) > 1:
        raise ManifestError(
            f"rule: the moves of the rules on the flinch ride {len(flinch)} carriers; the "
            "reaction replacement enters one"
        )


def pair_move(mv: Move) -> dict[str, Any]:
    """A pair move as `mhfu_port.lua` reads it, defaults left out."""
    out: dict[str, Any] = {"main": mv.main, "sub": mv.sub}
    if mv.clip is not None:
        out["clip"] = mv.clip
    if mv.anim is not None:
        out["anim"] = mv.anim
    if mv.latch != 1:
        out["latch"] = mv.latch
    if mv.min_gap != 2:
        out["min_gap"] = mv.min_gap
    if mv.after is not None:
        out["after"] = mv.after
    if mv.hold_max is not None:
        out["hold_max"] = mv.hold_max
    if mv.claim is not None:
        claim: dict[str, Any] = {"main": mv.claim.mains}
        if mv.claim.sub is not None:
            claim["sub"] = mv.claim.sub
        out["claim"] = claim
    return out


def _steer(m: Manifest, name: str, layout: Layout) -> dict[str, Any]:
    s = m.moves[name].steer
    out: dict[str, Any] = {"walls": s.walls}
    if s.dir:
        out["dir"] = s.dir
    if s.turn in ("hunter", "away"):
        out["turn"] = s.turn
        if s.rate is not None:
            out["rate"] = s.rate
    elif s.turn == "fixed":
        out |= {"turn": "fixed", "total": s.angle, "frames": s.frames}
    elif (keys := curve(m, name, layout)) is not None:
        out["curve"] = keys
    return out


def _own_move(m: Manifest, name: str, layout: Layout) -> dict[str, Any]:
    mv = m.moves[name]
    out: dict[str, Any] = {"entry": entry(m, name, layout)}
    if mv.clip is not None:
        out["clip"] = mv.clip
    if mv.attacks:
        out["attacks"] = [
            [a.frame, a.id, *([a.end] if a.end is not None else [])] for a in mv.attacks
        ]
    out["carrier"] = list(carrier(m, name))
    if mv.length is not None:
        out["length"] = mv.length
    if mv.host_attacks:
        out["host_attacks"] = True
    if mv.eager:
        out["eager"] = True
    out["steer"] = _steer(m, name, layout)
    if mv.after is not None:
        out["after"] = mv.after
    return out


def _rule(r: Rule) -> dict[str, Any]:
    out: dict[str, Any] = {"play": r.play}
    if r.on is not None:
        out["on"] = r.on
    if r.part is not None:
        out["part"] = r.part
    if r.from_move is not None:
        out["from"] = r.from_move
    if r.from_main:
        out["from_main"] = r.from_main
    if r.min_frames:
        out["min_frames"] = r.min_frames
    if r.dist != Rule("").dist:
        out["dist"] = list(r.dist)
    for flag in ("receding", "closing"):
        if getattr(r, flag):
            out[flag] = True
    for k in ("mode", "cooldown"):
        if getattr(r, k):
            out[k] = getattr(r, k)
    if r.count is not None:
        out["count"] = r.count
    if r.label:
        out["label"] = r.label
    return out


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat "
    "return then true until while".split()
)


def lua_key(k: str) -> str:
    """`k` as a Lua table key."""
    return k if _IDENT.fullmatch(k) and k not in _KEYWORDS else f"[{_str(k)}]"


def _str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def lua_value(v: object) -> str:
    """`v` as a Lua expression, on one line."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return _str(v)
    if isinstance(v, Mapping):
        return "{ " + ", ".join(f"{lua_key(k)} = {lua_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list | tuple):
        return "{ " + ", ".join(map(lua_value, v)) + " }"
    raise TypeError(f"no Lua for {v!r}")


def lua(m: Manifest, layout: Layout, known: Collection[int] | None = None) -> str:
    """The module; `check`s first."""
    check(m, layout, known)
    src = f"ports/{m.path.name}" if m.path is not None else f"the {m.port.name} manifest"
    out = [
        f"-- {module_name(m)}, GENERATED by mhfu-port from {src}: its moves and rules as",
        "-- mhfu_port's P.define reads them; an own move (entry, no main) as mhfu.move_play takes",
        "-- it, from the same build as the clips module. Do not edit: rebuild.",
        "return {",
        "  moves = {",
    ]
    for name, mv in m.moves.items():
        body = _own_move(m, name, layout) if mv.own else pair_move(mv)
        out.append(f"    {lua_key(name)} = {lua_value(body)},")
    out += ["  },", "  rules = {"]
    out += [f"    {lua_value(_rule(r))}," for r in m.rules]
    out += ["  },", "}", ""]
    return "\n".join(out)
