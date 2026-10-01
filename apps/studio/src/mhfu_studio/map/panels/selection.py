# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Selection panel: what is selected, a typed transform, the budget and the edits."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import kit

from ..core.edit import COLLISION, describe_op
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

#: the selected groups listed by name before "and N more"
SHOWN_GROUPS = 6
REMOVE_TIP = (
    "Clears the selected objects: their triangles stop drawing, and what they held becomes free"
    " budget that the Add panel can fill with something new."
)
BUDGET_TIP = (
    "A section can only draw what its primitives already hold; a new primitive never draws."
    " Removing an object frees its primitives, adding fills free ones. Moving, turning and"
    " scaling spend nothing."
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
        self.stats = kit.label(role="muted", selectable=True)
        self.groups = kit.label(role="muted", selectable=True)
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
        self.remove = kit.button(
            "Remove",
            tip=f"{REMOVE_TIP} (Delete in the view)",
            on=studio.act("remove", lambda: ws.remove_selected(solid=False)),
            role="danger",
            icon="ph.trash",
        )
        self.remove_solid = kit.button(
            "Remove + collision",
            tip=f"{REMOVE_TIP} The invisible collision inside the objects' box goes too, so the"
            " hunter can walk where they stood.",
            on=studio.act("remove with collision", lambda: ws.remove_selected(solid=True)),
            role="danger",
        )
        for w in (self.what, self.stats, self.groups):
            lay.addWidget(w)
        lay.addWidget(kit.row(self.frame, self.clear, stretch=True))
        lay.addWidget(kit.row(self.remove, self.remove_solid, stretch=True))

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
            tip="Moves, turns and scales the selection by these amounts as one edit; Undo takes"
            " it back",
            on=studio.act("transform", self._apply),
            role="primary",
        )
        reset = kit.button("Reset fields", tip="Puts the fields back to no change", on=self._reset)
        self.transform.body.addWidget(form)
        self.transform.body.addWidget(kit.row(apply, reset, stretch=True))
        lay.addWidget(self.transform)

        budget = kit.Section("Drawing budget", tip=BUDGET_TIP)
        self.budget = kit.label()
        self.budget.setToolTip(BUDGET_TIP)
        self.range = kit.Alert()
        budget.body.addWidget(self.budget)
        budget.body.addWidget(self.range)
        lay.addWidget(budget)

        edits = kit.Section("Edits", tip="This section's edits, newest first")
        self.count = kit.label(role="muted")
        self.undo = kit.button(
            "Undo",
            tip="Takes back the last edit",
            on=studio.act("undo", studio.undo),
            icon="ph.arrow-u-up-left",
        )
        self.redo = kit.button(
            "Redo",
            tip="Puts back what Undo took back",
            on=studio.act("redo", studio.redo),
            icon="ph.arrow-u-up-right",
        )
        self.ops = kit.Items(
            tip="Every edit to this section, newest first; Undo takes back the top one",
            empty="No edits to this section yet",
        )
        self.ops.setMinimumHeight(140)
        self.warnings = kit.Alert()
        edits.body.addWidget(self.count)
        edits.body.addWidget(kit.row(self.undo, self.redo, stretch=True))
        edits.body.addWidget(self.ops)
        edits.body.addWidget(self.warnings)
        lay.addWidget(edits)
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
        self.what.setText(sel.describe(sc))
        lines, groups = [], []
        if sel.empty:
            lines.append(
                "Click something in the view to select it. Shift-click adds, and a drag in the"
                " Select tool takes everything in a box."
            )
        else:
            lo, hi = sel.bounds(sc)
            c, size = (lo + hi) * 0.5, hi - lo
            if col:
                lines.append("The Collision panel edits these triangles' settings.")
            else:
                s = ws.selection
                lines.append(
                    f"{s.n_vertices} vertices, {s.n_faces(sc)} faces, {s.straddling(sc)} straddling"
                )
                for k in list(s.vertices)[:SHOWN_GROUPS]:
                    g = sc.group(*k)
                    tex = "none" if g.untextured else g.texture
                    groups.append(
                        f"{g.label}: material {g.material}, texture {tex},"
                        f" budget {g.budget.triangles}, {g.n_components} objects"
                    )
                if len(s.vertices) > SHOWN_GROUPS:
                    groups.append(f"and {len(s.vertices) - SHOWN_GROUPS} more groups")
            lines.append(
                f"centre ({c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f})"
                f"   size {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f}"
            )
        self.stats.setText("\n".join(lines))
        self.groups.setText("\n".join(groups))
        self.groups.setVisible(bool(groups))
        self.frame.setEnabled(not sel.empty)
        self.clear.setEnabled(not sel.empty)
        self.remove.setEnabled(not sel.empty and not col)
        self.remove_solid.setEnabled(not sel.empty and not col)
        self.transform.setEnabled(not sel.empty)
        self.undo.setEnabled(ws.document.can_undo())
        self.redo.setEnabled(ws.document.can_redo())
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
            f"{b['drawn']} of {b['total']} triangles drawn; {b['free']} free in cleared primitives"
        )
        bad = sess.range_check()
        where = ", ".join(f"sub{k[0]}.g{k[1]} x{n}" for k, n in bad.items())
        self.range.setText(f"Vertices past what the model can store: {where}" if bad else "")
        self.range.setVisible(bool(bad))
        n, steps = len(sess.ops), sess.n_steps
        self.count.setText(
            f"{n} edit{'s' * (n != 1)} in {steps} step{'s' * (steps != 1)}" if n else "No edits yet"
        )
        newest = reversed(list(enumerate(sess.ops, 1)))
        self.ops.set_items([kit.Item(f"{i:3d}  {describe_op(op)}") for i, op in newest])
        warnings = [plain(w) for w in sess.warnings()[:3]]
        self.warnings.setText("\n".join(warnings))
        self.warnings.setVisible(bool(warnings))
