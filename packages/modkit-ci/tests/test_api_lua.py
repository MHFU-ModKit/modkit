# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Lua API model from doc JSON and the break and doc rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from modkit_ci import api_lua, lua_tool, luals, registry
from modkit_ci.api_lua import Class, Const, Field, Func, LuaApi, Param

FIXTURE = Path(__file__).parent / "fixtures" / "lua"
REPO = Path(__file__).resolve().parents[3]
DIRS = ["meta", "lib"]

lua_api = lua_tool.load()


def _fixture_api(dirs: list[str] = DIRS) -> LuaApi:
    doc = json.loads((FIXTURE / "doc.json").read_text(encoding="utf-8"))
    return api_lua.model(doc, FIXTURE, dirs)


def test_funcs_ordered() -> None:
    names = [(f.file, f.line, f.name) for f in _fixture_api().funcs]
    assert names == sorted(names)
    assert [f.name for f in _fixture_api().funcs] == [
        "fx.helper",
        "fx.add",
        "fx.pair",
        "fx.bare",
    ]


def test_params_and_returns() -> None:
    add = {f.name: f for f in _fixture_api().funcs}["fx.add"]
    assert add.params == (
        Param("a", "integer", False, "first operand"),
        Param("b", "integer", True, "second operand"),
    )
    assert add.returns == ("integer",)
    assert (add.file, add.line) == ("meta/fx.d.lua", 31)


def test_multiple_returns_and_bare() -> None:
    funcs = {f.name: f for f in _fixture_api().funcs}
    assert funcs["fx.pair"].returns == ("integer", "integer")
    assert funcs["fx.bare"].desc == "" and funcs["fx.bare"].returns == ()


def test_example_split_from_desc() -> None:
    add = {f.name: f for f in _fixture_api().funcs}["fx.add"]
    assert add.desc == "Adds a to b."
    assert add.examples == ("local n = fx.add(1)",)


def test_example_blank_lines_and_two_fences() -> None:
    block = ["---Does it.", "---```lua", "---local a = 1", "---", "---use(a)", "---```"]
    block += ["---```lua", "---b()", "---```", "---@return true"]
    assert tuple(lua_api.block_fences(block)) == ("local a = 1\n\nuse(a)", "b()")


def test_type_block_is_prose_not_example() -> None:
    raw = "Does it.\n\n```lua\nreturn:\n    | true\n```"
    assert api_lua._prose(raw) == "Does it."


@pytest.mark.parametrize("block", [["---```lua", "---x"], ["---```"]])
def test_unbalanced_fence_raises(block: list[str]) -> None:
    with pytest.raises(ValueError, match="fence|closed"):
        lua_api.block_fences(block)


def test_examples_come_from_source(tmp_path: Path) -> None:
    src = ["---Doc.", "---```lua", "---f()", "---```", "---@return true", "function m.f() end"]
    src += ["", "---Doc.", "---@return true", "function m.g() end"]
    (tmp_path / "a.lua").write_text("\n".join(src), encoding="utf-8")
    cache: dict[str, list[str]] = {}
    assert api_lua._examples(tmp_path, "a.lua", 6, cache) == ("f()",)
    assert api_lua._examples(tmp_path, "a.lua", 10, cache) == ()


def test_consts_values_from_source() -> None:
    consts = {c.name: c for c in _fixture_api().consts}
    assert consts["fx.api_version"].value == "3"
    assert consts["fx.MAX"] == Const("fx.MAX", "8", "The item limit.", "meta/fx.d.lua", 15)
    assert consts["fx.MODE"].value == "{ A = 1, B = 2, }"


def test_classes_and_fields() -> None:
    classes = {c.name: c for c in _fixture_api().classes}
    assert set(classes) == {"fx", "fx.Opts"}
    assert classes["fx"].fields == (
        Field("count", "integer", False, "how many"),
        Field("label", "string", True, "optional label"),
    )
    assert classes["fx.Opts"].fields[1] == Field("name", "string", True, "the name")
    assert classes["fx.Opts"].desc == "Options of a call."


def test_dir_filter() -> None:
    assert "fx.outside" not in {f.name for f in _fixture_api().funcs}
    narrow = _fixture_api(["lib"])
    assert [f.name for f in narrow.funcs] == ["fx.helper"]
    assert narrow.classes == () and narrow.consts == ()
    assert "fx.outside" in {f.name for f in _fixture_api(["other"]).funcs}


def test_literal_values() -> None:
    assert api_lua._literal("{ A = 1, B = 0x2F, C = 'x', D = true }")
    assert not api_lua._literal("{ A = M.A }")
    assert not api_lua._literal("M.entity or {}")


# rules -------------------------------------------------------------------------------------

P = Param


def _f(
    name: str = "m.f", params: tuple[Param, ...] = (), returns: tuple[str, ...] = (), **kw: object
) -> Func:
    base = {"desc": "d", "examples": ("x",), "file": "a.lua", "line": 1}
    return Func(name, params, returns, **{**base, **kw})  # type: ignore[arg-type]


def _api(
    funcs: tuple[Func, ...] = (),
    consts: tuple[Const, ...] = (),
    classes: tuple[Class, ...] = (),
) -> LuaApi:
    return LuaApi(funcs, consts, classes)


def _c(name: str, value: str, desc: str = "d") -> Const:
    return Const(name, value, desc, "a.lua", 1)


def _k(name: str, *fields: Field, desc: str = "d") -> Class:
    return Class(name, fields, desc, "a.lua", 1)


def _kinds(base: LuaApi, head: LuaApi) -> list[tuple[str, str]]:
    return [(f.kind, f.message) for f in api_lua.diff("m", base, head)]


def _breaks(base: LuaApi, head: LuaApi) -> list[str]:
    return [m for k, m in _kinds(base, head) if k == "break"]


def test_no_change_is_clean() -> None:
    api = _api((_f(params=(P("a", "integer", False, ""),)),), (_c("m.X", "1"),), (_k("m.K"),))
    assert _kinds(api, api) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (_api((_f(),)), _api()),
        (_api(consts=(_c("m.X", "1"),)), _api()),
        (_api(classes=(_k("m.K"),)), _api()),
        (_api(classes=(_k("m.K", Field("n", "integer", False, "")),)), _api(classes=(_k("m.K"),))),
        (
            _api((_f(params=(P("a", "integer", False, ""),)),)),
            _api((_f(params=(P("a", "string", False, ""),)),)),
        ),
        (
            _api((_f(params=(P("a", "integer", True, ""),)),)),
            _api((_f(params=(P("a", "integer", False, ""),)),)),
        ),
        (_api((_f(),)), _api((_f(params=(P("a", "integer", False, ""),)),))),
        (_api((_f(returns=("integer",)),)), _api((_f(),))),
        (_api((_f(returns=("integer",)),)), _api((_f(returns=("string",)),))),
        (_api(consts=(_c("m.X", "1"),)), _api(consts=(_c("m.X", "2"),))),
        (
            _api(classes=(_k("m.K", Field("n", "integer", False, "")),)),
            _api(classes=(_k("m.K", Field("n", "string", False, "")),)),
        ),
        (
            _api(classes=(_k("m.K", Field("n", "integer", False, "")),)),
            _api(classes=(_k("m.K", Field("n", "integer", True, "")),)),
        ),
    ],
    ids=[
        "func removed",
        "const removed",
        "class removed",
        "field removed",
        "param type",
        "optional to required",
        "required param added",
        "return removed",
        "return type",
        "const value",
        "field type",
        "field optional",
    ],
)
def test_breaks(old: LuaApi, new: LuaApi) -> None:
    assert _breaks(old, new)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (_api((_f(),)), _api((_f(params=(P("a", "integer", True, ""),)),))),
        (_api((_f(),)), _api((_f(), _f("m.g")))),
        (_api(classes=(_k("m.K"),)), _api(classes=(_k("m.K", Field("n", "integer", False, "")),))),
        (_api((_f(returns=("integer",)),)), _api((_f(returns=("integer", "string")),))),
        (_api(consts=(_c("m.api_version", "1"),)), _api(consts=(_c("m.api_version", "2"),))),
        (_api(consts=(_c("m.T", "{ A = M.A }"),)), _api(consts=(_c("m.T", "{ A = M.B }"),))),
        (
            _api((_f(params=(P("a", "integer", False, ""),)),)),
            _api((_f(params=(P("z", "integer", False, ""),)),)),
        ),
    ],
    ids=[
        "trailing optional",
        "new function",
        "new field",
        "new return",
        "api_version",
        "non-literal const",
        "param renamed",
    ],
)
def test_not_breaks(old: LuaApi, new: LuaApi) -> None:
    assert _breaks(old, new) == []


def test_doc_rules_on_new_items() -> None:
    base = _api()
    head = _api(
        (_f("m.a", desc=""), _f("m.b", examples=()), _f("m.ok")),
        (_c("m.X", "1", desc=""),),
        (_k("m.K", desc=""),),
    )
    docs = {m for k, m in _kinds(base, head) if k == "doc"}
    assert docs == {
        "function m.a has no description",
        "function m.b has no example",
        "constant m.X has no description",
        "class m.K has no description",
    }


def test_removed_param_is_break() -> None:
    old = _api((_f(params=(P("a", "integer", False, ""), P("b", "integer", True, ""))),))
    new = _api((_f(params=(P("a", "integer", False, ""),)),))
    assert _breaks(old, new) == ["m.f: param 2 (b) removed"]


def test_trailing_optional_param_added_is_not_break() -> None:
    old = _api((_f(params=(P("a", "integer", False, ""),)),))
    new = _api((_f(params=(P("a", "integer", False, ""), P("b", "integer", True, ""))),))
    assert _breaks(old, new) == []


def test_doc_rule_example_removed() -> None:
    kinds = _kinds(_api((_f(),)), _api((_f(examples=()),)))
    assert kinds == [("doc", "m.f: example removed")]


def test_old_undocumented_not_flagged() -> None:
    old = _api((_f(desc="", examples=()),))
    assert _kinds(old, old) == []


def test_warning_when_version_stays() -> None:
    v = _c("m.api_version", "5")
    kinds = _kinds(_api((_f(),), (v,)), _api((_f(), _f("m.g")), (v,)))
    assert [k for k, _ in kinds] == ["warning"]


def test_no_warning_when_version_rises() -> None:
    old = _api((_f(),), (_c("m.api_version", "5"),))
    new = _api((_f(), _f("m.g")), (_c("m.api_version", "6"),))
    assert _kinds(old, new) == []


def test_no_warning_without_declaration_change() -> None:
    old = _api((_f(),), (_c("m.api_version", "5"),))
    new = _api((_f(desc="better"),), (_c("m.api_version", "5"),))
    assert _kinds(old, new) == []


def test_finding_location() -> None:
    new = _f(params=(P("a", "integer", False, ""),), file="x/y.lua", line=7)
    (finding,) = api_lua.diff("m", _api((_f(),)), _api((new,)))
    assert (finding.module, finding.path, finding.line) == ("m", "x/y.lua", 7)


def test_check_skips_module_without_lua(registry_root: Path) -> None:
    alpha = registry.load(registry_root).get("alpha")
    assert api_lua.check(alpha, registry_root, registry_root) == []


# integration: needs the real LuaLS ---------------------------------------------------------


def _need_luals() -> None:
    try:
        luals.binary(lambda url: (_ for _ in ()).throw(OSError("offline")))
    except luals.LualsMissing:
        pytest.skip("LuaLS is not installed")


def test_real_load_covers_registration_parser() -> None:
    _need_luals()
    text = (REPO / "framework/lua/meta/mhfu.d.lua").read_text(encoding="utf-8")
    parsed = lua_api.parse(text)
    api = api_lua.load(REPO, ["framework/lua/meta", "framework/lua/lib"])
    funcs = {f.name for f in api.funcs}
    consts = {c.name for c in api.consts}
    assert [f for f in parsed.funcs if f"mhfu.{f}" not in funcs] == []
    assert [c.name for c in parsed.consts if f"mhfu.{c.name}" not in consts] == []
    assert api_lua._version(api) is not None


def test_real_check_against_itself() -> None:
    _need_luals()
    module = registry.load(REPO).get("framework")
    assert [f for f in api_lua.check(module, REPO, REPO) if f.kind != "doc"] == []


def test_real_examples_match_lua_api() -> None:
    _need_luals()
    text = (REPO / "framework/lua/meta/mhfu.d.lua").read_text(encoding="utf-8")
    want = {f"mhfu.{k}": len(v) for k, v in lua_api.examples(text).items()}
    api = api_lua.load(REPO, ["framework/lua/meta", "framework/lua/lib"])
    got = {f.name: len(f.examples) for f in api.funcs if f.examples}
    assert {k: v for k, v in got.items() if k.startswith("mhfu.")} == want
