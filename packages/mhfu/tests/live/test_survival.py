import time

from mhfu import addresses as a
from mhfu.live import survival

TIGREX, POPO = a.RAM.start + 0x90_0000, a.RAM.start + 0x90_2000  # heap addresses in `fake`


def spawn(fake):
    registry = [0] * 21
    registry[3], registry[9] = POPO, TIGREX  # sparse: a null before the big monster
    fake.poke("21I", a.ENTITY_REGISTRY, *registry)
    fake.poke("I", TIGREX + a.ENTITY.VTABLE, a.TIGREX_VTABLE)
    fake.poke("B", TIGREX + a.ENTITY.SPECIES, 0x4B)
    fake.poke("f", TIGREX + a.ENTITY.ENGAGE, 1.0)
    fake.poke("I", POPO + a.ENTITY.VTABLE, a.POPO_VTABLE)
    fake.poke("f", a.SPECIES_TABLE + 0x4B * a.SPECIES.stride + a.SPECIES.SIGHT_RADIUS, 5000.0)


def test_big_monsters(s, fake):
    spawn(fake)
    assert [m.base for m in survival.big_monsters(s)] == [TIGREX]


def test_levers(s, fake):
    spawn(fake)
    survival.hold_timer(s)
    survival.top_up_hp(s, 150)
    survival.calm(s, survival.big_monsters(s))
    assert fake.peek("I", a.QUEST_SINGLETON + a.QUEST.TIMER) == (survival.FULL_TIMER,)
    assert fake.peek("I", a.QUEST_TIMER_MIRROR) == (survival.FULL_TIMER,)
    assert fake.peek("2H", a.PLAYER_ENTITY + a.ENTITY.HP_CAP) == (150, 0)
    assert fake.peek("H", a.PLAYER_ENTITY + a.ENTITY.HP) == (150,)
    assert fake.peek("f", TIGREX + a.ENTITY.ENGAGE) == (0.0,)
    assert s.game.species(0x4B).sight_radius == 0.0


def test_guard(s, fake):
    spawn(fake)
    lines = []
    with survival.Guard(s, tick=0.01, log=lines.append) as guard:
        deadline = time.monotonic() + 5
        while guard.hp_writes < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert guard.hp_writes >= 3
    assert fake.peek("H", a.PLAYER_ENTITY + a.ENTITY.HP) == (100,)
    assert fake.peek("f", TIGREX + a.ENTITY.ENGAGE) == (0.0,)
    assert guard.report() == (
        f"guard: hp pinned at 100 ({guard.hp_writes} writes), clock held, 1 big monster(s) calmed"
    )
    assert lines == [f"guard: big monsters ['{hex(TIGREX)}']"]
