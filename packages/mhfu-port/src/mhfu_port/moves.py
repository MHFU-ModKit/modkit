# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's moves and rules as the game runs them: `<port>_moves.lua`, written with the clips
module from the same build. `mhfu_port.lua`'s `P.define` reads it when a mod gives no `moves`
or `rules`. A rule carries its `conds` and `effects` as `{ op, arg, value }` with the board
indices resolved, its `signal` index and, for a path that plays nothing, `no_play`; `vars` and
`signals` name those indices for `port:var` and `port:fire`. `sever_below` (the tail-cut gate's HP
percent) and `natural_rage = false` appear only when the manifest sets them.

A pair move goes as the runtime paints it on its host pair. An own move goes as the move player
takes it (`mhfu.move_play`): its `slot` in the framework's registry (`slots`), its clip's executor
entry and its turn keys (the clip's facing, in every turn mode), both from the build's layout; the
attack windows, the carrier (`hub` when it names none), the steer and `after`, and `start` and
`stop`, the clip frames it plays from and to, when it sets them (`length` counts AI frames
instead), and `aim_out`.
The steer carries the move's own `travel` (`mhfu.steering.hex_of`), `lunge` and `code_turn`
(`mhfu.steering.turns_hex`, with `turn = "aim"` where the manifest says so) when the manifest has
them, and a fixed turn's `window`. `tip` is the tail tip's `{ joint, carrier }` pairs
(`rig.Tip`), which `mhfu.em_tip` takes.

`fatigue` is the stamina table `mhfu.em_fatigue` takes (`brain.fatigue`), given when the manifest's
`[brain] fatigue` is set; `mhfu_port.lua` arms it with the port, an own move keyed by name.
`charge` is the same for the charge helper (`mhfu.em_charge`, `brain.charge`, `[brain] charge`):
the charge-up moves' gains and the transform move, by name.

`slots` is the one numbering of own moves: this module writes it, `mhfu_port.lua` registers by it
and the brain splice's `enter OWN_MOVE_MAIN k 0` names it. `combos` is the same for the enter ids
past the slots: each (first, then) pair of own moves a `[brain.pairs]` list names is one id
(`combo_id`), written as `combos = { { id, "first", "then" } }`, which `mhfu_port.lua` registers
(`mhfu.em_combo`) and the splice enters as `enter OWN_MOVE_MAIN id 0`. The manifest it is given
has the own moves `[brain.pairs]` derive from the donor (`brain.own.expand`), which
`cli.build.modules` adds.
"""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from mhfu import addresses, hitbox, inject, steering
from mhfu.files import Extracted, em_overlay
from mhfu.live import moves as live

from . import behaviour
from .behaviour import Rule
from .layout import Layout
from .manifest import OWN_MOVES, Lunge, Manifest, ManifestError, Move, pair_of, span_problems
from .rig import Tip

Pair = tuple[int, int]
T = TypeVar("T")
FLOAT_MAX = 3.4e38

HUBS: dict[int, Pair] = {75: (0, 2)}
"""Host species -> the pair an own move rides when it names no carrier: em75's alert hub, which
takes one clip and hands back to the brain once it ends."""
KEYS: int = addresses.STEER_SPEC.KEYS.count or 0
"""Turn keys the move player holds for one move."""
POOL: int = addresses.EM_MOVES.KEYS.count or 0
"""Turn keys the framework holds for all of a port's own moves."""
SLOTS: int = OWN_MOVES
"""Own moves the framework's registry holds."""
IDS: int = addresses.EM_MOVES.COMBOS.count or 0
"""Enter ids, a byte's: the slots, then the combos."""
COMBOS: int = IDS - SLOTS
"""Combos the framework holds: the enter ids past the slots."""
SAMPLES: int = steering.MOST
"""Travel samples the move player holds for one move."""
SAMPLE_POOL: int = addresses.EM_MOVES.SAMPLES.count or 0
"""Travel samples the framework holds for all of a port's own moves."""
LUNGES: int = addresses.EM_MOVES.LUNGES.count or 0
"""Lunges the framework holds for a port's own moves."""
TURNS: int = steering.MOST_TURNS
"""Code-turn values the move player holds for one move."""
TURN_POOL: int = addresses.EM_MOVES.TURNS.count or 0
"""Code-turn values the framework holds for all of a port's own moves."""
COMBO_HUB: Pair = HUBS[75]
"""The carrier a combo's first move rides: the enter stub enters it, em75's alert hub."""


def _place(pool: list[T], run: Sequence[T]) -> int:
    """Where `pool` holds `run` whole (the first such place), else where `run` was appended; an
    empty run is at 0."""
    n = len(run)
    if not n:
        return 0
    i = -1
    while True:
        try:
            i = pool.index(run[0], i + 1)
        except ValueError:
            break
        if pool[i : i + n] == list(run):
            return i
    at = len(pool)
    pool.extend(run)
    return at


@dataclass
class Pools:
    """What the framework's registry holds of a port's own moves when `mhfu_em_move` fills it
    slot by slot (em_vhook.cpp `find_run` is this rule): a move's turn keys, travel samples, lunge
    record and code turn go where the pool already holds that run, anywhere in it, else at its
    end. The capacity check counts these, so a module it passes fits."""

    keys: list[int] = field(default_factory=list)
    samples: list[tuple[int, int]] = field(default_factory=list)
    lunges: list[bytes] = field(default_factory=list)
    turns: list[int] = field(default_factory=list)

    def add(
        self,
        keys: Sequence[int],
        samples: Sequence[tuple[int, int]],
        lunge: bytes | None = None,
        turns: Sequence[int] = (),
    ) -> tuple[int, int, int | None, int]:
        """Register one move's runs; where each starts: keys, samples, the lunge's index (None for
        no lunge), code turn."""
        at = (_place(self.keys, keys), _place(self.samples, samples))
        lunge_at = None if lunge is None else _place(self.lunges, [lunge])
        return *at, lunge_at, _place(self.turns, turns)


def slots(m: Manifest) -> dict[str, int]:
    """Each own move's registry slot: sorted by name, numbered from 0, at most `SLOTS` (`problems`
    names the rest)."""
    own = sorted(name for name, mv in m.moves.items() if mv.own)
    return {name: k for k, name in enumerate(own[:SLOTS])}


def combos(m: Manifest) -> list[tuple[str, str]]:
    """The (first, then) own-move pairs a manifest's combos are: every `[brain.pairs]` list value
    `[x, y]` whose x and y are both own moves with a slot, each pair once, in sorted order. The
    framework holds `COMBOS` (`problems` names the rest); a pair's place here is its enter id."""
    if m.brain is None:
        return []
    own = slots(m)
    found: set[tuple[str, str]] = set()
    for key in m.brain.pairs:
        names = m.brain.targets(pair_of(key))
        if len(names) == 2 and isinstance(names[0], str) and isinstance(names[1], str):
            if names[0] in own and names[1] in own:
                found.add((names[0], names[1]))
    return sorted(found)


def combo_id(m: Manifest, first: str, then: str) -> int | None:
    """The enter id of the combo (first, then): `SLOTS` plus its place in `combos`, what the
    splice enters (`enter OWN_MOVE_MAIN id 0`); None for a pair that is none or past the
    framework's table."""
    try:
        return SLOTS + combos(m)[:COMBOS].index((first, then))
    except ValueError:
        return None


def hub(species: int) -> Pair | None:
    """The carrier an own move on `species` rides by default; None where none is known."""
    return HUBS.get(species)


def module_name(m: Manifest) -> str:
    """The Lua module `P.define` requires for the port's moves and rules."""
    return inject.port_module(m.port.name, "moves")


BUILD = re.compile(r"^  build = 0x([0-9A-F]{8}),$", re.M)


def build_of(text: str) -> int:
    """The module's `build`: 31 bits of its CRC-32 without that line, never 0 (the bridge's
    "any"); 0 for a text without one."""
    found = BUILD.search(text)
    return int(found.group(1), 16) if found else 0


def sever_below(m: Manifest) -> int | None:
    """The HP percent the tail-cut gate holds, from the one part that sets it."""
    return next((p.sever_below for p in m.parts.values() if p.sever_below is not None), None)


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
    e = layout.entry(m.clips[mv.clip])
    if e is None:
        raise ManifestError(f"moves.{name}: clip {mv.clip!r} has no executor entry in this build")
    return e


def curve(m: Manifest, name: str, layout: Layout) -> str | None:
    """The turn keys YAW follows, for a clip that turns: the clip's facing, which `carry` took
    out of its pose, whatever the move's own turn is."""
    t = layout.turns.get(entry(m, name, layout))
    return None if t is None else t.lua()


def records(game: Extracted, species: int) -> frozenset[int]:
    """The attack ids `species` has a record for: its overlay's moveset table, blanks left out."""
    t = hitbox.primary_table(hitbox.tables(game.overlay(em_overlay(species))))
    off = hitbox.id_offset(species, species) or 0
    return frozenset(r.index - off for r in ([] if t is None else t.attacks) if any(r.raw))


def lunge_record(lunge: Lunge) -> bytes:
    """A lunge as the registry holds it (struct STEER_LUNGE)."""
    return live.Lunge(lunge.window, lunge.near, lunge.span, lunge.base, lunge.range).pack()


def keys_of(text: str | None) -> list[int]:
    """The turn keys of a clips module's hex `text` (`curve`)."""
    text = text or ""
    return [int(text[i : i + 4], 16) for i in range(0, len(text), 4)]


def pools(m: Manifest, layout: Layout) -> Pools:
    """The registry's pools once the own moves are registered in slot order, as `mhfu_port.lua`
    does; a move `problems` refuses adds nothing."""
    out = Pools()
    for name, mv in ((n, m.moves[n]) for n in slots(m)):
        try:
            entry(m, name, layout)
            keys = keys_of(curve(m, name, layout))
            samples = steering.samples(mv.travel or ())
            turns = steering.turn_values(mv.code_turn or ())
            record = None if mv.lunge is None else lunge_record(mv.lunge)
        except (ManifestError, ValueError, OverflowError, struct.error):
            continue
        out.add(keys, samples, record, turns)
    return out


def motion_problems(mv: Move, frames: int | None) -> list[tuple[str, str]]:
    """What the move player cannot carry of an own move's travel, code turn, lunge, aim and fixed
    turn window, as (where under the move, text): travel samples or code-turn values past its
    capacity, its reach or the clip's `frames`; an aim with no code turn or an `aim_out` that is
    not a byte; a lunge or window outside `[start, stop]` (the clip's end for no stop) or with a
    distance that is not a length."""
    out: list[tuple[str, str]] = []
    end = mv.stop or frames
    try:
        steering.samples(mv.travel or ())
    except ValueError as err:
        out.append((".travel", str(err)))
    last = mv.start + steering.STEP * (len(mv.travel or ()) - 1)
    if frames is not None and mv.travel and last > frames:
        out.append(
            (
                ".travel",
                f"its {len(mv.travel)} samples reach frame {last}, past the clip's {frames}",
            )
        )
    try:
        steering.turn_values(mv.code_turn or ())
    except ValueError as err:
        out.append((".code_turn", str(err)))
    last = mv.start + steering.STEP * (len(mv.code_turn or ()) - 1)
    if frames is not None and mv.code_turn and last > frames:
        out.append(
            (
                ".code_turn",
                f"its {len(mv.code_turn)} values reach frame {last}, past the clip's {frames}",
            )
        )
    if mv.steer.turn == "aim" and not mv.code_turn:
        out.append((".steer", "turn = aim needs a code_turn"))
    if not 0 <= mv.aim_out <= 0xFF:
        out.append((".aim_out", f"{mv.aim_out} is not 0 to 255 AI frames"))
    if (lunge := mv.lunge) is not None:
        lo, hi = lunge.window
        if not 0 <= lo < hi <= 0xFFFF:
            out.append((".lunge", f"window {lunge.window} is not 0 <= lo < hi"))
        elif lo < mv.start or (end is not None and hi > end):
            out.append((".lunge", f"window {lunge.window} is outside [{mv.start}, {end}]"))
        values = (lunge.near, lunge.span, lunge.base, lunge.range)
        if not all(abs(v) <= FLOAT_MAX for v in values) or lunge.near < 0 or lunge.span <= 0:
            out.append((".lunge", "near is 0 or more, span above 0, all of them finite"))
    s = mv.steer
    if s.turn == "fixed" and s.window is not None:
        lo, hi = s.window
        if lo < mv.start or (end is not None and hi > end):
            out.append((".steer", f"fixed window {s.window} is outside [{mv.start}, {end}]"))
    return out


def problems(m: Manifest, layout: Layout, known: Collection[int] | None = None) -> list[str]:
    """Every own move or rule the module cannot carry: more own moves than slots, a clip with no
    entry, a start or stop past its clip or an attack outside them (`manifest.span_problems`), an
    attack past its clip or with no record in `known` (the host's, when given), more turn keys,
    travel samples, code-turn values or lunges than the framework holds (`Pools`: a run it holds
    twice is held once), travel or a code turn past the clip, an aim without a code turn, a lunge
    or fixed turn window outside `[start, stop]`, no carrier; more combos than the framework
    holds, a combo whose first move does not ride the hub; rules on the flinch whose moves ride
    different carriers."""
    out: list[str] = []
    if (n := sum(mv.own for mv in m.moves.values())) > SLOTS:
        out.append(f"moves: {n} own moves, the framework holds {SLOTS}")
    for name, mv in m.moves.items():
        if not mv.own:
            continue
        w = f"moves.{name}"
        try:
            e = entry(m, name, layout)
            carrier(m, name)
        except ManifestError as err:
            out.append(str(err))
            continue
        frames = layout.frames.get(e)
        out += [f"{w}{sub}: {text}" for sub, text in span_problems(mv, frames)]
        out += [f"{w}{sub}: {text}" for sub, text in motion_problems(mv, frames)]
        for i, a in enumerate(mv.attacks):
            if known is not None and a.id not in known:
                out.append(
                    f"{w}.attack[{i}]: host species {m.port.host_species} has no "
                    f"attack record {a.id}"
                )
            if frames is not None and a.frame >= frames:
                out.append(f"{w}.attack[{i}]: frame {a.frame} is past the clip's {frames}")
        keys = len(keys_of(curve(m, name, layout)))
        if keys > KEYS:
            out.append(f"{w}: the clip's turn has {keys} keys, a move holds {KEYS}")
    held = pools(m, layout)
    if len(held.keys) > POOL:
        out.append(
            f"moves: the own moves' turns need {len(held.keys)} keys, the framework holds {POOL}"
        )
    if len(held.samples) > SAMPLE_POOL:
        out.append(
            f"moves: the own moves' travel needs {len(held.samples)} samples, the framework "
            f"holds {SAMPLE_POOL}"
        )
    if len(held.turns) > TURN_POOL:
        out.append(
            f"moves: the own moves' code turns need {len(held.turns)} values, the framework "
            f"holds {TURN_POOL}"
        )
    if len(held.lunges) > LUNGES:
        out.append(f"moves: {len(held.lunges)} lunges, the framework holds {LUNGES}")
    pairs = combos(m)
    if len(pairs) > COMBOS:
        out.append(f"moves: {len(pairs)} combos, the framework holds {COMBOS}")
    for first, then in pairs:
        try:
            ride = carrier(m, first)
        except ManifestError:
            continue  # its move's own problem, above
        if ride != COMBO_HUB:
            out.append(
                f"moves.{first}: the first of combo ({first}, {then}) rides {ride}, a combo's "
                f"first rides the hub {COMBO_HUB}"
            )
    flinch = set()
    try:
        rules = behaviour.compile(m)
    except ManifestError as err:
        rules = []
        out.append(str(err))
    for r in rules:
        if r.on == "flinch" and r.play in m.moves:
            try:
                flinch.add(carrier(m, r.play))
            except ManifestError:
                pass  # its move's own problem, above
    if len(flinch) > 1:
        out.append(
            f"rule: the moves of the rules on the flinch ride {len(flinch)} carriers; the "
            "reaction replacement enters one"
        )
    return out


def check(m: Manifest, layout: Layout, known: Collection[int] | None = None) -> None:
    """Raise `ManifestError` with the first of `problems`."""
    found = problems(m, layout, known)
    if found:
        raise ManifestError(found[0])


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
        if s.window is not None:
            out["window"] = list(s.window)
    elif s.turn == "fixed":
        out |= {"turn": "fixed", "total": s.angle}
        out |= {"window": list(s.window)} if s.window is not None else {"frames": s.frames}
    elif s.turn == "aim":
        out["turn"] = "aim"
    if (keys := curve(m, name, layout)) is not None:
        out["curve"] = keys
    mv = m.moves[name]
    if mv.travel:
        out["travel"] = steering.hex_of(mv.travel)
    if mv.code_turn:
        out["code_turn"] = steering.turns_hex(mv.code_turn)
    if (lunge := mv.lunge) is not None:
        out["lunge"] = {
            "window": list(lunge.window),
            "near": lunge.near,
            "span": lunge.span,
            "base": lunge.base,
            "range": lunge.range,
        }
    return out


def _own_move(m: Manifest, name: str, layout: Layout, slot: int) -> dict[str, Any]:
    mv = m.moves[name]
    out: dict[str, Any] = {"entry": entry(m, name, layout), "slot": slot}
    if mv.clip is not None:
        out["clip"] = mv.clip
    if mv.attacks:
        out["attacks"] = [
            [a.frame, a.id, *([a.end] if a.end is not None else [])] for a in mv.attacks
        ]
    out["carrier"] = list(carrier(m, name))
    if mv.length is not None:
        out["length"] = mv.length
    if mv.start:
        out["start"] = mv.start
    if mv.stop:
        out["stop"] = mv.stop
    if mv.aim_out:
        out["aim_out"] = mv.aim_out
    if mv.host_attacks:
        out["host_attacks"] = True
    out["steer"] = _steer(m, name, layout)
    if mv.after is not None:
        out["after"] = mv.after
    return out


def _rule(r: Rule) -> dict[str, Any]:
    out: dict[str, Any] = {"no_play": True} if r.play is None else {"play": r.play}
    if r.on is not None:
        out["on"] = r.on
    if r.part is not None:
        out["part"] = r.part
    if r.signal is not None:
        out["signal"] = r.signal
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
    if r.force:
        out["force"] = True
    for k in ("conds", "effects"):
        if ops := getattr(r, k):
            out[k] = [list(op) for op in ops]
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


def lua(
    m: Manifest,
    layout: Layout,
    known: Collection[int] | None = None,
    tip: Tip | None = None,
    fatigue: Mapping[str, Any] | None = None,
    charge: Mapping[str, Any] | None = None,
) -> str:
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
    at = slots(m)
    for name, mv in m.moves.items():
        body = _own_move(m, name, layout, at[name]) if mv.own else pair_move(mv)
        out.append(f"    {lua_key(name)} = {lua_value(body)},")
    out += ["  },"]
    if pairs := combos(m)[:COMBOS]:
        listed = [[SLOTS + k, first, then] for k, (first, then) in enumerate(pairs)]
        out.append(f"  combos = {lua_value(listed)},")
    out += ["  rules = {"]
    out += [f"    {lua_value(_rule(r))}," for r in behaviour.compile(m)]
    out += ["  },"]
    for k, names in (("vars", behaviour.vars(m)), ("signals", behaviour.signals(m))):
        if names:
            out.append(f"  {k} = {lua_value(names)},")
    if (pct := sever_below(m)) is not None:
        out.append(f"  sever_below = {pct},")
    if not m.behaviour.natural_rage:
        out.append("  natural_rage = false,")
    if tip is not None:
        out.append(f"  tip = {lua_value(tip.pairs)},")
    if fatigue is not None:
        out.append(f"  fatigue = {lua_value(fatigue)},")
    if charge is not None:
        out.append(f"  charge = {lua_value(charge)},")
    out += ["}", ""]
    text = "\n".join(out)
    build = zlib.crc32(text.encode("utf-8")) & 0x7FFF_FFFF or 1
    return text.replace("return {\n", f"return {{\n  build = 0x{build:08X},\n", 1)
