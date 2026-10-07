# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every call into the game goes through mhfu_call (mhfu/call.h): an address cast to a function
and called from C runs on psp-gcc's 8-aligned stack, where the game's sv.q halts it."""

import re
from pathlib import Path

FRAMEWORK = Path(__file__).parents[1]
DIRECT = re.compile(r"\)\s*(?:MHFU_\w+|mhfu_mem_read_u32\s*\([^;]*?\))\s*\)\s*\(")
"""`((fn_t)MHFU_X)(...)` or `((fn_t)mhfu_mem_read_u32(...))(...)`"""


def sources() -> dict[str, str]:
    files = [
        p
        for d in ("src", "mods", "boot", "include")
        for p in sorted((FRAMEWORK / d).rglob("*"))
        if p.suffix in (".cpp", ".h")
    ]
    return {str(p.relative_to(FRAMEWORK)): p.read_text(encoding="utf-8") for p in files}


def test_no_direct_call() -> None:
    found = [f"{n}: {m.group(0)}" for n, t in sources().items() for m in DIRECT.finditer(t)]
    assert found == []


def test_the_pattern() -> None:
    called = [
        "((enter_fn)MHFU_ENTER_ACTION)(ent, m, s, mode);",
        "((node_fn)mhfu_mem_read_u32(vtable + MHFU_ATTACK_NODE_VTABLE_END))(node);",
    ]
    assert all(DIRECT.search(c) for c in called)
    assert not DIRECT.search("mhfu_call(MHFU_ENTER_ACTION, ent, m, s, mode);")
    assert not DIRECT.search("if (mhfu_mem_read_u32(ent + MHFU_ENTITY_X) == 0) (void)0;")
