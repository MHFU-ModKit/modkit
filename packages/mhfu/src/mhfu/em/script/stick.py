# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An assembled AI script on the memory stick, for the framework to load.

    img = asm.assemble(prog)
    stick.deploy(prog, img)      # the container and the Lua mod that installs it, on the stick
    stick.ship(built)            # the same for a `build.Built` of a brain file

The mod calls `mhfu.brain_install`: the framework stages the container (PPSSPP's 64 MB window, or
a user-partition block on a real PSP and in 32 MB mode) and points the row's header words at it
after every overlay read, so the next quest runs it. A monster that is running keeps its script.
The mod carries the container's id, so a changed build is a changed mod, which the framework's Lua
host runs again; the framework installs an unchanged container a second time without staging it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ... import inject as sticks
from . import asm, ir
from .inject import Head, container

if TYPE_CHECKING:
    from .build import Built


def build(p: ir.Program, img: asm.Image) -> bytes:
    """The stick container of an assembled program: load 0, no maps for a live push."""
    return container(p, img, 0, origin=False)


def lua_module(row: int, path: str, id: int) -> str:
    """The Lua mod that installs the container at `path` (as the PRX opens it) and logs the
    result; `row` and `id` are the ones the container is expected to carry."""
    quoted = '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"""-- AI script of species row {row}, container {id:08X}, from `mhfu brain deploy`.
-- The framework points the row at the container after every overlay read. A new id, a new text.
local row, err = mhfu.brain_install({quoted})
if row == {row} then
  local id = mhfu.brain_status(row).id & 0xFFFFFFFF -- a u32, negative from 0x80000000
  if id == 0x{id:08X} then
    mhfu.log(string.format("[brain] row {row} installed, id %08X", id))
  else
    mhfu.log(string.format("[brain] row {row}: the container is %08X, not {id:08X}", id))
  end
else
  mhfu.log("[brain] row {row}: " .. (err or ("the container is row " .. tostring(row) .. "'s")))
end
"""


def _write(row: int, data: bytes, root: str | os.PathLike[str] | None) -> list[Path]:
    """The container `data` of `row` and its mod, the container first so the mod finds it, each
    atomically; both paths. The framework's mods directory must exist: FileNotFoundError, and
    nothing written."""
    mods = sticks.default_mods_dir(root)
    brain = sticks.default_brain_dir(root=root)
    mod = lua_module(row, sticks.brain_path(row), Head.parse(data).id)
    return [
        sticks.write_file(brain, sticks.brain_filename(row), data),
        sticks.write_file(mods, sticks.brain_module(row), mod.encode()),
    ]


def deploy(p: ir.Program, img: asm.Image, root: str | os.PathLike[str] | None = None) -> list[Path]:
    """Write `p`'s container and its mod to the memory stick (`inject.memstick(root)`)."""
    return _write(p.row, build(p, img), root)


def ship(built: Built, root: str | os.PathLike[str] | None = None) -> list[Path]:
    """Write a built row's container and its mod to the memory stick: `brain/brain_row_NN.mhai`,
    then `mods/brain_row_NN.lua`. ValueError for a build with an error, which has no container."""
    if built.container is None:
        raise ValueError(f"row {built.row} has errors, so there is no container to ship")
    return _write(built.row, built.container, root)
