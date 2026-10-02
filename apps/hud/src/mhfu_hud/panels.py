"""Sub-panels and formatting shared by more than one layout."""

from __future__ import annotations

import pygame
from mhfu.addresses import Field

from . import widgets as W
from .state import Cell, CellValue, Context, GameSnapshot, MonsterHUD, PlayerHUD
from .theme import C, font, hp_color

QUEST_TIMER_FPS = 30
"""QUEST.TIMER counts frames of the 30 Hz game logic, not the 60 Hz display."""

RectLike = pygame.Rect | tuple[int, int, int, int]


def cell_label(field: Field) -> str:
    """A field as `NAME +0xOFF`, from addresses.toml."""
    return f"{field.name} +0x{int(field):X}"


def fmt_value(field: Field, value: CellValue) -> str:
    """`value` formatted by the field's declared type."""
    kind = field.type.split("[", 1)[0]
    if isinstance(value, tuple):
        if kind in ("f32", "vec3"):
            return ", ".join(f"{float(v):.1f}" for v in value)
        return "/".join(str(v) for v in value)
    if kind in ("ptr", "vtable", "u32", "fn", "code"):
        return f"0x{int(value):08X}"
    if kind == "f32":
        return f"{float(value):.2f}"
    return str(value)


def fmt_cell(cell: Cell) -> str:
    return fmt_value(cell.field, cell.value)


def action_text(m: MonsterHUD) -> str:
    """The decoded action per slot where the species decodes, else the raw inputs."""
    if m.actions is None:
        return "input " + "/".join(str(v) for v in m.anim_input)
    if len(set(m.actions)) == 1:
        return f"action {m.actions[0]}"
    return "actions " + "/".join(str(v) for v in m.actions)


def state_text(m: MonsterHUD) -> str:
    main, sub = m.state
    return f"({main}, {sub})"


def section_text(snap: GameSnapshot) -> str:
    """The tracked section: "camp" for a base-camp area index, "?" while unknown."""
    if snap.tracked_section is not None:
        return str(snap.tracked_section)
    return "camp" if snap.tracked_section_source == "area_index" else "?"


def draw_vitals(surface: pygame.Surface, rect: RectLike, player: PlayerHUD) -> None:
    """HP and stamina bars with their numbers, like the game's own top-left HUD."""
    rect = pygame.Rect(rect)
    W.panel(surface, rect, title="VITALS")
    x, y, w = rect.x, rect.y, rect.width

    if player.weapon_drawn is None:
        badge, bc = "WEAPON ?", C.PLACEHOLDER
    elif player.weapon_drawn:
        badge, bc = "DRAWN", C.ACCENT
    else:
        badge, bc = "SHEATHED", C.TEXT_DIM
    W.text(surface, badge, (x + w - 10, y + 6), size=11, color=bc, bold=True, align="right")

    hp_row = y + 30
    W.text(surface, "HP", (x + 12, hp_row + 1), size=14, color=C.TEXT_DIM, bold=True)
    hp_bar = pygame.Rect(x + 44, hp_row, w - 134, 16)
    if player.hp is None:
        W.placeholder_box(surface, hp_bar, "not loaded")
        W.text(surface, "--/--", (x + w - 10, hp_row), size=14, color=C.PLACEHOLDER, align="right")
    else:
        hp_max = max(1, player.hp_max)
        frac = player.hp / hp_max
        recov = player.hp_recov if player.hp_recov is not None else player.hp
        W.hp_bar(surface, hp_bar, frac, recov / hp_max, hp_color(frac))
        W.text(
            surface,
            f"{player.hp}/{hp_max}",
            (x + w - 10, hp_row),
            size=14,
            color=C.TEXT,
            bold=True,
            align="right",
        )

    st_row = y + 56
    W.text(surface, "STA", (x + 12, st_row + 1), size=14, color=C.TEXT_DIM, bold=True)
    st_bar = pygame.Rect(x + 44, st_row, w - 134, 16)
    if player.stamina is None:
        W.placeholder_box(surface, st_bar, "not loaded")
        W.text(surface, "--/--", (x + w - 10, st_row), size=14, color=C.PLACEHOLDER, align="right")
    else:
        st_max = max(1, player.stamina_max)
        W.bar(surface, st_bar, player.stamina / st_max, C.STAMINA)
        W.text(
            surface,
            f"{player.stamina}/{st_max}",
            (x + w - 10, st_row),
            size=14,
            color=C.TEXT,
            bold=True,
            align="right",
        )


def fmt_timer(frames: int) -> str:
    """Quest timer frames as MM:SS."""
    secs = max(0, frames) // QUEST_TIMER_FPS
    return f"{secs // 60:02d}:{secs % 60:02d}"


def draw_raw_strip(surface: pygame.Surface, rect: RectLike, snapshot: GameSnapshot) -> pygame.Rect:
    """A row of raw memory oracles: the debug truth behind the HUD."""
    rect = pygame.Rect(rect)
    W.panel(surface, rect, fill=C.PANEL)
    big = sum(1 for m in snapshot.monsters if m.big)
    bag_used = sum(1 for s in snapshot.player.bag if not s.empty)
    cells = [
        ("CONTEXT", snapshot.context.value),
        ("SCREEN", snapshot.screen_state),
        ("SUB-AREA", snapshot.map_subsection),
        ("SCENE", f"0x{snapshot.scene:08X}"),
        ("CARVE", snapshot.carve_count),
        ("MON s/b", f"{len(snapshot.monsters) - big}/{big}"),
        ("BAG", f"{bag_used}/{len(snapshot.player.bag) or 24}"),
        ("POLL ms", f"{snapshot.poll_latency_ms:.0f}"),
    ]
    cw = rect.width / len(cells)
    for i, (label, value) in enumerate(cells):
        cx = rect.x + cw * i + 10
        size = 15 if font(15).size(str(value))[0] <= cw - 14 else 11  # a narrow strip
        W.text(surface, label, (cx, rect.y + 7), size=10, color=C.TEXT_FAINT, bold=True)
        W.text(surface, value, (cx, rect.y + 20 + (15 - size) // 2), size=size, color=C.TEXT)
    return rect


def draw_status_screen(surface: pygame.Surface, snapshot: GameSnapshot) -> None:
    """The whole canvas for a context without a layout: disconnected, menus, loading."""
    surface.fill(C.BG)
    w, h = surface.get_size()
    ctx = snapshot.context
    headline = {
        Context.DISCONNECTED: "NOT CONNECTED",
        Context.BOOT: "WAITING FOR GAME",
        Context.MENU: "IN MENUS",
        Context.LOADING: "LOADING AREA...",
    }.get(ctx, ctx.value.upper())
    color = {
        Context.DISCONNECTED: C.ERR,
        Context.BOOT: C.WARN,
        Context.MENU: C.TEXT_DIM,
        Context.LOADING: C.ACCENT,
    }.get(ctx, C.TEXT_DIM)

    W.text(
        surface,
        "MHFU LIVE HUD",
        (w // 2, h // 2 - 96),
        size=22,
        color=C.TEXT_DIM,
        bold=True,
        align="center",
    )
    W.text(
        surface, headline, (w // 2, h // 2 - 56), size=40, color=color, bold=True, align="center"
    )
    W.text(
        surface,
        snapshot.status_text,
        (w // 2, h // 2 + 4),
        size=16,
        color=C.TEXT_DIM,
        align="center",
    )
    if snapshot.game_title:
        W.text(
            surface,
            snapshot.game_title,
            (w // 2, h // 2 + 34),
            size=14,
            color=C.TEXT_FAINT,
            align="center",
        )
    hint = (
        "Start PPSSPP, load MHFU, and enable the remote debugger "
        "(Settings > Tools > Developer Tools)."
    )
    if ctx in (Context.MENU, Context.LOADING, Context.BOOT):
        hint = "The HUD switches automatically once you enter a village or quest."
    W.text(surface, hint, (w // 2, h // 2 + 80), size=13, color=C.TEXT_FAINT, align="center")
