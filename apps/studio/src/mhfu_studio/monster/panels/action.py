# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Action panel: what the HOST action expects, against the clip on screen.

A ported monster has no AI of its own: the engine runs the one MHFU overlay `port.host_species`
names, so every `(main, sub)` here is that host's, with the port's animation painted on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu import files
from mhfu.em.intel import SpeciesIntel

from mhfu_studio.monster import species
from mhfu_studio.monster.align import Alignment
from mhfu_studio.shell.widgets import plain

from .common import AMBER, GREY, LEVELS, colored, columns, span_all, table_flags, tooltip

if TYPE_CHECKING:
    from mhfu_port.manifest import Manifest

    from mhfu_studio.monster.workspace import MonsterWorkspace

#: never less than this tall: a header and one row reads as "the list is gone"
PAIR_TABLE_MIN = 150.0


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    m = ws.manifest
    if m is None:
        imgui.text_wrapped(
            "An action is a binding between a HOST behaviour pair and one of this port's clips, "
            "so it needs a manifest. Open a port manifest."
        )
        return
    species_row(ws)
    ws.sync_reference()
    intel = ws.intel
    if intel is None:
        imgui.text_wrapped(f"no action intel for em{ws.browsing_species or 0:02d}")
        return
    moves_row(ws, m)
    if ws.alignment is not None:
        alignment_view(ws, ws.alignment)
        bind_row(ws)
        imgui.separator()
    pair_table(ws, intel)


def species_choices(ws: MonsterWorkspace) -> list[int]:
    return species.available(ws.intel_root) if ws.intel_root else list(files.EM_SPECIES)


def species_row(ws: MonsterWorkspace) -> None:
    """Which overlay's actions these are, and the seventeen the port could ride."""
    from imgui_bundle import imgui

    ids = species_choices(ws)
    if not ids:
        return
    cur, hs = ws.browsing_species, ws.host_species
    imgui.set_next_item_width(110.0)
    labels = [f"em{s:02d}" + ("  (host)" if s == hs else "") for s in ids]
    changed, pick = imgui.combo("##species", ids.index(cur) if cur in ids else 0, labels)
    if changed and ids[pick] != cur:
        ws.browse_species(ids[pick])
    imgui.same_line()
    if ws.browsing_the_host:
        imgui.text_disabled(f"the host this port rides (port.host_species = {hs})")
    else:
        colored(
            f"! browsing em{cur or 0:02d}: this port rides em{hs or 0:02d}, so these pairs are NOT "
            "the ones its engine dispatches. Comparing hosts, not binding: host_species also "
            "picks the host frame PAC the porter files clips into, so changing it is a rebuild.",
            AMBER,
            wrapped=True,
        )
    changed, ws.show_host = imgui.checkbox(f"show em{cur or 0:02d} beside the port", ws.show_host)
    if changed and not ws.show_host:
        ws.sync_reference()
    imgui.same_line()
    imgui.text_disabled("what the action actually looks like")
    tooltip(
        "Loads the host species' own PAC and plays the clip its handler passes to the "
        "executor, beside your port. Both run at the same rate; each loops at its OWN end."
    )
    hosts_table(ws, len(ids))


def hosts_table(ws: MonsterWorkspace, n: int) -> None:
    """The overlays side by side: which host should this port ride?"""
    from imgui_bundle import imgui

    if not imgui.collapsing_header(f"compare the {n} MHFU action tables"):
        return
    imgui.text_wrapped(
        "Every overlay has the identical action tick: 8 mains, each fanning into a sub_state "
        "switch. What differs is how much vocabulary you inherit. 'timed' pairs test fixed clip "
        "frames your animation has to hit; 'budget' ones end on a frame countdown instead of on "
        "your clip."
    )
    hosts = ws.host_options()
    if hosts is None:
        imgui.text_disabled("surveying the overlays (built once, then cached)...")
        return
    if not imgui.begin_table("##hosts", 6, table_flags()):
        return
    for name in ("overlay", "pairs", "timed", "budget", "fx", "opaque"):
        imgui.table_setup_column(name)
    imgui.table_headers_row()
    for h in hosts:
        imgui.table_next_row()
        imgui.table_next_column()
        star = " *" if h.species == ws.host_species else ""
        label = f"em{h.species:02d}{star}##h{h.species}"
        if imgui.selectable(label, h.species == ws.browsing_species, span_all())[0]:
            ws.browse_species(h.species)
        for value in (h.pairs, h.timed, h.budget, h.effects):
            imgui.table_next_column()
            imgui.text(str(value))
        imgui.table_next_column()
        imgui.text_disabled(f"{h.opaque_mains}/8")
        tooltip(
            "mains with no visible sub_state jump table: 'we cannot see it', which is not "
            "'it does not exist'"
        )
    imgui.end_table()


def moves_row(ws: MonsterWorkspace, m: Manifest) -> None:
    from imgui_bundle import imgui

    if not m.moves:
        imgui.text_wrapped(
            "[moves] is empty: pick a pair below to try it against the clip on screen; bind it "
            "when it fits."
        )
        return
    imgui.text_disabled("moves")
    for name in sorted(m.moves):
        mv = m.moves[name]
        imgui.same_line()
        if imgui.small_button(f"{name} ({mv.main},{mv.sub})"):
            if mv.clip in m.clips:
                ws.play_slot(m.clips[mv.clip].slot)
            ws.select_pair(mv.main, mv.sub, name)


def bind_row(ws: MonsterWorkspace) -> None:
    """Writes the alignment down as `[moves.<name>]`; refused for a pair the census measured as
    never entered (`allow_unentered` belongs in the file, beside the reason)."""
    from imgui_bundle import imgui

    al = ws.alignment
    if al is None or ws.label_session is None:
        return
    if not ws.browsing_the_host:
        imgui.text_disabled(
            f"cannot bind: this pair belongs to em{ws.browsing_species or 0:02d}, and the engine "
            f"dispatches em{ws.host_species or 0:02d} for this port"
        )
        return
    imgui.set_next_item_width(140.0)
    _, ws.bind_buf = imgui.input_text("##bindname", ws.bind_buf)
    imgui.same_line()
    if any(f.code == "NEVER_ENTERED" for f in al.errors):
        imgui.text_disabled("cannot bind: the census says the engine never enters this pair")
        return
    if imgui.small_button("bind as move"):
        ws.bind_move()
    imgui.same_line()
    clip = f" / {al.clip}" if al.clip else ""
    imgui.text_disabled(f"-> [moves] on ({al.main},{al.sub}){clip}")


def pair_table(ws: MonsterWorkspace, intel: SpeciesIntel) -> None:
    """Every pair the overlay dispatches: choosing one is the authoring act, before a move
    exists, so the alignment follows whatever clip is playing."""
    from imgui_bundle import imgui

    imgui.set_next_item_width(120.0)
    _, ws.pair_filter = imgui.input_text("##pf", ws.pair_filter)
    imgui.same_line()
    imgui.text_disabled(f"filter {len(intel)} pairs em{ws.browsing_species or 0:02d} dispatches")
    height = max(PAIR_TABLE_MIN, imgui.get_content_region_avail().y)
    if not imgui.begin_table("##pairs", 5, table_flags(scroll=True), imgui.ImVec2(0.0, height)):
        return
    columns((("pair", 0.8), ("ends on", 0.9), ("tests", 1.0), ("fx", 0.3), ("after", 1.1)))
    needle = ws.pair_filter.strip().lower()
    for p in intel:
        gates = p.tested_frames
        nxt = " ".join(f"({m},{s})" for m, s in p.successors[:4])
        row = f"{p.main},{p.sub} {p.ends_on} {' '.join(f'{f:g}' for f in gates)} {nxt}"
        if needle and needle not in row.lower():
            continue
        imgui.table_next_row()
        imgui.table_next_column()
        sel = ws.pair == (p.main, p.sub)
        if imgui.selectable(f"({p.main},{p.sub})##p{p.main}_{p.sub}", sel, span_all())[0]:
            ws.select_pair(p.main, p.sub)
        imgui.table_next_column()
        if p.budget.gated:
            colored("budget", AMBER)
        else:
            imgui.text_disabled(p.ends_on or "?")
        imgui.table_next_column()
        imgui.text(", ".join(f"{f:g}" for f in gates[:4]) or "·")
        imgui.table_next_column()
        imgui.text(str(len(p.effects)) if p.effects else "")
        imgui.table_next_column()
        # blank on a handled pair: it never ends itself; "?": the intel has no hand-offs
        if p.next is None:
            imgui.text_disabled("?")
        elif not p.next:
            imgui.text_disabled("holds")
        else:
            imgui.text_disabled(nxt + (" +" if len(p.successors) > 4 else ""))
        if p.next:
            tooltip("\n".join(map(str, p.next)))
    imgui.end_table()


def alignment_view(ws: MonsterWorkspace, al: Alignment) -> None:
    """The headline, then the findings, folded unless something is wrong."""
    from imgui_bundle import imgui

    if imgui.small_button("< all pairs"):
        ws.clear_pair()
        return
    imgui.same_line()
    imgui.text(f"{al.move}  ->  ({al.main},{al.sub})")
    if al.clip:
        imgui.same_line()
        imgui.text_disabled(f"clip {al.clip}" + (f"  {al.frames}f" if al.frames else ""))
    colored(al.headline, (0.92, 0.94, 0.98, 1.0), wrapped=True)
    host_clip_row(ws, al)
    hits_with_row(ws, al)
    then_row(ws, al)
    if not al.findings:
        return
    n_err, n_warn = len(al.errors), len(al.warnings)
    s = "" if len(al.findings) == 1 else "s"
    counts = f"  -  {n_err} error, {n_warn} warning" if n_err or n_warn else ""
    imgui.set_next_item_open(bool(al.errors), imgui.Cond_.once.value)
    if not imgui.collapsing_header(f"{len(al.findings)} finding{s}{counts}###findings"):
        finding_dots(al)
        return
    for f in al.findings:
        mark = {"error": "x", "warning": "!"}.get(f.level, "-")
        colored(f"{mark}  {f.message}", LEVELS[f.level], wrapped=True)
    if al.pair is not None and al.pair.handler:
        a1 = ",".join(map(str, al.pair.a1)) or "-"
        imgui.text_disabled(f"handler 0x{al.pair.handler:08X}   a1 {a1}")
    species_effects(ws)


def then_row(ws: MonsterWorkspace, al: Alignment) -> None:
    """What the ENGINE does after this pair, beside what the move DECLARES."""
    from imgui_bundle import imgui

    p = al.pair
    if p is None or p.next is None:
        return
    if not p.next:
        colored(
            f"! ({p.main},{p.sub}) never ends by itself: forced, it stays until something else "
            "moves him. A move here needs `after =`.",
            AMBER,
            wrapped=True,
        )
    else:
        imgui.text_disabled("engine: after")
        for e in p.next:
            imgui.same_line()
            tgt = "/".join(f"({m},{s})" for m, s in e.to) or "?"
            if imgui.small_button(f"{tgt}##then{e.site}") and e.to:
                ws.select_pair(*e.to[0])
                return
            tooltip(str(e))
            if e.reason:
                imgui.same_line()
                imgui.text_disabled(e.reason)
    m = ws.manifest
    mv = None if m is None or not al.move else m.moves.get(al.move)
    if m is not None and mv is not None and mv.after:
        nxt = m.moves.get(mv.after)
        where = f" ({nxt.main},{nxt.sub})" if nxt else "  (no such move!)"
        imgui.text_disabled(f"declared after = {mv.after}{where}")
    imgui.same_line()
    if imgui.small_button("open in Moves tab"):
        ws._focus = "Moves"


def host_clip_row(ws: MonsterWorkspace, al: Alignment) -> None:
    """Which clip the host plays for this action. An a1 its pack lacks says so, rather than
    falling through to another a1: the wrong animal doing the wrong thing."""
    from imgui_bundle import imgui

    p = al.pair
    if p is None or not p.a1:
        return
    table = ws.host_clip_table() if ws.show_host else {}
    imgui.text_disabled("host plays")
    for a1 in p.a1:
        imgui.same_line()
        if not ws.show_host:
            imgui.text_disabled(str(a1))
            continue
        if a1 not in table:
            colored(f"{a1}?", GREY)
            tooltip(
                f"the handler names a1 = {a1} and em{ws.browsing_species or 0:02d}'s pack has no "
                f"slot {a1}. Either the action is unreachable, or its clip comes from somewhere "
                "this tool cannot see."
            )
            continue
        on = " *" if ws.host_clip == a1 else ""
        if imgui.small_button(f"{a1}{on}##a{a1}"):
            ws.play_host_clip(a1)
        frames, loop = table[a1]
        tooltip(
            f"{frames} frames{', loops' if loop else ''}: which of the {len(p.a1)} runs depends "
            "on runtime state"
        )
    if ws.show_host and table and not any(a in table for a in p.a1):
        colored(
            f"! none of this action's clips is in em{ws.browsing_species or 0:02d}'s pack, so "
            "the host beside you is showing its DEFAULT pose, not this action. 112 of em75's 225 "
            "pairs are like this.",
            AMBER,
            wrapped=True,
        )


def finding_dots(al: Alignment) -> None:
    """A one-line summary standing in for the folded findings."""
    from imgui_bundle import imgui

    bits = [(f.code.replace("_", " ").lower(), f.level) for f in al.findings if f.level != "info"]
    if not bits:
        imgui.text_disabled("nothing to flag")
        return
    for i, (code, level) in enumerate(bits):
        if i:
            imgui.same_line()
        colored(code, LEVELS[level])


def species_effects(ws: MonsterWorkspace) -> None:
    """The species' framed effect spawns: NOT attributed to any pair (they hang off a switch no
    pair handler calls), shown because only there is the timing legible."""
    from imgui_bundle import imgui

    fx = [] if ws.intel is None else ws.intel.framed_effects()
    if not fx or not imgui.collapsing_header(f"{len(fx)} framed effect site(s), species-wide"):
        return
    imgui.text_wrapped(
        "! these are NOT attributed to any (main,sub): they hang off a species-byte switch no "
        "pair handler calls, so which action fires them is not decidable offline. Bones are the "
        "host's."
    )
    rig = ws.port_rig()
    for e in fx:
        imgui.bullet_text(f"effect {e.id:<3d}  bone {e.bone!s:<3}  frame {e.frame}")
        if rig is not None and e.bone is not None:
            tooltip(f"on YOUR rig: {rig.describe(e.bone)}")


def hits_with_row(ws: MonsterWorkspace, al: Alignment) -> None:
    """What this action hits with: the attack records its handler spawns and their sets."""
    from imgui_bundle import imgui

    host, p = ws.host_attacks(), al.pair
    if host is None or p is None or not ws.browsing_the_host:
        return
    if not p.attack_ids:
        n = p.attack_sites_computed
        computed = f" ({n} computed id{'' if n == 1 else 's'})" if n else ""
        imgui.text_disabled(f"hits with: nothing the static scan can see{computed}")
        return
    recs = host.records_for(p.attack_ids, ws.host_species)
    if not recs:
        ids = ",".join(map(str, p.attack_ids))
        imgui.text_disabled(
            f"hits with: attack id(s) {ids}, unresolvable for species {ws.host_species} (no id "
            "offset known)"
        )
        return
    parts = []
    for a in recs:
        st = host.set(a.volume)
        desc = "" if st is None else st.describe()
        desc = "" if not desc else f" [{desc[:60]}{'...' if len(desc) > 60 else ''}]"
        parts.append(
            f"attack {a.id} (power {a.power}, elem 0x{a.element:02X}) -> set {a.volume}{desc}"
        )
    imgui.text("hits with:")
    imgui.same_line()
    imgui.text_wrapped(plain("; ".join(parts)))
    for st_index in sorted({a.volume for a in recs}):
        if imgui.small_button(f"edit set {st_index} in Hitboxes"):
            sess = ws.attack_session
            ws.show_attacks = True
            authored = sess is not None and bool(sess.volumes_of(st_index))
            ws.attacks_source = "port" if authored else "host"
            ws.select_set(st_index)
            ws.sync_attacks()
        imgui.same_line()
    imgui.new_line()
