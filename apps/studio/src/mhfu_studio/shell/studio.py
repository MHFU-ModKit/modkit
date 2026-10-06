# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The documents and actions behind the window, for any toolkit to drive.

Every action ends in `changed()`; a window listens once and re-reads what it shows, so no
control has to know which others depend on what it did. Questions (drop unsaved edits? where to
save?) go through hooks the window installs; without them the studio goes on unasked.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Hashable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_for
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from mhfu_studio.shell import places, settings
from mhfu_studio.shell.context import ContextError, attached, describe
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.workspace import Choice, Job, Warmup, Workspace, pick

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.viewport import Viewport


class Findings:
    """The active document's findings, re-checked on demand and, when `auto`, now and then.

    The interval adapts to the check's own cost so a slow check cannot eat the frame rate.
    """

    def __init__(self) -> None:
        self.auto = True
        self.found: list[Finding] = []
        self._doc: object | None = None
        self._at = float("-inf")
        self._cost = 0.0
        self._stale = True

    def stale(self) -> None:
        self._stale = True

    def get(self, doc: Any, now: float | None = None) -> list[Finding]:
        now = time.monotonic() if now is None else now
        if doc is None:
            self.found, self._doc = [], None
            return self.found
        due = self.auto and now - self._at >= max(1.0, 10 * self._cost)
        if self._stale or doc is not self._doc or due:
            t0 = time.monotonic()
            try:
                self.found = list(doc.findings())
            except Exception as e:
                self.found = [Finding("error", "check-failed", f"{type(e).__name__}: {e}")]
            self._cost = time.monotonic() - t0
            self._at, self._doc, self._stale = now, doc, False
        return self.found


#: the studio's own command line, run by the interpreter running the window
MAIN = "import sys; from mhfu_studio.cli import main; sys.exit(main())"
LOG_LINES = 2000
#: recent documents kept per workspace
RECENT = 8


def said(e: Exception) -> str:
    """`e` for the window: a missing place in words."""
    return e.words if isinstance(e, places.Missing) else str(e)


@dataclass(frozen=True)
class Opening:
    """A document waiting for its workspace's warm-up (`Studio.open_later`)."""

    ws: Workspace
    path: Path
    warmup: Warmup
    future: Future[object]


def command(job: Job) -> list[str]:
    """`job`'s full command line, the interpreter first."""
    return [sys.executable, "-c", MAIN, *job.argv]


class Runner(Protocol):
    """Runs one command at a time in the background; reports through `Studio.heard`/`ended`."""

    def start(self, argv: list[str], stdin: bytes) -> None: ...

    def stop(self) -> None: ...


def doc_name(ws: Workspace) -> str:
    """`ws`'s document as the window names it: its file, or "untitled <workspace>"."""
    doc = ws.document
    if doc is None:
        return "no document"
    return doc.path.name if doc.path is not None else f"untitled {ws.name}"


class Studio:
    """The registered workspaces, the active one, and what the window can do to them."""

    def __init__(self, workspaces: Sequence[Workspace], *, title: str = "MHFU Studio") -> None:
        if not workspaces:
            raise ValueError("no workspaces are installed")
        names = [w.name for w in workspaces]
        if len(set(names)) != len(names):
            raise ValueError(f"workspace names repeat: {names}")
        self.workspaces = list(workspaces)
        self.active = self.workspaces[0]
        self.title = title
        self.ctx: moderngl.Context | None = None
        self.renderer = ""
        #: why there is no GL, shown in the viewport instead of a picture
        self.error: str | None = None
        #: each workspace's last outcome; `message` is the active one's
        self._messages = {w.name: "" for w in self.workspaces}
        #: the first exception each action raised this run (label, traceback); the window goes on
        self.errors: list[tuple[str, str]] = []
        self.findings = Findings()
        #: "save", "discard" or "cancel" for the named documents' unsaved edits
        self.ask_discard: Callable[[list[str]], str] | None = None
        #: whether to drop the named document's unsaved edits and go back to its file
        self.ask_revert: Callable[[str], bool] | None = None
        #: where to save a document that has no file yet; None is a cancel
        self.ask_path: Callable[[Workspace], Path | None] | None = None
        #: runs a `Job` in the background (the window's); without one a job runs inline
        self.runner: Runner | None = None
        #: entered around every guarded call and a job's end: the view's GL context current, so
        #: what an action makes or frees is the view's (a VAO is not shared between contexts)
        self.gl_current: Callable[[], AbstractContextManager[object]] = nullcontext
        #: the running job, None when idle
        self.job: Job | None = None
        #: the workspace that started the last job (its status gets the outcome), and a Stop
        self._sender = self.active.name
        self._stopped = False
        #: what the jobs printed, oldest first, `LOG_LINES` at most
        self.log: list[str] = []
        self._closed = False
        self._listeners: list[Callable[[], None]] = []
        #: each workspace's `said` when we last looked
        self._heard = {w.name: w.said for w in self.workspaces}
        #: per workspace, what it showed when the start page was asked for over it
        self._start: dict[str, Hashable | None] = {}
        #: workspaces whose last document was reopened, or tried, this run
        self._resumed: set[str] = set()
        #: a document waiting for its warm-up
        self.opening: Opening | None = None
        self._pool: ThreadPoolExecutor | None = None

    @property
    def message(self) -> str:
        """The active workspace's last outcome, for the status bar."""
        return self._messages[self.active.name]

    @message.setter
    def message(self, text: str) -> None:
        self._messages[self.active.name] = text

    # ---- change ------------------------------------------------------------------- #
    def listen(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def changed(self) -> None:
        """Tells the listeners; a message the active workspace set since becomes ours."""
        ws = self.active
        if ws.said != self._heard.get(ws.name):
            self._heard[ws.name] = ws.said
            if ws.message:
                self.message = ws.message
        for fn in list(self._listeners):
            fn()

    def act(self, label: str, fn: Callable[[], object]) -> Callable[..., None]:
        """A control's slot: `fn` guarded, then `changed()`; ignores the signal's arguments."""
        guarded = self.guard(label, fn)

        def run(*_: object) -> None:
            guarded()
            self.changed()

        return run

    def guard(self, label: str, fn: Callable[[], object]) -> Callable[[], None]:
        """`fn` with its exceptions recorded and reported instead of ending the window."""

        def guarded() -> None:
            try:
                with self.gl_current():
                    fn()
            except Exception as e:
                self.message = f"{label}: {type(e).__name__}: {e}"
                if not any(lb == label for lb, _ in self.errors):  # once, not every frame
                    tb = traceback.format_exc()
                    print(f"studio: {label}: {tb}", file=sys.stderr)
                    self.errors.append((label, tb))

        return guarded

    # ---- actions ------------------------------------------------------------------ #
    def workspace(self, name: str) -> Workspace:
        for w in self.workspaces:
            if w.name == name:
                return w
        raise KeyError(f"no workspace {name!r} (have: {', '.join(self.names)})")

    @property
    def names(self) -> list[str]:
        return [w.name for w in self.workspaces]

    def switch(self, name: str) -> None:
        self.active = self.workspace(name)
        self.findings.stale()
        self.changed()

    def open(self, path: Path | str) -> bool:
        """Opens `path` in the first workspace that can, and switches to it; False when it
        did not open, or its unsaved edits were kept (`discard_ok`)."""
        path = Path(path)
        ws = pick(self.workspaces, path)
        if ws is None:
            self.message = f"nothing here opens {path.name}"
            self.changed()
            return False
        if not self.discard_ok(ws):
            self.changed()
            return False
        before = ws.said
        try:
            with self.gl_current():
                ws.open(path)
        except Exception as e:
            if isinstance(e, places.Missing):  # the start page's checklist says what to do
                self.active = ws
                self._start[ws.name] = ws.shown()
                self.findings.stale()
            self.message = f"could not open {path.name}: {said(e)}"
            self.changed()
            return False
        self._messages[ws.name] = f"opened {path}"
        self._heard[ws.name] = before  # what it said while opening shows instead
        self._record(ws, f"open:{path.resolve()}", path)
        self._start.pop(ws.name, None)
        self.switch(ws.name)
        return True

    def open_later(self, path: Path | str) -> bool:
        """`open(path)` once its workspace's `warmup` ran in the background (`poll` ends it);
        at once without one. True when it opened now."""
        path = Path(path)
        if self.opening is not None:
            self.message = f"busy: opening {self.opening.path.name}"
            self.changed()
            return False
        ws = pick(self.workspaces, path)
        warm = None if ws is None else ws.warmup(path)
        if ws is None or warm is None:
            return self.open(path)
        self._pool = self._pool or ThreadPoolExecutor(1, thread_name_prefix="warmup")
        self.opening = Opening(ws, path, warm, self._pool.submit(warm.run))
        self._messages[ws.name] = f"{warm.what}\u2026"
        self._heard[ws.name] = ws.said
        self.switch(ws.name)
        return False

    def poll(self) -> None:
        """Opens the document `open_later` waits for, once its warm-up is done."""
        o = self.opening
        if o is None or not o.future.done():
            return
        self.opening = None
        try:
            got = o.future.result()
        except Exception as e:
            self._messages[o.ws.name] = f"could not open {o.path.name}: {said(e)}"
            self.changed()
            return
        done = o.warmup.done
        if done is not None:
            self.guard("warm-up", lambda: done(got))()
        self.open(o.path)

    def wait(self, timeout: float = 120.0) -> None:
        """Blocks until the warm-up `opening` waits for is done, then `poll`s."""
        if self.opening is not None:
            wait_for([self.opening.future], timeout)
        self.poll()

    # ---- the start page ----------------------------------------------------------- #
    def on_start(self) -> bool:
        """The window shows the start page in the view's place: nothing worth showing, a
        document opening, or `show_start` and the view unchanged since."""
        ws = self.active
        if self.opening is not None and self.opening.ws is ws:
            return True
        shown = ws.shown()
        if shown is None:
            return True
        if ws.name in self._start and self._start[ws.name] != shown:
            del self._start[ws.name]  # something else came up: the page has done its job
        return ws.name in self._start

    def show_start(self, on: bool = True) -> None:
        """The start page over the active workspace's view, or back to the view."""
        if on:
            self._start[self.active.name] = self.active.shown()
        else:
            self._start.pop(self.active.name, None)
        self.changed()

    def choose(self, choice: Choice) -> bool:
        """A start-page entry: its document (`open_later`), or its key in the active
        workspace (`Workspace.choose`)."""
        if choice.path is not None:
            return self.open_later(choice.path)
        ws = self.active
        try:
            with self.gl_current():
                ws.choose(choice.key)
        except Exception as e:
            self.message = f"could not open {choice.label}: {said(e)}"
            self.changed()
            return False
        self._record(ws, f"choose:{choice.key}")
        self._start.pop(ws.name, None)
        self.findings.stale()
        self.changed()
        return True

    def recent(self, ws: Workspace | None = None) -> list[Path]:
        """`ws`'s (the active one's) documents, newest first, those still there."""
        ws = self.active if ws is None else ws
        got = settings.store.get(f"recent/{ws.name}") or ""
        return [p for p in map(Path, got.splitlines()) if p.exists()]

    def _record(self, ws: Workspace, last: str, path: Path | None = None) -> None:
        """What `resume` reopens next run; `path` heads the recent documents."""
        if path is not None:
            path = path.resolve()
            keep = [path, *(p for p in self.recent(ws) if p != path)][:RECENT]
            settings.store.put(f"recent/{ws.name}", "\n".join(map(str, keep)))
        settings.store.put(f"last/{ws.name}", last)

    def resume(self) -> None:
        """The active workspace's last document or choice, once a run, while it shows
        nothing: a first run (nothing recorded) stays on the start page."""
        ws = self.active
        if ws.name in self._resumed:
            return
        self._resumed.add(ws.name)
        if ws.shown() is not None:
            return
        kind, _, what = (settings.store.get(f"last/{ws.name}") or "").partition(":")
        if kind == "open" and Path(what).exists():
            self.open_later(what)
        elif kind == "choose" and what:
            self.choose(Choice(what, "", key=what))

    def remember(self, place: places.Place, path: Path | str | None) -> bool:
        """`place` is `path` from now on (None: found again); every workspace looks again.
        False, the reason in `message`, when `path` is not one."""
        try:
            found = places.remember(place, path)
        except (OSError, ValueError) as e:
            self.message = f"not used: {e}"
            self.changed()
            return False
        self.message = f"{place.name}: {found.says()}"
        self.located()
        return True

    def located(self) -> None:
        """Every workspace finds its files again (`Workspace.locate`)."""
        for w in self.workspaces:
            self.guard(f"find {w.name} files", w.locate)()
        self.findings.stale()
        self.changed()

    def discard_ok(self, *workspaces: Workspace) -> bool:
        """Whether their documents may be dropped: no unsaved edits, or `ask_discard` said to
        discard them, or to save them and they saved. A cancel clears the message."""
        dirty = [w for w in workspaces if w.document is not None and w.document.dirty]
        if not dirty or self.ask_discard is None:
            return True
        got = self.ask_discard([doc_name(w) for w in dirty])
        if got == "save":
            return all(self.save(ws=w) for w in dirty)
        if got != "discard":
            self.message = ""
        return got == "discard"

    def save(self, path: Path | str | None = None, ws: Workspace | None = None) -> bool:
        """`ws`'s document (the active one's) to `path`, or to its file; one without a file
        asks `ask_path`."""
        ws = self.active if ws is None else ws
        doc = ws.document
        ok = False
        if doc is not None and path is None and doc.path is None and self.ask_path is not None:
            path = self.ask_path(ws)
            if path is None:
                self.message = ""
                self.changed()
                return False
        if doc is None:
            self.message = "nothing to save"
        else:
            try:
                where = doc.save(None if path is None else Path(path))
            except Exception as e:
                self.message = f"not saved: {e}"
            else:
                if path is not None:
                    with self.gl_current():
                        ws.refresh()
                    self._record(ws, f"open:{Path(where).resolve()}", Path(where))
                self.findings.stale()
                self.message = f"saved {where}"
                ok = True
        self.changed()
        return ok

    def can_revert(self) -> bool:
        """The active document has unsaved edits and a file to go back to."""
        doc = self.active.document
        return doc is not None and doc.path is not None and doc.dirty

    def revert(self) -> None:
        """The active document back to its file, once `ask_revert` lets its edits go."""
        ws = self.active
        name = doc_name(ws)
        if not self.can_revert():
            self.message = "nothing to revert"
        elif self.ask_revert is not None and not self.ask_revert(name):
            self.message = ""
        else:
            try:
                with self.gl_current():
                    ws.revert()
            except Exception as e:
                self.message = f"not reverted: {e}"
            else:
                self._heard[ws.name] = ws.said  # ours, not what it said while reloading
                self.findings.stale()
                self.message = f"back to the saved {name}"
        self.changed()

    # ---- the game ---------------------------------------------------------------- #
    def send_blocker(self) -> str | None:
        """Why "Send to game" cannot run now; None when it can."""
        if self.job is not None:
            return f"busy: {self.job.title}"
        return self.active.send_blocker()

    def send(self) -> None:
        """The active workspace's edits to the game, the outcome in `message`."""
        why = self.send_blocker()
        if why is not None:
            self.message = why
        else:
            job = self.active.send()
            if job is not None:
                self.start(job)
                return
        self.changed()

    def start(self, job: Job) -> None:
        """Runs `job` through the runner, or inline without one."""
        if self.job is not None:
            self.message = f"busy: {self.job.title}"
            self.changed()
            return
        self.job, self._sender, self._stopped = job, self.active.name, False
        self.heard(f"$ studio {' '.join(job.argv)}")
        self.message = f"{job.title}…"
        self.changed()
        if self.runner is not None:
            self.runner.start(command(job), job.stdin)
            return
        env = {**os.environ, **places.environ()}
        done = subprocess.run(
            command(job), input=job.stdin, capture_output=True, check=False, env=env
        )
        for line in (done.stdout + done.stderr).decode(errors="replace").splitlines():
            self.heard(line)
        self.ended(done.returncode)

    def stop(self) -> None:
        if self.job is not None and self.runner is not None:
            self._stopped = True
            self.runner.stop()

    def heard(self, line: str) -> None:
        """One line of the running job's output."""
        self.log.append(line)
        del self.log[:-LOG_LINES]

    def ended(self, code: int) -> None:
        """The running job ended with exit `code`."""
        with self.gl_current():
            self._ended(code)

    def _ended(self, code: int) -> None:
        job, self.job = self.job, None
        self.heard(f"[exit {code}]")
        if job is not None:
            ws, ok = self.workspace(self._sender), code == 0 and not self._stopped
            self.guard("job ended", lambda: ws.ended(job, ok))()
        title = job.title if job is not None else "job"
        how = (
            "stopped" if self._stopped else "done" if code == 0 else f"failed ({code}), see the log"
        )
        self._messages[self._sender] = f"{title}: {how}"
        self.changed()

    def undo(self) -> None:
        self._history("undo")

    def redo(self) -> None:
        self._history("redo")

    def _history(self, which: str) -> None:
        doc = self.active.document
        if doc is None:
            return
        can = doc.can_undo() if which == "undo" else doc.can_redo()
        if not can:
            self.message = f"nothing to {which}"
        else:
            if which == "undo":
                doc.undo()
            else:
                doc.redo()
            with self.gl_current():  # a workspace may rebuild GL objects for the change
                self.active.refresh()
            self.findings.stale()
            self.message = which
        self.changed()

    def ensure_gl(self, ws: Workspace) -> Viewport | None:
        """Attaches to the window's GL context and sets `ws` up; call inside a frame."""
        vp = ws.viewport
        if vp is not None or self.error:
            return vp
        try:
            if self.ctx is None:
                self.ctx = attached()
            self.renderer = describe(self.ctx)
            return ws.setup(self.ctx)
        except ContextError as e:
            self.error = str(e)
            return None

    def close(self) -> None:
        """Releases every workspace's GL objects while the context still exists; once."""
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        for w in self.workspaces:
            w.close()
        self.ctx = None
