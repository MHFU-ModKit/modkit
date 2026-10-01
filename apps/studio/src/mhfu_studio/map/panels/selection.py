# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Selection panel: what is selected, a numeric transform, the budget, the ops."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import help_marker, plain

from ..core.edit import COLLISION, Selection, describe_op

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

WARN = (1.0, 0.75, 0.3, 1.0)
RANGE = (1.0, 0.6, 0.3, 1.0)
SHOWN_OPS = 14


class SelectionPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.reset()

    def reset(self) -> None:
        self.by = [0.0, 0.0, 0.0]
        self.rotate = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]

    def apply(self) -> None:
        self.ws.apply_numeric(self.by, self.rotate, self.scale)

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None:
            imgui.text("no section loaded")
            return
        sel = ws.selection
        if ws.tools.kind == COLLISION:
            imgui.text_wrapped(ws.col_sel.describe(sc) + "   (the Collision panel edits it)")
            if not ws.col_sel.empty:
                lo, hi = ws.col_sel.bounds(sc)
                c, size = (lo + hi) * 0.5, hi - lo
                imgui.text(
                    f"centre ({c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f})"
                    f"  size {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f}"
                )
                imgui.text("numeric transform (about the selection's centre)")
                _, self.by = imgui.input_float3("move##c", self.by, "%.1f")
                _, self.rotate = imgui.input_float3("rotate deg##c", self.rotate, "%.1f")
                if imgui.button("apply##c"):
                    self.apply()
        else:
            imgui.text_wrapped(sel.describe(sc))
        if not sel.empty and ws.tools.kind != COLLISION:
            lo, hi = sel.bounds(sc)
            c, size = (lo + hi) * 0.5, hi - lo
            imgui.text(
                f"{sel.n_vertices} vertices, {sel.n_faces(sc)} faces,"
                f" {sel.straddling(sc)} straddling"
            )
            imgui.text(
                f"centre ({c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f})"
                f"  size {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f}"
            )
            for k in sel.vertices:
                g = sc.group(*k)
                tex = "none" if g.untextured else g.texture
                imgui.text_disabled(
                    f"  {g.label}: material {g.material}, texture {tex},"
                    f" budget {g.budget.triangles}, {g.n_components} objects"
                )
            if imgui.button("frame (F)"):
                ws.frame_selection()
            imgui.same_line()
            if imgui.button("clear (ESC)"):
                ws.tools.select(Selection(ws.tools.kind))
            imgui.same_line()
            if imgui.button("remove (Del)"):
                ws.remove_selected(solid=False)
            imgui.same_line()
            if imgui.button("remove + collision"):
                ws.remove_selected(solid=True)
            imgui.same_line()
            help_marker(
                "`clear` the objects' primitives: a degenerate strip draws nothing and the"
                " primitives become free budget for the Add panel. With collision: the"
                " collision triangles inside the objects' box are unlinked too."
            )
            imgui.separator()
            imgui.text("numeric transform (about the selection's centre)")
            _, self.by = imgui.input_float3("move", self.by, "%.1f")
            _, self.rotate = imgui.input_float3("rotate deg", self.rotate, "%.1f")
            _, self.scale = imgui.input_float3("scale", self.scale, "%.3f")
            if imgui.button("apply"):
                self.apply()
            imgui.same_line()
            if imgui.button("reset fields"):
                self.reset()
        imgui.separator()
        b = sess.budget()
        imgui.text(
            f"budget: {b['drawn']} triangles drawn of {b['total']}; {b['free']} free in cleared"
            " primitives"
        )
        imgui.same_line()
        help_marker(
            "The section can only draw what its primitives already hold (a new PRIM never"
            " draws). Removing an object frees its primitives; adding fills free ones and, if"
            " you ask, the selected objects'. Transforms spend nothing."
        )
        bad = sess.range_check()
        if bad:
            where = ", ".join(f"sub{k[0]}.g{k[1]} x{n}" for k, n in bad.items())
            imgui.text_colored(imgui.ImVec4(*RANGE), f"[!] outside the PMO range: {where}")
        imgui.separator()
        imgui.text(f"ops: {len(sess.ops)} in {sess.n_steps} step(s)   (ctrl+Z / ctrl+shift+Z)")
        imgui.same_line()
        if imgui.small_button("undo##sel"):
            ws.undo()
        imgui.same_line()
        if imgui.small_button("redo##sel"):
            ws.redo()
        for i, op in enumerate(reversed(sess.ops[-SHOWN_OPS:])):
            imgui.text_disabled(f"{len(sess.ops) - i:2d}  {describe_op(op)}")
        for w in sess.warnings()[:3]:
            imgui.text_colored(imgui.ImVec4(*WARN), plain(w))
