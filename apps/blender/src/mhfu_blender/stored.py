# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What an imported monster carries in its custom properties, and the model read back from them;
no bpy.

On the armature object: the source bytes, so the .blend alone reads the model again (`model`),
and `CLIPS`, the clip in each animation slot. On each bone `JOINT`, on each mesh object `GROUP`
and its mesh `NORMAL`, on each Action its loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mhfu_port.model import Model

GAME = "mhfu_game"
"""`mhfu_port.model.MHFU` or `MHP3RD`."""
NAME = "mhfu_name"
PATH = "mhfu_path"
"""Where the model PAC was read from, for show and as the default export path."""
PAC = "mhfu_pac"
GEOMETRY = "mhfu_geometry"
"""A donor's geometry companion; absent on an MHFU PAC."""
MOVESET = "mhfu_moveset"
"""A donor's emNNN file; absent where none was read."""
EM_ID = "mhfu_em_id"
"""A donor's em id as the model was read with it; absent for none."""
CLIPS = "mhfu_clips"
"""`{str(slot): Action}`: which Action plays in each slot. The only record of a slot."""
JOINT = "mhfu_joint"
"""On a bone (`Bone`, not the pose bone): the skeleton joint it is."""
GROUP = "mhfu_group"
"""On a mesh object: the PMO group it is."""
NORMAL = "mhfu_normal"
"""On a mesh, a vertex attribute: each normal as the import showed it, so export can tell the
ones an edit moved (Blender 4.2 shows some several degrees off the file's)."""
LOOP = "mhfu_loop"
LOOP_START = "mhfu_loop_start"
"""On an Action: the clip's loop word and loop start."""


def props(model: Model) -> dict[str, Any]:
    """The armature's properties for `model`, all but `CLIPS`."""
    if model.pac is None:
        raise ValueError(f"{model.name}: no PAC bytes to keep")
    out: dict[str, Any] = {GAME: model.game, NAME: model.name, PAC: model.pac}
    if model.path is not None:
        out[PATH] = str(model.path)
    for key, value in ((GEOMETRY, model.geometry), (MOVESET, model.moveset)):
        if value is not None:
            out[key] = value
    if model.em_id is not None:
        out[EM_ID] = model.em_id
    return out


def model(owner: Mapping[str, Any]) -> Model:
    """The model an armature was imported from, read again from its properties."""
    path = owner.get(PATH)
    return Model.from_bytes(
        bytes(owner[PAC]),
        str(owner[NAME]),
        _bytes(owner.get(GEOMETRY)),
        _bytes(owner.get(MOVESET)),
        owner.get(EM_ID),
        path=None if path is None else Path(path),
    )


def _bytes(value: Any) -> bytes | None:
    return None if value is None else bytes(value)
