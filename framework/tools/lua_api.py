"""Render the C side of the Lua API from its declaration, lua/meta/mhfu.d.lua.

The declaration is the LuaLS definition file mod authors read, so completion and the registered
table cannot disagree. A function is `function mhfu.name(...) end` and a constant is
`mhfu.NAME = <int>`, optionally followed by `-- C_NAME`: the value is then taken from C and
checked against the declared one at compile time. Each needs a doc line above it.

    python tools/lua_api.py lua/meta/mhfu.d.lua -o build/gen

writes lua_api.gen.h (the lb_<name> prototypes and MHFU_LUA_API_VERSION) and lua_api.gen.inc
(the function and constant tables, for the one file that registers them).
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

FUNC = re.compile(r"^function mhfu\.([a-z_][a-z0-9_]*)\(.*\) end$")
CONST = re.compile(r"^mhfu\.(\w+) = (-?(?:0x[0-9A-Fa-f]+|\d+))(?: -- ([A-Z_][A-Z0-9_]*))?$")
VERSION = "api_version"


@dataclass(frozen=True)
class Const:
    name: str
    value: int
    literal: str
    c_name: str | None


@dataclass(frozen=True)
class Api:
    version: int
    funcs: tuple[str, ...]
    consts: tuple[Const, ...]


def parse(text: str) -> Api:
    """The functions and constants declared on `mhfu`, in file order."""
    funcs: list[str] = []
    consts: list[Const] = []
    seen: set[str] = set()
    documented = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        m = FUNC.match(line) or CONST.match(line)
        if m:
            name = m[1]
            if not documented:
                raise ValueError(f"line {n}: mhfu.{name} has no doc line")
            if name in seen:
                raise ValueError(f"line {n}: mhfu.{name} declared twice")
            seen.add(name)
            if m.re is FUNC:
                funcs.append(name)
            else:
                consts.append(Const(name, int(m[2], 0), m[2], m[3]))
        if not line.startswith("---"):
            documented = False
        elif not line.startswith("---@"):
            documented = True
    version = [k.value for k in consts if k.name == VERSION]
    if not version:
        raise ValueError(f"mhfu.{VERSION} is not declared")
    return Api(version[0], tuple(funcs), tuple(consts))


def render_header(api: Api) -> str:
    lines = [
        "/* The Lua API's C side, generated from lua/meta/mhfu.d.lua by tools/lua_api.py. */",
        "#ifndef MHFU_LUA_API_GEN_H",
        "#define MHFU_LUA_API_GEN_H",
        "",
        f"#define MHFU_LUA_API_VERSION {api.version}",
        "",
        *(f"int lb_{name}(lua_State *L);" for name in api.funcs),
        "",
        "#endif",
    ]
    return "\n".join(lines) + "\n"


def render_tables(api: Api) -> str:
    lines = [
        "/* mhfu's functions and constants, generated from lua/meta/mhfu.d.lua by",
        " * tools/lua_api.py. Included by the one file that registers them. */",
        "static const luaL_Reg k_mhfu_funcs[] = {",
        *(f'    {{ "{name}", lb_{name} }},' for name in api.funcs),
        "    { 0, 0 },",
        "};",
        "",
        "static const struct { const char *name; lua_Integer value; } k_mhfu_consts[] = {",
    ]
    for k in api.consts:
        value = f"(lua_Integer)({k.c_name})" if k.c_name else k.literal
        lines.append(f'    {{ "{k.name}", {value} }},')
    lines += ["    { 0, 0 },", "};", ""]
    for k in api.consts:
        if k.c_name:
            lines.append(
                f"static_assert((int32_t)({k.c_name}) == (int32_t)({k.literal}), "
                f'"mhfu.d.lua declares {k.name} = {k.literal}");'
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("decl", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    args = ap.parse_args(argv)
    api = parse(args.decl.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "lua_api.gen.h").write_text(render_header(api), encoding="utf-8")
    (args.out / "lua_api.gen.inc").write_text(render_tables(api), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
