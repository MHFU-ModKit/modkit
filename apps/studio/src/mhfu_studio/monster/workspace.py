# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster workspace: a port manifest (or a bare monster PAC), its scene in the viewport,
and the docks around it. The panels show; this holds what they share and does what they ask.

Every edit goes through the `PortDocument`, one undo step each; whatever depends on the
manifest (clip names, volumes, the alignment) is re-read once per frame when the document's
manifest is a different object, so an edit, an undo and a save-as all land the same way.
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Callable, Hashable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu import files, hitzone
from mhfu.em.intel import (
    AttackIntel,
    AttackRecord,
    HostSummary,
    PairIntel,
    PartIntel,
    SpeciesIntel,
)
from mhfu.files import Extracted
from mhfu_port import layout, slots
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
    clip_browser,
    clip_game,
    clips,
    inputs,
    move_game,
    rules,
    species,
)
from mhfu_studio.monster.attacks import AttackSession, hitbox_of
from mhfu_studio.monster.clip_browser import ClipBrowser, SourceClip
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels.graph import MoveGraph
from mhfu_studio.monster.parts import PartSession
from mhfu_studio.monster.tools import HIT, HURT, NOUN, VolumeTools, describe
from mhfu_studio.monster.tools import KEYS as VOLUME_KEYS
from mhfu_studio.monster.turn import TURN_KEY, TurnGizmo
from mhfu_studio.shell import places
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
    from mhfu.live.session import Session
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
OPEN_TIP = "Choose a port manifest (.toml) or a monster PAC; the monster workspace opens it"


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


@dataclass(frozen=True)
class _Warm:
    """What `warmup` read for `open`."""

    path: Path
    doc: PortDocument
    scene: Scene
    species: int | None
    intel: SpeciesIntel | None


class MonsterWorkspace(Workspace):
    name = "monster"
    filters = ("Port manifest", "*.toml", "Monster PAC", "*.bin *.pac")
    send_label = "Send hitboxes to game"

    def __init__(self, data: Data | None = None, intel_root: Path | None = None) -> None:
        self._data = data
        #: games handed in stay; else `locate` drops them when `places` finds others
        self._handed = data is not None
        self._found = _roots()
        #: what the last `warmup` read, for `open` to take
        self._warm: _Warm | None = None
        #: the manifest as the last Send hitboxes to game sent it
        self._sent: Manifest | None = None
        #: species intel from this directory instead of the cache built from the game
        self.intel_root = intel_root
        self.intel_cache: dict[int, SpeciesIntel | None] = {}
        #: why a species' intel is None in the cache
        self.intel_errors: dict[int, str] = {}
        self.doc: PortDocument | None = None
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
        self.play_own: Callable[[Session, str], bool] = move_game.play_own
        self.game_running: Callable[[], bool] = move_game.Running()
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
        #: the rule picked in Moves, by index
        self.picked_rule: int | None = None
        self.show_joint_ids = False
        self.undriven: dict[int, int] = {}
        self.markers: list[align.Marker] = []
        self.alignment: align.Alignment | None = None
        self.browse: int | None = None
        self.show_host = False
        self.host_scenes: dict[int, Scene | None] = {}
        self.host_clip: int | None = None
        self.pair: Pair | None = None
        self.move: str | None = None
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
        self._previews: dict[int, SceneClip] = {}
        #: the scene and the layout it was built with (`_relayout`)
        self._built_for: Scene | None = None
        self._built: dict[int, int] = {}

    # the shell's side

    @property
    def document(self) -> PortDocument | None:
        return self.doc

    @property
    def viewport(self) -> MonsterViewport | None:
        return self.vp

    def can_open(self, path: Path) -> bool:
        """A port manifest (a TOML with a `[port]` table) or a monster PAC (one with a skeleton)."""
        try:
            if path.suffix == ".toml":
                return "port" in tomllib.loads(path.read_text(encoding="utf-8"))
            if path.suffix not in (".bin", ".pac"):
                return False
            return any(Skeleton.sniff(e) for e in Pac.from_bytes(path.read_bytes()).entries)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            return False

    def open(self, path: Path) -> None:
        """A manifest is built in memory from the extracted games, or comes from `warmup`; a PAC
        is read as it is."""
        warm, self._warm = self._warm, None
        if warm is not None and warm.path == path:
            self.load(warm.scene, warm.doc)
        elif path.suffix == ".toml":
            doc = PortDocument.open(path)
            self.load(Scene.from_manifest(doc.manifest, None, "port", self.games()), doc)
        else:
            self.load(Scene.from_path(path))
        self.message = f"opened {path.name}"

    def load(self, scene: Scene, doc: PortDocument | None = None) -> None:
        """Shows `scene`; `doc` is the manifest it was built from."""
        from mhfu_studio.monster.render.skeleton import undriven_geometry

        self.scene, self.doc = scene, doc
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
        return None if sc is None else (None if self.doc is None else self.doc.path, sc.name)

    def start(self) -> Sequence[Shelf]:
        ports = tuple(port_choice(p) for p in port_files())
        note = "" if ports else "No ports next to the studio (modkit's ports folder)."
        needs = (places.MHFU, places.MHP3RD)
        return (Shelf("Ports", ports, note, ("Open\u2026", OPEN_TIP), needs),)

    def warmup(self, path: Path) -> Warmup | None:
        """A port's manifest, its build and its base monster's data, read off the GUI thread."""
        if path.suffix != ".toml":
            return None
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

    def next_steps(self) -> Sequence[Step]:
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
        return sc.name if sp is None else f"{sc.name} on {species.label(sp)}"

    def frame(self, dt: float) -> None:
        if self.doc is not None and self.doc.manifest is not self._seen:
            self.sync()
        if self.vp is not None:
            self.vp.tick(dt)
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
        """A finding's `(section, key)`: select it, bring its panel forward and land on `focus`."""
        if not isinstance(target, tuple) or len(target) != 2 or self.manifest is None:
            return
        self.landing = focus
        section, key = target
        m = self.manifest
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
        elif section == "hurtbox" and isinstance(key, int) and key < len(m.hurtboxes):
            self.show_parts, self.parts_source = True, PORT
            self.sync_hitboxes()
            self.select_volume(key)
            if m.hurtboxes[key].part:  # 0 is nobody
                self.select_part((m.hurtboxes[key].part or 0) & hitzone.PART_MASK)
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
        if m is None or not 0 <= (self.picked_rule or 0) < len(m.rules):
            self.picked_rule = None  # an undo took the rule away
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
        if self.doc is None:
            self.message = "this scene has no manifest file"
            return
        try:
            self.message = f"saved {self.doc.save()}"
        except (OSError, ManifestError) as e:
            self.message = f"{type(e).__name__}: {e}"

    def revert(self) -> None:
        if self.doc is not None:
            self.doc.revert()
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
        """The hit tables onto the memory stick (`deploy_hit`), no save needed."""
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
        self.intel_cache.clear()
        self.intel_errors.clear()
        self.host_scenes.clear()
        self.hosts, self._survey = None, None
        self._coverage = self._vocab = None
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

    def clip_table(self) -> dict[int, clips.Fingerprint]:
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
                placed = layout.of(m, donor, host).entries
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

    def plays(self, main: int, sub: int, move: str | None = None) -> actions.Plays:
        """What plays in this port while the game is in the base monster's `(main, sub)`."""
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
        slot = self.move_slot(mv)
        if slot is not None and slot < 0:
            self.play_source(-1 - slot)
        elif slot is not None:
            self.play_slot(slot)
        self.sync_steer()

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
        """The selected move goes; refused while another move, a rule or an effect names it."""
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
        return SourceClip(None, slot, c.frames, c.loop, name, label)

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

    # rules (the Moves panel)

    def new_rule(self) -> None:
        """A rule that plays the selected move, else the first; picked."""
        doc, m = self.doc, self.manifest
        if doc is None or m is None:
            self.message = "rules live in a port manifest: open one"
            return
        play = self.move if self.move in m.moves else next(iter(m.moves), None)
        if play is None:
            self.message = "make a move first: a rule plays one"
            return
        if self.edit("", lambda: rules.new_rule(doc, play)):
            self.picked_rule = len(doc.manifest.rules) - 1

    def pick_rule(self, index: int | None) -> None:
        self.picked_rule = index

    def set_rule(self, **fields: Any) -> bool:
        """`rules.FIELDS` of the picked rule, one undo step."""
        doc, i = self.doc, self.picked_rule
        if doc is None or i is None:
            return False
        return self.edit("", lambda: rules.set_rule(doc, i, **fields))

    def delete_rule(self) -> None:
        doc, i = self.doc, self.picked_rule
        if doc is not None and i is not None and self.edit("", lambda: rules.remove_rule(doc, i)):
            self.picked_rule = None

    # Play in game for an own move (the Moves panel)

    def play_move_blocker(self) -> str | None:
        """Why Play in game cannot play the selected move now; a game that does not answer is
        found by the play."""
        m, name = self.manifest, self.move
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

    def play_move_in_game(self) -> None:
        """Saves, sends the port's clips and moves modules to the memory stick
        (`move_game.deploy`) and asks the running port to play the selected own move."""
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
            sent = move_game.deploy(doc.manifest, self.games(), self.mods_dir())
            with self.game_session() as s:
                took = self.play_own(s, name)
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self.message = f"not played in the game: {e}"
            return
        if not took:
            self.message = (
                f"the game did not take {name}: no port rides its big monster, or the port has no"
                " own move by that name (a port takes new moves when its mod reloads)"
            )
            return
        where = _home(sent[0].parent) if sent else "the memory stick"
        self.message = (
            f"{name} plays in the game; saved, {', '.join(p.name for p in sent)} sent to {where}"
        )


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
