# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Game panel: push the loaded section's edits into the running PPSSPP.

It runs `studio map inject` as a subprocess and tails its output from a thread, so the window
stays responsive while a catch waits for the area reload.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import help_marker, plain

from ...stage.live import CLIMB, QUEST_CATCH

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

WARN = (1.0, 0.8, 0.3, 1.0)
SHOWN = 200
#: the studio's own command line, run by the interpreter running the window
MAIN = "import sys; from mhfu_studio.cli import main; sys.exit(main())"


def climbs(ops: list[dict[str, object]]) -> bool:
    """A collision op setting a climbable material: read at area load, so the push holds."""
    for o in ops:
        flags = o.get("flags")
        if o.get("op") == "collision" and isinstance(flags, dict):
            if flags.get("material") in CLIMB:
                return True
    return False


class GamePanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.proc: subprocess.Popen[str] | None = None
        self.lines: list[str] = []
        self.what = ""
        #: seconds the mesh catch waits; 0 in the village (row 0), whose PAC is never re-read
        self.catch = QUEST_CATCH
        self._stage: int | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def command(self, flags: list[str]) -> list[str]:
        ws = self.ws
        assert ws.doc.directory is not None and ws.scene is not None
        cmd = [sys.executable, "-c", MAIN, "map", "inject", str(ws.doc.directory)]
        cmd += ["--stage", str(ws.scene.stage), "--catch", str(self.catch)]
        if ws.game is not None:
            cmd += ["--data", str(ws.game.root)]
        cmd += flags
        if "--collision" in flags and ws.session is not None and climbs(ws.session.ops):
            cmd += ["--hold", str(self.catch)]
        return cmd

    def run(self, flags: list[str]) -> None:
        ws = self.ws
        if ws.scene is None or ws.session is None:
            return
        try:
            ws.doc.save()
        except (ValueError, OSError) as e:
            self.lines.append(f"not saved: {e}")
            return
        cmd = self.command(flags)
        self.what = "studio " + " ".join(cmd[3:])
        self.lines.append("$ " + self.what)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
        except OSError as e:
            self.lines.append(f"could not start: {e}")
            return
        self.proc = proc

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.lines.append(line.rstrip())
            self.lines.append(f"[exit {proc.wait()}]")

        threading.Thread(target=pump, daemon=True).start()

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        stage = ws.scene.stage if ws.scene else None
        if stage != self._stage:  # a section loaded: the default catch follows its row
            self._stage = stage
            self.catch = 0.0 if ws.row == 0 else QUEST_CATCH
        imgui.text_wrapped(
            "Push the loaded section's edits into the running PPSSPP through the debugger. Stand"
            " in the section first. Mesh edits land on the next area reload: walk out and back"
            " in while the catch is armed. Collision and textures land at once, except a"
            " CLIMBABLE material, which the game reads at area load: the push then holds for the"
            " same seconds; walk out and in once more."
        )
        if ws.doc.directory is None:
            imgui.text_colored(imgui.ImVec4(*WARN), "save the document first")
        imgui.set_next_item_width(90)
        _, self.catch = imgui.input_float("catch seconds", self.catch, 0, 0, "%.0f")
        imgui.same_line()
        help_marker(
            "The mesh catch is a PPSSPP write breakpoint that waits for the area reload. The game"
            " runs SLOWER while it is armed (until the reload is caught or it times out). A quest"
            " area re-reads its PAC on entry and needs it; the village never does, so there it"
            " is 0 and the edit shows after an interior round trip."
        )
        if ws.row == 0 and self.catch > 0:
            imgui.text_colored(
                imgui.ImVec4(*WARN),
                "village section: set catch to 0: the PAC is never re-read and an armed catch"
                " only slows the game",
            )
        imgui.begin_disabled(self.running or ws.doc.directory is None or ws.session is None)
        for label, flags in (
            ("push all", ["--mesh", "--collision", "--textures"]),
            ("mesh (catch)", ["--mesh"]),
            ("collision", ["--collision"]),
            ("textures", ["--textures"]),
            ("restore", ["--restore"]),
        ):
            if imgui.button(label):
                self.run(flags)
            imgui.same_line()
        imgui.end_disabled()
        if self.running and imgui.button("stop") and self.proc is not None:
            self.proc.terminate()
        imgui.same_line()
        if imgui.small_button("clear log"):
            self.lines = []
        imgui.text_disabled(self.what + ("  (running...)" if self.running else ""))
        imgui.begin_child("gamelog", imgui.ImVec2(0, 0), True)
        for line in self.lines[-SHOWN:]:
            imgui.text_unformatted(plain(line))
        if self.running:
            imgui.set_scroll_here_y(1.0)
        imgui.end_child()
