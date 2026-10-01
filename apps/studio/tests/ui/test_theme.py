# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import re
from pathlib import Path

import pytest
from mhfu_studio.shell.overlay import Ink
from mhfu_studio.ui import theme
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QStyle

SRC = Path(theme.__file__).parents[1]
COLOUR = re.compile(r"#[0-9a-fA-F]{6}\b|QColor\(|fromRgbF?\(|rgba?\(")


@pytest.mark.parametrize("family", theme.FAMILIES)
@pytest.mark.parametrize("dark", [True, False])
def test_every_theme_is_whole(family: str, dark: bool) -> None:
    t = theme.theme(family, dark)
    assert set(t.ink) == set(Ink)
    assert t.accent in theme.qss(t)


def test_apply_reaches_bound_icons(qapp: object) -> None:
    a = QAction()
    theme.apply(theme.theme("Ember", True))
    theme.bind(a, "ph.cursor")
    before = a.icon().pixmap(16, 16).toImage()
    theme.apply(theme.theme("Moss", False))
    assert a.icon().pixmap(16, 16).toImage() != before


def test_colours_live_in_the_theme() -> None:
    qt = [p for p in SRC.rglob("*.py") if "PySide6" in p.read_text() and p.name != "theme.py"]
    bad = [f"{p.relative_to(SRC)}:{m.group()}" for p in qt for m in COLOUR.finditer(p.read_text())]
    assert not bad


def test_item_views_match_the_controls() -> None:
    t = theme.theme("Moss", False)
    q = theme.qss(t)
    assert "QAbstractItemView::indicator" in q and "QCheckBox::indicator, QAbstractItemView" in q
    assert f"selection-background-color: {t.soft};\n    selection-color: {t.text};" in q


def test_no_mnemonic_underlines(qapp: QApplication) -> None:
    theme.apply(theme.theme("Moss", False))
    assert qapp.style().styleHint(QStyle.StyleHint.SH_UnderlineShortcut) == 0
