# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Test doubles for the window: imgui without a display, a workspace, and a real-window smoke.

`fake_imgui()` swaps a stand-in for `imgui_bundle` into `sys.modules`, so panel code that
imports imgui per call runs headless: every widget reports "nothing happened" unless a test
scripted a click, a key chord or a drag, and every string a panel shows is recorded. Its
`hello_imgui.run` plays frames through the runner's callbacks and the current layout's windows.
"""

from __future__ import annotations

import sys
import traceback
import types
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu_studio.shell.document import History
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.workspace import Panel, Workspace

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.app import Studio
    from mhfu_studio.shell.viewport import Viewport

MODULES = ("imgui", "hello_imgui", "imguizmo", "portable_file_dialogs")


class Sym:
    """An enum member stand-in: attributes chain, `|` joins names, `.value` is 0."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str) -> Sym:
        if attr.startswith("__"):
            raise AttributeError(attr)
        return Sym(f"{self.name}.{attr}")

    @property
    def value(self) -> int:
        return 0

    def __or__(self, other: object) -> Sym:
        return Sym(f"{self}|{other}")

    def __ror__(self, other: object) -> Sym:
        return Sym(f"{other}|{self}")

    def __int__(self) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Sym) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __str__(self) -> str:
        return self.name


class Vec:
    def __init__(self, *v: float) -> None:
        padded = [*v, 0.0, 0.0, 0.0, 0.0]
        self.x, self.y, self.z, self.w = (float(c) for c in padded[:4])


@dataclass
class FakeIo:
    delta_time: float = 1.0 / 60.0
    mouse_wheel: float = 0.0
    mouse_pos: Vec = field(default_factory=lambda: Vec(-1.0, -1.0))
    display_size: Vec = field(default_factory=lambda: Vec(1000.0, 700.0))
    display_framebuffer_scale: Vec = field(default_factory=lambda: Vec(1.0, 1.0))
    key_ctrl: bool = False
    key_shift: bool = False
    key_alt: bool = False
    key_super: bool = False
    want_text_input: bool = False


def _num(*vs: object) -> None:
    for v in vs:
        assert isinstance(v, int | float) and not isinstance(v, bool), v


def _int(*vs: object) -> None:
    for v in vs:
        assert isinstance(v, int) and not isinstance(v, bool), v


class FakeDrawList:
    """Checks the binding's argument order (`add_rect` is rounding, thickness, flags)."""

    def __init__(self, owner: FakeImgui) -> None:
        self.owner = owner

    def add_rect(
        self,
        a: Any,
        b: Any,
        col: int,
        rounding: float = 0.0,
        thickness: float = 1.0,
        flags: int = 0,
    ) -> None:
        _num(rounding, thickness)
        _int(col, flags)

    def add_rect_filled(
        self, a: Any, b: Any, col: int, rounding: float = 0.0, flags: int = 0
    ) -> None:
        _num(rounding)
        _int(col, flags)

    def add_line(self, a: Any, b: Any, col: int, thickness: float = 1.0) -> None:
        _num(thickness)
        _int(col)

    def add_text(self, pos: Any, col: int, text: str, text_end: str | None = None) -> None:
        _int(col)
        assert isinstance(text, str), text
        self.owner.drawn.append(text)

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *a, **k: None


class FakeImgui:
    """`imgui`: unpressed buttons, unchanged widgets, open menus and tables."""

    ImVec2 = Vec
    ImVec4 = Vec

    def __init__(self) -> None:
        self.io = FakeIo()
        #: labels whose next button/menu item/selectable/checkbox reports a click
        self.clicks: list[str] = []
        #: key chords (`chord("mod_ctrl", "z")`) whose next `shortcut` fires
        self.chords: list[str] = []
        #: mouse button -> drag delta reported while the item is active
        self.drags: dict[int, tuple[float, float]] = {}
        self.hovered = False
        self.active = False
        self.menus_open = True
        #: every string a widget showed this test, and every overlay text
        self.shown: list[str] = []
        self.drawn: list[str] = []
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name[:1].isupper():
            return Sym(name)
        self.calls.append(name)

        def call(*a: Any, **k: Any) -> bool:
            self._show(*a)
            return False

        return call

    # -- scripting ------------------------------------------------------------------- #
    def click(self, *labels: str) -> None:
        self.clicks += labels

    def press(self, *keys: str) -> None:
        """Queues one chord: `press("mod_ctrl", "z")`."""
        self.chords.append(chord(*keys))

    def _clicked(self, label: str) -> bool:
        self._show(label)
        visible = label.split("##")[0]
        for want in (label, visible):
            if want in self.clicks:
                self.clicks.remove(want)
                return True
        return False

    def _show(self, *a: Any) -> None:
        self.shown += [s for s in a if isinstance(s, str)]

    # -- widgets --------------------------------------------------------------------- #
    def ImTextureRef(self, tex_id: int) -> int:  # noqa: N802
        _int(tex_id)
        return tex_id

    def button(self, label: str, size: Any = None) -> bool:
        return self._clicked(label)

    def small_button(self, label: str) -> bool:
        return self._clicked(label)

    def invisible_button(self, label: str, size: Any, flags: int = 0) -> bool:
        return self._clicked(label)

    def menu_item_simple(
        self, label: str, shortcut: str | None = None, selected: bool = False, enabled: bool = True
    ) -> bool:
        return self._clicked(label) and enabled

    def menu_item(
        self, label: str, shortcut: str, selected: bool, enabled: bool = True
    ) -> tuple[bool, bool]:
        hit = self._clicked(label) and enabled
        return hit, (not selected) if hit else selected

    def selectable(self, label: str, selected: bool, *a: Any) -> tuple[bool, bool]:
        hit = self._clicked(label)
        return hit, selected or hit

    def checkbox(self, label: str, v: bool) -> tuple[bool, bool]:
        hit = self._clicked(label)
        return hit, (not v) if hit else v

    def radio_button(self, label: str, active: bool) -> bool:
        return self._clicked(label)

    def begin_menu(self, label: str, enabled: bool = True) -> bool:
        self._show(label)
        return self.menus_open and enabled

    def begin_tab_item(self, label: str, *a: Any) -> tuple[bool, bool]:
        self._show(label)
        return True, True

    def collapsing_header(self, label: str, *a: Any) -> bool:
        self._show(label)
        return True

    def begin_table(self, *a: Any) -> bool:
        return True

    def begin_child(self, *a: Any) -> bool:
        return True

    def begin_tab_bar(self, *a: Any) -> bool:
        return True

    def begin_combo(self, *a: Any) -> bool:
        return False

    def begin_popup(self, *a: Any) -> bool:
        return False

    def begin_popup_modal(self, *a: Any) -> tuple[bool, bool]:
        return False, False

    def tree_node(self, *a: Any) -> bool:
        return False

    def _unchanged(self, label: str, value: Any, *a: Any, **k: Any) -> tuple[bool, Any]:
        self._show(label)
        return False, value

    combo = input_int = input_int2 = input_int3 = input_int4 = input_text = _unchanged
    input_float = input_float2 = input_float3 = input_float4 = _unchanged
    drag_float = drag_float3 = drag_int = slider_float = slider_int = _unchanged
    color_edit3 = color_edit4 = _unchanged

    def shortcut(self, key_chord: Any, flags: Any = 0) -> bool:
        if str(key_chord) in self.chords:
            self.chords.remove(str(key_chord))
            return True
        return False

    def is_key_pressed(self, key: Any, *a: Any) -> bool:
        return self.shortcut(key)

    # -- state ------------------------------------------------------------------------ #
    def get_io(self) -> FakeIo:
        return self.io

    def get_content_region_avail(self) -> Vec:
        return Vec(640.0, 400.0)

    def get_cursor_screen_pos(self) -> Vec:
        return Vec(0.0, 0.0)

    def get_window_draw_list(self) -> FakeDrawList:
        return FakeDrawList(self)

    def get_color_u32(self, *a: Any) -> int:
        return 0

    def calc_text_size(self, text: str, *a: Any) -> Vec:
        return Vec(7.0 * len(text), 14.0)

    def get_frame_height(self) -> float:
        return 20.0

    def get_text_line_height(self) -> float:
        return 14.0

    def is_item_hovered(self, *a: Any) -> bool:
        return self.hovered

    def is_item_active(self, *a: Any) -> bool:
        return self.active

    def is_mouse_dragging(self, button: int, *a: Any) -> bool:
        return button in self.drags

    def get_mouse_drag_delta(self, button: int = 0, *a: Any) -> Vec:
        return Vec(*self.drags.get(button, (0.0, 0.0)))

    def text(self, s: str) -> None:
        self._show(s)

    def text_wrapped(self, s: str) -> None:
        self._show(s)

    def text_disabled(self, s: str) -> None:
        self._show(s)

    def text_colored(self, col: Any, s: str) -> None:
        self._show(s)


def chord(*keys: str) -> str:
    """The name `imgui.Key.mod_ctrl | imgui.Key.z` has under the fake."""
    return "|".join(f"Key.{k}" for k in keys)


class FakeHelloImgui:
    """`hello_imgui`: plain objects for the params, and a `run` that plays `frames` frames."""

    IniFolderType = Sym("IniFolderType")
    DefaultImGuiWindowType = Sym("DefaultImGuiWindowType")

    def __init__(self) -> None:
        self.params: Any = self.RunnerParams()
        self.layout = ""
        self.frames = 3
        #: called with the frame number before each frame
        self.before_frame: Callable[[int], None] | None = None
        self.ran = 0

    @staticmethod
    def RunnerParams() -> Any:  # noqa: N802
        ns = types.SimpleNamespace
        return ns(
            app_window_params=ns(window_geometry=ns(size=(0, 0))),
            imgui_window_params=ns(),
            callbacks=ns(
                post_init=None,
                before_exit=None,
                show_gui=None,
                show_menus=None,
                show_status=None,
                before_swap=None,
            ),
            fps_idling=ns(enable_idling=True, fps_idle=9.0, time_active_after_last_event=3.0),
            docking_params=None,
            alternative_docking_layouts=[],
            app_shall_exit=False,
        )

    @staticmethod
    def DockingParams() -> Any:  # noqa: N802
        return types.SimpleNamespace(layout_name="", docking_splits=[], dockable_windows=[])

    @staticmethod
    def DockingSplit() -> Any:  # noqa: N802
        return types.SimpleNamespace()

    @staticmethod
    def DockableWindow() -> Any:  # noqa: N802
        return types.SimpleNamespace(imgui_window_flags=0)

    def get_runner_params(self) -> Any:
        return self.params

    def switch_layout(self, name: str) -> None:
        self.layout = name

    def current_layout_name(self) -> str:
        return self.layout

    def layouts(self) -> list[Any]:
        return [self.params.docking_params, *self.params.alternative_docking_layouts]

    def run(self, params: Any) -> None:
        self.params = params
        self.layout = self.layout or params.docking_params.layout_name
        cb = params.callbacks
        if cb.post_init:
            cb.post_init()
        for i in range(self.frames):
            if self.before_frame:
                self.before_frame(i)
            for fn in (cb.show_menus, cb.show_gui):
                if fn:
                    fn()
            layout = next(d for d in self.layouts() if d.layout_name == self.layout)
            for w in layout.dockable_windows:
                if w.is_visible:
                    w.gui_function()
            if cb.show_status:
                cb.show_status()
            self.ran += 1
            if params.app_shall_exit:
                break
        if cb.before_exit:
            cb.before_exit()


class FakeGuizmo:
    """`imguizmo`: `im_guizmo` answers not hovered, not dragging, unless told otherwise."""

    def __init__(self) -> None:
        self.over = False
        self.using = False
        outer = self

        class Matrix16:
            def __init__(self, values: Sequence[float] = ()) -> None:
                self.values = list(values)

        class ImGuizmo:
            OPERATION = Sym("OPERATION")
            MODE = Sym("MODE")

            def __init__(self) -> None:
                self.Matrix16 = Matrix16
                self.Matrix3 = Matrix16

            def is_over(self) -> bool:
                return outer.over

            def is_using(self) -> bool:
                return outer.using

            def __getattr__(self, name: str) -> Callable[..., bool]:
                return lambda *a, **k: False

        self.im_guizmo = ImGuizmo()


class FakeDialogs:
    """`portable_file_dialogs`: every dialog is ready at once with `answer`."""

    def __init__(self) -> None:
        self.answer: list[str] = []
        self.asked: list[tuple[str, str]] = []

    def _dialog(self, kind: str, title: str, many: bool) -> Any:
        self.asked.append((kind, title))
        got = list(self.answer)
        return types.SimpleNamespace(
            ready=lambda timeout=0: True,
            result=lambda: got if many else (got[0] if got else ""),
        )

    def open_file(self, title: str, *a: Any, **k: Any) -> Any:
        return self._dialog("open", title, True)

    def save_file(self, title: str, *a: Any, **k: Any) -> Any:
        return self._dialog("save", title, False)

    def select_folder(self, title: str, *a: Any, **k: Any) -> Any:
        return self._dialog("folder", title, False)


@dataclass
class FakeBundle:
    imgui: FakeImgui
    hello_imgui: FakeHelloImgui
    imguizmo: FakeGuizmo
    portable_file_dialogs: FakeDialogs


@contextmanager
def fake_imgui() -> Iterator[FakeBundle]:
    """`imgui_bundle` replaced in `sys.modules` for the duration; restored afterwards."""
    fake = FakeBundle(FakeImgui(), FakeHelloImgui(), FakeGuizmo(), FakeDialogs())
    bundle = types.ModuleType("imgui_bundle")
    names = ["imgui_bundle", *(f"imgui_bundle.{m}" for m in MODULES)]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["imgui_bundle"] = bundle
    for m in MODULES:
        setattr(bundle, m, getattr(fake, m))
        sys.modules[f"imgui_bundle.{m}"] = getattr(fake, m)
    try:
        yield fake
    finally:
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod


class FakeDocument:
    """A `Document` over a list of strings, with `History` undo and settable findings."""

    def __init__(self, path: Path | None = None, value: Sequence[str] = ()) -> None:
        self.path = path
        self.history: History[list[str]] = History(list(value))
        self.found: list[Finding] = []
        self.checks = 0
        self.saved_to: list[Path] = []

    @property
    def dirty(self) -> bool:
        return self.history.dirty

    def edit(self, item: str) -> None:
        self.history.edit(lambda v: v.append(item))

    def save(self, path: Path | None = None) -> Path:
        where = path or self.path
        if where is None:
            raise ValueError("no path to save to")
        self.path = where
        self.saved_to.append(where)
        self.history.saved()
        return where

    def findings(self) -> list[Finding]:
        self.checks += 1
        return list(self.found)

    def can_undo(self) -> bool:
        return self.history.can_undo()

    def can_redo(self) -> bool:
        return self.history.can_redo()

    def undo(self) -> None:
        self.history.undo()

    def redo(self) -> None:
        self.history.redo()


class FakeWorkspace(Workspace):
    """Opens files with `suffix` as a `FakeDocument`; its viewport is `make_viewport(ctx)`."""

    def __init__(
        self,
        name: str,
        suffix: str = ".txt",
        make_viewport: Callable[[moderngl.Context], Viewport] | None = None,
    ) -> None:
        self.name = name
        self.suffix = suffix
        self.filters = (f"{name} files", f"*{suffix}")
        self.make_viewport = make_viewport
        self.doc: FakeDocument | None = None
        self.vp: Viewport | None = None
        self.log: list[tuple[str, object]] = []
        self.panel_frames = 0

    @property
    def document(self) -> FakeDocument | None:
        return self.doc

    @property
    def viewport(self) -> Viewport | None:
        return self.vp

    def can_open(self, path: Path) -> bool:
        return path.suffix == self.suffix

    def open(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        self.doc = FakeDocument(path, path.read_text().split())
        self.log.append(("open", path))

    def setup(self, ctx: moderngl.Context) -> Viewport:
        if self.make_viewport is None:
            from mhfu_studio.harness.selftest import Selftest

            self.make_viewport = Selftest
        self.vp = self.make_viewport(ctx)
        return self.vp

    def panels(self) -> Sequence[Panel]:
        return [Panel("Items", "Left", self._items)]

    def _items(self) -> None:
        from imgui_bundle import imgui

        self.panel_frames += 1
        for item in [] if self.doc is None else self.doc.history.value:
            imgui.text(item)

    def status(self) -> str:
        return f"{0 if self.doc is None else len(self.doc.history.value)} items"

    def reveal(self, target: Hashable) -> None:
        self.log.append(("reveal", target))

    def refresh(self) -> None:
        self.log.append(("refresh", None))

    def close(self) -> None:
        if self.vp is not None:
            self.vp.release()
            self.vp = None
        self.log.append(("close", None))


@dataclass
class Smoke:
    """What a real-window run did; `lit` counts back-buffer pixels that are not black."""

    frames: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    screen: tuple[int, int] | None = None
    lit: int = 0


def run_window(
    studio: Studio,
    frames: int = 16,
    step: Callable[[int], None] | None = None,
    out: Path | None = None,
) -> Smoke:
    """Opens the real window, plays `frames` frames calling `step(i)`, samples the back buffer.

    It reads what would be presented, not the offscreen target: the target was right all
    along the time a bound framebuffer left the window black. Needs a display; on macOS the
    main thread of a foreground process.
    """
    import numpy as np
    from imgui_bundle import hello_imgui, imgui

    got = Smoke()
    params = studio.runner_params()
    frame = params.callbacks.show_gui

    def counted() -> None:
        frame()
        if step is not None:
            try:
                step(got.frames)
            except Exception:
                got.errors.append(("step", traceback.format_exc()))
                params.app_shall_exit = True
        got.frames += 1
        if got.frames >= frames:
            params.app_shall_exit = True

    def before_swap() -> None:
        if got.screen is not None or studio.ctx is None or got.frames < frames - 1:
            return
        screen = studio.ctx.screen
        io = imgui.get_io()
        scale = max(io.display_framebuffer_scale.x, 1.0)
        w, h = int(io.display_size.x * scale), int(io.display_size.y * scale)
        if screen is None or w < 1 or h < 1:
            return
        raw = screen.read(viewport=(0, 0, w, h), components=3, alignment=1)
        img = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)[::-1]
        got.screen, got.lit = (w, h), int((img.max(axis=2) > 24).sum())
        if out is not None:
            from mhfu_studio.shell.target import write_png

            alpha = np.full((h, w, 1), 255, np.uint8)
            write_png(out, np.concatenate([img, alpha], axis=2))

    params.callbacks.show_gui = counted
    params.callbacks.before_swap = before_swap
    try:
        hello_imgui.run(params)
    finally:
        studio.close()
    got.errors += studio.errors
    return got
