"""The game's live structs as typed views: `Game` for the fixed addresses, `Entity` for
monsters, the player's combat entity and village NPCs."""

from __future__ import annotations

from . import addresses as a
from .memory import Memory
from .views import View, ptr, u8, u16, vec3


class Game(View):
    """Fixed addresses: screen, area and the entity registry."""

    screen_state = u8(a.SCREEN_STATE)
    area_index = u16(a.AREA_INDEX)


class Species(View):
    """A SPECIES_TABLE row, shared by every entity of that species on the map."""

    struct = a.SPECIES
    hurtbox_set = ptr(a.SPECIES.HURTBOX_SET)
    hitzone_states = ptr(a.SPECIES.HITZONE_STATES)

    @classmethod
    def at(cls, mem: Memory, species: int) -> Species:
        return cls(mem, a.SPECIES_TABLE + species * a.SPECIES.stride)


class Entity(View):
    struct = a.ENTITY

    vtable = ptr(a.ENTITY.VTABLE)
    species = u8(a.ENTITY.SPECIES)
    position = vec3(a.ENTITY.POSITION)
    section = u16(a.ENTITY.SECTION)
    hp = u16(a.ENTITY.HP)
    max_hp = u16(a.ENTITY.MAX_HP)
