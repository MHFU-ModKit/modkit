# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Lua reference page rendered from a model."""

from __future__ import annotations

from modkit_ci.api_lua import Class, Const, Field, Func, LuaApi, Param
from modkit_ci.lua_md import anchor, render, signature

READ = Func(
    name="mhfu.read_u8",
    params=(Param("addr", "integer", False, "the address"), Param("n", "integer", True, "a | b")),
    returns=("integer", "boolean"),
    desc="Reads a byte.",
    examples=("local v = mhfu.read_u8(1)\nprint(v)",),
    file="meta/mhfu.d.lua",
    line=3,
)
METHOD = Func("mhfu.Entity:hp", (), (), "", (), "lib/p.lua", 9)
API = LuaApi(
    funcs=(READ, METHOD),
    consts=(
        Const("mhfu.api_version", "11", "The version.", "m", 1),
        Const("mhfu.big", "{ " + "a = 1, " * 30 + "}", "", "m", 2),
    ),
    classes=(
        Class("mhfu.Entity", (Field("ptr", "integer", True, "the pointer"),), "A handle.", "m", 5),
    ),
)


def test_anchor() -> None:
    assert anchor("mhfu.Entity:hp") == "mhfu.Entity.hp"
    assert anchor("a b") == "a-b"


def test_signature() -> None:
    assert signature(READ) == "mhfu.read_u8(addr: integer, n?: integer): integer, boolean"
    assert signature(METHOD) == "mhfu.Entity:hp()"


def test_page() -> None:
    page = render(API, "Lua reference: framework")
    assert page.startswith("# Lua reference: framework\n")
    assert "### `mhfu.read_u8` { #mhfu.read_u8 }" in page
    assert "### `mhfu.Entity:hp` { #mhfu.Entity.hp }" in page
    assert "| `addr` | `integer` | the address |" in page
    assert "| `n` | `integer?` | a \\| b |" in page
    assert "Returns: `integer`, `boolean`" in page
    assert '<a id="mhfu.api_version"></a>`mhfu.api_version` | `11` | The version.' in page
    assert "| `ptr` | `integer?` | the pointer |" in page
    assert page.endswith("\n") and not page.endswith("\n\n")


def test_example_fence_kept() -> None:
    page = render(API, "t")
    assert "Example:\n\n```lua\nlocal v = mhfu.read_u8(1)\nprint(v)\n```" in page


def test_long_value_cut() -> None:
    row = next(r for r in render(API, "t").splitlines() if "mhfu.big" in r)
    assert "...`" in row and len(row) < 200


def test_empty_sections_left_out() -> None:
    page = render(LuaApi((), (), ()), "t")
    assert page == "# t\n"
