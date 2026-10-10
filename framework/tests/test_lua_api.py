# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import importlib.util
import re
from pathlib import Path

import pytest

FRAMEWORK = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("lua_api", FRAMEWORK / "tools" / "lua_api.py")
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

DECL = """---@meta
---@class mhfu
mhfu = {}

---The version.
mhfu.api_version = 3

---Reads a byte.
---@param addr integer
---@return integer
function mhfu.read_u8(addr) end

---The up bit.
mhfu.CTRL_UP = 0x0010 -- PSP_CTRL_UP
"""


def test_parses_functions_and_constants() -> None:
    api = tool.parse(DECL)
    assert api.version == 3
    assert api.funcs == ("read_u8",)
    assert [(k.name, k.value, k.c_name) for k in api.consts] == [
        ("api_version", 3, None),
        ("CTRL_UP", 0x10, "PSP_CTRL_UP"),
    ]


def test_renders_prototypes_and_tables() -> None:
    api = tool.parse(DECL)
    assert "int lb_read_u8(lua_State *L);" in tool.render_header(api)
    assert "#define MHFU_LUA_API_VERSION 3" in tool.render_header(api)
    tables = tool.render_tables(api)
    assert '{ "read_u8", lb_read_u8 },' in tables
    assert '{ "api_version", 3 },' in tables
    assert '{ "CTRL_UP", (lua_Integer)(PSP_CTRL_UP) },' in tables
    assert "static_assert((int32_t)(PSP_CTRL_UP) == (int32_t)(0x0010)" in tables


@pytest.mark.parametrize(
    ("decl", "error"),
    [
        (DECL.replace("---Reads a byte.\n", ""), "no doc line"),
        (DECL + "\n---Again.\nfunction mhfu.read_u8(addr) end\n", "declared twice"),
        (DECL.replace("mhfu.api_version = 3", "mhfu.version = 3"), "api_version"),
    ],
)
def test_rejects(decl: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        tool.parse(decl)


def test_the_declaration_parses() -> None:
    api = tool.parse((FRAMEWORK / "lua" / "meta" / "mhfu.d.lua").read_text(encoding="utf-8"))
    assert api.version >= 1
    assert "on_bigmonster_action" in api.funcs


def test_the_name_lists_follow_the_addresses() -> None:
    """The rule ops and monster events are named in addresses.toml; the declaration lists them
    for completion, and a name added there must be added here."""
    from mhfu import addresses

    text = (FRAMEWORK / "lua" / "meta" / "mhfu.d.lua").read_text(encoding="utf-8")

    def names(alias: str) -> tuple[str, ...]:
        found = re.search(rf"^---@alias {re.escape(alias)} (.+)$", text, re.M)
        assert found, alias
        return tuple(re.findall(r'"(\w+)"', found[1]))

    assert names("mhfu.EmCond") == addresses.EM_COND.names
    assert names("mhfu.EmEffect") == addresses.EM_EFFECT.names
    assert names("mhfu.MonsterEventKind") == addresses.MONSTER_EVENT_KIND.names
