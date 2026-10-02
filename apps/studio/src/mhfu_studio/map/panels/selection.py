# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Selection panel: what is selected, a typed transform, remove, and the area's edits;
sizes, groups and the space left to draw under More. Undo and Redo are the Edit menu's."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

from ..core.edit import COLLISION, count, describe_op
from ..core.scene import group_name
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

#: the selected groups listed by name before "and N more"
SHOWN_GROUPS = 6
REMOVE_TIP = (
    "Clears the selected objects: they stop drawing, and their drawing slots are free for the"
    " Add panel to fill."
)
HINT = (
    "Click something in the view to select it. Shift-click adds; in the Select tool a drag takes"
    " everything in a box."
)
BUDGET_TIP = (
    "An area draws only what its drawing slots already hold; removing an object frees slots,"
    " adding fills them. Moving, turning and scaling cost nothing."
)


class SelectionPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.what = kit.label(role="title")
        self.hint = kit.label(HINT, role="muted")
        self.frame = kit.button(
            "Frame",
            tip="Points the camera at the selection (F in the view)",
            on=studio.act("frame", ws.frame_selection),
            icon="ph.frame-corners",
        )
        self.clear = kit.button(
            "Clear",
            tip="Selects nothing (Esc in the view)",
            on=studio.act("clear selection", ws.tools.clear),
            icon="ph.selection-slash",
        )
        for w in (self.what, self.hint):
            lay.addWidget(w)
        lay.addWidget(kit.row(self.frame, self.clear, stretch=True))

        self.transform = kit.Section(
            "Move, rotate or scale",
            tip="Typed amounts about the selection's centre, applied as one edit",
        )
        form = kit.Form()
        self.by = kit.Vec3(tip="How far to move the selection, in map units", step=10.0)
        self.turn = kit.Vec3(tip="Degrees to turn the selection about its centre", step=5.0)
        self.factor = kit.Vec3(
            (1.0, 1.0, 1.0),
            tip="Size factor about the centre; 1 keeps the size, 2 doubles it",
            step=0.05,
            decimals=3,
        )
        form.row("Move by", self.by)
        form.row("Rotate", self.turn)
        form.row("Scale", self.factor)
        apply = kit.button(
            "Apply",
            tip="Moves, turns and scales the selection by these amounts as one edit",
            on=studio.act("transform", self._apply),
            role="primary",
        )
        reset = kit.button("Reset fields", tip="Puts the fields back to no change", on=self._reset)
        self.transform.body.addWidget(form)
        self.transform.body.addWidget(kit.row(apply, reset, stretch=True))
        lay.addWidget(self.transform)

        self.remove = kit.button(
            "Remove",
            tip=f"{REMOVE_TIP} (Delete in the view)",
            on=studio.act("remove", lambda: ws.remove_selected(solid=False)),
            role="danger",
            icon="ph.trash",
        )
        self.remove_solid = kit.button(
            "Remove + collision",
            tip="Removes the objects and the invisible collision inside their box, so the"
            " hunter can walk where they stood.",
            on=studio.act("remove with collision", lambda: ws.remove_selected(solid=True)),
            role="danger",
        )
        lay.addWidget(kit.row(self.remove, self.remove_solid, stretch=True))
        self.range = kit.Alert()
        lay.addWidget(self.range)

        edits = kit.Section(
            "Edits", tip="This area's edits, newest first. Undo and Redo are in the Edit menu."
        )
        self.count = kit.label(role="muted")
        self.ops = kit.Items(
            tip="Every edit to this area, newest first; Undo takes back the top one",
            empty="No edits to this area yet",
        )
        self.ops.setMinimumHeight(140)
        self.warnings = kit.Alert()
        for part in (self.count, self.ops, self.warnings):
            edits.body.addWidget(part)
        lay.addWidget(edits)

        more = kit.More(tip="The selection's size and groups, and the area's space left to draw")
        self.stats = kit.label(role="muted", selectable=True)
        self.groups = kit.label(role="muted", selectable=True)
        self.budget = kit.label(role="muted")
        self.budget.setToolTip(BUDGET_TIP)
        for w in (self.stats, self.groups, self.budget):
            more.body.addWidget(w)
        lay.addWidget(more)
        lay.addStretch(1)
        self.gate = Gate(page, "select things and edit them")
        self.body.addWidget(self.gate)
        #: the session and revision the budget and the edit list were read at
        self._read: tuple[object, int] | None = None

    def _apply(self) -> None:
        self.ws.apply_numeric(self.by.value(), self.turn.value(), self.factor.value())

    def _reset(self) -> None:
        for v in (self.by, self.turn, self.factor):
            v.reset()

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc, sess = ws.scene, ws.session
        assert sc is not None and sess is not None
        col = ws.tools.kind == COLLISION
        sel: Any = ws.col_sel if col else ws.selection
        said = ws.selected()
        self.what.setText("Nothing selected" if sel.empty else said[:1].upper() + said[1:])
        self.hint.setVisible(sel.empty)
        lines, groups = [], []
        if not sel.empty:
            lo, hi = sel.bounds(sc)
            c, size = (lo + hi) * 0.5, hi - lo
            if col:
                lines.append("The Collision panel changes these triangles.")
            else:
                s = ws.selection
                spans = s.straddling(sc)
                lines.append(
                    f"{s.n_vertices} vertices, {count(s.n_faces(sc), 'face')}"
                    + (f", {spans} spanning two groups" if spans else "")
                )
                for k in list(s.vertices)[:SHOWN_GROUPS]:
                    g = sc.group(*k)
                    tex = "no texture" if g.untextured else f"texture slot {g.texture}"
                    groups.append(f"{g.label}: {tex}, {count(g.n_components, 'object')}")
                if len(s.vertices) > SHOWN_GROUPS:
                    groups.append(f"and {len(s.vertices) - SHOWN_GROUPS} more groups")
            lines.append(
                f"centre {c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f}"
                f"   size {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f}"
            )
        self.stats.setText("\n".join(lines))
        self.stats.setVisible(bool(lines))
        self.groups.setText("\n".join(groups))
        self.groups.setVisible(bool(groups))
        self.frame.setEnabled(not sel.empty)
        self.clear.setEnabled(not sel.empty)
        self.remove.setEnabled(not sel.empty and not col)
        self.remove_solid.setEnabled(not sel.empty and not col)
        self.transform.setEnabled(not sel.empty)
        read = (sess, sess.revision)
        if read != self._read:
            self._read = read
            self._read_session()

    def _read_session(self) -> None:
        """The budget, the range check and the edit list: they change only with an edit."""
        sess = self.ws.session
        assert sess is not None
        b = sess.budget()
        self.budget.setText(
            f"Space left to draw: {b['free']} triangles free, {b['drawn']} of {b['total']} drawn"
        )
        bad = sess.range_check()
        where = ", ".join(f"{group_name(k)}: {n}" for k, n in bad.items())
        self.range.setText(f"Vertices past what the model can store ({where})" if bad else "")
        self.range.setVisible(bool(bad))
        n = len(sess.ops)
        self.count.setText(count(n, "edit") if n else "No edits yet")
        newest = reversed(list(enumerate(sess.ops, 1)))
        self.ops.set_items([kit.Item(f"{i}. {describe_op(op)}") for i, op in newest])
        warnings = [plain(w) for w in sess.warnings()[:3]]
        self.warnings.setText("\n".join(warnings))
        self.warnings.setVisible(bool(warnings))
