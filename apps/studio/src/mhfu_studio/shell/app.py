# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The window: one hello_imgui runner, a docking layout per workspace, menus, findings.

hello_imgui owns the window and moderngl only attaches to its context (moderngl-window would
load a second libglfw, which macOS rejects). Workspaces are switched as hello_imgui layouts, so
each keeps its own docking arrangement in the ini.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu_studio.shell import studio as core
from mhfu_studio.shell.findings import LEVELS
from mhfu_studio.shell.widgets import (
    LEVEL_COLORS,
    camera_line,
    mouse_buttons,
    no_scroll_flags,
    overlay_text,
    plain,
)
from mhfu_studio.shell.workspace import MAIN, Gesture, View, Workspace

if TYPE_CHECKING:
    from imgui_bundle import hello_imgui

    from mhfu_studio.shell.camera import OrbitCamera
    from mhfu_studio.shell.viewport import Viewport

INI_NAME = "mhfu_studio"
VIEWPORT = "Viewport"
FINDINGS = "Findings"
#: menu labels for the shortcuts; imgui's Ctrl is Cmd on macOS
MOD = "Cmd" if sys.platform == "darwin" else "Ctrl"


class IdleHold:
    """Holds hello_imgui's fps idling off while something animates, then gives it back.

    Idling drops to 9 fps three seconds after the last input, which is right for a still
    picture and wrong for playback; the user's own setting is borrowed, not overwritten.
    """

    def __init__(self) -> None:
        self.pref: bool | None = None

    def sync(self, idling: Any, animating: bool) -> None:
        if animating:
            if self.pref is None:
                self.pref = bool(idling.enable_idling)
            idling.enable_idling = False
        elif self.pref is not None:
            idling.enable_idling = self.pref
            self.pref = None


class Studio(core.Studio):
    """The imgui window around the controller; `run` blocks until the window closes."""

    def __init__(
        self,
        workspaces: Sequence[Workspace],
        *,
        size: tuple[int, int] = (1500, 940),
        title: str = "MHFU Studio",
        ini_folder: Path | None = None,
    ) -> None:
        super().__init__(workspaces, title=title)
        self.size = size
        #: None: the per-user config folder; a test passes a temp folder
        self.ini_folder = ini_folder
        self.idle = IdleHold()
        self._opened = False
        self._live = False
        self._dialog: tuple[str, Any] | None = None

    def switch(self, name: str) -> None:
        super().switch(name)
        if self._running():
            from imgui_bundle import hello_imgui

            hello_imgui.switch_layout(name)

    def open(self, path: Path | str) -> bool:
        ok = super().open(path)
        self._opened = self._opened or ok
        return ok

    def close(self) -> None:
        self._live = False
        super().close()

    def _ensure_gl(self, ws: Workspace) -> Viewport | None:
        return self.ensure_gl(ws)

    # ---- the runner ---------------------------------------------------------------- #
    def runner_params(self) -> hello_imgui.RunnerParams:
        from imgui_bundle import hello_imgui

        p = hello_imgui.RunnerParams()
        p.app_window_params.window_title = self.title
        p.app_window_params.window_geometry.size = self.size
        p.app_window_params.restore_previous_geometry = True
        if self.ini_folder is None:  # the default would drop an ini wherever it was started
            p.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
            p.ini_filename = f"{INI_NAME}.ini"
        else:
            p.ini_folder_type = hello_imgui.IniFolderType.absolute_path
            p.ini_filename = str(Path(self.ini_folder) / f"{INI_NAME}.ini")
        wp = p.imgui_window_params
        wp.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
        )
        wp.enable_viewports = False
        wp.show_menu_bar = True
        wp.show_status_bar = True
        p.callbacks.show_menus = self.guard("menus", self._menus)
        p.callbacks.show_status = self.guard("status", self._status)
        p.callbacks.show_gui = self.guard("frame", self._frame)
        p.callbacks.post_init = self._post_init
        p.callbacks.before_exit = self.close
        layouts = [self.docking(w) for w in self.workspaces]
        p.docking_params = layouts[0]
        p.alternative_docking_layouts = layouts[1:]
        p.remember_selected_alternative_layout = True
        return p

    def docking(self, ws: Workspace) -> hello_imgui.DockingParams:
        """The layout of one workspace: its splits and panels around the shell's windows."""
        from imgui_bundle import hello_imgui, imgui

        splits = []
        for s in ws.layout():
            sp = hello_imgui.DockingSplit()
            sp.initial_dock, sp.new_dock = s.parent, s.name
            sp.direction = getattr(imgui.Dir, s.side)
            sp.ratio = s.ratio
            splits.append(sp)
        docks = {MAIN} | {s.name for s in ws.layout()}

        def window(label: str, dock: str, fn: Callable[[], None], focus: bool, scroll: bool) -> Any:
            w = hello_imgui.DockableWindow()
            w.label = label
            w.dock_space_name = dock if dock in docks else MAIN
            w.gui_function = self.guard(label, fn)
            w.is_visible = True
            w.focus_window_at_next_frame = focus
            if not scroll:
                w.imgui_window_flags = no_scroll_flags()
            return w

        bottom = "Bottom" if "Bottom" in docks else MAIN
        windows = [
            window(VIEWPORT, MAIN, self._viewport_window, True, False),
            window(FINDINGS, bottom, self._findings_window, False, True),
        ]
        windows += [window(p.label, p.dock, p.draw, p.focus, p.scroll) for p in ws.panels()]
        d = hello_imgui.DockingParams()
        d.layout_name = ws.name
        d.docking_splits = splits
        d.dockable_windows = windows
        return d

    def run(self) -> None:
        from imgui_bundle import hello_imgui

        try:
            hello_imgui.run(self.runner_params())
        finally:
            self.close()

    def _running(self) -> bool:
        return self._live

    def _post_init(self) -> None:
        from imgui_bundle import hello_imgui

        self._live = True
        current = hello_imgui.current_layout_name()
        if self._opened and current != self.active.name:
            hello_imgui.switch_layout(self.active.name)
        elif current in self.names:
            self.active = self.workspace(current)

    # ---- per frame ------------------------------------------------------------------ #
    def _frame(self) -> None:
        """Every frame: follow a layout switched from the View menu, keys, open dialogs."""
        from imgui_bundle import hello_imgui

        current = hello_imgui.current_layout_name()
        if current in self.names and current != self.active.name:
            self.active = self.workspace(current)
            self.findings.stale()
        self._keys()
        self._poll_dialog()

    def _keys(self) -> None:
        from imgui_bundle import imgui

        k, mod = imgui.Key, imgui.Key.mod_ctrl  # Cmd on macOS
        flags = imgui.InputFlags_.route_global  # a focused text field keeps its own undo
        if imgui.shortcut(mod | imgui.Key.mod_shift | k.z, flags) or imgui.shortcut(
            mod | k.y, flags
        ):
            self.redo()
        elif imgui.shortcut(mod | k.z, flags):
            self.undo()
        if imgui.shortcut(mod | k.s, flags):
            self.save()
        if imgui.shortcut(mod | k.o, flags):
            self.ask_open()

    def ask_open(self) -> None:
        from imgui_bundle import portable_file_dialogs as pfd

        filters: list[str] = []
        for w in self.workspaces:
            filters += [f for f in w.filters]
        if "*" not in filters[1::2]:
            filters += ["All files", "*"]
        self._dialog = ("open", pfd.open_file("Open", "", filters))

    def ask_save_as(self) -> None:
        from imgui_bundle import portable_file_dialogs as pfd

        doc = self.active.document
        if doc is None:
            return
        start = str(doc.path or "")
        self._dialog = ("save", pfd.save_file("Save as", start, list(self.active.filters)))

    def _poll_dialog(self) -> None:
        """A native dialog runs beside the window; its answer is picked up when it is ready."""
        if self._dialog is None:
            return
        kind, dialog = self._dialog
        if not dialog.ready(0):
            return
        self._dialog = None
        got = dialog.result()
        if kind == "open" and got:
            self.open(got[0])
        elif kind == "save" and got:
            self.save(got)

    def _menus(self) -> None:
        from imgui_bundle import imgui

        doc = self.active.document
        if imgui.begin_menu("File"):
            if imgui.menu_item_simple("Open...", f"{MOD}+O"):
                self.ask_open()
            if imgui.menu_item_simple("Save", f"{MOD}+S", enabled=doc is not None):
                self.save()
            if imgui.menu_item_simple("Save As...", enabled=doc is not None):
                self.ask_save_as()
            imgui.end_menu()
        if imgui.begin_menu("Edit"):
            undo = doc is not None and doc.can_undo()
            if imgui.menu_item_simple("Undo", f"{MOD}+Z", enabled=undo):
                self.undo()
            redo = doc is not None and doc.can_redo()
            if imgui.menu_item_simple("Redo", f"{MOD}+Shift+Z", enabled=redo):
                self.redo()
            imgui.end_menu()
        if imgui.begin_menu("Workspace"):
            for w in self.workspaces:
                if imgui.menu_item_simple(w.name, selected=w is self.active):
                    self.switch(w.name)
            imgui.end_menu()

    def _status(self) -> None:
        from imgui_bundle import imgui

        ws, doc = self.active, self.active.document
        name = "" if doc is None or doc.path is None else doc.path.name
        dirty = "*" if doc is not None and doc.dirty else ""
        parts = [f"{ws.name} {name}{dirty}".strip(), plain(ws.status()), plain(self.message)]
        parts.append(self.renderer or "attaching to the GL context...")
        imgui.text("   ".join(p for p in parts if p))

    def _viewport_window(self) -> None:
        from imgui_bundle import hello_imgui, imgui, imguizmo

        if self.error:
            imgui.text_wrapped(self.error)
            return
        ws = self.active
        vp = self._ensure_gl(ws)
        if vp is None:
            return
        imguizmo.im_guizmo.begin_frame()
        io = imgui.get_io()
        self.idle.sync(hello_imgui.get_runner_params().fps_idling, ws.animating())
        ws.frame(io.delta_time)
        ws.toolbar()
        avail = imgui.get_content_region_avail()
        w, h = int(max(avail.x, 1)), int(max(avail.y, 1))
        # imgui's scale, not ctx.screen.size: moderngl reports twice the real back buffer
        scale = float(max(io.display_framebuffer_scale.x, 1.0))
        vp.resize((int(w * scale), int(h * scale)))
        vp.draw()
        pos = imgui.get_cursor_screen_pos()
        size = imgui.ImVec2(float(w), float(h))
        uv0, uv1 = vp.target.uv
        texture = imgui.ImTextureRef(vp.target.gl_texture_id)
        imgui.image(texture, size, imgui.ImVec2(*uv0), imgui.ImVec2(*uv1))
        # an image is not an item that can be active: a button over it captures the drag,
        # unless an overlay (a gizmo) wants the press
        if ws.wants_mouse():
            mx, my = io.mouse_pos.x, io.mouse_pos.y
            hovered = pos.x <= mx < pos.x + w and pos.y <= my < pos.y + h
            active = False
        else:
            imgui.set_cursor_screen_pos(pos)
            imgui.invisible_button("##viewport", size, mouse_buttons())
            hovered, active = imgui.is_item_hovered(), imgui.is_item_active()
        view = View((pos.x, pos.y), (w, h), hovered, active, scale)
        camera_input(vp.camera, view, ws.input(view))
        ws.overlay(view)
        overlay_text(view.origin, "\n".join(t for t in (ws.hud(), camera_line(vp.camera)) if t))

    def _findings_window(self) -> None:
        from imgui_bundle import imgui

        doc = self.active.document
        if doc is None:
            imgui.text_disabled("no document open")
            return
        if imgui.button("Check"):
            self.findings.stale()
        imgui.same_line()
        _, self.findings.auto = imgui.checkbox("auto", self.findings.auto)
        found = self.findings.get(doc)
        imgui.same_line()
        counts = [f"{sum(f.level == lv for f in found)} {lv}" for lv in LEVELS]
        imgui.text_disabled(", ".join(counts))
        for i, f in enumerate(found):
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*LEVEL_COLORS[f.level]))
            clicked, _ = imgui.selectable(f"{plain(str(f))}##finding{i}", False)
            imgui.pop_style_color()
            if clicked and f.target is not None:
                self.active.reveal(f.target)


DRAG_ORBIT, DRAG_PAN, DRAG_PAN_ALT = 0, 1, 2


def camera_input(cam: OrbitCamera, view: View, consumed: Gesture) -> None:
    """Left-drag orbits, right or middle-drag pans, the wheel dollies, except what was consumed."""
    from imgui_bundle import imgui

    if view.active:
        if Gesture.ORBIT not in consumed and imgui.is_mouse_dragging(DRAG_ORBIT):
            d = imgui.get_mouse_drag_delta(DRAG_ORBIT)
            cam.orbit(d.x, d.y)
            imgui.reset_mouse_drag_delta(DRAG_ORBIT)
        if Gesture.PAN not in consumed:
            for btn in (DRAG_PAN, DRAG_PAN_ALT):
                if imgui.is_mouse_dragging(btn):
                    d = imgui.get_mouse_drag_delta(btn)
                    cam.pan(d.x, d.y, view.size[1])
                    imgui.reset_mouse_drag_delta(btn)
    wheel = imgui.get_io().mouse_wheel
    if view.hovered and wheel and Gesture.DOLLY not in consumed:
        cam.dolly(wheel)
