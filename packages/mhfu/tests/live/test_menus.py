import pytest
from mhfu import addresses as a
from mhfu.live import menus
from mhfu.live.menus import ITEM_BOX_MENU, NPC_MENU, QUEST_RANK_MENU


def cursor(fake, fmt, address, rows, row=0, *, to_y=lambda r: r, every=1):
    """Script a cursor that wraps over `rows` and moves on every `every`-th up or down press."""
    state = {"row": row, "seen": 0}
    fake.poke(fmt, address, to_y(row))

    def press(button):
        if button not in ("up", "down"):
            return
        state["seen"] += 1
        if state["seen"] % every == 0:
            state["row"] = (state["row"] + (1 if button == "down" else -1)) % rows
            fake.poke(fmt, address, to_y(state["row"]))

    fake.on_press.append(press)
    return state


def test_goto_survives_debounce(s, fake):
    state = cursor(fake, "H", a.QUEST_RANK_CURSOR, 7, every=2)
    QUEST_RANK_MENU.goto(s, "6_star")
    assert state["row"] == 5 and QUEST_RANK_MENU.row(s) == 5
    assert fake.presses == ["up"] * 4  # 0 -> 6 -> 5, each move costing two presses


def test_goto_takes_the_short_way_round(s, fake):
    cursor(fake, "H", a.QUEST_RANK_CURSOR, 7, row=1)
    QUEST_RANK_MENU.goto(s, "urgent")
    assert fake.presses == ["up", "up"]


def test_unknown_row(s):
    with pytest.raises(KeyError, match="no row 'fortsetzen'"):
        QUEST_RANK_MENU.goto(s, "fortsetzen")


def test_highlight_off_the_menu(s, fake):
    fake.poke("H", a.BOX_MENU_HIGHLIGHT, 33)
    with pytest.raises(ValueError, match="is that menu open"):
        ITEM_BOX_MENU.row(s)


def test_stuck_cursor(s, fake):
    with pytest.raises(TimeoutError, match="stuck on row 0"):
        QUEST_RANK_MENU.goto(s, 3)
    assert len(fake.presses) == menus.STUCK_PRESSES


def test_npc_menu_rows_are_live(s, fake):
    base = a.NPC_MENU_STATE
    fake.poke("B", base + a.NPC_MENU.ROW_COUNT, 3)
    cursor(fake, "B", base + a.NPC_MENU.CURSOR, 3)
    NPC_MENU.goto(s, 2)
    assert fake.presses == ["up"]
    with pytest.raises(ValueError, match="outside 0..2"):
        NPC_MENU.goto(s, 3)
    fake.poke("B", base + a.NPC_MENU.ROW_COUNT, 0)
    with pytest.raises(ValueError, match="is it open"):
        NPC_MENU.goto(s, 0)
