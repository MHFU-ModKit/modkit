# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The examples in mhfu.d.lua run against the fake `mhfu` and call the function they document."""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

FRAMEWORK = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("lua_api", FRAMEWORK / "tools" / "lua_api.py")
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

DECL = (FRAMEWORK / "lua" / "meta" / "mhfu.d.lua").read_text(encoding="utf-8")
SECTION = re.compile(r"^-- .+ \(.+\) -+$")
EXAMPLES = [(name, code) for name, codes in tool.examples(DECL).items() for code in codes]

# The fake's stubs return 0; these return what the example indexes.
RETURNS = {
    "entities_of_type": "{}",
    "em_status": "{ brain_fires = 0, last_main = 0, last_sub = 0 }",
    "monster_state": "{ combat = false }",
}
# Events whose handler the example registers and the test calls, to reach what it documents.
HANDLERS = {"on_quest_targets_building": (0,)}


def sections() -> dict[str, set[str]]:
    """Section comment -> the functions declared under it."""
    found: dict[str, set[str]] = {}
    here = ""
    for line in DECL.splitlines():
        if SECTION.match(line):
            here = line
            found[here] = set()
        elif m := tool.FUNC.match(line):
            found[here].add(m[1])
    return found


def run(lua: Any, code: str) -> Any:
    stub = "".join(
        f"do local f = mhfu.{fn}; mhfu.{fn} = function(...) f(...); return {value} end end\n"
        for fn, value in RETURNS.items()
    )
    lua.execute(stub + code)
    m = lua.globals().mhfu
    for call in list(m.calls.values()):
        if call[1] in HANDLERS:
            call[2](*HANDLERS[call[1]])
    return m


@pytest.mark.parametrize(
    ("name", "code"), EXAMPLES, ids=[f"{n}-{i}" for i, (n, _) in enumerate(EXAMPLES)]
)
def test_example_calls_its_function(lua: Any, name: str, code: str) -> None:
    m = run(lua, code)
    called = [c[1] for c in m.calls.values()]
    if name == "log":  # the fake keeps log lines apart from calls
        called += ["log"] * len(m.logs)
    assert name in called


def test_every_section_has_an_example() -> None:
    documented = {name for name, _ in EXAMPLES}
    bare = [s for s, funcs in sections().items() if not funcs & documented]
    assert not bare
    assert len(sections()) >= 19


def test_examples_are_plain_ascii() -> None:
    assert all(code.isascii() for _, code in EXAMPLES)
