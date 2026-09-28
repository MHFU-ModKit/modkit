import shutil
import subprocess
import tomllib
from importlib.resources import files

import pytest
from lupa.lua54 import LuaRuntime
from mhfu import addresses

RAW = tomllib.loads(files("mhfu").joinpath("addresses.toml").read_text(encoding="utf-8"))
BASE = addresses.RAM.start


def constants(t: addresses.Table) -> dict[str, int]:
    """Every generated constant by its C name without the MHFU_ prefix."""
    out = {name: int(a) for name, a in t.addresses.items()}
    for s in t.structs.values():
        out |= {f"{s.name}_{f}": int(v) for f, v in s.fields.items()}
        if s.size:
            out[f"{s.name}_SIZE"] = s.size
        if s.stride:
            out[f"{s.name}_STRIDE"] = s.stride
    return out


def in_c(t: addresses.Table, tmp_path) -> dict[str, int]:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "addresses.gen.h").write_text(addresses.render_c(t))
    prints = "".join(f'    printf("{n} %lu\\n", (unsigned long)MHFU_{n});\n' for n in constants(t))
    (tmp_path / "main.c").write_text(
        '#include <stdio.h>\n#include "addresses.gen.h"\n'
        f"int main(void) {{\n{prints}    return 0;\n}}\n"
    )
    exe = tmp_path / "main"
    subprocess.run([cc, "-std=c99", "-Wall", "-Werror", "-o", exe, tmp_path / "main.c"], check=True)
    lines = subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split("\n")
    return {n: int(v) for n, v in (line.split() for line in lines if line)}


def in_lua(t: addresses.Table) -> dict[str, int]:
    addr = LuaRuntime().execute(addresses.render_lua(t))
    out = {name: addr[name] for name in t.addresses}
    for s in t.structs.values():
        out |= {f"{s.name}_{f}": v for f, v in addr[s.name].items()}
    return out


def test_python_matches_the_file():
    for name, entry in RAW["address"].items():
        assert getattr(addresses, name) == entry["eu"]
        assert getattr(addresses, name).type == entry["type"]
    for sname, struct in RAW.get("struct", {}).items():
        for fname, entry in struct["fields"].items():
            assert getattr(getattr(addresses, sname), fname) == entry["offset"]


def test_c_header_matches_python(tmp_path):
    assert in_c(addresses.table(), tmp_path) == constants(addresses.table())


def test_lua_module_matches_python():
    assert in_lua(addresses.table()) == constants(addresses.table())


def test_an_added_address_reaches_all_three_languages(tmp_path):
    data = tomllib.loads(files("mhfu").joinpath("addresses.toml").read_text(encoding="utf-8"))
    data["address"]["ADDED"] = {"eu": BASE + 0x123450, "type": "u32", "doc": "Test entry."}
    data.setdefault("struct", {})["ADDED_STRUCT"] = {
        "doc": "Test struct.",
        "size": 0x10,
        "fields": {"FIELD": {"offset": 0x4, "type": "u16", "doc": "Test field."}},
    }
    t = addresses.parse(data)
    assert t.addresses["ADDED"] == BASE + 0x123450
    for found in (in_c(t, tmp_path), in_lua(t)):
        assert found["ADDED"] == BASE + 0x123450
        assert found["ADDED_STRUCT_FIELD"] == 0x4


@pytest.mark.parametrize("lang", ["c", "lua"])
def test_cli_writes_the_rendering(lang, tmp_path):
    out = tmp_path / f"addresses.gen.{lang}"
    assert addresses.main([lang, "-o", str(out)]) == 0
    assert out.read_text() == addresses.RENDER[lang](addresses.table())


def test_unknown_names_raise():
    with pytest.raises(AttributeError):
        addresses.NOT_AN_ADDRESS  # noqa: B018
    with pytest.raises(AttributeError):
        addresses.ENTITY.NOT_A_FIELD  # noqa: B018


def entry(value=BASE, type="u32", doc="A doc."):
    return {"eu": value, "type": type, "doc": doc}


def entry_field(offset=0):
    return {"offset": offset, "type": "u8", "doc": "d"}


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"address": {"lower": entry()}}, "UPPER_SNAKE_CASE"),
        ({"address": {"A": entry(0x10)}}, "outside"),
        ({"address": {"A": entry(), "B": entry()}}, "is already A"),
        ({"address": {"A": entry(type="int")}}, "unknown type"),
        ({"address": {"A": entry(doc="two\nlines")}}, "one line"),
        ({"address": {"A": entry(doc="ends */ early")}}, "one line"),
        ({"address": {"A": {"eu": BASE, "type": "u8"}}}, "expects exactly"),
        ({"address": {"A": entry() | {"na": BASE}}}, "expects exactly"),
        ({"struct": {"S": {"doc": "d", "fields": {}}}}, "has no fields"),
        (
            {"struct": {"S": {"doc": "d", "stride": 0, "fields": {"F": entry_field()}}}},
            "stride must be",
        ),
        (
            {
                "struct": {
                    "S": {
                        "doc": "d",
                        "size": 4,
                        "fields": {"F": {"offset": 8, "type": "u8", "doc": "d"}},
                    }
                }
            },
            "outside the struct",
        ),
        (
            {
                "struct": {
                    "S": {
                        "doc": "d",
                        "fields": {
                            "F": {"offset": 0, "type": "u8", "doc": "d"},
                            "G": {"offset": 0, "type": "u8", "doc": "d"},
                        },
                    }
                }
            },
            "is already F",
        ),
        (
            {
                "address": {"S_F": entry()},
                "struct": {
                    "S": {"doc": "d", "fields": {"F": {"offset": 0, "type": "u8", "doc": "d"}}}
                },
            },
            "clashes",
        ),
        ({"regions": {}}, "unknown top-level"),
    ],
)
def test_parse_rejects(data, problem):
    with pytest.raises(ValueError, match=problem):
        addresses.parse(data)
