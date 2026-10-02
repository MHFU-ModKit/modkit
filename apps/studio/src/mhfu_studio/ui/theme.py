# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every colour the studio shows: two families, each dark and light, as tokens.

The stylesheet, the palette, the icons and the overlay inks all come from the current `Theme`;
no other module names a colour (a test holds that), so a theme switch reaches everything.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QProxyStyle,
    QStyle,
    QStyleHintReturn,
    QStyleOption,
    QWidget,
)
from shiboken6 import isValid

from mhfu_studio.shell.findings import LEVELS, Level
from mhfu_studio.shell.overlay import RGBA, Color, Ink

FAMILIES = ("Ember", "Moss")
Mode = Literal["system", "dark", "light"]
MODES: tuple[Mode, ...] = ("system", "dark", "light")


def _rgba(hex_: str, a: float = 1.0) -> RGBA:
    h = hex_.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def _inks(dark: bool, accent: str) -> dict[Ink, RGBA]:
    return {
        Ink.AXIS_X: _rgba("#ef5350" if dark else "#e53935"),
        Ink.AXIS_Y: _rgba("#66bb6a" if dark else "#43a047"),
        Ink.AXIS_Z: _rgba("#42a5f5" if dark else "#1e88e5"),
        Ink.AXIS_VIEW: _rgba("#e6e6e6" if dark else "#37474f"),
        Ink.HOT: _rgba("#ffd54f" if dark else "#d97706"),
        Ink.SELECTION: _rgba(accent),
        Ink.HOVER: _rgba(accent, 0.6),
        Ink.BOX: _rgba(accent, 0.9),
        # over the picture, whatever the theme: light text on a dark shadow
        Ink.TEXT: _rgba("#f2f2f2", 0.92),
        Ink.SHADOW: _rgba("#000000", 0.6),
        Ink.WARNING: _rgba("#fbbf24" if dark else "#d97706"),
        Ink.ERROR: _rgba("#f87171" if dark else "#dc2626"),
    }


@dataclass(frozen=True)
class Theme:
    family: str
    dark: bool
    #: the window gradient, top left to bottom right
    stops: tuple[str, str, str]
    #: a panel's fill and every hairline
    card: str
    line: str
    #: an input's fill
    entry: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    #: text on an accent fill
    on_accent: str
    #: hover and selection fills
    soft: str
    #: the second accent: the unsaved chip, highlights that are not the selection
    second: str
    danger: str
    warning: str
    #: menus, popups, tooltips: opaque
    menu: str
    #: the viewport's clear colour
    view: RGBA
    ink: dict[Ink, RGBA] = field(compare=False)

    @property
    def name(self) -> str:
        return f"{self.family} {'dark' if self.dark else 'light'}"


def _ember(dark: bool) -> Theme:
    if dark:
        return Theme(
            "Ember", True, ("#120d0c", "#26130f", "#3f1c12"),
            "rgba(255,236,224,0.05)", "rgba(255,236,224,0.10)", "rgba(10,5,3,0.45)",
            "#f4ebe4", "#b9a194", "#ff9f43", "#ffb76b", "#1f1006", "rgba(255,159,67,0.18)",
            "#f25f5c", "#f87171", "#fbbf24", "#241411", _rgba("#1a1311"),
            _inks(True, "#ff9f43"),
        )  # fmt: skip
    return Theme(
        "Ember", False, ("#fdf8f3", "#f7ebe0", "#efd9c6"),
        "rgba(255,255,255,0.72)", "rgba(90,40,10,0.13)", "#fffdfb",
        "#2b1a10", "#86695a", "#e8590c", "#f06f24", "#ffffff", "rgba(232,89,12,0.12)",
        "#c2410c", "#dc2626", "#b45309", "#fffaf5", _rgba("#9b877c"),
        _inks(False, "#e8590c"),
    )  # fmt: skip


def _moss(dark: bool) -> Theme:
    if dark:
        return Theme(
            "Moss", True, ("#091613", "#10261f", "#1e3d34"),
            "rgba(230,255,245,0.05)", "rgba(230,255,245,0.10)", "rgba(2,10,8,0.45)",
            "#e2efe9", "#8fb1a4", "#4ade80", "#7ae9a0", "#06210f", "rgba(74,222,128,0.16)",
            "#fbbf24", "#f87171", "#fbbf24", "#10241e", _rgba("#0f1a17"),
            _inks(True, "#4ade80"),
        )  # fmt: skip
    return Theme(
        "Moss", False, ("#f5faf7", "#e5f1ea", "#d2e6db"),
        "rgba(255,255,255,0.72)", "rgba(16,60,40,0.13)", "#fcfefd",
        "#11261e", "#5a786c", "#16a34a", "#22b858", "#ffffff", "rgba(22,163,74,0.12)",
        "#d97706", "#dc2626", "#b45309", "#f8fcfa", _rgba("#80948a"),
        _inks(False, "#16a34a"),
    )  # fmt: skip


_BUILD: dict[str, Callable[[bool], Theme]] = {"Ember": _ember, "Moss": _moss}


def theme(family: str, dark: bool) -> Theme:
    return _BUILD.get(family, _ember)(dark)


def system_dark() -> bool:
    hints = QGuiApplication.styleHints()
    hints.unsetColorScheme()  # forget the one `apply` set, to read the system's
    return hints.colorScheme() != Qt.ColorScheme.Light


def resolve(family: str, mode: Mode) -> Theme:
    return theme(family, system_dark() if mode == "system" else mode == "dark")


# ---- the current theme ------------------------------------------------------------------- #

_current = theme("Ember", True)
_bound: weakref.WeakKeyDictionary[QAbstractButton | QAction, tuple[str, bool]] = (
    weakref.WeakKeyDictionary()
)
_listeners: list[Callable[[Theme], None]] = []


class Style(QProxyStyle):
    """Fusion, the one style that honours every stylesheet rule the same way, without the
    mnemonic underlines it draws in menus off macOS."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    def styleHint(  # noqa: N802
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        data: QStyleHintReturn | None = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, data)


def current() -> Theme:
    return _current


def apply(t: Theme) -> None:
    """Makes `t` current: the stylesheet, the palette, the native appearance, bound icons."""
    global _current
    _current = t
    app = QApplication.instance()
    if isinstance(app, QApplication):
        if not isinstance(app.style(), Style):
            app.setStyle(Style())
        app.setPalette(palette(t))
        app.setStyleSheet(qss(t))
        QGuiApplication.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if t.dark else Qt.ColorScheme.Light  # native dialogs, title bar
        )
    for target, (name, outlined) in list(_bound.items()):
        if isValid(target):  # a deleted widget's wrapper lingers until Python collects it
            target.setIcon(icon(name, outlined=outlined))
    for fn in list(_listeners):
        fn(t)


def on_change(fn: Callable[[Theme], None]) -> None:
    _listeners.append(fn)


def icon(name: str, *, accent: bool = False, outlined: bool = False) -> QIcon:
    """A qtawesome icon in the theme's colours. Checked, it takes the on-accent colour of a
    filled button, or the accent of an `outlined` one."""
    import qtawesome as qta

    t = _current
    base = t.accent if accent else t.muted
    on = t.accent if outlined else t.on_accent
    got: QIcon = qta.icon(
        name,
        color=base,
        color_active=t.text,
        color_disabled=t.line,
        color_on=on,
        color_on_active=on,
    )
    return got


def bind(target: QAbstractButton | QAction, name: str, *, outlined: bool = False) -> None:
    """Gives `target` the icon `name` now and again after every theme change."""
    _bound[target] = (name, outlined)
    target.setIcon(icon(name, outlined=outlined))


def color(c: Color) -> QColor:
    r, g, b, a = _current.ink[c] if isinstance(c, Ink) else c
    return QColor.fromRgbF(r, g, b, a)


def level(lv: Level) -> QColor:
    return QColor(_level(_current, lv))


def _level(t: Theme, lv: Level) -> str:
    return {"error": t.danger, "warning": t.warning}.get(lv, t.muted)


def palette(t: Theme) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    solid = QColor(t.menu)
    for r, c in (
        (role.Window, solid),
        (role.WindowText, QColor(t.text)),
        (role.Base, solid),
        (role.AlternateBase, solid.darker(110) if t.dark else solid.darker(103)),
        (role.Text, QColor(t.text)),
        (role.Button, solid),
        (role.ButtonText, QColor(t.text)),
        (role.Highlight, QColor(t.accent)),
        (role.HighlightedText, QColor(t.on_accent)),
        (role.ToolTipBase, solid),
        (role.ToolTipText, QColor(t.text)),
        (role.PlaceholderText, QColor(t.muted)),
        (role.Link, QColor(t.accent)),
    ):
        p.setColor(r, c)
    p.setColor(QPalette.ColorGroup.Disabled, role.Text, QColor(t.muted))
    p.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, QColor(t.muted))
    p.setColor(QPalette.ColorGroup.Disabled, role.WindowText, QColor(t.muted))
    return p


def qss(t: Theme) -> str:
    s0, s1, s2 = t.stops
    levels = "\n".join(
        f'QLabel[level="{lv}"], #Problems[level="{lv}"] {{ color: {_level(t, lv)}; }}'
        for lv in LEVELS
    )
    return f"""
* {{ color: {t.text}; }}
QMainWindow {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 {s0}, stop:0.55 {s1}, stop:1 {s2}); }}
QMainWindow::separator {{ background: transparent; width: 8px; height: 8px; }}
QLabel[role="muted"], QLabel[role="hint"] {{ color: {t.muted}; }}
QLabel[role="title"] {{ font-weight: 600; }}
QLabel[role="dock"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="caps"] {{ color: {t.muted}; font-size: 11px; font-weight: 600; }}
QLabel[role="chip"] {{ color: {t.second}; }}
QLabel[role="mono"] {{ font-family: "Menlo", "DejaVu Sans Mono", monospace; }}
{levels}
#Card {{ background: {t.card}; border: none; border-radius: 8px; }}
#Body, QScrollArea {{ background: transparent; border: none; }}
#Seg {{ background: {t.entry}; border: 1px solid {t.line}; border-radius: 7px; }}
#Seg QToolButton {{ border: none; border-radius: 5px; padding: 3px 10px; color: {t.muted}; }}
#Seg QToolButton:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 600; }}
#Seg QToolButton:hover:!checked {{ color: {t.text}; background: {t.soft}; }}
QToolBar {{ background: transparent; border: none; spacing: 3px; padding: 0 8px 4px 8px; }}
QToolBar::separator {{ width: 1px; background: {t.line}; margin: 7px 8px; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px 8px;
    color: {t.muted}; }}
QToolButton:hover {{ background: {t.soft}; color: {t.text}; }}
QToolButton:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 600; }}
QToolButton#More {{ padding: 2px 0; }}
QToolButton#More:checked {{ background: transparent; color: {t.text}; font-weight: normal; }}
QToolButton#More:hover, QToolButton#More:focus {{ background: transparent; color: {t.accent}; }}
QToolButton[toggle="true"] {{ border: 1px solid {t.line}; padding: 3px 9px; }}
QToolButton[toggle="true"]:checked {{ background: {t.soft}; color: {t.accent};
    border-color: {t.accent}; font-weight: normal; }}
#Send {{ background: {t.accent}; color: {t.on_accent}; font-weight: 600; padding: 4px 12px; }}
#Send:hover {{ background: {t.accent_hover}; }}
#Send:disabled {{ background: {t.soft}; color: {t.muted}; }}
#Send[busy="true"] {{ background: {t.soft}; color: {t.text}; }}
QPushButton {{ background: {t.entry}; border: 1px solid {t.line}; border-radius: 6px;
    padding: 4px 10px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.soft}; }}
QPushButton:disabled {{ color: {t.muted}; background: transparent; }}
QPushButton[role="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: none;
    font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {t.accent_hover}; }}
QPushButton[role="primary"]:disabled {{ background: {t.soft}; color: {t.muted}; }}
QPushButton[role="danger"] {{ color: {t.danger}; }}
QPushButton[role="danger"]:hover {{ border-color: {t.danger}; }}
QAbstractSpinBox, QLineEdit, QComboBox, QPlainTextEdit {{ background: {t.entry};
    border: 1px solid {t.line}; border-radius: 6px; padding: 2px 6px;
    selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QAbstractSpinBox:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {t.accent}; }}
QAbstractSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {t.menu}; border: 1px solid {t.line};
    selection-background-color: {t.soft}; selection-color: {t.text}; outline: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.line}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.accent}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QAbstractItemView::indicator {{ width: 14px; height: 14px;
    border-radius: 4px; border: 1px solid {t.line}; background: {t.entry}; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ background: {t.accent};
    border-color: {t.accent}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px;
    border: 1px solid {t.line}; background: {t.entry}; }}
QRadioButton::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QAbstractItemView {{ background: transparent; border: none; outline: none;
    alternate-background-color: {t.card}; selection-background-color: {t.soft};
    selection-color: {t.text}; }}
QAbstractItemView::item {{ padding: 3px 6px; border-radius: 4px; }}
QAbstractItemView::item:hover, QAbstractItemView::item:selected {{ background: {t.soft};
    color: {t.text}; }}
QHeaderView::section {{ background: transparent; color: {t.muted}; border: none;
    border-bottom: 1px solid {t.line}; padding: 4px 6px; font-weight: 600; }}
QTableView {{ gridline-color: transparent; }}
QTableView::item {{ border-radius: 0; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 4px 7px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle {{ background: {t.line}; border-radius: 3px; min-height: 28px;
    min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QStatusBar {{ background: transparent; color: {t.muted}; }}
QStatusBar QLabel {{ color: {t.muted}; padding: 0 6px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel#Hint {{ color: {t.text}; }}
QPushButton#Problems {{ background: transparent; border: none; padding: 1px 8px;
    font-weight: 600; }}
QPushButton#Problems:hover {{ background: {t.soft}; }}
QMenuBar {{ background: transparent; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {t.soft}; }}
QMenu {{ background: {t.menu}; border: 1px solid {t.line}; padding: 6px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.soft}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.line}; margin: 5px 8px; }}
QToolTip {{ background: {t.menu}; color: {t.text}; border: 1px solid {t.line}; padding: 6px 8px; }}
"""
