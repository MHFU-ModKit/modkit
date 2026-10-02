# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster workspace: a port manifest (or a bare monster PAC), its scene in the viewport,
and the docks around it. The panels show; this holds what they share and does what they ask.

Every edit goes through the `PortDocument`, one undo step each; whatever depends on the
manifest (clip names, volumes, the alignment) is re-read once per frame when the document's
manifest is a different object, so an edit, an undo and a save-as all land the same way.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Hashable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu import files, hitzone
from mhfu.em.intel import AttackIntel, HostSummary, PairIntel, PartIntel, SpeciesIntel
from mhfu.files import Extracted
from mhfu_port import slots
from mhfu_port.data import Data
from mhfu_port.manifest import Clip as ManifestClip
from mhfu_port.manifest import Manifest, ManifestError
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

from mhfu_studio.monster import align, clips, inputs, species
from mhfu_studio.monster.attacks import AttackSession
from mhfu_studio.monster.core.scene import MHFU, Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels.graph import MoveGraph
from mhfu_studio.monster.parts import PartSession
from mhfu_studio.monster.tools import HIT, HURT, NOUN, VolumeTools, describe
from mhfu_studio.monster.tools import KEYS as VOLUME_KEYS
from mhfu_studio.shell.input import Key, Mod, Pointer
from mhfu_studio.shell.overlay import Overlay
from mhfu_studio.shell.text import keys
from mhfu_studio.shell.workspace import Dock, Gesture, Shortcut, Workspace, register

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.monster.render.hitboxes import HitboxOverlay
    from mhfu_studio.monster.render.viewport import MonsterViewport
    from mhfu_studio.monster.runtime import AttackTables
    from mhfu_studio.shell.studio import Studio

Pair = tuple[int, int]
#: a table's source: the base monster's, or the port's own (the panels' switch)
PORT, HOST = "port", "host"
#: the Timeline's height: its title, the transport and the frame strip; the rest scrolls
TIMELINE_H = 120
PLAY = Shortcut(("Space",), "Plays the clip, or pauses it")
STEP = Shortcut(("Left", "Right"), "One game frame back, or on")
REWIND = Shortcut(("Home",), "Back to the clip's first frame")
#: `intel_gap`'s subjects in words
WORDS = {"part": "hurtbox", "attack": "attack", "action": "action"}


class MonsterWorkspace(Workspace):
    name = "monster"
    filters = ("Port manifest", "*.toml", "Monster PAC", "*.bin *.pac")

    def __init__(self, data: Data | None = None, intel_root: Path | None = None) -> None:
        self._data = data
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
        self._seen: Manifest | None = None
        #: every overlay summarised, once surveyed
        self.hosts: list[HostSummary] | None = None
        self._survey: Future[list[HostSummary]] | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._reset()

    def _reset(self) -> None:
        """Per-document view state."""
        self.tools.reset()
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
        self._parts: PartSession | None = None
        self._attacks: AttackSession | None = None

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
        """A manifest is built in memory from the extracted games; a PAC is read as it is."""
        if path.suffix == ".toml":
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
        self.undriven = undriven_geometry(scene)
        if doc is not None:
            doc.pac = scene.pac
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

    def docks(self) -> Sequence[Dock]:
        def build(module: str, panel: str) -> Callable[[Studio], Any]:
            """The panel class imported when its dock is first built (Qt loads only then)."""
            return lambda studio: getattr(
                import_module(f"mhfu_studio.monster.panels.{module}"), panel
            )(self, studio)

        return (
            Dock(
                "Clips", "left", build("clips", "ClipsPanel"),
                "Every anim, what is really in it, and the name it goes by.",
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
            Dock(
                "Moves", "bottom", build("moves", "MovesPanel"),
                "The base monster's actions as a graph of which leads to which. Click one to"
                " read it; double-click to work on it in Action.",
                shown=False,
            ),
            Dock(
                "Action", "bottom", build("action", "ActionPanel"),
                "Which of your clips plays for each of the base monster's actions, and what the"
                " action expects of it: its frames, effects and hits.",
                shown=False,
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

    def animating(self) -> bool:
        vp = self.vp
        if vp is None or vp.actor is None:
            return False
        ref = vp.reference
        return vp.actor.playback.playing or (ref is not None and ref.playback.playing)

    def refresh(self) -> None:
        self.tools.cancel()  # an undo under a drag: the drag's start is gone
        self.sync()

    def take_focus(self) -> str | None:
        label, self._focus = self._focus, None
        return label

    def focus(self, dock: str) -> None:
        """Brings the dock labelled `dock` to the front after this change."""
        self._focus = dock

    def pointer(self, ev: Pointer) -> Gesture:
        """Picking and the gizmo (`tools`); a drag off a handle stays the camera's."""
        return self.tools.pointer(ev)

    def key(self, ev: Key) -> bool:
        """The picked volume's keys, then the transport's while a clip is on screen."""
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
        return (PLAY, STEP, REWIND, *VOLUME_KEYS)

    def hint(self) -> str:
        sc, vp = self.scene, self.vp
        if sc is None:
            return "Open a port manifest (ports/<name>.toml) with File > Open"
        bits = []
        clip = None if vp is None else vp.clip
        if vp is None or clip is None:
            bits.append("Pick a clip in Clips to play it")
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
        if picked:
            bits.append(picked)
        elif self.selected_set is not None:
            bits.append(f"hit group {self.selected_set}: pick one of its hitboxes in Hitboxes")
        return " \u00b7 ".join(bits)

    def paint(self, o: Overlay) -> None:
        from mhfu_studio.monster.panels import viewport

        viewport.joint_labels(self, o)
        self.tools.paint(o)

    def reveal(self, target: Hashable, focus: str = "") -> None:
        """A finding's `(section, key)`: select it, bring its panel forward and land on `focus`."""
        if not isinstance(target, tuple) or len(target) != 2 or self.manifest is None:
            return
        self.landing = focus
        section, key = target
        m = self.manifest
        if section == "clips" and isinstance(key, str) and key in m.clips:
            self.play_slot(m.clips[key].slot)
            self.focus("Clips")
        elif section == "moves" and isinstance(key, str) and key in m.moves:
            mv = m.moves[key]
            if mv.clip in m.clips:
                self.play_slot(m.clips[mv.clip].slot)
            self.select_pair(mv.main, mv.sub, key)
            self.focus("Action")
        elif section == "hurtbox" and isinstance(key, int) and key < len(m.hurtboxes):
            self.show_parts, self.parts_source = True, PORT
            self.sync_hitboxes()
            self.select_volume(key)
            if m.hurtboxes[key].part is not None:
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
            if e.move in m.moves:
                self.select_pair(m.moves[e.move].main, m.moves[e.move].sub, e.move)
            self.focus("Action")

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
        m = self.manifest
        self._seen = m
        if self.scene is not None and m is not None:
            self.scene.attach_manifest(m)
        self._vocab = None
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

    def _module(self) -> tuple[Manifest, int | None, AttackTables | None, str]:
        """What `runtime.export` takes for the document as it is now."""
        from mhfu_studio.monster import runtime

        if self.doc is None:
            raise ValueError("no port manifest open")
        m, intel = self.doc.manifest, self.host_intel()
        source = f"{runtime.source_of(m)}, unsaved edits" if self.doc.dirty else ""
        return m, runtime.host_capacity(intel), runtime.host_attack_tables(intel), source

    def export_hit(self) -> None:
        """`<name>_hit.lua` in the working directory."""
        from mhfu_studio.monster import runtime

        m, cap, tables, source = self._module()
        path = runtime.export(m, None, cap, tables, source).resolve()
        self.message = f"wrote {path} (id {runtime.content_id(m)})"

    def deploy_hit(self) -> None:
        """Exported fresh into the studio's cache, then copied with a stale `mhfu_port.lua`
        to the memory stick's mods folder."""
        from mhfu_studio.monster import runtime

        m, cap, tables, source = self._module()
        out = runtime.cache_dir() / runtime.module_name(m)
        dep = runtime.ship(m, out, cap, tables, mods_dir=self.mods_dir(), source=source)
        lib = "" if dep.library is None else f" and {runtime.LIB_SUBDIR}/{runtime.LIBRARY}"
        self.message = (
            f"sent {dep.module.name}{lib} to {_home(dep.module.parent)}; it applies when a mod"
            f" defines the port '{m.port.name}' and the monster is in the area (id"
            f" {runtime.content_id(m)})"
        )

    @staticmethod
    def mods_dir() -> Path:
        """The memory stick's mods folder; FileNotFoundError says where it looked."""
        from mhfu import inject

        return inject.default_mods_dir()

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
            runtime.check(m, runtime.host_attack_tables(self.host_intel()))
        except ManifestError as e:
            gap = self.intel_gap("attack")
            return f"cannot send hitboxes or attacks. {gap}" if gap else f"cannot send: {e}"
        try:
            self.mods_dir()
        except FileNotFoundError as e:
            return f"no memory stick to send to: {e}"
        return None

    def send(self) -> None:
        """The hit tables onto the memory stick (`deploy_hit`), no save needed."""
        try:
            self.deploy_hit()
        except (OSError, ValueError) as e:  # ManifestError is a ValueError
            self.message = f"send failed: {e}"

    # the games and the intel

    def games(self) -> Data:
        if self._data is None:
            self._data = Data.find()
        return self._data

    def fu(self) -> Extracted:
        return self._data.fu if self._data is not None else Extracted.find()

    def intel_of(self, sp: int | None) -> SpeciesIntel | None:
        if sp is None:
            return None
        if sp not in self.intel_cache:
            root = self._data.fu.root if self._data is not None else None
            try:
                self.intel_cache[sp] = species.load(sp, self.intel_root, root)
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
        """The overlay the Action panel shows: the host's unless browsing another."""
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
            self._pool = self._pool or ThreadPoolExecutor(1)
            root = self._data.fu.root if self._data is not None else None
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
            self._labels = clips.LabelSession(self.doc, self.clip_table(), self.scene.build_id)
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
        host = donor = None
        try:
            games = self.games()
        except FileNotFoundError as e:
            notes.append(f"no base monster's pack and no original moveset: {e}")
            return clips.coverage(port), notes
        for what, read in (
            ("base monster's pack", inputs.host_anim),
            ("original moveset", inputs.donor_anim),
        ):
            try:
                got = read(m, games)
            except (OSError, ValueError) as e:
                notes.append(f"{what} unreadable ({e})")
                continue
            if read is inputs.host_anim:
                host = got
            else:
                donor = got
        return clips.coverage(port, host, donor), notes

    def vocabulary(self) -> clips.Vocabulary:
        if self._vocab is None:
            cov, notes = self.coverage()
            build = None if self.scene is None else self.scene.build_id
            self._vocab = clips.survey(self.manifest, self.clip_table(), cov, build, notes)
        return self._vocab

    def manifest_clip(self, slot: int) -> tuple[str, ManifestClip] | None:
        m = self.manifest
        return None if m is None else clips.entry(m, slot)

    def pick_clip(self, slot: int) -> None:
        """Selects `slot` for naming and loads its name and label into the boxes."""
        self.edit_slot = slot
        found = self.manifest_clip(slot)
        self.name_buf = found[0] if found else clips.clip_key(slot)
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
        self.recompute_alignment()

    def label(self) -> None:
        s = self.label_session
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
        name = found[0] if found else self.name_buf or clips.clip_key(slot)
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
        self.pair, self.move = (int(main), int(sub)), move
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
        found = None if clip is None else clips.entry(m, clip.slot)
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
        """The alignment as `[moves.<name>]`, `move_<main>_<sub>` when unnamed: a move of that
        name is updated in place, an unnamed clip on screen named."""
        al, s = self.alignment, self.label_session
        if al is None or s is None:
            return
        name = name.strip() or f"move_{al.main}_{al.sub}"
        if self.edit("", lambda: s.bind_move(name, al.main, al.sub, al.slot)):
            self.select_pair(al.main, al.sub, name)

    def host_pair(self) -> PairIntel | None:
        """The selected pair's intel when it is the host's; None while browsing."""
        if self.pair is None or not self.browsing_the_host:
            return None
        si = self.host_intel()
        return None if si is None else si.pair(*self.pair)

    def pair_sets(self) -> list[int]:
        """The volume sets the selected pair's handler hits with."""
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


def _home(path: Path) -> str:
    """`path` with the home directory as `~`."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


register("monster", MonsterWorkspace)
