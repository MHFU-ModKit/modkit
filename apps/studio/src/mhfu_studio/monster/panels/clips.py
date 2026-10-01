# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Clips panel: every slot, what is really in it (a FILLER slot is a copy of idle, and forcing
it looks exactly like a failed override), and the names it carries, keyed to this build."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.monster.clips import SlotCoverage, Vocabulary
from mhfu_studio.monster.render.playback import wall_clock

from .common import AMBER, COVERAGE, colored, columns, save_row, span_all, table_flags, tooltip

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    vp, sc = ws.vp, ws.scene
    if vp is None or sc is None:
        imgui.text_disabled("no GL context yet" if sc is not None else "no scene yet")
        return
    if not sc.clips:
        imgui.text_disabled("this PAC has no animation sub-resource")
        return
    vocab = ws.vocabulary()
    imgui.text(f"{len(sc.clips)} clips")
    imgui.same_line()
    imgui.text_disabled(f"{sum(c.loop for c in sc.clips)} looping")
    if vocab.build:
        imgui.text_disabled(vocab.build)
        tooltip(
            "the build a label typed here is keyed to. Clip ids are PER BUILD: rebuild and "
            "they shift."
        )
    coverage_line(vocab)
    for n in vocab.notes:
        imgui.text_disabled(f"* {n}")
    label_health(vocab)
    _, ws.clip_filter = imgui.input_text("##filter", ws.clip_filter)
    imgui.same_line()
    imgui.text_disabled("filter")
    editor_h = 150.0 if ws.manifest is not None else 34.0  # the editor must not scroll away
    if imgui.begin_table("##clips", 6, table_flags(scroll=True), imgui.ImVec2(0.0, -editor_h)):
        # a1 has its own column: the executor argument IS the slot, the number a script types
        columns(
            (
                ("a1", 0.45),
                ("name", 1.0),
                ("cov", 0.7),
                ("frames", 0.75),
                ("loop", 0.45),
                ("travel", 0.6),
            )
        )
        needle = ws.clip_filter.strip().lower()
        current = None if vp.clip is None else vp.clip.slot
        speed = vp.playback.speed
        for c in sc.clips:
            cov = vocab.coverage.slots.get(c.slot)
            found = ws.manifest_clip(c.slot)
            text = f"{c.slot} {' '.join(c.names)} {cov.kind if cov else ''}"
            text += f" {found[1].label}" if found else ""
            if needle and needle not in text.lower():
                continue
            imgui.table_next_row()
            imgui.table_next_column()
            if imgui.selectable(f"{c.slot}##c{c.slot}", current == c.slot, span_all())[0]:
                vp.play_clip(c)
                ws.pick_clip(c.slot)
                ws.recompute_alignment()
            label = f"\n{found[1].label}" if found and found[1].label else ""
            why = cov.why() if cov else "no coverage verdict"
            tooltip(f"{why}\n{wall_clock(c.frames, speed):.2f} s at speed {speed:.2f}{label}")
            imgui.table_next_column()
            if c.names:
                imgui.text(c.name)
            else:
                imgui.text_disabled("-")
            imgui.table_next_column()
            coverage_cell(cov)
            imgui.table_next_column()
            imgui.text(str(c.frames))
            imgui.table_next_column()
            imgui.text("loop" if c.loop else "")
            imgui.table_next_column()
            net, _ = ws.travel(c.slot)
            imgui.text(f"{net:.0f}" if net >= 1.0 else "·")
            if not c.whole_rig:
                tooltip("partial: present in only some joint-partition streams")
        imgui.end_table()
    imgui.separator()
    label_editor(ws)


def label_editor(ws: MonsterWorkspace) -> None:
    """Name a clip in the manifest, keyed to THIS build."""
    from imgui_bundle import imgui

    if ws.manifest is None:
        imgui.text_disabled("labels need a manifest: open a port manifest")
        return
    if ws.edit_slot is None:
        imgui.text_disabled("pick a clip to name it")
        return
    cov = ws.vocabulary().coverage.slots.get(ws.edit_slot)
    imgui.text(f"slot {ws.edit_slot}")
    imgui.same_line()
    coverage_cell(cov)
    imgui.set_next_item_width(-64.0)
    _, ws.name_buf = imgui.input_text("name", ws.name_buf)
    imgui.set_next_item_width(-64.0)
    _, ws.label_buf = imgui.input_text("label", ws.label_buf)
    if imgui.button("stage"):
        ws.label()
    imgui.same_line()
    save_row(ws)
    if ws.message:
        imgui.text_wrapped(ws.message)


def coverage_line(vocab: Vocabulary) -> None:
    from imgui_bundle import imgui

    cov = vocab.coverage
    if not cov.has_source:
        return
    n = cov.counts()
    for i, kind in enumerate(("CARRIED", "FILLER", "HOST", "ALTERED")):
        if i:
            imgui.same_line()
        text = f"{n[kind]} {kind.lower() if kind != 'CARRIED' else 'carried'}"
        if n[kind]:
            colored(text, COVERAGE[kind])
        else:
            imgui.text_disabled(text)
    if cov.dropped:
        imgui.text_disabled(
            f"{len(cov.dropped)} donor clip(s) dropped: " + ", ".join(map(str, sorted(cov.dropped)))
        )
        tooltip(
            "the host pack has no slot of that index, so the porter had nowhere to file "
            "them. They are not in this build at all."
        )


def coverage_cell(cov: SlotCoverage | None) -> None:
    from imgui_bundle import imgui

    if cov is None:
        imgui.text_disabled("-")
        return
    colored(cov.kind[:7].lower(), COVERAGE[cov.kind])
    tooltip(cov.why())


def label_health(vocab: Vocabulary) -> None:
    """Speaks up only when a label stopped meaning what it says."""
    from imgui_bundle import imgui

    bad = vocab.suspect
    if not bad:
        return
    colored(f"! {len(bad)} label(s) do not match this build", AMBER, wrapped=True)
    for t in bad:
        imgui.bullet_text(f"{t.name} (slot {t.slot}): {t.status}")
        tooltip(t.message)
