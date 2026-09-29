import pytest
from mhfu import addresses as a
from mhfu.memory import Image
from mhfu.structs import Entity, Game
from mhfu.views import View, u8, u16, u32

BASE = 0x0900_0000  # noaddr: an arbitrary entity base


def test_fields_read_and_write():
    mem = Image(bytes(0x800), BASE)
    e = Entity(mem, BASE)
    e.hp = 1234
    e.position = (1.0, 2.0, 3.0)
    assert mem.u16(BASE + a.ENTITY.HP) == 1234
    assert e.position == (1.0, 2.0, 3.0)
    assert Entity.hp.address(e) == BASE + a.ENTITY.HP


def test_absolute_view():
    mem = Image(bytes(0x10), a.SCREEN_STATE)
    mem.write_u8(a.SCREEN_STATE, 17)
    assert Game(mem).screen_state == 17


def test_type_must_match_the_table():
    with pytest.raises(TypeError, match="u16"):
        u32(a.ENTITY.HP)


# Python 3.11 wraps an error in __set_name__ in a RuntimeError
WRONG_OWNER = (TypeError, RuntimeError)


def test_field_must_belong_to_the_struct():
    with pytest.raises(WRONG_OWNER):

        class Wrong(View):  # noqa: F841
            struct = a.QUEST
            hp = u16(a.ENTITY.HP)

    with pytest.raises(WRONG_OWNER):

        class Absolute(View):  # noqa: F841
            hp = u16(a.ENTITY.HP)

    with pytest.raises(WRONG_OWNER):

        class Relative(View):  # noqa: F841
            struct = a.ENTITY
            screen = u8(a.SCREEN_STATE)
