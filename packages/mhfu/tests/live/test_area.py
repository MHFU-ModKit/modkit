import pytest
from mhfu import addresses as a
from mhfu.live import area
from mhfu.structs import Screen


def after_reads(fake, address, steps):
    """At read n of `address`, apply `steps[n]` (a function), if there is one."""
    count = {"n": 0}

    def hook(addr):
        if addr == address:
            step = steps.get(count["n"])
            count["n"] += 1
            if step:
                step()

    fake.on_read.append(hook)


def enter(fake, index, subsection=1):
    fake.poke("B", a.SCREEN_STATE, Screen.IN_AREA)
    fake.poke("H", a.AREA_INDEX, index)
    fake.poke("B", a.MAP_SUBSECTION, subsection)


def test_wait_for_in_area(s, fake):
    fake.poke("B", a.SCREEN_STATE, Screen.MENU)
    after_reads(fake, a.SCREEN_STATE, {4: lambda: enter(fake, 98)})
    assert area.wait_for_in_area(s) == 98


def test_transition_between_sections_that_share_a_subsection(s, fake):
    enter(fake, 99)
    steps = {3: lambda: fake.poke("B", a.SCREEN_STATE, Screen.MENU), 6: lambda: enter(fake, 92)}
    after_reads(fake, a.SCREEN_STATE, steps)
    assert area.wait_for_transition(s) == 92


def test_transition_already_over(s, fake):
    enter(fake, 92)
    assert area.wait_for_transition(s, start=99) == 92
    assert fake.presses == []


def test_no_transition(s, fake):
    enter(fake, 99)
    with pytest.raises(TimeoutError, match="zone load"):
        area.wait_for_transition(s, timeout=2)


def test_settled_index_waits_out_churn(s, fake):
    enter(fake, 183)
    after_reads(fake, a.AREA_INDEX, {1: lambda: fake.poke("H", a.AREA_INDEX, 108)})
    after_reads(fake, a.AREA_INDEX, {2: lambda: fake.poke("H", a.AREA_INDEX, 101)})
    assert area.settled_index(s) == 101


def test_settled_index_needs_the_area(s, fake):
    fake.poke("H", a.AREA_INDEX, 35)
    with pytest.raises(TimeoutError, match="settled area index"):
        area.settled_index(s, timeout=2)
