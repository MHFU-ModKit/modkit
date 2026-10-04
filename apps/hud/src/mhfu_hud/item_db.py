"""Item id (a PLAYER_BAG slot's u16) -> name and icon slug; only the ids listed are named."""

from __future__ import annotations

ITEM_NAMES = {
    0x0040: ("Paintball", "paintball"),
}


def identify(item_id: int) -> tuple[str, str | None]:
    """(name, icon slug); an unknown id is its hex, without a slug."""
    if item_id == 0:
        return "", None
    return ITEM_NAMES.get(item_id, (f"0x{item_id:04X}", None))
