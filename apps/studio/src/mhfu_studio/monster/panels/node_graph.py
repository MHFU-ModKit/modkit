# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A node-graph view over NodeGraphQt that never owns the graph: the owner's document does.

The owner calls `GraphView.show(nodes, links)` after every edit (undo too); the view diffs by
node id and moves only what differs. A gesture comes out as an intent signal, one event-loop turn
late (so the owner may rebuild whatever the gesture touched), and the view holds the gesture's
change only until then: when no `show` came, the view goes back to what it last showed. A
refused edit therefore needs no code.

NodeGraphQt's own model, commands and undo are bypassed: its nodes are only items in a scene.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from NodeGraphQt import BaseNode, NodeGraph
from NodeGraphQt.constants import PortTypeEnum
from NodeGraphQt.qgraphics.node_base import NodeItem
from NodeGraphQt.widgets.node_widgets import NodeBaseWidget
from PySide6.QtCore import QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent, QPainter, QShowEvent, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QMenu,
    QSpinBox,
    QStyleOptionGraphicsItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell.overlay import Color, Ink
from mhfu_studio.ui import theme

Point = tuple[float, float]
Option = tuple[str, object]
#: a palette group: its title, and its kinds as (kind, title, tip)
PaletteGroup = tuple[str, Sequence[tuple[str, str, str]]]
KINDS = ("int", "float", "choice", "part", "mains")
#: the reserved name of a node's note widget; no parameter is called this
NOTE = "@note"
NONE = "none"
#: where a number field with no bound ends
_FAR = 10**9


class ParamLike(Protocol):
    """What the view needs of a parameter; `mhfu_port.behaviour.Param` has these names."""

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def lo(self) -> float | None: ...
    @property
    def hi(self) -> float | None: ...
    @property
    def choices(self) -> Sequence[object]: ...
    @property
    def optional(self) -> bool: ...
    @property
    def tip(self) -> str: ...


@dataclass(frozen=True)
class NodeSpec:
    """One node as the owner wants it drawn.

    `options[name]` is a combo's or a multi-select's list of (label, value), else the
    parameter's `choices`; `values[name]` is a number, a value of the list, `None` for "none"
    (an optional parameter), or for `mains` the values picked. `label` is the one free-text
    note; `tip` the node's tooltip; `dim` draws it faded.
    """

    id: str
    title: str
    role: str
    at: Point
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    params: Sequence[ParamLike] = ()
    values: Mapping[str, object] = field(default_factory=dict)
    options: Mapping[str, Sequence[Option]] = field(default_factory=dict)
    badge: str = ""
    dim: bool = False
    label: str = ""
    tip: str = ""


@dataclass(frozen=True)
class LinkSpec:
    src: str
    src_port: str
    dst: str
    dst_port: str


def option(item: object) -> Option:
    """A choice as (label, value): a pair stays, anything else is its own label."""
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item[0], item[1]
    return str(item), item


# ---- NodeGraphQt, cut down to a scene of items -------------------------------------------- #


class _NoUndo(QUndoStack):
    """NodeGraphQt's undo stack, holding nothing: a command runs and is dropped."""

    def push(self, cmd: QUndoCommand) -> None:
        cmd.redo()

    def beginMacro(self, text: str) -> None:  # noqa: N802
        pass

    def endMacro(self) -> None:  # noqa: N802
        pass


class _Item(NodeItem):  # type: ignore[misc]
    """A node's drawing: a badge in the title bar, the owner's tooltip, no renaming."""

    badge = ""
    tip = ""

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None
    ) -> None:
        super().paint(painter, option, widget)
        if self.badge:
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSizeF(8.0)
            painter.setFont(font)
            painter.setPen(theme.color(Ink.TEXT))
            area = QRectF(self.boundingRect()).adjusted(0.0, 3.0, -8.0, 0.0)
            painter.drawText(
                area, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.badge
            )

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()  # NodeGraphQt would edit the title here; a title is the owner's

    def _tooltip_disable(self, state: bool) -> None:
        self.setToolTip(self.tip)


class _Node(BaseNode):  # type: ignore[misc]
    __identifier__ = "mhfu.studio"
    NODE_NAME = "node"

    def __init__(self) -> None:
        super().__init__(qgraphics_item=_Item)


class _Graph(NodeGraph):  # type: ignore[misc]
    """NodeGraphQt's controller, with the three handlers that would edit its model turned
    into signals."""

    #: (pairs of port items cut, pairs made)
    wired = Signal(object, object)
    #: the ids of the nodes a drag moved
    dragged = Signal(object)

    def __init__(self) -> None:
        super().__init__(undo_stack=_NoUndo())

    def _on_connection_changed(self, disconnected: list[Any], connected: list[Any]) -> None:
        self.wired.emit(disconnected, connected)

    def _on_connection_sliced(self, ports: list[Any]) -> None:
        self.wired.emit(ports, [])

    def _on_nodes_moved(self, node_data: Mapping[Any, Any]) -> None:
        self.dragged.emit([item.id for item in node_data])


# ---- a parameter on a node ---------------------------------------------------------------- #


class _Field(NodeBaseWidget):  # type: ignore[misc]
    """One parameter's control, or the note, on a node; `value_changed` is the user's edit and
    never `set_value`'s."""

    def __init__(
        self, parent: Any, param: ParamLike | None, options: Sequence[Option] = ()
    ) -> None:
        super().__init__(
            parent, NOTE if param is None else param.name, "" if param is None else param.title
        )
        self.optional = param is not None and param.optional
        self._vals: list[object] = []
        self._opts: list[Option] = []
        self._built = False
        self._ctl = self._make(param)
        self.set_custom_widget(self._ctl)
        if param is not None and param.tip:
            self.widget().setToolTip(param.tip)  # the group box: the tip of whatever is hovered
        self.widget().setMaximumWidth(150)
        self.set_options(options)

    def _make(self, param: ParamLike | None) -> QWidget:
        if param is None:
            line = QLineEdit()
            line.setPlaceholderText("note")
            line.setMinimumWidth(120)
            line.editingFinished.connect(self.on_value_changed)
            return line
        if param.type in ("int", "float"):
            return self._number(param)
        if param.type == "mains":
            button = QToolButton()
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMinimumWidth(120)
            menu = QMenu(button)
            menu.triggered.connect(self.on_value_changed)  # a click, not a `setChecked`
            button.setMenu(menu)
            return button
        combo = QComboBox()
        combo.setMinimumWidth(120)
        combo.currentIndexChanged.connect(self.on_value_changed)
        return combo

    def _number(self, param: ParamLike) -> QWidget:
        lo = -_FAR if param.lo is None else param.lo
        hi = _FAR if param.hi is None else param.hi
        box: QSpinBox | QDoubleSpinBox
        if param.type == "int":
            box = QSpinBox()
            box.setRange(int(lo) - param.optional, int(hi))
        else:
            box = QDoubleSpinBox()
            step = 0.1 if hi - lo >= 1 else 0.01
            box.setDecimals(3)
            box.setSingleStep(step)
            box.setRange(lo - step * param.optional, hi)
        if param.optional:
            box.setSpecialValueText(NONE)  # the least value shows as "none"
        box.setKeyboardTracking(False)  # one change per edit, not per keystroke
        box.setMinimumWidth(80)
        box.valueChanged.connect(self.on_value_changed)
        return box

    # -- the value -- #

    def get_value(self) -> object:
        ctl = self._ctl
        if isinstance(ctl, QLineEdit):
            return ctl.text()
        if isinstance(ctl, QSpinBox | QDoubleSpinBox):
            if self.optional and ctl.value() == ctl.minimum():
                return None
            return int(ctl.value()) if isinstance(ctl, QSpinBox) else float(ctl.value())
        if isinstance(ctl, QComboBox):
            return (
                self._vals[ctl.currentIndex()]
                if 0 <= ctl.currentIndex() < len(self._vals)
                else None
            )
        return tuple(
            v for (_, v), a in zip(self._opts, self._actions(), strict=True) if a.isChecked()
        )

    def set_value(self, value: object) -> None:
        ctl = self._ctl
        with QSignalBlocker(ctl):
            if isinstance(ctl, QLineEdit):
                ctl.setText("" if value is None else str(value))
            elif isinstance(ctl, QSpinBox | QDoubleSpinBox):
                ctl.setValue(cast(Any, ctl.minimum() if value is None else value))
            elif isinstance(ctl, QComboBox):
                if value not in self._vals:  # a value the options lack still shows
                    self._vals.append(value)
                    ctl.addItem(str(value))
                ctl.setCurrentIndex(self._vals.index(value))
            else:
                picked = list(cast(Any, value or ()))
                for (_, v), a in zip(self._opts, self._actions(), strict=True):
                    a.setChecked(v in picked)
                self._summary()

    def same(self, value: object) -> bool:
        """Whether the control already shows `value` (numbers to the box's own rounding)."""
        have = self.get_value()
        if isinstance(have, float) and isinstance(value, int | float):
            return abs(have - value) < 5e-4
        if isinstance(self._ctl, QToolButton):
            return set(cast(Any, have)) == set(cast(Any, value or ()))
        return have == value

    def set_options(self, options: Sequence[Option]) -> bool:
        """Replaces a combo's or a multi-select's entries, keeping what is picked; whether
        anything changed."""
        ctl = self._ctl
        if not isinstance(ctl, QComboBox | QToolButton):
            return False
        built = self._built
        if built and list(options) == self._opts:
            return False
        keep = self.get_value() if built else None
        self._built, self._opts = True, list(options)
        if isinstance(ctl, QComboBox):
            self._vals = ([None] if self.optional else []) + [v for _, v in self._opts]
            with QSignalBlocker(ctl):
                ctl.clear()
                ctl.addItems([NONE] * self.optional + [label for label, _ in self._opts])
        else:
            menu = ctl.menu()
            menu.clear()
            for label, _ in self._opts:
                menu.addAction(label).setCheckable(True)
        if built:
            self.set_value(keep)
        elif isinstance(ctl, QToolButton):
            self._summary()
        return True

    def _actions(self) -> list[Any]:
        return list(cast(QToolButton, self._ctl).menu().actions())

    def _summary(self) -> None:
        names = [
            label
            for (label, _), a in zip(self._opts, self._actions(), strict=True)
            if a.isChecked()
        ]
        text = ", ".join(names) if 0 < len(names) <= 2 else f"{len(names)} picked"
        cast(QToolButton, self._ctl).setText(text if names else (NONE if self.optional else "-"))

    def on_value_changed(self, *_: object) -> None:
        if isinstance(self._ctl, QToolButton):
            self._summary()
        super().on_value_changed()


# ---- the view ----------------------------------------------------------------------------- #


@dataclass
class _Shown:
    """One node on the canvas, and what it was built from."""

    node: Any
    sig: tuple[object, ...]
    ins: dict[str, Any]
    outs: dict[str, Any]
    fields: dict[str, _Field]
    #: NodeGraphQt's own body and border colours, for a role with none
    base: tuple[Any, Any]
    tint: tuple[Any, Any] | None = None
    title: str | None = None

    @property
    def view(self) -> Any:
        return self.node.view


def _signature(spec: NodeSpec) -> tuple[object, ...]:
    """What a node is built from: a change of it rebuilds the node, any other updates it."""
    params = tuple((p.name, p.type, p.title, p.lo, p.hi, p.optional, p.tip) for p in spec.params)
    return spec.inputs, spec.outputs, params


class GraphView(QWidget):
    """The canvas. Signals are the user's intents; none fires from `show`."""

    link_requested = Signal(str, str, str, str)
    unlink_requested = Signal(str, str, str, str)
    delete_requested = Signal(list)
    #: once per node at a drag's end
    moved = Signal(str, float, float)
    param_changed = Signal(str, str, object)
    create_requested = Signal(str, float, float)
    #: the id of the one node selected, else `None`
    picked = Signal(object)
    label_changed = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._graph = _Graph()
        self._shown: dict[str, _Shown] = {}
        self._nodes: Sequence[NodeSpec] = ()
        self._links: Sequence[LinkSpec] = ()
        self._roles: Mapping[str, Color] = {}
        self._menus: list[QMenu] = []
        self._pending: list[tuple[Callable[[], None], bool]] = []
        self._renders = 0
        self._busy = 0
        self._pick: str | None = None
        self._framed = False  # the first non-empty `show` frames its nodes

        viewer = self._graph.viewer()
        viewer.setMinimumSize(240, 160)
        for act in (viewer.qaction_for_undo(), viewer.qaction_for_redo()):
            act.setShortcuts([])  # else Ctrl+Z stops here, at an empty stack, not at the studio
            viewer.context_menus()["graph"].removeAction(act)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._graph.widget)
        viewer.scene().selectionChanged.connect(self._selection)
        self._graph.wired.connect(self._wires)
        self._graph.dragged.connect(self._dragged)

    @property
    def viewer(self) -> Any:
        return self._graph.viewer()

    # -- what the owner calls -- #

    def show(self, nodes: Sequence[NodeSpec] | None = None, links: Sequence[LinkSpec] = ()) -> None:
        """Draws `nodes` and `links`, and no more than differs from what is drawn. Called bare
        it is `QWidget.show`."""
        if nodes is None:
            super().show()
            return
        self._apply(nodes, links)
        if nodes and self.isVisible():
            self._frame_once()

    def set_roles(self, colors: Mapping[str, Color]) -> None:
        """A node's colour by `NodeSpec.role`; a role not named keeps NodeGraphQt's grey."""
        self._roles = colors
        self._apply(self._nodes, self._links)

    def set_palette(self, groups: Sequence[PaletteGroup]) -> None:
        """What the right-click menu offers to create: a submenu per group."""
        menu = self.viewer.context_menus()["graph"]
        menu.clear()
        for old in self._menus:
            old.deleteLater()
        self._menus = []
        for title, kinds in groups:
            sub = menu.addMenu(title)
            sub.setToolTipsVisible(True)
            self._menus.append(sub)
            for kind, name, tip in kinds:
                act = sub.addAction(name)
                act.setToolTip(tip)
                act.triggered.connect(lambda _=False, k=kind: self._create(k))

    def select(self, ids: Sequence[str]) -> None:
        """Selects exactly these nodes, without a `picked`."""
        self._busy += 1
        try:
            self.viewer.scene().clearSelection()
            for i in ids:
                if i in self._shown:
                    self._shown[i].view.setSelected(True)
        finally:
            self._busy -= 1
        self._pick = self._picked()

    def selected(self) -> list[str]:
        """The selected nodes' ids, in the order of the last `show`."""
        picked = {n.id for n in self.viewer.selected_nodes()}
        return [n.id for n in self._nodes if n.id in picked]

    def fit(self) -> None:
        """Frames every node."""
        views = [s.view for s in self._shown.values()]
        if views:
            self.viewer.zoom_to_nodes(views)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._shown:
            QTimer.singleShot(0, self, self._frame_once)

    def _frame_once(self) -> None:
        if not self._framed:
            self._framed = True
            self.fit()

    # -- drawing -- #

    def _apply(self, nodes: Sequence[NodeSpec], links: Sequence[LinkSpec]) -> None:
        self._check(nodes, links)
        self._nodes, self._links = nodes, links
        self._renders += 1
        self._busy += 1
        try:
            keep = set(self.selected())
            want = {n.id for n in nodes}
            for gone in [i for i in self._shown if i not in want]:
                self._drop(gone)
            for spec in nodes:
                have = self._shown.get(spec.id)
                if have is not None and have.sig != _signature(spec):
                    self._drop(spec.id)
                    have = None
                self._sync(have or self._make(spec), spec)
            self._relink(links)
            for i in keep & want:
                if not self._shown[i].view.isSelected():
                    self._shown[i].view.setSelected(True)
        finally:
            self._busy -= 1
        self._pick = self._picked()

    @staticmethod
    def _check(nodes: Sequence[NodeSpec], links: Sequence[LinkSpec]) -> None:
        """Everything that could fail half way, before anything is drawn."""
        ports = {n.id: (set(n.inputs), set(n.outputs)) for n in nodes}
        if len(ports) != len(nodes):
            raise ValueError("two nodes share an id")
        for n in nodes:
            if len(ports[n.id][0]) != len(n.inputs) or len(ports[n.id][1]) != len(n.outputs):
                raise ValueError(f"node {n.id}: a port name twice")
            for p in n.params:
                if p.type not in KINDS:
                    raise ValueError(f"node {n.id}, parameter {p.name!r}: unknown type {p.type!r}")
        for ln in links:
            if ln.src not in ports or ln.src_port not in ports[ln.src][1]:
                raise ValueError(f"link from {ln.src}.{ln.src_port}: no such output")
            if ln.dst not in ports or ln.dst_port not in ports[ln.dst][0]:
                raise ValueError(f"link to {ln.dst}.{ln.dst_port}: no such input")

    def _make(self, spec: NodeSpec) -> _Shown:
        node = _Node()
        node.NODE_NAME = spec.id  # unique: the graph then never renames a node
        node.model.id = node.view.id = spec.id
        ins = {p: node.add_input(p, multi_input=True).view for p in spec.inputs}
        outs = {p: node.add_output(p, multi_output=True).view for p in spec.outputs}
        fields: dict[str, _Field] = {}
        for param in spec.params:
            fields[param.name] = f = _Field(node.view, param, self._options(spec, param))
            f.value_changed.connect(lambda name, v, i=spec.id: self._param(i, name, v))
        fields[NOTE] = note = _Field(node.view, None)
        note.value_changed.connect(lambda _, v, i=spec.id: self._note(i, cast(str, v)))
        for f in fields.values():
            node.view.add_widget(f)
        self._graph.add_node(node, pos=spec.at, selected=False, push_undo=False)
        base = (node.view.color, node.view.border_color)
        shown = _Shown(node, _signature(spec), ins, outs, fields, base)
        self._shown[spec.id] = shown
        return shown

    @staticmethod
    def _options(spec: NodeSpec, param: ParamLike) -> list[Option]:
        got = spec.options.get(param.name)
        return list(got) if got is not None else [option(c) for c in param.choices]

    def _drop(self, node_id: str) -> None:
        shown = self._shown.pop(node_id)
        for port in shown.view.inputs + shown.view.outputs:
            for pipe in list(port.connected_pipes):
                pipe.delete()
        self._graph.remove_node(shown.node, push_undo=False)

    def _sync(self, s: _Shown, spec: NodeSpec) -> None:
        """Brings one node to `spec`: what the user can change is read back from the canvas,
        the rest compared with what was set last."""
        view = s.view
        if s.title != spec.title:
            s.title = view.name = spec.title
        view.tip = spec.tip
        view.setToolTip(spec.tip)
        tint = self._tint(spec.role) or s.base
        if tint != s.tint:
            s.tint = tint
            view.color, view.border_color = tint
        if view.badge != spec.badge:
            view.badge = spec.badge
            view.update()
        view.setOpacity(0.35 if spec.dim else 1.0)
        x, y = view.xy_pos
        if abs(x - spec.at[0]) > 1e-3 or abs(y - spec.at[1]) > 1e-3:
            view.xy_pos = spec.at
        resized = False
        for param in spec.params:
            f = s.fields[param.name]
            opts = self._options(spec, param)
            if f.set_options(opts):
                resized = True
            want = spec.values.get(param.name)
            if (want is not None or f.optional) and not f.same(want):
                f.set_value(want)
        if not s.fields[NOTE].same(spec.label):
            s.fields[NOTE].set_value(spec.label)
        if resized:
            view.draw_node()

    def _tint(self, role: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
        ink = self._roles.get(role)
        if ink is None:
            return None
        edge = theme.color(ink)
        body = edge.darker(320)
        return (
            (body.red(), body.green(), body.blue(), 255),
            (edge.red(), edge.green(), edge.blue(), 255),
        )

    def _pipes(self) -> dict[LinkSpec, Any]:
        out: dict[LinkSpec, Any] = {}
        for node_id, s in self._shown.items():
            for name, port in s.outs.items():
                for pipe in list(port.connected_pipes):
                    to = pipe.input_port
                    key = LinkSpec(node_id, name, to.node.id, to.name)
                    if key in out:  # twice drawn
                        pipe.delete()
                    out[key] = pipe
        return out

    def _relink(self, links: Sequence[LinkSpec]) -> None:
        have = self._pipes()
        want = set(links)
        for key, pipe in have.items():
            if key not in want:
                pipe.delete()
        for key in want - have.keys():
            self._shown[key.src].outs[key.src_port].connect_to(
                self._shown[key.dst].ins[key.dst_port]
            )

    # -- what the canvas tells -- #

    def _raise(self, fn: Callable[[], None], *, revert: bool = True) -> None:
        """Runs `fn` after the event that raised it; if it changed the canvas and `show` did
        not follow, the canvas goes back."""
        self._pending.append((fn, revert))
        if len(self._pending) == 1:
            QTimer.singleShot(0, self, self._flush)

    def _flush(self) -> None:
        pending, self._pending = self._pending, []
        seen = self._renders
        for fn, _ in pending:
            fn()
        if self._renders == seen and any(revert for _, revert in pending):
            self._apply(self._nodes, self._links)

    def _wires(self, unlinked: Sequence[Any], linked: Sequence[Any]) -> None:
        cut, made = [self._link_of(*p) for p in unlinked], [self._link_of(*p) for p in linked]

        def emit() -> None:
            for ln in cut:
                self.unlink_requested.emit(ln.src, ln.src_port, ln.dst, ln.dst_port)
            for ln in made:
                self.link_requested.emit(ln.src, ln.src_port, ln.dst, ln.dst_port)

        self._raise(emit)

    @staticmethod
    def _link_of(a: Any, b: Any) -> LinkSpec:
        out, into = (a, b) if a.port_type == PortTypeEnum.OUT.value else (b, a)
        return LinkSpec(out.node.id, out.name, into.node.id, into.name)

    def _dragged(self, ids: Sequence[str]) -> None:
        at = [(i, *self._shown[i].view.xy_pos) for i in ids if i in self._shown]

        def emit() -> None:
            for i, x, y in at:
                self.moved.emit(i, x, y)

        self._raise(emit)

    def _param(self, node_id: str, name: str, value: object) -> None:
        self._raise(lambda: self.param_changed.emit(node_id, name, value))

    def _note(self, node_id: str, text: str) -> None:
        if text != self._text_of(node_id):
            self._raise(lambda: self.label_changed.emit(node_id, text))

    def _text_of(self, node_id: str) -> str:
        return next((n.label for n in self._nodes if n.id == node_id), "")

    def _create(self, kind: str) -> None:
        x, y = self._graph.cursor_pos()
        self._raise(lambda: self.create_requested.emit(kind, x, y), revert=False)

    def _picked(self) -> str | None:
        ids = self.selected()
        return ids[0] if len(ids) == 1 else None

    def _selection(self) -> None:
        if not self._busy:
            self._raise(self._emit_picked, revert=False)

    def _emit_picked(self) -> None:
        now = self._picked()
        if now != self._pick:
            self._pick = now
            self.picked.emit(now)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not event.modifiers():
            self._delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def _delete_selected(self) -> None:
        ids = self.selected()
        cuts = [self._link_of(p.input_port, p.output_port) for p in self.viewer.selected_pipes()]

        def emit() -> None:
            for ln in cuts:
                self.unlink_requested.emit(ln.src, ln.src_port, ln.dst, ln.dst_port)
            if ids:
                self.delete_requested.emit(ids)

        if ids or cuts:
            self._raise(emit, revert=False)
