# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
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
    out |= {f"{name}_COUNT": a.count for name, a in t.addresses.items() if a.count}
    for s in t.structs.values():
        out |= {f"{s.name}_{f}": int(v) for f, v in s.fields.items()}
        out |= {f"{s.name}_{f}_COUNT": v.count for f, v in s.fields.items() if v.count}
        if s.size:
            out[f"{s.name}_SIZE"] = s.size
        if s.stride:
            out[f"{s.name}_STRIDE"] = s.stride
    for e in t.enums.values():
        out |= {f"{e.name}_{n.upper()}": k for k, n in enumerate(e.names, 1)}
        out[f"{e.name}_COUNT"] = len(e.names)
    for st in t.sets.values():
        out |= {f"{st.name}_{a.name}": int(a) for a in st.of}
    return out


def in_c(t: addresses.Table, tmp_path) -> dict[str, int]:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "addresses.gen.h").write_text(addresses.render_c(t))
    members = {f"{st.name}_{a.name}" for st in t.sets.values() for a in st.of}
    names = [n for n in constants(t) if n not in members]
    prints = "".join(f'    printf("{n} %lu\\n", (unsigned long)MHFU_{n});\n' for n in names)
    sets = ""
    for st in t.sets.values():
        # the initializer, in the file's order, and a COUNT that matches it
        sets += f"static const unsigned long set_{st.name}[] = MHFU_{st.name};\n"
        sets += f"typedef char count_{st.name}[MHFU_{st.name}_COUNT == {len(st.of)} ? 1 : -1];\n"
        prints += "".join(
            f'    printf("{st.name}_{a.name} %lu\\n", set_{st.name}[{k}]);\n'
            for k, a in enumerate(st.of)
        )
    (tmp_path / "main.c").write_text(
        f'#include <stdio.h>\n#include "addresses.gen.h"\n{sets}'
        f"int main(void) {{\n{prints}    return 0;\n}}\n"
    )
    exe = tmp_path / "main"
    subprocess.run([cc, "-std=c99", "-Wall", "-Werror", "-o", exe, tmp_path / "main.c"], check=True)
    lines = subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split("\n")
    return {n: int(v) for n, v in (line.split() for line in lines if line)}


# walks k_mhfu_addr the way lua_host's mhfu.addr does, printing the names constants() uses
TABLE_MAIN = r"""
#include <ctype.h>
#include <stdio.h>
#include "addresses_table.gen.inc"
int main(void) {
    for (const mhfu_addr_entry *e = k_mhfu_addr; e->name; e++) {
        if (e->fields)
            for (const mhfu_addr_field *f = e->fields; f->name; f++)
                printf("%s_%s %lu\n", e->name, f->name, (unsigned long)f->value);
        else if (e->names) {
            int k = 0;
            for (; e->names[k]; k++) {
                printf("%s_", e->name);
                for (const char *c = e->names[k]; *c; c++) putchar(toupper(*c));
                printf(" %d\n", k + 1);
            }
            printf("%s_COUNT %d\n", e->name, k);
        } else
            printf("%s %lu\n", e->name, (unsigned long)e->value);
    }
    return 0;
}
"""


def in_c_table(t: addresses.Table, tmp_path) -> dict[str, int]:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "addresses.gen.h").write_text(addresses.render_c(t))
    (tmp_path / "addresses_table.gen.inc").write_text(addresses.render_c_table(t))
    (tmp_path / "table.c").write_text(TABLE_MAIN)
    exe = tmp_path / "table"
    subprocess.run(
        [cc, "-std=c99", "-Wall", "-Werror", "-o", exe, tmp_path / "table.c"], check=True
    )
    lines = subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split("\n")
    return {n: int(v) for n, v in (line.split() for line in lines if line)}


def in_lua(t: addresses.Table) -> dict[str, int]:
    addr = LuaRuntime().execute(addresses.render_lua(t))
    out = {name: addr[name] for name in t.addresses}
    out |= {f"{name}_COUNT": addr[f"{name}_COUNT"] for name, a in t.addresses.items() if a.count}
    for s in t.structs.values():
        out |= {f"{s.name}_{f}": v for f, v in addr[s.name].items()}
    for e in t.enums.values():
        names = list(addr[e.name].values())
        out |= {f"{e.name}_{n.upper()}": k for k, n in enumerate(names, 1)}
        out[f"{e.name}_COUNT"] = len(names)
    for st in t.sets.values():
        out |= {f"{st.name}_{m}": v for m, v in addr[st.name].items()}
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


def test_c_table_matches_python(tmp_path):
    assert in_c_table(addresses.table(), tmp_path) == constants(addresses.table())


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
    for found in (in_c(t, tmp_path), in_c_table(t, tmp_path), in_lua(t)):
        assert found["ADDED"] == BASE + 0x123450
        assert found["ADDED_STRUCT_FIELD"] == 0x4


@pytest.mark.parametrize("lang", ["c", "c-table", "lua"])
def test_cli_writes_the_rendering(lang, tmp_path):
    out = tmp_path / f"addresses.gen.{lang}"
    assert addresses.main([lang, "-o", str(out)]) == 0
    assert out.read_text() == addresses.RENDER[lang](addresses.table())


def test_an_array_carries_its_count(tmp_path):
    data = {
        "address": {"ROWS": entry(type="ROW[3]"), "WORDS": entry(BASE + 4, type="u32[5]")},
        "struct": {
            "ROW": {"doc": "d", "size": 8, "fields": {"F": entry_field()}},
            "HOLDER": {"doc": "d", "fields": {"ROWS": entry_field() | {"type": "ROW[2]"}}},
        },
    }
    t = addresses.parse(data)
    assert (t.addresses["ROWS"].count, t.addresses["WORDS"].count) == (3, 5)
    assert t.structs["ROW"].F.count is None
    for found in (in_c(t, tmp_path), in_c_table(t, tmp_path), in_lua(t)):
        assert (found["ROWS_COUNT"], found["WORDS_COUNT"], found["HOLDER_ROWS_COUNT"]) == (3, 5, 2)
        assert "ROW_F_COUNT" not in found


def test_an_enum_numbers_its_names_from_one(tmp_path):
    t = addresses.parse({"enum": {"KIND": {"doc": "d", "names": ["a_b", "c"]}}})
    assert t.enums["KIND"].names == ("a_b", "c") and t.enums["KIND"].number("c") == 2
    for found in (in_c(t, tmp_path), in_c_table(t, tmp_path), in_lua(t)):
        assert (found["KIND_A_B"], found["KIND_C"], found["KIND_COUNT"]) == (1, 2, 2)
    assert '#define MHFU_KIND_NAMES "a_b", "c"' in addresses.render_c(t)


def test_a_set_reaches_all_three_languages(tmp_path):
    data = {
        "address": {"A": entry(BASE + 8), "B": entry(BASE + 4), "C": entry(BASE)},
        "set": {"AB": {"of": ["A", "B"], "doc": "d"}},
    }
    t = addresses.parse(data)
    ab = t.sets["AB"]
    assert ab == frozenset({BASE + 8, BASE + 4}) and ab.of == (BASE + 8, BASE + 4)
    assert (ab.name, ab.doc) == ("AB", "d")
    for found in (in_c(t, tmp_path), in_c_table(t, tmp_path), in_lua(t)):
        assert (found["AB_A"], found["AB_B"]) == (BASE + 8, BASE + 4)
    assert "#define MHFU_AB { MHFU_A, MHFU_B }" in addresses.render_c(t)


def test_a_set_is_a_frozenset():
    roam = addresses.FREE_ROAM_SCENES
    assert isinstance(roam, frozenset) and roam.name == "FREE_ROAM_SCENES"
    assert addresses.SCENE_VILLAGE in roam and addresses.SCENE_PROMPT not in roam


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
        ({"address": {"A": entry(addresses.RAM.stop + 1)}}, "outside"),
        ({"address": {"A": entry(), "B": entry()}}, "is already A"),
        ({"address": {"A": entry(type="int")}}, "unknown type"),
        ({"address": {"A": entry(type="NO_SUCH_STRUCT[2]")}}, "unknown type"),
        ({"address": {"A": entry(type="u8[0]")}}, "unknown type"),
        ({"address": {"A": entry(type="u8[2]"), "A_COUNT": entry(BASE + 4)}}, "clashes"),
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
        ({"address": {"A": entry()}, "set": {"S": {"of": ["A", "B"], "doc": "d"}}}, "B is not"),
        ({"address": {"A": entry()}, "set": {"S": {"of": [], "doc": "d"}}}, "distinct address"),
        ({"address": {"A": entry()}, "set": {"S": {"of": ["A", "A"], "doc": "d"}}}, "distinct"),
        ({"address": {"A": entry()}, "set": {"S": {"of": [1], "doc": "d"}}}, "distinct"),
        ({"address": {"A": entry()}, "set": {"S": {"of": ["A"]}}}, "expects exactly of"),
        ({"address": {"A": entry()}, "set": {"s": {"of": ["A"], "doc": "d"}}}, "UPPER_SNAKE"),
        ({"address": {"A": entry()}, "set": {"A": {"of": ["A"], "doc": "d"}}}, "is a set and"),
        (
            {
                "address": {"A": entry(), "S_COUNT": entry(BASE + 4)},
                "set": {"S": {"of": ["A"], "doc": "d"}},
            },
            "clashes",
        ),
        ({"enum": {"K": {"doc": "d", "names": ["A"]}}}, "lower_snake_case"),
        ({"enum": {"K": {"doc": "d", "names": ["a", "a"]}}}, "distinct"),
        ({"enum": {"K": {"doc": "d", "names": []}}}, "distinct"),
        ({"enum": {"K": {"names": ["a"]}}}, "expects exactly"),
        ({"address": {"K": entry()}, "enum": {"K": {"doc": "d", "names": ["a"]}}}, "an enum and"),
        (
            {"address": {"K_A": entry()}, "enum": {"K": {"doc": "d", "names": ["a"]}}},
            "clashes",
        ),
    ],
)
def test_parse_rejects(data, problem):
    with pytest.raises(ValueError, match=problem):
        addresses.parse(data)


def test_parse_end_bound():
    assert addresses.parse({"address": {"END": entry(addresses.RAM.stop)}}).addresses["END"]
