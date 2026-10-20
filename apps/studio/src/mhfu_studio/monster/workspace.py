# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster workspace: a port manifest (or a bare monster PAC), its scene in the viewport,
and the docks around it. The panels show; this holds what they share and does what they ask.

Every edit goes through the `PortDocument`, one undo step each; whatever depends on the
manifest (clip names, volumes, the alignment) is re-read once per frame when the document's
manifest is a different object, so an edit, an undo and a save-as all land the same way.

A big monster of the game's own opens with its overlay's brain file (`species_doc`, a
`SpeciesDocument`): the Brain tab edits its AI script through `edit_brain`, the builds come off the
brain worker (`brain_built`, `brain_view`, `brain_outline`, ...), and Send and Push put the
build shown on the memory stick and into the game. With a port open there is no such file.
"""

from __future__ import annotations

import dataclasses
import time
import tomllib
from collections.abc import Callable, Hashable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mhfu import files, hitzone
from mhfu.em.intel import (
    AttackIntel,
    AttackRecord,
    HostSummary,
    PairIntel,
    PartIntel,
    SpeciesIntel,
)
from mhfu.em.script import brainfile, ir, stick
from mhfu.em.script import graph as script_graph
from mhfu.em.script import inject as script_inject
from mhfu.em.script.brainfile import BrainFileError
from mhfu.em.script.budget import LIMIT, Budget
from mhfu.em.script.build import Built
from mhfu.em.script.check import Finding as ScriptFinding
from mhfu.em.script.edit import EditError
from mhfu.em.script.inject import Container
from mhfu.files import Extracted
from mhfu.live import script as live_script
from mhfu.live.script import Trace
from mhfu_port import continuity, layout, sequence, slots
from mhfu_port.behaviour import Trigger
from mhfu_port.data import Data
from mhfu_port.manifest import MOVE_ATTACKS, AttackWindow, Manifest, ManifestError, Move
from mhfu_port.manifest import Clip as ManifestClip
from mhfu_port.model import MHFU, clip_key
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton
from ppsspp_debug import DebuggerError

from mhfu_studio.monster import (
    actions,
    align,
    authoring,
    behaviour,
    brain,
    brain_edit,
    brain_game,
    brain_graph,
    clip_browser,
    clip_game,
    clips,
    inputs,
    move_game,
    rule_game,
    sequences,
    species,
)
from mhfu_studio.monster.attacks import AttackSession, hitbox_of
from mhfu_studio.monster.brain import BrainView
from mhfu_studio.monster.clip_browser import ClipBrowser, SourceClip
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels.graph import MoveGraph
from mhfu_studio.monster.parts import PartSession
from mhfu_studio.monster.species_doc import Builds, SpeciesDocument
from mhfu_studio.monster.tools import HIT, HURT, NOUN, VolumeTools, describe
from mhfu_studio.monster.tools import KEYS as VOLUME_KEYS
from mhfu_studio.monster.turn import TURN_KEY, TurnGizmo
from mhfu_studio.shell import places
from mhfu_studio.shell.findings import LEVELS, Finding, Level
from mhfu_studio.shell.input import Key, Mod, Pointer
from mhfu_studio.shell.overlay import Overlay
from mhfu_studio.shell.text import keys
from mhfu_studio.shell.workspace import (
    SEND_KEY,
    Choice,
    Dock,
    Gesture,
    Shelf,
    Shortcut,
    Step,
    Warmup,
    Workspace,
    register,
)

if TYPE_CHECKING:
    import moderngl
    from mhfu.em.script import asm
    from mhfu.live.session import Session
    from mhfu.writes import Pushed
    from mhfu_port.model import Clip as SceneClip

    from mhfu_studio.monster.render.hitboxes import HitboxOverlay
    from mhfu_studio.monster.render.viewport import MonsterViewport
    from mhfu_studio.monster.runtime import Host
    from mhfu_studio.shell.studio import Studio

Pair = tuple[int, int]
#: a table's source: the base monster's, or the port's own (the panels' switch)
PORT, HOST = "port", "host"
#: the Timeline's height: its title, the transport and the frame strip; the rest scrolls
TIMELINE_H = 120
#: the left docks' width: the Actions table's four columns
ACTIONS_W = 460
PLAY = Shortcut(("Space",), "Plays the clip, or pauses it")
STEP = Shortcut(("Left", "Right"), "One game frame back, or on")
REWIND = Shortcut(("Home",), "Back to the clip's first frame")
#: `intel_gap`'s subjects in words
WORDS = {"part": "hurtbox", "attack": "attack", "action": "action"}
OPEN_TIP = (
    "Choose a port manifest (.toml), a brain file (emNN.toml) or a monster PAC; the monster"
    " workspace opens it"
)
SEND_HITBOXES, SEND_BRAIN = "Send hitboxes to game", "Send brain to game"
SENT = "The monster in the quest keeps the script it started with; yours runs from the next quest."
PUSHED = "The running monster runs it now, until the next quest load. Send keeps it."
DEBOUNCE = 0.15
"""Seconds after the last change before the script builds again."""
BRAIN_HINT = (
    "Edit the script in the Brain tab: pick a node, then change it in the bar or the palette"
)
FIXES = {
    "OPS_PER_RUN": "Put an enter on the long path: only a move ends a run.",
    "RESTART_LOOP": "Put an enter on the path, or a condition that stops it.",
    "FALLS_OFF": "End every path with an end or a goto.",
    "DEAD_CODE": "Nothing reaches it: delete it, or move the end that cuts it off.",
    "END_KIND_CONTEXT": "Pick an end that belongs to this routine's list.",
    "GATE_NONZERO": "Set the gate to 0, as the game's own moves have it.",
    "WEIGHT_ZERO": "Give the branch a weight, or take it out.",
    "RANDOM_WEIGHTS": "The weights must add up to 32: Even sets them.",
    "CORRELATED_DRAW": "Fold the two chances into one pick.",
    "PAIR_UNKNOWN": "Pick a pair from the list the bar offers.",
    "PAIR_RANGE": "main is 0 to 7.",
    "DOC_ANCHOR": "Its anchor is gone: remove this edit, or Back to the game's.",
    "DOC_CONFLICT": "These edits cannot go together: undo one.",
    "DOC_BUILD": "The file's edits are for another build of the game.",
}
"""What to do about a build's finding, by code."""


def port_files() -> list[Path]:
    """modkit's `ports/*.toml`, beside the working directory or the studio's own source."""
    here = Path(__file__).resolve().parents
    roots = [Path.cwd() / "ports", Path.cwd() / "modkit" / "ports"]
    roots += [here[5] / "ports"] if len(here) > 5 else []  # modkit/apps/studio/src/...
    found: dict[Path, None] = {}
    for root in roots:
        for f in sorted(root.glob("*.toml")) if root.is_dir() else []:
            found.setdefault(f.resolve())
    return list(found)


def port_choice(path: Path) -> Choice:
    """A port manifest as the start page lists it: its name, on its base monster."""
    try:
        port = tomllib.loads(path.read_text(encoding="utf-8")).get("port", {})
    except (OSError, tomllib.TOMLDecodeError):
        port = {}
    name = " ".join(w.capitalize() for w in str(port.get("name", path.stem)).split("_"))
    host = port.get("host_species")
    base = species.label(host if isinstance(host, int) else None)
    return Choice(
        name, f"Opens {path.name}: {name}, built on {base}", path=path, detail=f"on {base}"
    )


def toml_kind(path: Path) -> Literal["port", "brain"] | None:
    """What a TOML file is: a port manifest (a `[port]` table) or a brain file (`schema` and
    `overlay`); a malformed brain file is still one, `open` says what is wrong with it."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # TOMLDecodeError and a bad encoding are ValueErrors
        return None
    if "port" in data:
        return "port"
    return "brain" if "schema" in data and "overlay" in data else None


def budget_text(b: Budget) -> str:
    """The longest run between two yields as the Brain tab says it: `44 of 999`."""
    return f"{0 if b.worst is None else b.worst.count} of {LIMIT}"


def species_choice(sp: int, game: Extracted) -> Choice:
    """A big monster as the start page lists it: its model, with its brain file attached."""
    name = species.NAMES.get(sp, f"em{sp:02d}")
    return Choice(
        name,
        f"Opens {name}'s model and its brain: the AI script as a graph you can edit",
        path=game.path(files.monster_pac(sp)),
        detail=f"em{sp:02d}",
    )


class _Unchanged(Exception):
    """A gesture that changes nothing: `edit_brain` commits no undo step for it."""


def _finding(f: ScriptFinding, names: Mapping[str, str]) -> Finding:
    """A build's finding for the shell: it jumps to the node, else to the routine."""
    level: Level = next((lv for lv in LEVELS if lv == f.level), "warning")
    target = ("node", f.node) if f.node else (("routine", f.item) if f.item else None)
    where = "" if f.item is None else brain_graph.named(f.item, names)
    return Finding(level, f.code, f.text, where, target, "", FIXES.get(f.code, ""))


class BrainDocument(SpeciesDocument):
    """A species document whose findings include those of the build the Brain tab shows: the
    shell asks the document, and the workspace holds the builds (`extra` reads its cache)."""

    extra: Callable[[], list[Finding]] | None = None

    @classmethod
    def of(cls, doc: SpeciesDocument) -> BrainDocument:
        """`doc`, opened and not yet edited, as one that carries the build's findings too."""
        return cls(doc.brainfile, doc.path)

    def findings(self) -> list[Finding]:
        return [*super().findings(), *(self.extra() if self.extra is not None else [])]


@dataclass(frozen=True)
class _Warm:
    """What `warmup` read for `open`."""

    path: Path
    doc: PortDocument
    scene: Scene
    species: int | None
    intel: SpeciesIntel | None


@dataclass(frozen=True)
class _WarmBrain:
    """What `warmup` read for `open` of a monster that has a brain file."""

    path: Path
    scene: Scene
    doc: BrainDocument | None
    serves: dict[int, int] | None
    """The script rows each overlay serves, read to find the file."""
    note: str
    """Why there is no document, when the file was there and did not read."""


@dataclass
class _Shown:
    """A build the Brain tab shows, with what is made from it. One per row and build; the view,
    outline and bar are made on the GUI thread when asked and kept here."""

    built: Built
    proj: script_graph.Projection
    pairs: dict[Pair, str]
    """What the projection was made with."""
    edited: frozenset[str]
    views: dict[str, BrainView] = field(default_factory=dict)
    outlines: dict[tuple[str, tuple[tuple[str, str], ...]], brain_graph.Outline] = field(
        default_factory=dict
    )
    index: ir.Index | None = None
    blocks: dict[str, ir.Block] | None = None
    """The block of each arm, by the arm's id."""
    routines: dict[str, str] | None = None
    """The routine of each node, by id."""
    budget: Future[Budget] | None = None

    def has(self, view: BrainView | None) -> bool:
        return view is not None and any(v is view for v in self.views.values())


@dataclass(frozen=True)
class _Brain:
    """What the brain worker made for `brain`."""

    view: BrainView
    species: int | None
    """The species of the overlay that serves the row, whose intel labels it."""
    intel: SpeciesIntel | None
    """That intel, read on the worker when the cache had none (building it takes seconds)."""


class MonsterWorkspace(Workspace):
    name = "monster"
    filters = ("Port manifest or brain file", "*.toml", "Monster PAC", "*.bin *.pac")
    send_label = SEND_HITBOXES

    def __init__(self, data: Data | None = None, intel_root: Path | None = None) -> None:
        self._data = data
        #: games handed in stay; else `locate` drops them when `places` finds others
        self._handed = data is not None
        self._found = _roots()
        #: what the last `warmup` read, for `open` to take
        self._warm: _Warm | _WarmBrain | None = None
        #: the manifest as the last Send hitboxes to game sent it
        self._sent: Manifest | None = None
        #: species intel from this directory instead of the cache built from the game
        self.intel_root = intel_root
        self.intel_cache: dict[int, SpeciesIntel | None] = {}
        #: why a species' intel is None in the cache
        self.intel_errors: dict[int, str] = {}
        self.doc: PortDocument | None = None
        #: the brain file open for editing: a monster's own PAC attaches its overlay's, and with
        #: a port open there is none
        self.species_doc: BrainDocument | None = None
        self.scene: Scene | None = None
        self.vp: MonsterViewport | None = None
        self.message = ""
        #: a dock to bring forward, for the window (`take_focus`)
        self._focus: str | None = None
        #: a finding's control for its panel to land on (`land`)
        self.landing = ""
        self.tools = VolumeTools(self)
        self.turn = TurnGizmo(self)
        self.windows = authoring.WindowDrag(self._window_dragged)
        self._seen: Manifest | None = None
        #: every overlay summarised, once surveyed
        self.hosts: list[HostSummary] | None = None
        self._survey: Future[list[HostSummary]] | None = None
        self._pool: ThreadPoolExecutor | None = None
        #: the running game, for Play in game
        self.game_session: Callable[[], AbstractContextManager[Session]] = clip_game.attached
        #: asks the running port for an own move (Play in game in Moves)
        self.play_own: Callable[[Session, str, bool, int], bool] = move_game.play_own
        self.game_running: Callable[[], bool] = move_game.Running()
        #: reads the brain's rules from the running game, on a worker (Watch the game)
        self.read_brain: Callable[
            [Callable[[], AbstractContextManager[Session]], int | None], rule_game.Read
        ] = rule_game.fetch
        self.watch = rule_game.Watch()
        #: reads the species row's live monsters, on a worker (Follow the game); `seen` is
        #: Follow's, which finds a container on the stick
        self.read_follow: Callable[
            [
                Callable[[], AbstractContextManager[Session]],
                int,
                Container | None,
                brain_game.Seen,
            ],
            brain_game.Read,
        ] = brain_game.fetch
        self.follow = brain_game.Follow()
        #: a timed per-node count of the followed monster, on the same worker as Follow's reads
        self.count = brain_game.Count(self.follow)
        #: runs the dispatch trace in the game (Count), and puts a build into it (Push)
        self.trace_script: Callable[[Session, int, float], Trace] = live_script.trace
        self.push_script: Callable[[Session, ir.Program, asm.Image], Pushed] = script_inject.push
        #: the debounce's clock
        self.clock: Callable[[], float] = time.monotonic
        #: a species row's program and the rows each overlay serves, read from the extracted game
        self.decode_brain: Callable[[Extracted, int], ir.Program] = brain.decode
        self.scan_brain: Callable[[Extracted], dict[int, int]] = brain.rows
        #: why there is no game to read the AI scripts from
        self.brain_note = ""
        #: the brain worker: a decode never queues behind the host survey on `_pool`
        self._brain_pool: ThreadPoolExecutor | None = None
        #: by game root and species row, then the header asked for
        self._programs: dict[tuple[Path, int], ir.Program] = {}
        self._brain_views: dict[tuple[Path, int, str | None], BrainView] = {}
        self._brain_jobs: dict[tuple[Path, int, str | None], Future[_Brain]] = {}
        self._brain_errors: dict[tuple[Path, int], str] = {}
        self._brain_rows: dict[Path, dict[int, int]] = {}
        self._brain_scans: dict[Path, Future[dict[int, int]]] = {}
        self._reset()

    def _reset(self) -> None:
        """Per-document view state."""
        self.tools.reset()
        self.turn.cancel()
        self.windows.cancel()
        #: the attack window picked on the Timeline, of the selected own move
        self.picked_window: int | None = None
        #: the attack id a window drawn on the Timeline gets; None: `window_id`'s default
        self.next_window_id: int | None = None
        #: a block a finding named, for the Behaviour tab to pick and frame (`take_block`)
        self._block: str | None = None
        #: what the Behaviour canvas shows: `behaviour.ALL`, or a trigger's key
        self.scope = behaviour.ALL
        #: and what All shows: its rules in order, or the stored canvas
        self.all_view = behaviour.ORDER
        self.watch.reset()
        self.show_joint_ids = False
        self.undriven: dict[int, int] = {}
        self.markers: list[align.Marker] = []
        self.alignment: align.Alignment | None = None
        self.browse: int | None = None
        #: the species row `browse_brain` picked for the Brain tab
        self.brain_pick: int | None = None
        self._reset_brain()
        self.show_host = False
        self.host_scenes: dict[int, Scene | None] = {}
        self.host_clip: int | None = None
        self.pair: Pair | None = None
        self.move: str | None = None
        #: the step of a played sequence on screen (`play_sequence`)
        self.playing_step: str | None = None
        #: the played sequence went on to a step since the Timeline last looked
        self._stepped = False
        #: how the clip picker orders the clips (`sequences.BY`), and the Clips table (id, fit)
        self.pick_by = "fit"
        self.clip_order = "id"
        self.graph = MoveGraph()
        self.clip_filter = ""
        self.show_parts = False
        self.parts_source = HOST
        self.selected_part: int | None = None
        self.selected_volume: int | None = None
        self.only_selected_part = False
        self.part_name_buf = ""
        self.grid_state = 0
        #: a grid state to bring forward next frame (a finding was revealed)
        self.show_state: int | None = None
        self.show_attacks = False
        self.attacks_source = HOST
        self.selected_set: int | None = None
        self.selected_attack_volume: int | None = None
        self.sets_of_move_only = True
        self.attack_label_buf = ""
        self.edit_slot: int | None = None
        self.name_buf = ""
        self.label_buf = ""
        self.part_orphans: tuple[object, ...] = ()
        self.attack_orphans: tuple[object, ...] = ()
        self._vocab: clips.Vocabulary | None = None
        self._coverage: tuple[clips.Coverage, list[str]] | None = None
        self._travel: dict[int, tuple[float, float]] = {}
        self._counts: dict[int, int] | None = None
        self._labels: clips.LabelSession | None = None
        #: the Actions rows and what they were made from
        self._rows: tuple[tuple[object, ...], list[actions.ActionRow]] | None = None
        self._parts: PartSession | None = None
        self._attacks: AttackSession | None = None
        #: the donor clip picked in Clips, by MHP3rd id
        self.edit_clip: int | None = None
        self._browser: ClipBrowser | None = None
        #: why there is no browser
        self.browser_note = ""
        #: the donor's end poses and what they were read for (`ends`)
        self._ends: tuple[object, continuity.Ends | None] | None = None
        #: why there are none
        self.ends_note = ""
        self._previews: dict[int, SceneClip] = {}
        #: the scene and the layout it was built with (`_relayout`)
        self._built_for: Scene | None = None
        self._built: dict[int, int] = {}

    def _reset_brain(self) -> None:
        """The brain editor's state, for the document just loaded."""
        doc = self.species_doc
        self._builds = None if doc is None else Builds(doc)
        #: by species row: the build the tab shows, the one before it, the build under way (and
        #: the document version it was asked at), the version a build failed at and why
        self._shown: dict[int, _Shown] = {}
        self._past: dict[int, _Shown] = {}
        self._making: dict[int, tuple[int, Future[_Shown]]] = {}
        self._failed: dict[int, int] = {}
        self._build_errors: dict[int, str] = {}
        #: one per row and header, so a view draws only the routines an edit changed
        self._renderers: dict[tuple[int, str], brain.Renderer] = {}
        #: the document version the debounce last saw, and when the next build may start
        self._version = -1
        self._due = 0.0
        self._pairs: tuple[tuple[object, ...], dict[Pair, str]] | None = None
        #: the build Send last wrote to the stick
        self._sent_brain: int | None = None
        #: a node a finding or the outline named, where it is, and how often that happened
        self.brain_focus: str | None = None
        self.brain_routine: str | None = None
        self.brain_header: str | None = None
        self.brain_scope = script_graph.ROOT
        self.brain_focused = 0
        self.count.reset()

    # the shell's side

    @property
    def document(self) -> PortDocument | BrainDocument | None:
        """The port's manifest, else the brain file."""
        return self.doc if self.doc is not None else self.species_doc

    @property
    def viewport(self) -> MonsterViewport | None:
        return self.vp

    def can_open(self, path: Path) -> bool:
        """A port manifest (a TOML with a `[port]` table), a brain file (`emNN.toml`) or a monster
        PAC (one with a skeleton)."""
        try:
            if path.suffix == ".toml":
                return toml_kind(path) is not None
            if path.suffix not in (".bin", ".pac"):
                return False
            return any(Skeleton.sniff(e) for e in Pac.from_bytes(path.read_bytes()).entries)
        except (OSError, ValueError):
            return False

    def open(self, path: Path) -> None:
        """A manifest is built in memory from the extracted games, or comes from `warmup`; a
        brain file opens on its overlay's own monster; a PAC is read as it is, and a big
        monster's takes its overlay's brain file."""
        warm, self._warm = self._warm, None
        note = ""
        if warm is not None and warm.path == path:
            if isinstance(warm, _WarmBrain):
                note = self._take(warm)
                self.load(warm.scene, brain=warm.doc)
            else:
                self.load(warm.scene, warm.doc)
        elif path.suffix == ".toml" and toml_kind(path) == "brain":
            doc = BrainDocument.of(SpeciesDocument.open(path))
            scene = Scene.from_path(self.fu().path(files.monster_pac(doc.overlay)))
            self.load(scene, brain=doc)
        elif path.suffix == ".toml":
            port = PortDocument.open(path)
            self.load(Scene.from_manifest(port.manifest, None, "port", self.games()), port)
        else:
            got = self._attach(path)
            note = self._take(got)
            self.load(got.scene, brain=got.doc)
        if self.species_doc is not None:
            self.focus("Brain")
        self.message = f"opened {path.name}" + (f"; {note}" if note else "")

    def _attach(self, path: Path) -> _WarmBrain:
        """The monster PAC at `path` and, for a big monster, the brain file of the overlay that
        serves its row. Seconds the first time (the scan of the overlays): `warmup` does it off
        the GUI thread."""
        scene = Scene.from_path(path)
        row = species.of_model_file(path)
        if row is None:
            return _WarmBrain(path, scene, None, None, "")
        try:
            game = self.fu()
            serves = self.scan_brain(game)
            doc = BrainDocument.of(SpeciesDocument.for_species(row, serves))
        except (brain.NoScript, places.Missing):
            return _WarmBrain(path, scene, None, None, "")
        except (OSError, ValueError, LookupError) as e:  # BrainFileError is a ValueError
            return _WarmBrain(path, scene, None, None, f"its brain file was not read: {e}")
        return _WarmBrain(path, scene, doc, serves, "")

    def _take(self, got: _WarmBrain) -> str:
        """What a `_WarmBrain` read becomes known to the workspace; why it has no brain file."""
        if got.doc is not None and got.serves is not None:
            self._brain_rows.setdefault(self.fu().root, got.serves)
        return got.note

    def load(
        self,
        scene: Scene,
        doc: PortDocument | None = None,
        brain: BrainDocument | None = None,
    ) -> None:
        """Shows `scene`; `doc` is the manifest it was built from, `brain` the brain file open
        beside a monster of the game's own."""
        from mhfu_studio.monster.render.skeleton import undriven_geometry

        self.scene, self.doc = scene, doc
        self.species_doc = brain
        if brain is not None:
            brain.extra = self._brain_findings
        self.send_label = SEND_BRAIN if doc is None and brain is not None else SEND_HITBOXES
        self._reset()
        self._sent = None
        self.undriven = undriven_geometry(scene)
        if doc is not None:
            doc.pac, doc.sources = scene.pac, self.sources()
            doc.intel = self.host_intel()
        if self.vp is not None:
            self.vp.set_scene(scene)
        self.sync()

    def setup(self, ctx: moderngl.Context) -> MonsterViewport:
        from mhfu_studio.monster.render.viewport import MonsterViewport

        self.vp = MonsterViewport(ctx)
        if self.scene is not None:
            self.vp.set_scene(self.scene)
            self.sync()
        return self.vp

    def shown(self) -> Hashable | None:
        sc = self.scene
        doc = self.document
        return None if sc is None else (None if doc is None else doc.path, sc.name)

    def start(self) -> Sequence[Shelf]:
        ports = tuple(port_choice(p) for p in port_files())
        note = "" if ports else "No ports next to the studio (modkit's ports folder)."
        needs = (places.MHFU, places.MHP3RD)
        return (
            Shelf("Ports", ports, note, ("Open\u2026", OPEN_TIP), needs),
            self._monsters(),
        )

    def _monsters(self) -> Shelf:
        """The big monsters, each opened on its model with its brain file."""
        found = places.find(places.MHFU).path
        if found is None:
            note = "The monsters come from the game: choose the MHFU extraction under Setup."
            return Shelf("Monsters", note=note, needs=(places.MHFU,))
        game = Extracted(found)
        choices = sorted(
            (species_choice(sp, game) for sp in files.EM_SPECIES), key=lambda c: c.label
        )
        note = "A monster's brain is its AI script: edit it as a graph, send it to the game."
        return Shelf("Monsters", tuple(choices), note, needs=(places.MHFU,))

    def warmup(self, path: Path) -> Warmup | None:
        """A port's manifest, its build and its base monster's data, or a monster and its brain
        file, read off the GUI thread."""
        if path.suffix == ".toml" and toml_kind(path) == "brain":
            return None  # the model is one file: `open` reads it
        if path.suffix != ".toml":
            if species.of_model_file(path) is None:
                return None
            return Warmup(
                f"Opening {path.name}: reading its brain file (seconds the first time)",
                lambda: self._attach(path),
                self._warmed,
            )
        try:
            games = self.games()
        except places.Missing:
            return None  # `open` says what is missing
        intel_root, known = self.intel_root, set(self.intel_cache)

        def run() -> _Warm:
            doc = PortDocument.open(path)
            scene = Scene.from_manifest(doc.manifest, None, "port", games)
            sp = doc.manifest.port.host_species
            root = None if intel_root is not None else games.fu.root
            si = None if sp is None or sp in known else species.find(sp, intel_root, root)
            return _Warm(path, doc, scene, sp, si)

        def done(got: object) -> None:
            if isinstance(got, _Warm):
                self._warm = got
                if got.species is not None and got.intel is not None:
                    self.intel_cache.setdefault(got.species, got.intel)

        return Warmup(
            f"Opening {path.name}: building it, and its base monster's data the first time"
            " (about ten seconds)",
            run,
            done,
        )

    def _warmed(self, got: object) -> None:
        if isinstance(got, _WarmBrain):
            self._warm = got

    def next_steps(self) -> Sequence[Step]:
        if self.doc is None and self.species_doc is not None:
            return self._brain_steps(self.species_doc)
        m = self.manifest
        if self.scene is None or m is None:
            return ()
        return (
            Step("Pick an action", self.pair is not None),
            Step("Copy the base monster's hitboxes", bool(m.hitboxes)),
            Step("Change one", self._hitboxes_changed(m)),
            Step(self.send_label, self._sent is m, SEND_KEY),
        )

    def _hitboxes_changed(self, m: Manifest) -> bool:
        """One of the port's hitboxes is not a copy of the base monster's."""
        host = self.host_attacks()
        if host is None:
            return False
        own = {
            st.index: [dataclasses.replace(hitbox_of(sp, st.index), label="") for sp in st.spheres]
            for st in host.sets
        }
        return any(dataclasses.replace(h, label="") not in own.get(h.set, []) for h in m.hitboxes)

    def docks(self) -> Sequence[Dock]:
        def build(module: str, panel: str) -> Callable[[Studio], Any]:
            """The panel class imported when its dock is first built (Qt loads only then)."""
            return lambda studio: getattr(
                import_module(f"mhfu_studio.monster.panels.{module}"), panel
            )(self, studio)

        return (
            Dock(
                "Actions", "left", build("action", "ActionsPanel"),
                "Which clip plays for each of the base monster's actions: pick one to watch it,"
                " give it another clip, check its timing.",
                size=ACTIONS_W,
            ),
            Dock(
                "Clips", "left", build("clips", "ClipsPanel"),
                "Every anim, what is really in it, and the name it goes by.",
            ),
            Dock(
                "Moves", "left", build("moves", "MovesPanel"),
                "Your moves: on the base monster's actions, or your own, with their attacks and"
                " turn.",
                shown=False,
            ),
            Dock(
                "Scene", "left", build("scene", "ScenePanel"),
                "What the opened port holds: the model, its skeleton, clips and textures.",
                shown=False,
            ),
            Dock(
                "View", "left", build("scene", "ViewPanel"),
                "What the view shows and how. Changes nothing in the port.",
                shown=False,
            ),
            Dock(
                "Joints", "left", build("scene", "JointsPanel"),
                "The skeleton's bones; pick one to find it on the model.",
                shown=False,
            ),
            Dock(
                "Hitboxes", "right", build("hitboxes", "HitboxesPanel"),
                "Where the monster hits you: its attack spheres and their sizes, to tweak.",
            ),
            Dock(
                "Parts", "right", build("parts", "PartsPanel"),
                "Where the monster can be hit, and how much each spot takes.",
                shown=False,
            ),
            Dock(
                "Behaviour", "centre", build("behaviour", "BehaviourPanel"),
                "When the port plays which move: events, states and conditions wired to your"
                " moves on a canvas; higher blocks are checked first.",
                shown=False,
            ),
            Dock(
                "Brain", "centre", build("brain", "BrainPanel"),
                "The AI script that picks the monster's next move: edit it as a graph and as code"
                " when a brain file is open, read it with a port; follow its calls, find the"
                " line that plays an action.",
                shown=False,
            ),
            Dock(
                "Timeline", "bottom", build("timeline", "TimelinePanel"),
                "Play the clip at the game's own speed, and see where the base monster's action"
                " checks it.",
                alone=True,
                size=TIMELINE_H,
            ),
        )  # fmt: skip

    def status(self) -> str:
        """What is open; the last action's outcome is the studio's `message`."""
        sc, sp = self.scene, self.host_species
        if sc is None:
            return ""
        if self.doc is None and self.species_doc is not None:
            return f"{species.label(self.species_doc.overlay)} brain"
        return sc.name if sp is None else f"{sc.name} on {species.label(sp)}"

    def frame(self, dt: float) -> None:
        if self.doc is not None and self.doc.manifest is not self._seen:
            self.sync()
        if self.vp is not None:
            self.vp.tick(dt)
            self._advance_sequence()
            self.light_live()

    def animating(self) -> bool:
        vp = self.vp
        if vp is None or vp.actor is None:
            return False
        ref = vp.reference
        return vp.actor.playback.playing or (ref is not None and ref.playback.playing)

    def refresh(self) -> None:
        self.tools.cancel()  # an undo under a drag: the drag's start is gone
        self.turn.cancel()
        self.windows.cancel()
        self.sync()

    def take_focus(self) -> str | None:
        label, self._focus = self._focus, None
        return label

    def focus(self, dock: str) -> None:
        """Brings the dock labelled `dock` to the front after this change."""
        self._focus = dock

    def pointer(self, ev: Pointer) -> Gesture:
        """The turn gizmo's handle, then picking and the volume gizmo (`tools`); a drag off a
        handle stays the camera's."""
        got = self.turn.pointer(ev)
        return self.tools.pointer(ev) if got is None else got

    def key(self, ev: Key) -> bool:
        """The turn gizmo's, the picked volume's keys, then the transport's while a clip is on
        screen."""
        if ev.mods == Mod.NONE and ev.name == "Escape" and self.turn.dragged is not None:
            self.turn.cancel()
            return True
        if ev.mods == Mod.NONE and ev.name == TURN_KEY.keys[0]:
            self.turn.shown = not self.turn.shown
            self.turn.cancel()
            return True
        if self.tools.key(ev):
            return True
        acts: dict[str, Callable[[], None]] = {
            PLAY.keys[0]: self.play_pause,
            STEP.keys[0]: lambda: self.step(-1),
            STEP.keys[1]: lambda: self.step(1),
            REWIND.keys[0]: self.rewind,
        }
        fn = acts.get(ev.name)
        if fn is None or ev.mods != Mod.NONE or self.vp is None or self.vp.clip is None:
            return False
        fn()
        return True

    def shortcuts(self) -> Sequence[Shortcut]:
        return (PLAY, STEP, REWIND, TURN_KEY, *VOLUME_KEYS)

    def hint(self) -> str:
        sc, vp = self.scene, self.vp
        if sc is None:
            return "Pick a port on the start page, or open one with File > Open"
        if self.doc is None and self.species_doc is not None:
            return BRAIN_HINT
        bits = []
        clip = None if vp is None else vp.clip
        if vp is None or clip is None:
            bits.append("Pick an action in Actions, or a clip in Clips, to play it")
        else:
            found = self.manifest_clip(clip.slot)
            name = f" {found[0]}" if found else ""
            bits += [
                f"clip {clip.slot}{name}",
                f"{keys(PLAY.keys)} {'pause' if vp.playback.playing else 'play'}",
                f"{keys(STEP.keys)} step a frame",
                f"{keys(REWIND.keys)} rewind",
            ]
        picked = self.tools.hint()
        t = self.turn.target()
        if picked:
            bits.append(picked)
        elif self.selected_set is not None:
            bits.append(f"hit group {self.selected_set}: pick one of its hitboxes in Hitboxes")
        elif t is not None and t.writes is not None:
            bits.append(f"drag the ring's handle to set the turn \u00b7 {keys(TURN_KEY.keys)} hide")
        return " \u00b7 ".join(bits)

    def paint(self, o: Overlay) -> None:
        from mhfu_studio.monster.panels import viewport

        viewport.joint_labels(self, o)
        self.turn.paint(o)
        self.tools.paint(o)

    def reveal(self, target: Hashable, focus: str = "") -> None:
        """A finding's `(section, key)`: select it, bring its panel forward and land on `focus`.
        With a brain file open, `("node", id)` or `("routine", name)` of the script."""
        if not isinstance(target, tuple) or len(target) != 2:
            return
        self.landing = focus
        section, key = target
        m = self.manifest
        if m is None:
            if self.species_doc is not None:
                self._reveal_brain(section, key)
            return
        if section == "clips" and isinstance(key, str) and key in m.clips:
            at = clips.at(m.clips[key], self.sources())
            if at is None:
                self.play_source(m.clips[key].id)
            else:
                self.play_slot(at)
            self.focus("Clips")
        elif section == "moves" and isinstance(key, str) and key in m.moves:
            self.select_move(key)
            self.focus("Moves" if m.moves[key].own else "Actions")
        elif section == "block" and isinstance(key, str) and key in m.behaviour.blocks:
            self._block = key
            self.focus("Behaviour")
        elif section == "behaviour":
            self.focus("Behaviour")
        elif section == "hurtbox" and isinstance(key, int) and key < len(m.hurtboxes):
            self.show_parts, self.parts_source = True, PORT
            self.sync_hitboxes()
            self.select_volume(key)
            part = m.hurtboxes[key].part
            if part is not None:
                self.select_part(part & hitzone.PART_MASK)
            self.focus("Parts")
        elif section == "part" and isinstance(key, int):
            self.show_parts, self.parts_source = True, PORT
            self.sync_hitboxes()
            self.select_part(key)
            self.focus("Parts")
        elif section == "hitzone" and isinstance(key, int):
            self.parts_source, self.show_state = PORT, key
            self.focus("Parts")
        elif section == "hitbox" and isinstance(key, int) and key < len(m.hitboxes):
            self.show_attacks, self.attacks_source = True, PORT
            self.select_set(m.hitboxes[key].set)
            self.sync_attacks()
            self.select_attack_volume(key)
            self.focus("Hitboxes")
        elif section == "set" and isinstance(key, int):
            self.edit_set(key)
        elif section == "attack" and isinstance(key, int) and key < len(m.attacks):
            a, host = m.attacks[key], self.host_attacks()
            rec = None if host is None else host.attack(a.id)
            known = host is not None and a.volume is not None and host.set(a.volume) is not None
            vol = a.volume if known else None if rec is None else rec.volume
            self.show_attacks, self.attacks_source = True, PORT
            if vol is not None:
                self.select_set(vol)
            self.sync_attacks()
            self.focus("Hitboxes")
        elif section == "effect" and isinstance(key, int) and key < len(m.effects):
            e = m.effects[key]
            if self.vp is not None:
                self.vp.select_joint(e.bone)
            if e.move in m.moves and (pr := m.moves[e.move].pair) is not None:
                self.select_action(*pr, e.move)
            self.focus("Actions")

    def close(self) -> None:
        if self.vp is not None:
            self.vp.release()
            self.vp = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self._brain_pool is not None:
            self._brain_pool.shutdown(wait=False, cancel_futures=True)
            self._brain_pool = None

    # what the document says

    @property
    def manifest(self) -> Manifest | None:
        return None if self.doc is None else self.doc.manifest

    def sync(self) -> None:
        """Re-reads everything the manifest decides."""
        self._relayout()
        m = self.manifest
        self._seen = m
        if self.scene is not None and m is not None:
            self.scene.attach_manifest(m)
        self._vocab = None
        if self.pair is None and m is not None and self.move not in m.moves:
            self.move = None  # an undo took the own move away
        mv = self.own_move()
        if mv is None or not 0 <= (self.picked_window or 0) < len(mv.attacks):
            self.picked_window = None
        self.sync_steer()
        self.sync_hitboxes()
        self.sync_attacks()
        self.recompute_alignment()

    def edit(self, what: str, fn: Callable[[], object]) -> bool:
        """Runs a session call, a refusal reported instead of raised; `what` (or the call's own
        message) goes to the status bar."""
        try:
            got = fn()
        except ManifestError as e:
            self.message = str(e)
            return False
        self.sync()
        self.message = got if isinstance(got, str) and not what else what
        return True

    def edit_volume(self, which: str, index: int, **fields: Any) -> bool:
        """Fields of volume `index` of `which` (`HURT`, `HIT`), one undo step: the forms' and the
        gizmo's one way in."""
        sess = self.part_session if which == HURT else self.attack_session
        if sess is None:
            self.message = "no manifest to write to"
            return False
        msg = f"{NOUN[which]} {index}: {describe(fields)}"
        return self.edit(msg, lambda: sess.edit_volume(index, **fields))

    def save(self) -> None:
        doc = self.document
        if doc is None:
            self.message = "this scene has no manifest file"
            return
        try:
            self.message = f"saved {doc.save()}"
        except (OSError, ManifestError, BrainFileError) as e:
            self.message = f"{type(e).__name__}: {e}"

    def revert(self) -> None:
        if self.doc is not None:
            self.doc.revert()
        elif self.species_doc is not None:
            self.species_doc.revert()
        else:
            return
        self.sync()
        self.message = "back to the file on disk"

    # the runtime module: the manifest as it is now, saved or not (its header says which)

    def exportable(self) -> bool:
        """The manifest has tables the runtime module carries."""
        from mhfu_studio.monster import runtime

        return self.manifest is not None and runtime.has_tables(self.manifest)

    def _module(self) -> tuple[Manifest, Host | None, str]:
        """What `runtime.export` takes for the document as it is now."""
        from mhfu_studio.monster import runtime

        if self.doc is None:
            raise ValueError("no port manifest open")
        m, intel = self.doc.manifest, self.host_intel()
        source = f"{runtime.source_of(m)}, unsaved edits" if self.doc.dirty else ""
        return m, runtime.host(intel), source

    def export_hit(self) -> None:
        """`<name>_hit.lua` in the working directory."""
        from mhfu_studio.monster import runtime

        m, host, source = self._module()
        path = runtime.export(m, None, host, source).resolve()
        self.message = f"wrote {path} (id {runtime.content_id(m)})"

    def deploy_hit(self) -> None:
        """Exported fresh into the studio's cache, then copied with a stale `mhfu_port.lua`
        to the memory stick's mods folder."""
        from mhfu_studio.monster import runtime

        m, host, source = self._module()
        out = runtime.cache_dir() / runtime.module_name(m)
        dep = runtime.ship(m, out, host, mods_dir=self.mods_dir(), source=source)
        self._sent = m
        lib = "" if dep.library is None else f" and {runtime.LIB_SUBDIR}/{runtime.LIBRARY}"
        self.message = (
            f"sent {dep.module.name}{lib} to {_home(dep.module.parent)}; it applies when a mod"
            f" defines the port '{m.port.name}' and the monster is in the area (id"
            f" {runtime.content_id(m)})"
        )

    @staticmethod
    def mods_dir() -> Path:
        """The memory stick's mods folder; FileNotFoundError says why there is none."""
        return places.mods_dir()

    def send_blocker(self) -> str | None:
        from mhfu_studio.monster import runtime

        m = self.manifest
        if m is None and self.species_doc is not None:
            return self.brain_send_blocker()
        if m is None:
            if self.scene is None:
                return "no port open: open a port manifest (ports/<name>.toml)"
            return "a bare PAC has no hit tables: open its port manifest (.toml) instead"
        if not runtime.has_tables(m):
            return (
                "nothing to send yet: your port has no hitboxes, hurtboxes or damage grid. Copy"
                " the base monster's in Hitboxes or Parts first."
            )
        try:
            runtime.check(m, runtime.host(self.host_intel()))
        except ManifestError as e:
            return self._cannot("send", m, e)
        try:
            self.mods_dir()
        except places.Missing as e:
            return f"no memory stick to send to: {e.words}"
        except FileNotFoundError as e:
            return f"the framework is not on the memory stick ({e}): install it there first"
        return None

    def send(self) -> None:
        """The hit tables onto the memory stick (`deploy_hit`), no save needed; with a brain
        file open, the script (`send_brain`)."""
        if self.doc is None and self.species_doc is not None:
            self.send_brain()
            return
        try:
            self.deploy_hit()
        except (OSError, ValueError) as e:  # ManifestError is a ValueError
            self.message = f"send failed: {e}"

    def _cannot(self, verb: str, m: Manifest, e: ManifestError) -> str:
        """Why the tables cannot go: the missing intel when that is it, else `e`."""
        attacks = bool(m.hitboxes or m.attacks)
        gap = self.intel_gap("attack" if attacks else "part")
        what = "hitboxes or attacks" if attacks else "hurtboxes or the damage grid"
        return f"cannot {verb} {what}. {gap}" if gap else f"cannot {verb}: {e}"

    def push_blocker(self) -> str | None:
        """Why "Push to game" cannot run now; a game not attached is found by the push."""
        from mhfu_studio.monster import runtime

        m = self.manifest
        if m is None or not runtime.has_tables(m):
            return self.send_blocker()
        try:
            runtime.check(m, runtime.host(self.host_intel()))
        except ManifestError as e:
            return self._cannot("push", m, e)
        return None

    def push(self) -> None:
        """The hit tables straight into the running game, read back; no save, no stick."""
        from mhfu_studio.monster import push

        try:
            m, host, source = self._module()
            done = push.to_game(m, host, source)
        except (OSError, ValueError) as e:  # Refused and ManifestError are ValueErrors
            self.message = f"push failed: {e}"
            return
        try:
            stale = push.stick_differs(m, self.mods_dir())
        except OSError:  # no stick: nothing there to fight the push
            stale = ""
        self.message = done.describe() + stale

    # the games and the intel

    def games(self) -> Data:
        """Both extracted games; `places.Missing` says which is not there."""
        if self._data is None:
            self._data = places.games()
        return self._data

    def fu(self) -> Extracted:
        return self._data.fu if self._data is not None else places.extracted()

    def locate(self) -> None:
        """Other games from `places`: what was read from the old ones goes."""
        found = _roots()
        if self._handed or found == self._found:
            return
        self._found, self._data = found, None
        for held in (self._shown, self._past, self._making, self._failed, self._build_errors):
            held.clear()
        self.intel_cache.clear()
        self.intel_errors.clear()
        self.host_scenes.clear()
        self.hosts, self._survey = None, None
        self._coverage = self._vocab = self._ends = None
        if self.doc is not None:
            self.doc.intel = self.host_intel()

    def intel_of(self, sp: int | None) -> SpeciesIntel | None:
        if sp is None:
            return None
        if sp not in self.intel_cache:
            try:
                root = None if self.intel_root is not None else self.fu().root
                self.intel_cache[sp] = species.load(sp, self.intel_root, root)
            except places.Missing as e:
                self.intel_errors[sp] = e.words
                self.intel_cache[sp] = None
            except LookupError as e:
                self.intel_errors[sp] = str(e)
                self.intel_cache[sp] = None
            except (OSError, ValueError) as e:
                self.intel_errors[sp] = f"reading or building it failed ({e})"
                self.message = f"no data for {species.label(sp)}: {self.intel_errors[sp]}"
                self.intel_cache[sp] = None
        return self.intel_cache[sp]

    def intel_gap(self, what: str, sp: int | None = None) -> str:
        """Why `sp`'s (the base monster's by default) `what` intel ("part", "attack",
        "action") is missing, and what to do; empty when it is there."""
        sp = self.host_species if sp is None else sp
        if sp is None:
            return "No base monster: a bare PAC names none. Open its port manifest (.toml) instead."
        si = self.intel_of(sp)
        if si is None:
            why = self.intel_errors.get(sp, "it was not loaded")
            return f"No {WORDS[what]} data for {species.label(sp)}: {why}."
        if what == "part" and not si.parts.present:
            why = si.parts.grid_reason
        elif what == "attack" and not si.attacks.present:
            why = si.attacks.reason
        else:
            return ""
        name = species.label(sp)
        return f"No {WORDS[what]} data for {name}: its code has none the studio reads ({why})."

    @property
    def host_species(self) -> int | None:
        m = self.manifest
        return None if m is None else m.port.host_species

    def host_intel(self) -> SpeciesIntel | None:
        return self.intel_of(self.host_species)

    def host_parts(self) -> PartIntel | None:
        """The host's part intel: the volumes the port rides, whatever overlay is browsed."""
        si = self.host_intel()
        return si.parts if si is not None and si.parts.present else None

    def host_attacks(self) -> AttackIntel | None:
        si = self.host_intel()
        return si.attacks if si is not None and si.attacks.present else None

    @property
    def browsing_species(self) -> int | None:
        return self.host_species if self.browse is None else self.browse

    @property
    def browsing_the_host(self) -> bool:
        return self.browsing_species == self.host_species

    @property
    def intel(self) -> SpeciesIntel | None:
        """The overlay the Actions panel shows: the host's unless browsing another."""
        return self.intel_of(self.browsing_species)

    def browse_species(self, sp: int) -> None:
        """Another overlay's action table: comparing, not re-hosting (host_species also picks
        the host PAC the porter files clips into, so changing it is a rebuild)."""
        self.browse = int(sp)
        self.clear_pair()
        self.sync_reference()

    def host_options(self) -> list[HostSummary] | None:
        """Every overlay summarised, surveyed in the background on first ask (building the
        cache takes a minute); None until it is done."""
        if self.hosts is not None:
            return self.hosts
        if self._survey is None:
            try:
                root = None if self.intel_root is not None else self.fu().root
            except places.Missing as e:
                self.message = f"could not compare the monsters: {e.words}"
                self.hosts = []
                return self.hosts
            self._pool = self._pool or ThreadPoolExecutor(1)
            self._survey = self._pool.submit(species.survey, self.intel_root, root)
        if not self._survey.done():
            return None
        try:
            self.hosts = self._survey.result()
        except (OSError, ValueError) as e:
            self.message = f"could not compare the monsters: {e}"
            self.hosts = []
        return self.hosts

    # the AI script

    @property
    def brain_species(self) -> int | None:
        """The species row the Brain tab shows: the one `browse_brain` picked, else the port's
        base monster, else the opened monster PAC's."""
        if self.brain_pick is not None:
            return self.brain_pick
        if self.host_species is not None:
            return self.host_species
        path = None if self.scene is None else self.scene.path
        return None if path is None else species.of_model_file(path)

    def browse_brain(self, row: int) -> None:
        self.brain_pick = int(row)
        self.count.reset()

    def brain_model(self, row: int) -> Path | None:
        """The model to open, with its overlay's brain file, when the Brain tab picks species row
        `row`: a monster of the game's own is open and its brain file does not take edits for
        `row`. None to read `row` in place: a port is open, the open file serves it, or there is
        no such model."""
        if self.doc is not None or self.species_doc is None or self.brain_editable(row):
            return None
        game = self._brain_game()
        path = None if game is None else game.path(files.monster_pac(row))
        return path if path is not None and path.is_file() else None

    def _brain_game(self) -> Extracted | None:
        """The game the scripts are read from; None, `brain_note` saying why, without one."""
        try:
            game = self.fu()
        except places.Missing as e:
            self.brain_note = e.words
            return None
        self.brain_note = ""
        return game

    def _brain_worker(self) -> ThreadPoolExecutor:
        self._brain_pool = self._brain_pool or ThreadPoolExecutor(1)
        return self._brain_pool

    def brain_rows(self) -> dict[int, int] | None:
        """The script rows the overlays serve, each with its overlay's species: what the choice
        of species lists. The first call starts the scan (seconds, on the brain worker, behind
        any decode asked before it); None until it is done."""
        game = self._brain_game()
        if game is None:
            return None
        root = game.root
        if root not in self._brain_rows:
            scan = self._brain_scans.get(root)
            if scan is None:
                self._brain_scans[root] = self._brain_worker().submit(self.scan_brain, game)
                return None
            if not scan.done():
                return None
            del self._brain_scans[root]
            try:
                self._brain_rows[root] = scan.result()
            except (OSError, ValueError, LookupError) as e:
                self.message = f"could not list the AI scripts: {e}"
                self._brain_rows[root] = {}
        return self._brain_rows[root]

    def brain_headers(self, row: int) -> list[brain.Variant]:
        """The row's headers and the map types each runs; empty until its program is decoded."""
        game = self._brain_game()
        p = None if game is None else self._programs.get((game.root, row))
        return [] if p is None else brain.variants(p)

    def brain_error(self, row: int) -> str:
        """Why `row` has no script view: a sentence; empty while it is on its way or there."""
        game = self._brain_game()
        if game is None:
            return self.brain_note
        return self._brain_errors.get((game.root, row)) or self._build_errors.get(row, "")

    def brain(self, row: int, header: str | None = None) -> BrainView | None:
        """Species row `row`'s script as `header` runs it: by default the header most map types
        use, and an unknown one is the default. None until the brain worker has decoded it
        (seconds for the biggest): ask again from the next sync, until `brain_error` has a
        reason. Another header of a decoded row is quick."""
        game = self._brain_game()
        if game is None:
            return None
        root = game.root
        asked = (root, row, header)
        decoded = self._programs.get((root, row))
        key = asked if decoded is None else (root, row, brain.pick_header(decoded, header))
        if key in self._brain_views:
            return self._brain_views[key]
        if (root, row) in self._brain_errors:
            return None
        job = self._brain_jobs.get(asked)
        if job is None:
            known = frozenset(self.intel_cache)
            self._brain_jobs[asked] = self._brain_worker().submit(
                self._make_brain, game, row, header, known
            )
            return None
        if not job.done():
            return None
        del self._brain_jobs[asked]
        label = species.label(row)
        try:
            built = job.result()
        except brain.NoScript:
            self._brain_errors[root, row] = f"{label} has no AI script the studio reads"
            return None
        except (OSError, ValueError, LookupError) as e:
            self._brain_errors[root, row] = f"reading the AI script of {label} failed ({e})"
            return None
        if built.species is not None and built.intel is not None:
            self.intel_cache.setdefault(built.species, built.intel)
        self._label_brain(built.view, built.species)
        self._brain_views[key] = self._brain_views[root, row, built.view.header] = built.view
        return built.view

    def _make_brain(
        self, game: Extracted, row: int, header: str | None, known: frozenset[int]
    ) -> _Brain:
        """On the brain worker: the program (decoded once per row) as `header` runs it, and the
        intel that labels it unless `known` has it."""
        p = self._programs.get((game.root, row))
        if p is None:
            p = self._programs[game.root, row] = self.decode_brain(game, row)
        view = brain.view(p, brain.pick_header(p, header))
        sp = row if row in files.EM_SPECIES else self.scan_brain(game).get(row)
        si = None
        if sp is not None and sp not in known:
            root = None if self.intel_root is not None else game.root
            try:
                si = species.find(sp, self.intel_root, root)
            except (OSError, ValueError):
                pass  # intel_of says why, on the GUI thread
        return _Brain(view, sp, si)

    def _label_brain(self, view: BrainView, sp: int | None) -> None:
        """What each `enter` line plays, from the intel of the overlay that serves the row."""
        if sp is None or self.intel_of(sp) is None:
            return
        for at, (main, sub) in view.enters.items():
            view.labels[at] = self.plays(main, sub, species=sp).text()

    def poll_follow(self, view: BrainView | None) -> bool:
        """One step of Follow the game, at most one read a second; True when its marks changed.
        A view of an edited build is marked from that build's image."""
        heads = [] if view is None else self.brain_headers(view.row)
        return self.follow.poll(
            view, heads, self.game_running(), self._ask_follow, self._followed(view)
        )

    def place_follow(self, view: BrainView | None) -> bool:
        """Marks `view`, a new one, from the last read of the game; True when they changed."""
        heads = [] if view is None else self.brain_headers(view.row)
        return self.follow.place(view, heads, self._followed(view))

    def _ask_follow(self, row: int, known: Container | None) -> Future[brain_game.Read]:
        self._pool = self._pool or ThreadPoolExecutor(1)
        return self._pool.submit(self.read_follow, self.game_session, row, known, self.follow.seen)

    def _followed(self, view: BrainView | None) -> Built | None:
        """The build `view` shows when that is an edited one: Follow and Count read the monster
        against its image. An unedited build is the game's own script, which a view of the lift
        already is."""
        if view is None:
            return None
        for held in (self._shown.get(view.row), self._past.get(view.row)):
            if held is not None and held.has(view):
                return None if held.built.edits.is_empty() else held.built
        return None

    def count_brain(self, seconds: float = brain_game.SECONDS) -> bool:
        """Counts the followed monster's script by node for `seconds` emulated seconds, on the
        worker Follow reads on (`count` holds the answer); False, `count.status` in `message`,
        when it cannot start."""
        row = self.brain_species
        lift = None if row is None else self.brain_lift(row)
        started = self.count.start(self._shown_edit(row), self._run_trace, seconds, lift)
        if not started:
            self.message = self.count.status
        return started

    def poll_count(self) -> bool:
        """Takes a finished count; True when what the tab shows changed."""
        return self.count.poll(self._shown_edit(self.brain_species))

    def _shown_edit(self, row: int | None) -> Built | None:
        """The build shown for `row` when it differs from the game's own script."""
        held = None if row is None else self._shown.get(row)
        return None if held is None or held.built.edits.is_empty() else held.built

    def _run_trace(self, entity: int, seconds: float) -> Future[Trace]:
        self._pool = self._pool or ThreadPoolExecutor(1)
        return self._pool.submit(self._trace, entity, seconds)

    def _trace(self, entity: int, seconds: float) -> Trace:
        with self.game_session() as s:
            return self.trace_script(s, entity, seconds)

    # the brain editor: a brain file's builds, and what the Brain tab draws from them

    def brain_editable(self, row: int) -> bool:
        """The open brain file takes edits for species row `row`: its overlay serves it."""
        doc = self.species_doc
        if doc is None:
            return False
        if row == doc.overlay:
            return True
        game = self._brain_game()
        serves = None if game is None else self._brain_rows.get(game.root)
        return serves is not None and serves.get(row) == doc.overlay

    def brain_lift(self, row: int) -> ir.Program | None:
        """The game's own program of `row`, as the worker decoded it; None until then. The
        gestures edit against it."""
        game = self._brain_game()
        return None if game is None else self._programs.get((game.root, row))

    def brain_names(self, row: int) -> dict[str, str]:
        """The display names the file gives routines of `row`, by routine name."""
        es = None if self.species_doc is None else self.species_doc.brainfile.edits(row)
        return {} if es is None else dict(es.names)

    def brain_notes(self, row: int) -> dict[str, str]:
        """The notes the file gives nodes, inserts and routines of `row`, by id."""
        es = None if self.species_doc is None else self.species_doc.brainfile.edits(row)
        return {} if es is None else dict(es.notes)

    def brain_pairs(self) -> dict[Pair, str]:
        """What names a move pair of the open file's overlay: the file's own names, else what
        the species' intel says plays (`plays`). Labels the graph and the bar's pair list."""
        doc = self.species_doc
        if doc is None:
            return {}
        si = self.intel_cache.get(doc.overlay)
        key = (id(si), *sorted(doc.brainfile.pairs.items()))
        if self._pairs is None or self._pairs[0] != key:
            labels: dict[Pair, str] = {}
            if si is not None:
                for p in si:
                    what = self.plays(p.main, p.sub, species=doc.overlay)
                    if what.slot is not None:
                        labels[p.main, p.sub] = what.text()
            self._pairs = (key, labels | doc.brainfile.pairs)
        return self._pairs[1]

    def brain_built(self, row: int) -> Built | None:
        """The newest build of `row` that is done: while the next computes this one stays, and
        `brain_pending` says there is one. None until the first, and for a row the file does not
        edit. Call it from the panel's sync, and from its timer while `brain_pending`."""
        held = self._advance(row)
        return None if held is None else held.built

    def brain_pending(self, row: int) -> bool:
        """What `brain_built` shows is not the build of the file as it is now: a change is
        waiting out the debounce, or its build is computing. False once it failed."""
        doc = self.species_doc
        if doc is None or self._builds is None or not self.brain_editable(row):
            return False
        if self._failed.get(row) == doc.version or self.brain_error(row):
            return False
        held = self._shown.get(row)
        return held is None or self._builds.current(row) is not held.built

    def brain_projection(self, row: int) -> script_graph.Projection | None:
        """The graphs of the build shown, one projection per build (the overview is made on the
        worker)."""
        held = self._shown.get(row)
        if held is None:
            return None
        pairs = self.brain_pairs()
        if pairs != held.pairs:
            held.proj = script_graph.Projection(held.built.applied, held.edited, pairs)
            held.pairs = pairs
            held.outlines.clear()
        return held.proj

    def brain_view(self, row: int, header: str | None = None) -> BrainView | None:
        """The script of `row` as `header` runs it, for the Brain tab: with a brain file that
        edits the row, the build shown (rows flagged `edited`, one view per build and header);
        else the game's own (`brain`). None until it is made; ask again from the next sync."""
        if not self.brain_editable(row):
            return self.brain(row, header)
        held = self._advance(row)
        if held is None:
            return None
        h = brain.pick_header(held.built.program, header)
        if h not in held.views:
            renderer = self._renderers.setdefault((row, h), brain.Renderer())
            view = brain.view(held.built.program, h, held.edited, renderer)
            self._label_brain(view, None if self.species_doc is None else self.species_doc.overlay)
            held.views[h] = view
        return held.views[h]

    def brain_outline(self, row: int, header: str | None = None) -> brain_graph.Outline | None:
        """The outline of the build shown, with the file's names and the build's findings."""
        proj = self.brain_projection(row)
        held = self._shown.get(row)
        if proj is None or held is None:
            return None
        h = brain.pick_header(held.built.program, header)
        names = self.brain_names(row)
        key = (h, tuple(sorted(names.items())))
        if key not in held.outlines:
            held.outlines[key] = brain_graph.outline(proj.overview(), h, names, held.built.findings)
        return held.outlines[key]

    def brain_routine_graph(
        self, row: int, routine: str, header: str | None = None
    ) -> script_graph.RoutineGraph | None:
        """The graph of one routine of the build shown, its links through `header`."""
        proj, held = self.brain_projection(row), self._shown.get(row)
        if proj is None or held is None or routine not in proj.routines:
            return None
        return proj.routine(routine, brain.pick_header(held.built.program, header))

    def brain_plan(
        self, row: int, routine: str, scope: str = script_graph.ROOT, header: str | None = None
    ) -> brain_graph.Plan | None:
        """The nodes of one scope of a routine, before they are placed: the file's names and the
        build's findings counted on them."""
        rg, held = self.brain_routine_graph(row, routine, header), self._shown.get(row)
        if rg is None or held is None or scope not in rg.scopes:
            return None
        return brain_graph.plan(
            rg, scope, findings=held.built.findings, labels=self.brain_names(row)
        )

    def brain_bar(self, row: int, ref: str, header: str | None = None) -> brain_edit.Bar | None:
        """The operand bar of a node or arm of the build shown (`ref` is its id). The
        gestures' ids are this program's."""
        held = self._shown.get(row)
        if held is None or self.species_doc is None:
            return None
        program = held.built.program
        if held.index is None:
            held.index = ir.Index(program)
            held.blocks = {
                a.id: n
                for r in program.routines()
                for n in ir.walk(r.body)
                if isinstance(n, ir.Block)
                for a in n.arms
            }
        try:
            node = held.index.node(ref)
        except KeyError:
            return None
        block = (held.blocks or {}).get(ref) if isinstance(node, ir.Arm) else None
        try:
            return brain_edit.bar(
                node,
                program,
                block=block,
                header=header,
                pairs=self.brain_pairs(),
                names=self.brain_names(row),
                fresh=sorted(held.built.edits.routines),
            )
        except EditError as e:
            self.message = str(e)
            return None

    def brain_budget(self, row: int) -> Budget | None:
        """The build's run budget (`budget_text`), worked out on the brain worker the first time
        it is asked, as it is the slow part of a check; None until then."""
        held = self._shown.get(row)
        if held is None:
            return None
        if held.budget is None:
            built = held.built
            held.budget = self._brain_worker().submit(lambda: built.budget)
        if not held.budget.done():
            return None
        try:
            return held.budget.result()
        except (LookupError, ValueError):
            return None

    def _advance(self, row: int) -> _Shown | None:
        """Takes a finished build of `row` and starts the next when the file has changed and the
        debounce has run out (the first build starts at once); the build shown, if any."""
        doc, builds = self.species_doc, self._builds
        if doc is None or builds is None or not self.brain_editable(row):
            return None
        if self.brain(row) is None:  # the decode and the intel, on the worker
            return None
        lift = self.brain_lift(row)
        if lift is None:
            return None
        now = self.clock()
        if doc.version != self._version:
            self._version, self._due = doc.version, now + DEBOUNCE
        making = self._making.get(row)
        if making is not None and making[1].done():
            del self._making[row]
            self._took(row, *making)
            making = None
        held = self._shown.get(row)
        stale = held is None or builds.current(row) is not held.built
        if making is None and stale and self._failed.get(row) != doc.version:
            if held is None or now >= self._due:
                job = self._brain_worker().submit(
                    self._make_build,
                    builds,
                    row,
                    lift,
                    self.intel_cache.get(doc.overlay),
                    self.brain_pairs(),
                )
                self._making[row] = (doc.version, job)
        return held

    def _took(self, row: int, version: int, job: Future[_Shown]) -> None:
        try:
            got = job.result()
        except (LookupError, ValueError, OSError) as e:
            self._build_errors[row] = f"building the script failed ({e})"
            self._failed[row] = version
            return
        self._build_errors.pop(row, None)
        self._failed.pop(row, None)
        if row in self._shown:
            self._past[row] = self._shown[row]
        self._shown[row] = got

    @staticmethod
    def _make_build(
        builds: Builds,
        row: int,
        lift: ir.Program,
        intel: SpeciesIntel | None,
        pairs: dict[Pair, str],
    ) -> _Shown:
        """On the brain worker: the row built, and its projection warmed so the outline costs
        the GUI thread nothing."""
        built = builds.get(row, lift, intel)
        edited = script_graph.edited_ids(built.applied)
        proj = script_graph.Projection(built.applied, edited, pairs)
        proj.overview()
        return _Shown(built, proj, pairs, edited)

    # gestures

    def edit_brain(self, g: brain_edit.Gesture, row: int | None = None) -> bool:
        """One gesture on the open brain file, one undo step. False, the reason in `message`, and
        nothing committed when it is refused or changes nothing, or when the view lags the file
        over new code (its ids follow its place, which a change renumbers; the game's ids hold,
        so those gestures go through while a build is pending). The panel runs it in
        `studio.act(g.label, ...)`."""
        doc = self.species_doc
        row = self.brain_species if row is None else row
        lift = None if row is None else self.brain_lift(row)
        if doc is None or row is None or not self.brain_editable(row):
            self.message = f"{g.label}: no brain file takes edits for this monster"
            return False
        if lift is None or self._lagging(row):
            self.message = f"{g.label}: the script is still building; try again in a moment"
            return False

        def change(bf: brainfile.BrainFile) -> None:
            before = brainfile.dumps(bf)
            g.edit(bf, row, lift)
            if brainfile.dumps(bf) == before:
                raise _Unchanged

        try:
            doc.edit(change)
        except _Unchanged:
            self.message = f"{g.label}: nothing changes"
            return False
        except (EditError, BrainFileError) as e:
            self.message = f"{g.label}: {e}"
            return False
        self._version, self._due = doc.version, self.clock() + DEBOUNCE
        self.message = g.label
        return True

    def _lagging(self, row: int) -> bool:
        """The build shown is behind the file, or there is none, and one of them has new code."""
        if not self.brain_pending(row):
            return False
        held = self._shown.get(row)
        if held is None or self.species_doc is None:
            return True
        now = self.species_doc.brainfile.edits(row)
        return any(es is not None and (es.inserts or es.routines) for es in (now, held.built.edits))

    # findings and where they are

    def _brain_findings(self) -> list[Finding]:
        """The findings of the build shown, for the shell: read from what the worker made, never
        computed here."""
        row = self.brain_species
        if row is None:
            return []
        held, names = self._shown.get(row), self.brain_names(row)
        found = [] if held is None else [_finding(f, names) for f in held.built.findings]
        if row in self._build_errors:
            found.append(Finding("error", "BUILD", self._build_errors[row]))
        return found

    def _reveal_brain(self, section: object, key: object) -> None:
        if section == "node" and isinstance(key, str):
            self.show_brain_node(key)
        elif section == "routine" and isinstance(key, str):
            self.show_brain_node(None, key)

    def show_brain_node(self, node: str | None, routine: str | None = None) -> bool:
        """Points the Brain tab at a node of the build shown (or a routine's start): sets
        `brain_focus` (the node's id), `brain_routine`, `brain_header` and `brain_scope`, the scope
        that draws it (`brain_graph.scope_for`), counts `brain_focused` and brings the tab
        forward. False, `message` saying why, when this script has no such place."""
        row = self.brain_species
        held = None if row is None else self._shown.get(row)
        if row is None or held is None:
            self.message = "the script is not built yet"
            return False
        if held.routines is None:
            held.routines = {
                x.id: r.name for r in held.built.program.routines() for x in ir.walk(r.body) if x.id
            }
        name = routine if node is None else held.routines.get(node)
        proj = self.brain_projection(row)
        if name is None or proj is None or name not in proj.routines:
            self.message = f"{node or routine} is not in this script"
            return False
        runs = proj.reach.get(name, ())
        header = self.brain_header if self.brain_header in runs else next(iter(runs), None)
        header = brain.pick_header(held.built.program, header)
        rg = proj.routine(name, header)
        here = self.brain_scope if self.brain_routine == name else script_graph.ROOT
        scope = script_graph.ROOT if node is None else brain_graph.scope_for(rg, node, here)
        if scope is None:
            self.message = f"{node} is not in this script"
            return False
        self.brain_focus, self.brain_routine = node, name
        self.brain_header, self.brain_scope = header, scope
        self.brain_focused += 1
        self.focus("Brain")
        return True

    # Send and Push

    def _brain_blocker(self) -> str | None:
        """Why the build shown cannot go anywhere: none, still building, or with errors."""
        row = self.brain_species
        held = None if row is None else self._shown.get(row)
        if row is None or not self.brain_editable(row):
            return "no brain file takes edits for this monster: open its brain file"
        if held is None or self.brain_pending(row):
            return "the script is still building; try again in a moment"
        if not held.built.ok:
            first = next(f for f in held.built.findings if f.level == "error")
            return f"the script has errors, which Findings lists: {first.text}"
        return None

    def brain_send_blocker(self) -> str | None:
        """Why Send brain to game cannot run now."""
        why = self._brain_blocker()
        if why is not None:
            return why
        try:
            self.mods_dir()
        except places.Missing as e:
            return f"no memory stick to send to: {e.words}"
        except FileNotFoundError as e:
            return f"the framework is not on the memory stick ({e}): install it there first"
        return None

    def send_brain(self) -> None:
        """The build shown onto the memory stick: its container, then the mod that installs it
        (`stick.ship`). The next quest runs it; no save is needed."""
        why = self.brain_send_blocker()
        row = self.brain_species
        held = None if row is None else self._shown.get(row)
        if why is not None or held is None:
            self.message = f"not sent: {why}"
            return
        try:
            stick.ship(held.built, places.path(places.MEMSTICK))
        except (OSError, ValueError) as e:
            self.message = f"send failed: {e}"
            return
        self._sent_brain = held.built.id
        self.message = SENT

    def brain_push_blocker(self) -> str | None:
        """Why Push brain to game cannot run now; a game that is not attached is found by the
        push."""
        return self._brain_blocker()

    def push_brain(self) -> None:
        """The build shown into the running game, read back (`inject.push`): its monsters run
        it until the next quest load."""
        why = self.brain_push_blocker()
        row = self.brain_species
        held = None if row is None else self._shown.get(row)
        if why is not None or held is None or held.built.image is None:
            self.message = f"not pushed: {why}"
            return
        try:
            with self.game_session() as s:
                done = self.push_script(s, held.built.program, held.built.image)
        except (LookupError, OSError, ValueError, DebuggerError) as e:  # Refused is a ValueError
            self.message = f"push failed: {e}"
            return
        self.message = done.describe() if done.mismatched else PUSHED

    def _brain_steps(self, doc: BrainDocument) -> Sequence[Step]:
        row = self.brain_species
        held = None if row is None else self._shown.get(row)
        sent = held is not None and held.built.id is not None and held.built.id == self._sent_brain
        return (
            Step("Pick a node in the Brain tab", self.brain_focus is not None),
            Step("Change it", doc.dirty or doc.can_undo()),
            Step(self.send_label, sent, SEND_KEY),
        )

    # sessions

    @property
    def label_session(self) -> clips.LabelSession | None:
        if self._labels is None and self.doc is not None and self.scene is not None:
            sources = self.coverage()[0].sources()
            self._labels = clips.LabelSession(
                self.doc, self.clip_table(), self.scene.build_id, sources
            )
        return self._labels

    @property
    def part_session(self) -> PartSession | None:
        if self._parts is None and self.doc is not None and self.scene is not None:
            self._parts = PartSession(self.doc, self.scene.rig.n)
            host = self.host_parts()
            self._parts.capacity = None if host is None else host.capacity
        return self._parts

    @property
    def attack_session(self) -> AttackSession | None:
        if self._attacks is None and self.doc is not None and self.scene is not None:
            self._attacks = AttackSession(self.doc, self.scene.rig.n)
            host = self.host_attacks()
            if host is not None:
                self._attacks.capacities = {st.index: st.capacity for st in host.sets}
        return self._attacks

    # clips

    def clip_table(self) -> Mapping[int, clips.Fingerprint]:
        """The open build's fingerprints."""
        sc = self.scene
        if sc is None:
            return {}
        if sc.game == MHFU and sc.pac is not None:
            return clips.pac_clip_table(sc.pac)
        return sc.clip_table()

    def coverage(self) -> tuple[clips.Coverage, list[str]]:
        """What is in each slot: needs the donor moveset and the host pack, and says so."""
        if self._coverage is None:
            self._coverage = self._measure_coverage()
        return self._coverage

    def _measure_coverage(self) -> tuple[clips.Coverage, list[str]]:
        sc, m, notes = self.scene, self.manifest, []
        if sc is None or sc.pac is None or sc.game != MHFU:
            cov = clips.Coverage()
            for c in [] if sc is None else sc.clips:
                cov.slots[c.slot] = clips.SlotCoverage(c.slot, clips.UNKNOWN, c.frames, c.loop)
            notes.append("this is the original's pack: the kinds describe a built port")
            return cov, notes
        port = slots.anim_of(sc.pac)
        if m is None:
            notes.append(
                "no manifest: anims cannot be sorted into kinds, and a name typed here has "
                "nowhere to go"
            )
            return clips.coverage(port), notes
        try:
            games = self.games()
        except FileNotFoundError as e:
            notes.append(f"no base monster's pack and no original moveset: {e}")
            return clips.coverage(port), notes
        host = donor = placed = None
        try:
            host = inputs.host_anim(m, games)
        except (OSError, ValueError) as e:
            notes.append(f"base monster's pack unreadable ({e})")
        try:
            donor = inputs.donor_clips(m, games)
        except (OSError, ValueError) as e:
            notes.append(f"original moveset unreadable ({e})")
        if host is not None and donor is not None:
            try:
                placed = layout.of(m, donor, host)
            except ValueError as e:
                notes.append(f"no clip layout ({e}): each anim read as its own clip id")
        return clips.coverage(port, host, donor, placed), notes

    def vocabulary(self) -> clips.Vocabulary:
        if self._vocab is None:
            cov, notes = self.coverage()
            build = None if self.scene is None else self.scene.build_id
            self._vocab = clips.survey(self.manifest, self.clip_table(), cov, build, notes)
        return self._vocab

    def sources(self) -> dict[int, int]:
        """The open build's layout, entry -> MHP3rd id; empty without one (the pins say)."""
        sc = self.scene
        return {} if sc is None else {e: cid for cid, e in sc.placed.items()}

    def manifest_clip(self, slot: int) -> tuple[str, ManifestClip] | None:
        m = self.manifest
        return None if m is None else clips.entry(m, slot, self.sources())

    def pick_clip(self, slot: int) -> None:
        """Selects `slot` for naming and loads its name and label into the boxes."""
        self.edit_slot = slot
        found = self.manifest_clip(slot)
        self.name_buf = found[0] if found else clip_key(slot)
        self.label_buf = found[1].label if found else ""

    def play_slot(self, slot: int) -> None:
        """Plays `slot` from frame 0, the host beside restarted with it."""
        if self.scene is None or self.vp is None:
            return
        try:
            self.vp.play_clip(self.scene.clip(slot))
        except KeyError:
            self.message = f"anim {slot} is not in this PAC"
            return
        self.vp.playback.play()
        self.pick_clip(slot)
        self.sync_steer()
        self.recompute_alignment()

    def label(self) -> None:
        """Names the picked clip: the one in the picked anim, else a clip in none (by id)."""
        s, br, cid = self.label_session, self.browser(), self.edit_clip
        if self.edit_slot is None and br is not None and cid is not None:
            build = None if self.scene is None else self.scene.build_id
            self.edit("", lambda: br.name(cid, self.name_buf, self.label_buf, build))
            return
        if s is None or self.edit_slot is None:
            self.message = "no manifest to write to"
            return
        slot = self.edit_slot
        self.edit(f"clips.{self.name_buf}", lambda: s.label(slot, self.name_buf, self.label_buf))

    def set_impact_here(self) -> None:
        """The current frame becomes this clip's `impact_frame`."""
        s, vp = self.label_session, self.vp
        if s is None:
            self.message = "impact frames live in the manifest: open a port manifest"
            return
        if vp is None or vp.clip is None:
            self.message = "no clip is playing"
            return
        slot, frame = vp.clip.slot, int(round(vp.playback.phase))
        found = self.manifest_clip(slot)
        name = found[0] if found else self.name_buf or clip_key(slot)
        label = found[1].label if found else self.label_buf
        if self.edit("", lambda: s.label(slot, name, label, impact_frame=frame)):
            self.message = f"impact = frame {frame}.  {self.message}"

    def travel(self, slot: int) -> tuple[float, float]:
        """`root_travel`, once per clip: it samples the clip."""
        from mhfu_studio.monster.render.playback import root_travel

        if slot not in self._travel and self.scene is not None:
            self._travel[slot] = root_travel(self.scene, self.scene.clip(slot))
        return self._travel.get(slot, (0.0, 0.0))

    def joint_counts(self) -> dict[int, int]:
        from mhfu_studio.monster.render.skeleton import vertex_counts

        if self._counts is None:
            self._counts = {} if self.scene is None else vertex_counts(self.scene)
        return self._counts

    # the action

    def port_rig(self) -> align.PortRig | None:
        sc = self.scene
        if sc is None:
            return None
        driven = {j for c in sc.clips for j in c.driven}
        return align.PortRig(sc.rig.n, driven, self.joint_counts())

    def select_pair(self, main: int, sub: int, move: str | None = None) -> None:
        """The action against the clip on screen, which keeps playing."""
        self.pair, self.move = (int(main), int(sub)), move
        self.recompute_alignment()
        self.follow_action()

    def action_rows(self) -> list[actions.ActionRow]:
        """The Actions rows, made again when the manifest, the intel or the coverage changes."""
        m, si = self.manifest, self.host_intel()
        if m is None:
            return []
        cov = self.coverage()[0]
        made = (m, si, cov)
        if self._rows is None or any(a is not b for a, b in zip(self._rows[0], made, strict=True)):
            self._rows = made, actions.rows(m, si, cov, self.host_attacks(), self.host_species)
        return self._rows[1]

    def plays(
        self, main: int, sub: int, move: str | None = None, species: int | None = None
    ) -> actions.Plays:
        """What plays in this port while the game is in the base monster's `(main, sub)`. For
        another `species`, what its own intel says: no port, so no clip names."""
        if species is not None and species != self.host_species:
            other = self.intel_of(species)
            p = None if other is None else other.pair(main, sub)
            return actions.plays_now(None, p, main, sub, None, clips.Coverage())
        si = self.host_intel()
        p = None if si is None else si.pair(main, sub)
        return actions.plays_now(self.manifest, p, main, sub, move, self.coverage()[0])

    def select_action(self, main: int, sub: int, move: str | None = None) -> None:
        """The one way to pick an action (a row, the graph, the table of every action): `move`,
        else the one bound on it; what plays now from frame 0, the base monster's anim beside."""
        main, sub = int(main), int(sub)
        move = move if move is not None else actions.bound_move(self.manifest, main, sub)
        self.pair, self.move = (main, sub), move
        self.graph.picked = self.pair
        now = self.plays(main, sub, move) if self.browsing_the_host else None
        if now is not None and now.playable and now.slot is not None:
            self.play_slot(now.slot)
        elif self.vp is not None and self.vp.clip is not None:
            self.rewind()
            if now is not None:
                self.message = (
                    f"anim {now.slot} is not in this build: the game finds no clip for"
                    f" ({main},{sub})"
                    if now.slot is not None
                    else f"({main},{sub}) names no anim: the game keeps the clip it was playing"
                )
        self.recompute_alignment()
        self.follow_action()

    def clear_pair(self) -> None:
        self.pair, self.move, self.alignment, self.markers = None, None, None, []

    def recompute_alignment(self) -> None:
        """The host pair against the clip on screen; its length from the SCENE, since a
        rebuild moves clips and a stale length gives the wrong answer."""
        m = self.manifest
        if m is None or self.pair is None:
            self.alignment, self.markers = None, []
            return
        clip = None if self.vp is None else self.vp.clip
        found = None if clip is None else self.manifest_clip(clip.slot)
        mv = m.moves.get(self.move or "")
        self.alignment = align.align_pair(
            m,
            self.pair[0],
            self.pair[1],
            self.intel,
            move=self.move,
            clip=found[0] if found else (clip.name if clip else None),
            slot=None if clip is None else clip.slot,
            clip_frames=None if clip is None else clip.frames,
            impact=found[1].impact_frame if found else None,
            allow_unentered=mv.allow_unentered if mv is not None else False,
            rig=self.port_rig(),
        )
        self.markers = self.alignment.markers

    def bind_move(self, name: str = "") -> None:
        """The clip on screen for the selected action, as `[moves.<name>]`: by default the
        selected move, updated in place (its after, hold_max and claim kept), else a new
        `move_<main>_<sub>`; an unnamed clip gets a name."""
        al, s = self.alignment, self.label_session
        if al is None or s is None:
            return
        name = name.strip() or self.move or f"move_{al.main}_{al.sub}"
        if self.edit("", lambda: s.bind_move(name, al.main, al.sub, al.slot)):
            self.select_pair(al.main, al.sub, name)

    def unbind(self) -> None:
        """Removes the selected move: the action plays the base monster's anim again."""
        doc, name, pair = self.doc, self.move, self.pair
        if doc is None or name is None or pair is None:
            return
        if self.edit("", lambda: clips.drop_move(doc, name)):
            self.select_action(*pair)

    def rename_move(self, new: str) -> None:
        doc, old = self.doc, self.move
        if doc is not None and old is not None:
            if self.edit("", lambda: clips.rename_move(doc, old, new)):
                self.move = new.strip()

    # own moves (the Moves panel, the Timeline's attack lanes, the turn gizmo)

    def own_move(self) -> Move | None:
        """The selected move when it is an own move."""
        m, name = self.manifest, self.move
        mv = None if m is None or name is None else m.moves.get(name)
        return mv if mv is not None and mv.own else None

    def move_slot(self, mv: Move) -> int | None:
        """The anim `mv`'s clip plays in, or the preview of a clip in none (`preview_slot`)."""
        m = self.manifest
        if mv.clip is None or m is None or mv.clip not in m.clips:
            return mv.anim
        c = m.clips[mv.clip]
        at = clips.at(c, self.sources())
        return at if at is not None else clip_browser.preview_slot(c.id)

    def own_move_on_screen(self) -> Move | None:
        """The selected own move while its clip is the one on screen."""
        mv, vp = self.own_move(), self.vp
        if mv is None or vp is None or vp.clip is None:
            return None
        return mv if self.move_slot(mv) == vp.clip.slot else None

    def clip_name_on_screen(self) -> str | None:
        """The manifest's name for the clip on screen."""
        vp, m = self.vp, self.manifest
        if vp is None or vp.clip is None or m is None:
            return None
        if vp.clip.slot < 0:
            return layout.holder(m, -1 - vp.clip.slot)
        found = self.manifest_clip(vp.clip.slot)
        return None if found is None else found[0]

    def select_move(self, name: str) -> None:
        """A move: a pair's as Actions picks it; an own one plays its clip, turning as it
        steers."""
        m = self.manifest
        mv = None if m is None else m.moves.get(name)
        if mv is None:
            self.message = f"no move {name!r}"
            return
        if mv.pair is not None:
            self.select_action(*mv.pair, name)
            return
        self.clear_pair()
        self.move, self.picked_window, self.next_window_id = name, None, None
        self.graph.picked = None
        self.play_move_clip(mv)
        self.sync_steer()

    def play_move_clip(self, mv: Move) -> None:
        """Plays `mv`'s clip from frame 0."""
        slot = self.move_slot(mv)
        if slot is not None and slot < 0:
            self.play_source(-1 - slot)
        elif slot is not None:
            self.play_slot(slot)

    def new_move(self, name: str = "") -> None:
        """An own move playing the clip on screen, named after it unless `name`; selected."""
        doc, vp, m = self.doc, self.vp, self.manifest
        if doc is None or m is None or vp is None or vp.clip is None:
            self.message = "play a clip first: a new move plays the clip on screen"
            return
        slot, held = vp.clip.slot, self.clip_name_on_screen()
        base = held or (clip_key(slot) if slot >= 0 else "")
        name = name.strip() or authoring.free_name(m, base or "move")
        s = self.label_session
        if slot >= 0 and s is not None:
            done = self.edit("", lambda: s.own_move(name, slot))
        elif held is not None:
            done = self.edit("", lambda: authoring.new_move(doc, name, held))
        else:
            self.message = "this clip is in no anim: place it in Clips, then make a move of it"
            return
        if done:
            self.select_move(name)

    def delete_move(self) -> None:
        """The selected move goes; refused while another move, a block or an effect names it."""
        doc, name, pair = self.doc, self.move, self.pair
        if doc is None or name is None:
            return
        if self.edit("", lambda: clips.drop_move(doc, name)):
            if pair is not None:
                self.select_action(*pair)
            else:
                self.move = None
                self.sync_steer()

    def set_move(self, **fields: Any) -> bool:
        """`authoring.FIELDS` of the selected own move, one undo step."""
        doc, name = self.doc, self.move
        if doc is None or name is None:
            return False
        return self.edit("", lambda: authoring.set_fields(doc, name, **fields))

    def set_steer(self, **fields: Any) -> bool:
        """The selected own move's steer, one undo step; `fixed` starts over the clip's AI
        frames."""
        doc, name, vp = self.doc, self.move, self.vp
        if doc is None or name is None:
            return False
        hint = 1
        if vp is not None and vp.clip is not None and vp.actor is not None:
            hint = max(1, round(vp.clip.frames / max(vp.playback.speed, 1e-6)))
        return self.edit("", lambda: authoring.set_steer(doc, name, hint, **fields))

    def sync_steer(self) -> None:
        """The view turns the port as the own move on screen steers, else as its clip does."""
        if self.vp is not None:
            mv = self.own_move_on_screen()
            self.vp.set_steer(None if mv is None else mv.steer)

    def clip_turn(self) -> tuple[float, bool] | None:
        """Degrees the clip on screen turns YAW by its end, and whether the manifest sets it."""
        from mhfu_studio.monster.render.playback import end_turn

        vp, m, held = self.vp, self.manifest, self.clip_name_on_screen()
        if vp is None or vp.clip is None or vp.scene is None:
            return None
        given = m is not None and held is not None and m.clips[held].turn is not None
        return end_turn(vp.scene, vp.clip), given

    def set_turn(self, deg: float | None) -> bool:
        """The turn the gizmo shows: a fixed steer's angle, else the clip on screen's `turn`
        (None: its own body's)."""
        t = self.turn.target()
        if t is not None and t.writes == "steer" and deg is not None:
            return self.set_steer(angle=deg)
        return self.set_clip_turn(deg)

    def set_clip_turn(self, deg: float | None) -> bool:
        """`[clips.x] turn` of the clip on screen, naming it when it has no name."""
        doc, vp, held = self.doc, self.vp, self.clip_name_on_screen()
        if doc is None or vp is None or vp.clip is None:
            self.message = "no clip of a port on screen"
            return False
        if held is not None:

            def change(m: Manifest) -> None:
                m.clips[held].turn = deg

            word = "its own body's" if deg is None else f"{deg:+g} degrees"
            ok = self.edit(f"clips.{held}: turns {word}", lambda: doc.edit(change))
        else:
            s, slot = self.label_session, vp.clip.slot
            if s is None or slot < 0 or deg is None:
                self.message = "this clip is in no anim: place it in Clips first"
                return False
            ok = self.edit("", lambda: s.label(slot, s.default_name(slot), turn=deg))
        if ok:
            vp.repose()
        return ok

    # sequences (the Moves panel, the Timeline's bar, the Clips table)

    def sequence_of(self, name: str | None = None) -> list[str]:
        """The moves of the sequence `name` (else the selected move) is in, in order."""
        m, name = self.manifest, name or self.move
        if m is None or name is None or name not in m.moves:
            return []
        return sequence.chain(m, sequence.head_of(m, name))

    def step_of(self, name: str) -> sequences.Step:
        """Move `name` as the strip and the bar show it: its clip's frames, else the scene's."""
        m = self.manifest
        assert m is not None
        mv = m.moves[name]
        c = m.clips.get(mv.clip or "")
        frames = None if c is None else c.frames
        slot = self.move_slot(mv)
        if frames is None and self.scene is not None and slot is not None and slot >= 0:
            frames = next((k.frames for k in self.scene.clips if k.slot == slot), None)
        return sequences.Step(name, mv.clip, frames, c.label if c else mv.label, mv.own)

    def ends(self) -> continuity.Ends | None:
        """The donor's clips' end poses, read once per donor; None with `ends_note` saying why."""
        m = self.manifest
        if m is None:
            return None
        key = (m.source, m.build)
        if self._ends is None or self._ends[0] != key:
            found, note = None, ""
            try:
                found = continuity.donor_ends(m, self.games())
            except (OSError, ValueError) as e:
                note = f"no original moveset to rate the clips ({e})"
            self._ends, self.ends_note = (key, found), note
        return self._ends[1]

    def fits_after(self, cid: int | None = None) -> dict[int, float]:
        """Degrees from the end of donor clip `cid` (else the one on screen) to the start of
        each donor clip, best first; empty without the donor or the clip."""
        cid = self.playing_clip() if cid is None else cid
        e = self.ends()
        if e is None or cid is None or cid not in e.ids:
            return {}
        return dict(continuity.fits_after(e, cid, itself=True))

    def pick_rows(self, after: str | None) -> list[sequences.PickRow]:
        """The clips of the manifest as a step after clip `after`, in the picker's order."""
        m = self.manifest
        return [] if m is None else sequences.pick_rows(m, self.ends(), after, self.pick_by)

    def add_step(self, clip: str) -> None:
        """A step playing `clip` after the selected move; selected."""
        doc, m, after = self.doc, self.manifest, self.move
        if doc is None or m is None or after is None:
            return
        new = sequence.step_name(m, sequence.head_of(m, after))
        if self.edit("", lambda: sequences.append_step(doc, after, clip)):
            self.select_move(new)

    def remove_step(self) -> None:
        """The selected step goes; the one before it, else after it, is selected."""
        doc, name, steps = self.doc, self.move, self.sequence_of()
        if doc is None or name not in steps:
            return
        i = steps.index(name)
        near = steps[i - 1] if i else steps[1] if len(steps) > 1 else None
        if self.edit("", lambda: sequences.remove_step(doc, name)):
            if near is not None:
                self.select_move(near)
            else:
                self.move = None
                self.sync_steer()

    def move_step(self, delta: int) -> None:
        """The selected step one place earlier (-1) or later (1)."""
        doc, name = self.doc, self.move
        if doc is not None and name is not None:
            self.edit("", lambda: sequences.move_step(doc, name, delta))

    def split_step(self) -> None:
        """The selected step starts a sequence of its own."""
        doc, name = self.doc, self.move
        if doc is not None and name is not None:
            self.edit("", lambda: sequences.split(doc, name))

    def set_step_clip(self, clip: str) -> None:
        """The selected step plays `clip` instead, from its start."""
        doc, name = self.doc, self.move
        if (
            doc is not None
            and name is not None
            and self.edit("", lambda: sequences.set_clip(doc, name, clip))
        ):
            self.select_move(name)

    def play_sequence(self) -> None:
        """Plays the selected move's sequence from its head, step after step."""
        steps = self.sequence_of()
        if not steps:
            self.message = "pick a move first"
            return
        self._play_step(steps[0])

    def _play_step(self, name: str) -> None:
        """Plays move `name` once through, turning as it steers."""
        m, vp = self.manifest, self.vp
        assert m is not None
        mv = m.moves[name]
        self.select_move(name)
        if mv.pair is not None:
            self.play_move_clip(mv)
        if vp is not None and vp.clip is not None:
            vp.playback.loop = False
            vp.playback.play()
            self.playing_step = name

    def _advance_sequence(self) -> None:
        """The played sequence on to the step its step hands to once that has ended: the clip
        played out, or the move's Length reached. Something else picked or played stops it."""
        m, vp, name = self.manifest, self.vp, self.playing_step
        mv = None if m is None or name is None else m.moves.get(name)
        if mv is None or vp is None or vp.clip is None:
            self.playing_step = None
            return
        if self.move != name or self.move_slot(mv) != vp.clip.slot:
            self.playing_step = None
            return
        pb = vp.playback
        if not (pb.at_end or (mv.length and pb.phase >= mv.length * pb.speed)):
            return
        self.playing_step = None
        self._stepped = True
        if mv.after is not None:
            self._play_after(mv.after)

    def _play_after(self, name: str) -> None:
        """Plays step `name` where the step on screen stands: facing as it faces, from its root."""
        from mhfu_studio.monster.render.playback import carry_after

        vp = self.vp
        assert vp is not None and vp.actor is not None and vp.clip is not None
        a, clip, phase = vp.actor, vp.clip, vp.playback.phase
        was = a.steer, a.carry
        self._play_step(name)
        if self.playing_step == name and vp.clip is not None:
            carry = carry_after(
                a.scene,
                clip,
                phase,
                vp.clip,
                strip_root=a.strip_root,
                steer=was[0],
                speed=vp.playback.speed,
                carry=was[1],
            )
            vp.set_carry(carry)

    def take_stepped(self) -> bool:
        """Whether the played sequence went on (or ended) since the last ask."""
        stepped, self._stepped = self._stepped, False
        return stepped

    # the attack windows of the selected own move

    def window_frames(self) -> int:
        """Clip frames the lanes span: the own move's clip, on screen."""
        vp = self.vp
        on = self.own_move_on_screen() is not None and vp is not None and vp.clip is not None
        return vp.clip.frames if on and vp is not None and vp.clip is not None else 0

    def window_id(self) -> int:
        """The attack a new window gets: the one set for it, else the picked window's, else
        the base monster's first record with a hit group."""
        mv = self.own_move()
        if self.next_window_id is not None:
            return self.next_window_id
        if mv is not None and mv.attacks:
            i = self.picked_window
            return mv.attacks[i if i is not None else -1].id
        host = self.host_attacks()
        off = None if host is None else host.id_offset(self.host_species or 0)
        recs = [] if host is None or host.primary is None or off is None else host.primary.attacks
        return next((r.id - (off or 0) for r in recs if not r.is_blank), 0)

    def press_window(self, lane: int, frame: float, slop: float = 0.5) -> bool:
        """A press on the Timeline's attack lanes, in clip frames (`authoring.WindowDrag`)."""
        mv = self.own_move_on_screen()
        if mv is None:
            return False
        self.windows.frames, self.windows.id = self.window_frames(), self.window_id()
        if lane < len(mv.attacks):
            self.picked_window = lane
        if len(mv.attacks) >= MOVE_ATTACKS and lane >= len(mv.attacks):
            self.message = f"the move player holds {MOVE_ATTACKS} attacks a move"
            return False
        return self.windows.press(mv.attacks, lane, frame, slop)

    def _window_dragged(self, index: int | None, w: AttackWindow) -> None:
        doc, name = self.doc, self.move
        if doc is None or name is None:
            return
        if index is None:
            if self.edit("", lambda: authoring.add_window(doc, name, w)):
                mv = self.own_move()
                self.picked_window = None if mv is None else len(mv.attacks) - 1
                self.message = f"attack {w.id} from frame {w.frame}" + (
                    "" if w.end is None else f" to {w.end}"
                )
            return
        self.set_window(index, frame=w.frame, end=w.end)

    def set_window(self, index: int, **fields: Any) -> bool:
        doc, name = self.doc, self.move
        if doc is None or name is None:
            return False
        return self.edit("", lambda: authoring.set_window(doc, name, index, **fields))

    def remove_window(self, index: int | None = None) -> bool:
        """The picked window, by default."""
        doc, name = self.doc, self.move
        i = self.picked_window if index is None else index
        if doc is None or name is None or i is None:
            return False
        if self.edit("", lambda: authoring.remove_window(doc, name, i)):
            self.picked_window = None
            return True
        return False

    def live_windows(self) -> list[int]:
        """The own move's windows whose attack is out at the playhead."""
        mv, vp = self.own_move_on_screen(), self.vp
        if mv is None or vp is None or vp.actor is None:
            return []
        return authoring.live(mv.attacks, vp.playback.phase)

    def attack_record(self, attack: int) -> AttackRecord | None:
        """The base monster's record `attack` a move spawns; None for none or a blank one."""
        host = self.host_attacks()
        recs = [] if host is None else host.records_for([attack], self.host_species)
        return recs[0] if recs else None

    def attack_set(self, attack: int) -> int | None:
        """The hit group attack record `attack` spawns: the port's [[attack]] volume, else the
        base monster's record's."""
        m = self.manifest
        mine = None if m is None else next((a for a in m.attacks if a.id == attack), None)
        if mine is not None and mine.volume is not None:
            return mine.volume
        rec = self.attack_record(attack)
        return None if rec is None else rec.volume

    def light_live(self) -> None:
        """While an own move plays with the hit groups shown, its live attack's group is lit."""
        vp, mv = self.vp, self.own_move_on_screen()
        ov = None if vp is None or not self.show_attacks else vp.attacks
        if ov is None or mv is None or self.selected_set is not None:
            return
        sets = [self.attack_set(mv.attacks[i].id) for i in self.live_windows()]
        ov.set_selected_group(next((s for s in sets if s is not None), None))

    def host_pair(self) -> PairIntel | None:
        """The selected pair's intel when it is the host's; None while browsing."""
        if self.pair is None or not self.browsing_the_host:
            return None
        si = self.host_intel()
        return None if si is None else si.pair(*self.pair)

    def pair_sets(self) -> list[int]:
        """The volume sets the selected pair's handler hits with, or the own move's attacks."""
        mv = self.own_move()
        if mv is not None:
            return sorted({s for a in mv.attacks if (s := self.attack_set(a.id)) is not None})
        host, p = self.host_attacks(), self.host_pair()
        if host is None or p is None or not p.attack_ids:
            return []
        return host.sets_for(p.attack_ids, self.host_species)

    # the transport (the Timeline and the keys)

    def play_pause(self) -> None:
        if self.vp is not None and self.vp.clip is not None:
            self.vp.play_pause()

    def step(self, frames: int) -> None:
        """Whole game frames at the clip's speed; pauses."""
        if self.vp is not None and self.vp.clip is not None:
            self.vp.playback.step(frames)
            self.vp.set_pose(self.vp.clip, self.vp.playback.phase)

    def rewind(self) -> None:
        if self.vp is not None and self.vp.clip is not None:
            self.vp.restart()

    def seek(self, frame: float) -> None:
        if self.vp is not None and self.vp.clip is not None:
            self.vp.playback.seek(frame)
            self.vp.set_pose(self.vp.clip, self.vp.playback.phase)

    # the host reference

    def host_scene(self) -> Scene | None:
        """The browsed species' own model PAC, read once; None with the reason in `message`."""
        sp = self.browsing_species
        if sp is None:
            return None
        if sp not in self.host_scenes:
            scene = None
            try:
                data = self.fu().read(files.monster_pac(sp))
                scene = Scene.from_bytes(data, f"em{sp:02d}")
            except (OSError, ValueError) as e:
                self.message = f"no model for {species.label(sp)}: {e}"
            self.host_scenes[sp] = scene
        return self.host_scenes[sp]

    def host_clip_table(self) -> dict[int, tuple[int, bool]]:
        sc = self.host_scene()
        return {} if sc is None else sc.clip_table()

    def set_show_host(self, on: bool) -> None:
        """The browsed species' own model beside the port, playing the selected action."""
        self.show_host = on
        self.sync_reference()

    def sync_reference(self) -> None:
        """The viewport's reference follows the toggle and the browsed species."""
        vp = self.vp
        if vp is None:
            return
        want = self.host_scene() if self.show_host else None
        have = None if vp.reference is None else vp.reference.scene
        if want is have:
            return
        self.host_clip = None
        if want is None:
            vp.clear_reference(frame_camera=True)
            return
        vp.set_reference(want)
        self.follow_action()
        vp.sync_focus("hitboxes")

    def host_a1(self) -> list[int]:
        al = self.alignment
        return [] if al is None or al.pair is None else list(al.pair.a1)

    def follow_action(self) -> None:
        """The reference plays the first a1 of the pair that its pack populates."""
        vp = self.vp
        if vp is None or vp.reference is None:
            return
        table = self.host_clip_table()
        a1 = next((a for a in self.host_a1() if a in table), None)
        if a1 is not None:
            self.play_host_clip(a1)

    def play_host_clip(self, a1: int) -> None:
        vp = self.vp
        if vp is None or vp.reference is None:
            return
        self.host_clip = a1
        vp.play_reference_clip(vp.reference.scene.clip(a1), 0.0)

    # volumes

    def sync_hitboxes(self) -> None:
        """The chosen source's hurtboxes into the viewport; the host actor draws the host's."""
        from mhfu_studio.monster.render.hitboxes import volumes

        vp = self.vp
        if vp is None or vp.scene is None:
            return
        if not self.show_parts:
            vp.clear_hitboxes()
            self.part_orphans = ()
            return
        host = self.host_parts()
        if self.parts_source == PORT:
            sess = self.part_session
            vols = volumes(sess.volumes() if sess is not None else [])
        else:
            vols = volumes(host.spheres() if host is not None else [])
        ov = vp.set_hitboxes(vols)
        self.part_orphans = () if ov is None else ov.orphans
        if ov is not None:
            ov.set_selected_group(self.selected_part)
            ov.set_selected_volume(self.selected_volume)
        vp.set_reference_hitboxes(volumes(host.spheres()) if host is not None else [])

    def select_volume(self, index: int | None) -> None:
        """Hurtbox `index` of the source shown; the base monster's too, to look at."""
        self.selected_volume = index
        self.tools.last = HURT
        ov = self._overlay(HURT)
        if ov is not None:
            ov.set_selected_volume(index)

    def select_part(self, part: int | None) -> None:
        self.selected_part = part
        sess = self.part_session
        self.part_name_buf = "" if sess is None or part is None else sess.name_of(part)
        ov = self._overlay("hitboxes")
        if ov is not None and self.vp is not None:
            ov.set_selected_group(part)
            self.vp.sync_focus("hitboxes")

    def visible_sets(self) -> list[int] | None:
        """The selected set; else the selected move's; else all authored on the port, none on
        the host (a whole overlay's sets at once is two hundred volumes of fog)."""
        if self.selected_set is not None:
            return [self.selected_set]
        ps = self.pair_sets()
        if ps and self.sets_of_move_only:
            return ps
        return None if self.attacks_source == PORT else []

    def sync_attacks(self) -> None:
        from mhfu_studio.monster.render.hitboxes import attack_volumes, volumes

        vp = self.vp
        if vp is None or vp.scene is None:
            return
        if not self.show_attacks:
            vp.clear_attacks()
            self.attack_orphans = ()
            return
        host = self.host_attacks()
        if self.attacks_source == PORT:
            sess = self.attack_session
            vols = volumes(sess.volumes() if sess is not None else [])
        else:
            vols = attack_volumes(host.sets) if host is not None else []
        ov = vp.set_attacks(vols)
        self.attack_orphans = () if ov is None else ov.orphans
        if ov is not None:
            ov.set_visible(self.visible_sets())
            ov.set_selected_group(self.selected_set)
            ov.set_selected_volume(self.selected_attack_volume)
        vp.set_reference_attacks(attack_volumes(host.sets) if host is not None else [])
        vp.sync_focus("attacks")

    def select_set(self, index: int | None) -> None:
        self.selected_set = index
        self.selected_attack_volume = None
        ov = self._overlay("attacks")
        if ov is not None and self.vp is not None:
            ov.set_visible(self.visible_sets())
            ov.set_selected_group(index)
            ov.set_selected_volume(None)
            self.vp.sync_focus("attacks")

    def edit_set(self, index: int) -> None:
        """Attack set `index` in Hitboxes: the port's copy when it has one, else the host's."""
        sess = self.attack_session
        self.show_attacks = True
        self.attacks_source = PORT if sess is not None and sess.volumes_of(index) else HOST
        self.select_set(index)
        self.sync_attacks()
        self.focus("Hitboxes")

    def select_attack_volume(self, index: int | None) -> None:
        """Hitbox `index` of the source shown; the base monster's too, to look at."""
        self.selected_attack_volume = index
        self.tools.last = HIT
        ov = self._overlay(HIT)
        if ov is not None:
            ov.set_selected_volume(index)

    def _overlay(self, which: str) -> HitboxOverlay | None:
        vp = self.vp
        return None if vp is None else vp.hitboxes if which == "hitboxes" else vp.attacks

    # every donor clip by MHP3rd id (the Clips panel)

    def browser(self) -> ClipBrowser | None:
        """None without a manifest or the games; `browser_note` says why."""
        if self._browser is None and not self.browser_note and self.doc is not None:
            m = self.doc.manifest
            try:
                games = self.games()
                donor, host = inputs.donor_clips(m, games), inputs.host_anim(m, games)
            except (OSError, ValueError) as e:
                self.browser_note = f"no original moveset to list ({e}): the anims of this build"
                return None
            self._browser = ClipBrowser(self.doc, donor, host)
        return self._browser

    def source_rows(self) -> list[SourceClip]:
        """Every donor clip by id, then the build's anims that hold none; every anim of the
        build without the donor or a layout."""
        sc, br = self.scene, self.browser()
        if sc is None:
            return []
        own = [self._anim_row(c.slot) for c in sc.clips]
        if br is None:
            return own
        try:
            placed = br.layout().entries
        except ManifestError as e:
            self.browser_note = f"no layout ({e}): the anims of this build"
            return own
        return br.rows(r for r in own if r.entry not in placed)

    def _anim_row(self, slot: int) -> SourceClip:
        assert self.scene is not None
        c, found = self.scene.clip(slot), self.manifest_clip(slot)
        name, label = (found[0], found[1].label) if found else ("", "")
        used = tuple(sequences.used_in(self.manifest, name)) if name and self.manifest else ()
        return SourceClip(None, slot, c.frames, c.loop, name, label, used)

    def source_clip(self, cid: int) -> SceneClip:
        """What plays donor clip `cid`: the open build's anim holding it, else its preview."""
        br, sc = self.browser(), self.scene
        if br is None or sc is None or self.doc is None or cid not in br.donor:
            raise KeyError(f"no clip {cid} of the original here")
        e = br.layout().ids.get(cid)
        if e is not None:
            c = next((c for c in sc.clips if c.slot == e), None)
            if c is not None and (c.frames, c.loop) == br.prints[cid]:
                return c
        if cid not in self._previews:
            self._previews[cid] = clip_browser.preview(self.doc.manifest, self.games(), cid)
        return self._previews[cid]

    def play_source(self, cid: int) -> None:
        """Plays donor clip `cid` from frame 0 and picks it for naming."""
        from mhfu_studio.monster.render.playback import root_travel

        try:
            clip = self.source_clip(cid)
        except (OSError, ValueError, KeyError) as e:
            self.message = f"clip {cid} does not play: {e}"
            return
        self.edit_clip = cid
        if clip.slot >= 0:
            self.play_slot(clip.slot)
            self.pick_clip(clip.slot)
            return
        assert self.scene is not None
        self._travel.setdefault(clip.slot, root_travel(self.scene, clip))
        held = None if self.manifest is None else layout.holder(self.manifest, cid)
        c = None if held is None or self.manifest is None else self.manifest.clips[held]
        self.edit_slot, self.name_buf, self.label_buf = None, held or "", c.label if c else ""
        if self.vp is not None:
            self.vp.play_clip(clip)
            self.vp.playback.play()
        self.sync_steer()
        self.recompute_alignment()

    def playing_clip(self) -> int | None:
        """The donor clip on screen, by id."""
        clip = None if self.vp is None else self.vp.clip
        if clip is None:
            return None
        if clip.slot < 0:
            return -1 - clip.slot
        return self.coverage()[0].sources().get(clip.slot)

    def picked_clip(self) -> int | None:
        """The donor clip picked for naming, by id: the one in the picked anim, else a clip in
        none (`edit_clip`)."""
        br = self.browser()
        if br is None:
            return None
        if self.edit_slot is not None:
            return br.layout().entries.get(self.edit_slot)
        return self.edit_clip

    def place_clip(self, entry: int) -> None:
        """The picked clip into anim `entry`, under the typed name if it has none; the port is
        built again."""
        br, cid = self.browser(), self.picked_clip()
        if br is None or cid is None:
            self.message = "pick one of the original's clips first"
            return
        at = br.layout().ids.get(cid)
        typed = self.name_buf.strip()
        name = clip_key(entry) if typed in ("", clip_key(at if at is not None else -1)) else typed
        if self.edit("", lambda: br.place(cid, entry, name)):
            said, self.message = self.message, ""
            self.play_source(cid)
            self.message = said

    def _relayout(self) -> None:
        """The port built again when an edit or its undo moved a clip; the view follows."""
        from mhfu_studio.monster.render.skeleton import undriven_geometry

        m, sc, br = self.manifest, self.scene, self.browser()
        if m is None or sc is None or br is None:
            return
        try:
            now = br.layout().entries
        except ManifestError:
            return
        if self._built_for is not sc:
            self._built_for, self._built = sc, now
            return
        if now == self._built:
            return
        self._built = now
        try:
            scene = Scene.from_manifest(m, None, "port", self.games())
        except (OSError, ValueError) as e:
            self.message = f"not built again ({e}): the view shows the last build"
            return
        cid = self.playing_clip()
        self.scene = self._built_for = scene
        if self.doc is not None:
            self.doc.pac, self.doc.sources = scene.pac, self.sources()
        self.undriven = undriven_geometry(scene)
        self._coverage = self._vocab = self._labels = self._rows = self._counts = None
        self._travel, self._previews = {}, {}
        vp = self.vp
        if vp is not None:
            strip, speed = vp.strip_root, None if vp.actor is None else vp.playback.speed
            vp.set_scene(scene, frame_camera=False)
            vp.strip_root = strip
            if speed is not None:
                vp.playback.speed = speed
        if cid is not None:
            self.play_source(cid)

    def game_entry(self) -> int:
        """The anim Play in game forces: the picked clip's (`entry_in_game`)."""
        br, cid, doc = self.browser(), self.picked_clip(), self.doc
        if br is None or cid is None or doc is None:
            if self.edit_slot is None:
                raise LookupError("pick a clip first")
            return self.edit_slot
        return self.entry_in_game(cid)

    def entry_in_game(self, cid: int) -> int:
        """Donor clip `cid`'s anim, refused while it differs from the saved manifest's: the game
        holds the build injected from the file."""
        br, doc = self.browser(), self.doc
        if br is None or doc is None:
            raise LookupError(self.browser_note or "no layout: open a port manifest")
        now = br.layout().ids.get(cid)
        saved = layout.of(doc.saved_manifest, br.donor, br.host).ids.get(cid)
        if now is None:
            raise LookupError(f"clip {cid} has no anim in this layout: place it first")
        if now != saved:
            raise LookupError(
                f"clip {cid} is anim {now} here but {saved} in the saved manifest: save, then"
                " build and inject the port (mhfu-port inject) before playing it in the game"
            )
        return now

    def play_in_game(self) -> None:
        """Holds the picked clip's anim on the running game's big monster (`clip_game`)."""
        try:
            entry = self.game_entry()
            with self.game_session() as s:
                held = clip_game.force(s, entry, self.host_species)
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self.message = f"not played in the game: {e}"
            return
        self.message = held.says()

    def release_in_game(self) -> None:
        """Lets the big monster's own brain pick again."""
        try:
            with self.game_session() as s:
                slot, acked = clip_game.release(s, self.host_species)
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self.message = f"not released: {e}"
            return
        self.message = f"monster {slot} released" + ("" if acked else "; no ack")

    # the behaviour graph (the Behaviour tab)

    def take_block(self) -> str | None:
        """The block a finding named, once; the tab picks and frames it."""
        block, self._block = self._block, None
        return block

    # Watch the game (the Behaviour tab's marks)

    @property
    def fires(self) -> dict[int, int] | None:
        """How often the game fired each rule, by rank; None when there is nothing to show."""
        return self.watch.fires

    def poll_game(self) -> bool:
        """One step of Watch the game, at most one read a second; True when the marks changed."""
        return self.watch.poll(self.manifest, self.game_running(), self._ask_brain)

    def fire_blocker(self, t: Trigger) -> str | None:
        """Why Fire cannot raise `t` in the game now; None when it can."""
        if self.manifest is None:
            return "no port manifest open"
        if t.kind not in rule_game.FIRE:
            return "nothing in the studio raises this trigger"
        return self.watch.blocker()

    def fire(self, t: Trigger) -> None:
        """Raises `t` in the running game (`rule_game.FIRE`) at the brain Watch the game found. The
        game is not the document: no undo step, and the status says what was done."""
        m, cfg, why = self.manifest, self.watch.cfg, self.fire_blocker(t)
        if why is not None or m is None or cfg is None:
            self.message = f"not fired: {why}"
            return
        try:
            with self.game_session() as s:
                self.message = rule_game.FIRE[t.kind](s, cfg, m, t)
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self.message = f"not fired: {e}"

    def _ask_brain(self, cfg: int | None) -> Future[rule_game.Read]:
        self._pool = self._pool or ThreadPoolExecutor(1)
        return self._pool.submit(self.read_brain, self.game_session, cfg)

    # Play in game for an own move (the Moves panel)

    def play_move_blocker(self, name: str | None = None) -> str | None:
        """Why Play in game cannot play `name` (the selected move) now; a game that does not
        answer is found by the play."""
        m, name = self.manifest, name or self.move
        mv = None if m is None or name is None else m.moves.get(name)
        if mv is None:
            return "pick a move first"
        if not mv.own:
            return (
                f"{name} rides the base monster's ({mv.main},{mv.sub}): the game plays it when"
                " its brain enters that action. Play in game in Clips holds its clip."
            )
        try:
            self.games()
            self.mods_dir()
        except places.Missing as e:
            return f"nothing to build or send the moves module with: {e.words}"
        except FileNotFoundError as e:
            return f"the framework is not on the memory stick ({e}): install it there first"
        if not self.game_running():
            return "no game running: start PPSSPP with the port in a quest"
        return None

    def play_move_in_game(self, force: bool = False) -> None:
        """Saves, sends the port's clips and moves modules to the memory stick
        (`move_game.deploy`) and asks the running port to play the selected own move; `force`
        plays it at once even before combat."""
        why = self.play_move_blocker()
        doc, name = self.doc, self.move
        if why is not None or doc is None or name is None:
            self.message = f"not played in the game: {why}"
            return
        clip = doc.manifest.moves[name].clip
        try:
            if clip is not None:
                self.entry_in_game(doc.manifest.clips[clip].id)
            doc.save()
            sent, build = move_game.deploy(doc.manifest, self.games(), self.mods_dir())
            with self.game_session() as s:
                took = self.play_own(s, name, force, build)
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self.message = f"not played in the game: {e}"
            return
        if not took:
            self.message = (
                f"the game did not take {name}: no port rides its big monster, or the port has no"
                " own move by that name"
            )
            return
        news = (
            f"{', '.join(p.name for p in sent)} sent to {_home(sent[0].parent)}"
            if sent
            else "the memory stick's modules were current"
        )
        self.message = f"{name} plays in the game; saved, {news}"


def _roots() -> tuple[Path | None, ...]:
    """Where the two games are now."""
    return tuple(places.find(p).path for p in (places.MHFU, places.MHP3RD))


def _home(path: Path) -> str:
    """`path` with the home directory as `~`."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


register("monster", MonsterWorkspace)
