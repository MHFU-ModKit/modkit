"""The only code that writes game memory: QUEST_PREP's staged edits, written on the writer's own
thread to the monsters in the snapshots the reader publishes.

An edit writes each matching monster once. Entering a quest area with monsters (QUEST, IN_AREA
and a monster in the registry, after a snapshot without) re-arms every edit, so a new quest or
area gets them again. Snapshots without a game connected leave that state alone.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field, replace
from typing import Protocol

from mhfu.memory import Live
from mhfu.structs import Entity, Screen
from ppsspp_debug import Client

from .edits import Edit, Kind, Staged, WriterStatus
from .state import Context, GameSnapshot

WAIT_S = 0.25
"""How long the thread waits for a snapshot before checking for stop."""


class Source(Protocol):
    """What the writer needs of the reader: its snapshots and its connection."""

    def subscribe(self) -> queue.SimpleQueue[GameSnapshot]: ...

    @property
    def client(self) -> Client | None: ...


def write(edit: Edit, e: Entity) -> None:
    if edit.kind is Kind.SIZE:
        e.resize(edit.value)
    elif edit.kind is Kind.SPECIES:
        e.species = int(edit.value) & 0xFF
    else:
        e.hp = int(edit.value) & 0xFFFF


def in_area(snap: GameSnapshot) -> bool:
    """In a quest area with monsters loaded: entering it re-arms the edits."""
    quest = snap.context is Context.QUEST and snap.screen_state == Screen.IN_AREA
    return quest and bool(snap.monsters)


@dataclass
class _Entry:
    edit: Edit
    applied: set[int] = field(default_factory=set)
    """Entity pointers written."""


class GameWriter:
    """Owns the staged edits; the window changes them, the thread writes them."""

    def __init__(self, source: Source) -> None:
        self._source = source
        self._snapshots = source.subscribe()
        self._lock = threading.Lock()
        self._entries: list[_Entry] = []
        self._enabled = True
        self._writes = 0
        self._error = ""
        self._armed = False
        self._status = WriterStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- the window's side; each call is under the lock ---

    @property
    def status(self) -> WriterStatus:
        with self._lock:
            return self._status

    def stage(self, edit: Edit) -> None:
        with self._lock:
            self._entries.append(_Entry(edit))
            self._publish()

    def change(self, index: int, edit: Edit | None) -> None:
        """Replace edit `index`, or remove it with None; a new kind or value writes again."""
        with self._lock:
            if not 0 <= index < len(self._entries):
                return
            old = self._entries[index]
            if edit is None:
                del self._entries[index]
            elif replace(edit, enabled=True) == replace(old.edit, enabled=True):
                old.edit = edit
            else:
                self._entries[index] = _Entry(edit)
            self._publish()

    def toggle(self) -> None:
        """Flip the master switch."""
        with self._lock:
            self._enabled = not self._enabled
            self._publish()

    def _publish(self) -> None:
        staged = tuple(Staged(e.edit, len(e.applied)) for e in self._entries)
        self._status = WriterStatus(staged, self._enabled, self._writes, self._error)

    # --- the thread ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mhfu-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                batch = [self._snapshots.get(timeout=WAIT_S)]
            except queue.Empty:
                continue
            while not self._snapshots.empty():
                batch.append(self._snapshots.get_nowait())
            try:
                self.apply(*batch)
            except Exception as e:  # keep the thread: a dead writer would go unseen
                with self._lock:
                    self._error = repr(e)
                    self._publish()

    def apply(self, *snapshots: GameSnapshot) -> int:
        """Track quest-area entry over `snapshots` in order and write what is due to the last
        one's monsters; the number of writes."""
        live = [s for s in snapshots if s.context not in (Context.DISCONNECTED, Context.BOOT)]
        if not live:
            return 0
        with self._lock:
            for snap in live:
                now = in_area(snap)
                if now and not self._armed:
                    for entry in self._entries:
                        entry.applied.clear()
                self._armed = now
            due = [
                (entry, entry.edit, m.ptr)
                for m in live[-1].monsters
                for entry in self._entries
                if self._enabled
                and entry.edit.enabled
                and m.ptr not in entry.applied
                and entry.edit.matches(m)
            ]
            self._publish()
        client = self._source.client
        if not due or client is None or client.closed:
            return 0
        mem = Live(client)
        done: list[tuple[_Entry, int]] = []
        error = ""
        for entry, edit, ptr in due:
            try:
                write(edit, Entity(mem, ptr))
            except Exception as e:  # the next snapshot retries it
                error = repr(e)
                continue
            done.append((entry, ptr))
        with self._lock:
            for entry, ptr in done:
                entry.applied.add(ptr)
            self._writes += len(done)
            self._error = error
            self._publish()
        return len(done)
