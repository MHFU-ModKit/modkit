import pytest
from mhfu import addresses as a
from mhfu.live import hall, quests, village
from mhfu.structs import Screen

TEXT = a.RAM.start + 0x90_0000
CARDS = [
    ("Hunt the Velocidrome", "Hunt a Velocidrome", "Velocidrome\nVelociprey"),
    ("Mushroom Delivery", "Deliver 10 mushrooms", ""),
    ("Jungle Danger", "Hunt a Yian Kut-Ku", "Yian Kut-Ku"),
    ("Velociprey Cull", "Slay 10 Velociprey", "Velociprey"),
]


def write(fake, address, text):
    raw = text.encode() + b"\0"
    fake.poke(f"{len(raw)}s", address, raw)


class Board:
    """The elder's board: a talk of `pages` lines, the rank menu, a line, the quest list, the
    fee question, a line, free roam. The first talk says `first` and ends without a board; the
    first `declines` fee questions are backed out of."""

    def __init__(self, fake, monkeypatch, cards=CARDS, pages=2, locked=(), first=None, declines=0):
        self.fake, self.cards, self.pages, self.locked, self.first = (
            fake,
            cards,
            pages,
            locked,
            first,
        )
        self.declines = declines
        self.talks, self.contract, self.stage = [], None, "roam"
        self.show(a.SCENE_VILLAGE)
        fake.poke("B", a.SCREEN_STATE, Screen.BOOT)
        fake.on_press.append(self.press)
        monkeypatch.setattr(village, "talk_to", self.talk)

    def show(self, scene, line=None):
        if line is not None:
            write(self.fake, TEXT, line)
            self.fake.poke("I", a.DIALOG_TEXT, TEXT)
        self.fake.poke("I", a.SCENE_OBJECT, scene)

    def card(self, index):
        name, objective, monsters = self.cards[index]
        base = a.QUEST_CARD_TEXT
        write(self.fake, base + a.QUEST_CARD.NAME, name)
        write(self.fake, base + a.QUEST_CARD.OBJECTIVE, objective)
        write(self.fake, base + a.QUEST_CARD.MONSTERS, monsters)
        self.fake.poke("H", a.QUEST_INDEX, index)
        self.index = index

    def talk(self, s, npc):
        ends = self.first is not None and not self.talks
        lines = list(self.first if ends else ["What level of Quest?"] * self.pages)
        self.talks.append(lines[0])
        self.left, self.ends, self.stage = lines[1:], ends, "talk"
        self.show(a.SCENE_DIALOG, lines[0])
        return lines[0]

    def press(self, button):
        stage, cross = self.stage, button == "cross"
        if button == "circle" or (stage == "talk" and cross and self.ends and not self.left):
            self.stage = "roam"
            self.show(a.SCENE_VILLAGE)
        elif stage == "talk" and cross and self.left:
            self.show(a.SCENE_DIALOG, self.left.pop(0))
        elif stage == "talk" and cross:
            self.stage, self.rank = "rank", 0
            self.fake.poke("H", a.QUEST_RANK_CURSOR, 0)
            self.show(a.SCENE_QUEST_RANK)
        elif stage == "rank" and button in ("up", "down"):
            self.rank = (self.rank + (1 if button == "down" else -1)) % len(quests.RANKS)
            self.fake.poke("H", a.QUEST_RANK_CURSOR, self.rank)
        elif stage == "rank" and cross and self.rank not in self.locked:
            self.stage = "handoff"
            self.show(a.SCENE_DIALOG, "You can handle that.")
        elif stage == "handoff" and cross:
            self.stage = "list"
            self.card(0)
            self.show(a.SCENE_QUEST_LIST)
        elif stage == "list" and button in ("left", "right"):
            self.card((self.index + (1 if button == "right" else -1)) % len(self.cards))
        elif stage == "list" and cross:
            self.stage = "fee"
            self.show(a.SCENE_YES_NO)
        elif stage == "fee" and cross and self.declines:
            self.declines -= 1
            self.stage = "roam"
            self.show(a.SCENE_VILLAGE)
        elif stage == "fee" and cross:
            self.stage = "ready"
            self.show(a.SCENE_DIALOG, "When you're ready, press square at the exit.")
        elif stage == "ready" and cross:
            self.stage, self.contract = "roam", self.cards[self.index][0]
            self.show(a.SCENE_VILLAGE_CONTRACT)
            self.fake.poke("B", a.SCREEN_STATE, Screen.CONTRACT)


def test_open_board(s, fake, monkeypatch):
    board = Board(fake, monkeypatch)
    quests.open_board(s, "2_star")
    assert quests.in_quest_list(s)
    assert (board.rank, board.talks) == (1, ["What level of Quest?"])


def test_open_board_cancels_a_contract(s, fake, monkeypatch):
    cancel = ["Would you like to cancel the contract?", "Too bad. I'll cancel it."]
    board = Board(fake, monkeypatch, first=cancel)
    quests.open_board(s, 0)
    assert quests.in_quest_list(s)
    assert len(board.talks) == 2


def test_locked_rank(s, fake, monkeypatch):
    Board(fake, monkeypatch, locked={5})
    with pytest.raises(quests.LockedRankError, match="6_star"):
        quests.open_board(s, 5)


def test_locked_counter(s, fake, monkeypatch):
    board = Board(fake, monkeypatch)
    turned_away = ["Only the best hunters here.", "Come back when you have improved."]

    def talk_to_counter(s, role):
        board.first, board.talks = turned_away, []
        board.talk(s, None)
        return True

    monkeypatch.setattr(hall, "talk_to_counter", talk_to_counter)
    with pytest.raises(quests.LockedRankError, match="counter_high.*improved"):
        quests.open_board(s, 0, board="hall", counter="counter_high")


def test_no_board(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    monkeypatch.setattr(village, "talk_to", lambda s, npc: None)
    with pytest.raises(TimeoutError, match="elder in 3 tries"):
        quests.open_board(s)


def test_list_quests(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    quests.open_board(s)
    cards = quests.list_quests(s)
    assert [c.name for c in cards] == [name for name, _, _ in CARDS]
    assert cards[0].monsters == ("Velocidrome", "Velociprey")
    assert s.game.quest_index == 0


def test_a_list_of_one(s, fake, monkeypatch):
    Board(fake, monkeypatch, cards=CARDS[:1])
    quests.open_board(s)
    assert [c.name for c in quests.list_quests(s)] == [CARDS[0][0]]


@pytest.mark.parametrize(
    ("want", "found"),
    [
        ("velociprey", "Velociprey Cull"),  # a later name beats an earlier monster
        ("kut-ku", "Jungle Danger"),
        ("MUSHROOM", "Mushroom Delivery"),
        ("Tigrex", None),
    ],
)
def test_select(s, fake, monkeypatch, want, found):
    Board(fake, monkeypatch)
    quests.open_board(s)
    card = quests.select(s, want)
    assert (card and card.name) == found
    if card:
        assert s.game.quest_index == card.index


def test_select_ignores_where_the_cursor_starts(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    quests.open_board(s)
    quests.page(s)
    quests.page(s)
    assert quests.select(s, "velocidrome").name == "Hunt the Velocidrome"


def test_select_names_only(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    quests.open_board(s)
    assert quests.select(s, "kut-ku", names_only=True) is None


def test_accept(s, fake, monkeypatch):
    board = Board(fake, monkeypatch)
    quests.open_board(s)
    quests.select(s, "danger")
    assert quests.accept(s) == "Jungle Danger"
    assert board.contract == "Jungle Danger"
    assert fake.presses[-3:] == ["cross"] * 3


def test_take_retries_until_a_contract_departs(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    departs = []

    def depart(s, attempts):
        departs.append(attempts)
        if len(departs) == 1:
            raise TimeoutError("no departure prompt")
        return 98

    monkeypatch.setattr(village, "depart", depart)
    said = []
    taken = quests.take(s, 0, "velocidrome", log=said.append)
    assert (taken.card.name, taken.area) == ("Hunt the Velocidrome", 98)
    assert departs == [2, 2]
    assert said[2:4] == [
        "accepted 'Hunt the Velocidrome'",
        "no contract took (attempt 1/3): no departure prompt",
    ]


def test_take_retries_a_declined_contract_at_once(s, fake, monkeypatch):
    board = Board(fake, monkeypatch, declines=1)
    departs = []
    monkeypatch.setattr(village, "depart", lambda s, attempts: departs.append(attempts) or 98)
    said = []
    assert quests.take(s, 0, "velocidrome", log=said.append).area == 98
    assert (departs, board.contract) == ([2], "Hunt the Velocidrome")
    assert said[2].startswith("no contract took (attempt 1/3)")


def test_take_gives_up(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    departs = []

    def depart(s, attempts):
        departs.append(attempts)
        raise TimeoutError("no departure prompt")

    monkeypatch.setattr(village, "depart", depart)
    with pytest.raises(quests.NoContractError):
        quests.take(s, 0, "velocidrome")
    assert departs == [2, 2, 6]


def test_take_from_the_hall(s, fake, monkeypatch):
    board = Board(fake, monkeypatch)
    monkeypatch.setattr(hall, "talk_to_counter", lambda s, role: board.talk(s, role) and True)
    monkeypatch.setattr(hall, "depart", lambda s, attempts: 107)
    assert quests.take(s, 0, "mushroom", board="hall").area == 107


def test_take_without_leaving(s, fake, monkeypatch):
    board = Board(fake, monkeypatch)
    monkeypatch.setattr(village, "depart", lambda s, attempts: pytest.fail("departed"))
    taken = quests.take(s, 0, "cull", leave=False)
    assert (taken.area, board.contract) == (None, "Velociprey Cull")


def test_take_nothing_matches(s, fake, monkeypatch):
    Board(fake, monkeypatch)
    with pytest.raises(LookupError, match="Tigrex"):
        quests.take(s, 0, "Tigrex")
