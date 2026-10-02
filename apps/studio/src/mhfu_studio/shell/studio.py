# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The documents and actions behind the window, for any toolkit to drive.

Every action ends in `changed()`; a window listens once and re-reads what it shows, so no
control has to know which others depend on what it did. Questions (drop unsaved edits? where to
save?) go through hooks the window installs; without them the studio goes on unasked.
"""

from __future__ import annotations

import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from mhfu_studio.shell.context import ContextError, attached, describe
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.workspace import Job, Workspace, pick

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
        #: where to save a document that has no file yet; None is a cancel
        self.ask_path: Callable[[Workspace], Path | None] | None = None
        #: runs a `Job` in the background (the window's); without one a job runs inline
        self.runner: Runner | None = None
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
            ws.open(path)
        except Exception as e:
            self.message = f"could not open {path.name}: {e}"
            self.changed()
            return False
        self._messages[ws.name] = f"opened {path}"
        self._heard[ws.name] = before  # what it said while opening shows instead
        self.switch(ws.name)
        return True

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
                    ws.refresh()
                self.findings.stale()
                self.message = f"saved {where}"
                ok = True
        self.changed()
        return ok

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
        done = subprocess.run(command(job), input=job.stdin, capture_output=True, check=False)
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
        job, self.job = self.job, None
        self.heard(f"[exit {code}]")
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
        for w in self.workspaces:
            w.close()
        self.ctx = None
