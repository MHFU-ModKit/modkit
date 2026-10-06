# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
import os
import struct
from pathlib import Path

import pytest
from mhfu import addresses as a
from mhfu import files, names, symbols
from mhfu.files import Extracted
from mhfu.names import Module
from mhfu.names.decomp import Decomp, placeholder, reach
from mhfu.names.demangle import demangle
from mhfu.overlay import TEXT, Overlay
from modkit_testing import mips as asm

JP_LOAD, EU_LOAD = 0x0010_0000, 0x0010_4C80
JP_DATA, EU_DATA = 0x0020_0000, 0x0020_4F30


@pytest.mark.parametrize(
    ("symbol", "cpp"),
    [
        ("act_ck__7ObjBaseFUcUc", "ObjBase::act_ck(unsigned char, unsigned char)"),
        ("draw__7ObjBaseFv", "ObjBase::draw()"),
        ("__ct__7ObjBaseFv", "ObjBase::ObjBase()"),
        ("__dt__Q23std9exceptionFv", "std::exception::~exception()"),
        ("what__Q23std9exceptionCFv", "std::exception::what() const"),
        ("printfSJIS__10SystemFontFssPce", "SystemFont::printfSJIS(short, short, char*, ...)"),
        ("drawLayer__10SystemFontFSc", "SystemFont::drawLayer(signed char)"),
        ("__DecodeSignedNumber__FPcPi", "__DecodeSignedNumber(char*, int*)"),
        ("objectPtr__24Singleton<11TaskManager>", "Singleton<TaskManager>::objectPtr"),
        ("f__FPCcRA4_i", "f(const char*, int[4]&)"),
        (
            "setFixMemoryMap__10ObjManagerFPQ210ObjManager8MapEntry",
            "ObjManager::setFixMemoryMap(ObjManager::MapEntry*)",
        ),
        ("memset", "memset"),
        ("__sinit_stage266.cpp", "__sinit_stage266.cpp"),
        ("objectPtr__16Singleton<5Shift>", "objectPtr__16Singleton<5Shift>"),  # a bad length
    ],
)
def test_demangle(symbol, cpp):
    assert demangle(symbol) == cpp


def test_placeholder():
    assert placeholder("func_eboot_08865CB0") and placeholder("D_em75_09D2F5A8")
    assert not placeholder("method_08865A5C__7ObjBaseFUi")
    assert reach("em75") == ("em75", "eboot", "game_task", "game_sub")
    assert reach("game_sub") == ("game_sub", "eboot")


def _overlay(load, words):
    text = bytes(0x40) + struct.pack(f"<{len(words)}I", *words)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, load, len(text), 0, 0, 0, 0)
    return Overlay(header.ljust(TEXT, b"\0") + text)


def _caller(at):
    return [
        asm.addiu("sp", "sp", -0x10),
        asm.sw("ra", 0, "sp"),
        asm.lui("a0", asm.hi(at("DATA"))),
        asm.addiu("a0", "a0", asm.lo(at("DATA"))),
        asm.jal(at("leaf")),
        asm.NOP,
        asm.jal(at("grown")),
        asm.NOP,
        asm.lw("ra", 0, "sp"),
        asm.RET,
        asm.addiu("sp", "sp", 0x10),
    ]


def _const(*values):
    return lambda at: [*(asm.li(f"v{k % 2}", v) for k, v in enumerate(values)), asm.RET, asm.NOP]


def _build(load, data, funcs):
    """An overlay of `funcs` (name, body), each body given a lookup of the addresses."""
    at = {"DATA": data}
    va = load + TEXT + 0x40
    for name, body in funcs:
        at[name] = va
        va += 4 * len(body(lambda _: 0))
    return _overlay(load, [w for _, body in funcs for w in body(at.__getitem__)]), at


def _sides():
    leaf = ("leaf", lambda at: [asm.lw("v0", 0x10, "a0"), asm.RET, asm.NOP])
    tail = ("tail", lambda at: [asm.lbu("v0", 0x1E8, "a0"), asm.RET, asm.NOP])
    jp = [("caller", _caller), leaf, ("grown", _const(1, 2, 3, 4)), tail, ("tweak", _const(5, 6))]
    eu = [("caller", _caller), leaf, ("new1", _const(7)), ("new2", _const(8))]
    eu += [("grown", _const(1, 2, 3, 9)), tail, ("tweak", _const(5, 7))]
    return _build(JP_LOAD, JP_DATA, jp), _build(EU_LOAD, EU_DATA, eu)


def test_match():
    (jp, at_j), (eu, at_e) = _sides()
    mod = Module("eboot", jp, eu)
    assert mod.jp.starts[at_j["caller"]].words == mod.eu.starts[at_e["caller"]].words
    how = {n: mod.matches.get(at_j[n]) for n in ("caller", "leaf", "grown", "tail", "tweak")}
    assert {n: m and (m.how, m.eu == at_e[n]) for n, m in how.items()} == {
        "caller": ("exact", True),
        "leaf": ("exact", True),
        "grown": None,  # a stretch that changed length
        "tail": ("exact", True),
        "tweak": ("order", True),
    }
    assert names._propagate({"eboot": mod}) == {"agree": 2}
    grown = mod.matches[at_j["grown"]]
    assert (grown.how, grown.eu, grown.ratio) == ("call", at_e["grown"], 0.833)
    assert mod.to_eu(at_j["caller"] + 8) == (at_e["caller"] + 8, mod.matches[at_j["caller"]])
    assert mod.to_eu(at_j["tweak"] + 4) is None  # inside a function that changed


def test_decomp(tmp_path):
    config = tmp_path / "config"
    (config / "em").mkdir(parents=True)
    (config / "eboot.symbol_addrs.txt").write_text(
        "act_ck__7ObjBaseFUcUc = 0x0010; // type:func\nfunc_eboot_00000020 = 0x0020;\n"
    )
    (config / "em" / "em75.symbol_addrs.txt").write_text(
        "draw__7ObjBaseFv = 0x0030; // type:func\nother = 0x0010;\nown = 0x0110;\nfar = 0x0300;\n"
    )
    blob = b"BOOT"
    (config / "eboot.sha1").write_text(f"{hashlib.sha1(blob).hexdigest()}  modules/eboot.elf\n")
    (config / "overlays.sha1").write_text(f"{'0' * 40}  modules/em/em75.ovl\n")
    decomp = Decomp.find(tmp_path)
    texts = {"eboot": range(0, 0x100), "em75": range(0x100, 0x200)}
    assert decomp.names(texts) == {
        "eboot": {0x10: "act_ck__7ObjBaseFUcUc", 0x30: "draw__7ObjBaseFv"},
        "em75": {0x110: "own"},
    }
    decomp.check("eboot", blob)
    with pytest.raises(ValueError, match="not the decomp's"):
        decomp.check("eboot", b"other")
    with pytest.raises(FileNotFoundError):
        Decomp.find(tmp_path / "config")


def test_blank_boot(tmp_path):
    (tmp_path / "data_files").mkdir()
    (tmp_path / "PSP_GAME" / "SYSDIR").mkdir(parents=True)
    (tmp_path / "PSP_GAME" / "SYSDIR" / "BOOT.BIN").write_bytes(bytes(64))
    with pytest.raises(FileNotFoundError, match="decrypted EBOOT"):
        Extracted.find(tmp_path).code(files.JP)
    assert files.JP.em(75) == 6056 and files.EU.em(75) == files.em_overlay(75)


def test_symbols_lookups(tmp_path, monkeypatch):
    path = tmp_path / "names.json"
    monkeypatch.setenv("MHFU_NAMES", str(path))
    symbols.functions.cache_clear()
    assert symbols.functions() == {}
    names.write({"names": {}, "functions": {"0x00100000": {"jp": "0x00F00000"}}}, path)
    symbols.functions.cache_clear()
    assert symbols.functions() == {0x0010_0000: {"jp": "0x00F00000"}}
    symbols.functions.cache_clear()
    assert symbols.own(a.ACT_SET) == "ACT_SET"


def _need(var: str) -> Path:
    if not os.environ.get(var):
        pytest.skip(f"{var} is not set")
    return Path(os.environ[var])


@pytest.fixture(scope="module")
def table():
    eu = Extracted.find(_need("MHFU_DATA"))
    jp = Extracted.find(_need("MHP2G_DATA"))
    return names.build(eu, jp, Decomp.find(_need("MHP2G_DECOMP")))


ACT_CK = 0x08865CB8  # noaddr: ObjBase::act_ck, which act_set calls


def test_anchors(table):
    act_ck = table["names"][f"0x{ACT_CK:08X}"]
    assert act_ck["name"] == "ObjBase::act_ck(unsigned char, unsigned char)"
    assert table["functions"][f"0x{a.ACTION_EXECUTOR:08X}"]["jp"] == "0x09AC04F8"  # noaddr
    assert table["functions"][f"0x{a.ACT_SET:08X}"]["jp"] == "0x09AC3960"  # noaddr
    task = table["data"]["0x08A8B1E8"]  # noaddr
    assert task["name"] == "Singleton<TaskManager>::objectPtr"
    assert int(task["jp"], 16) + 0x4F30 == 0x08A8B1E8  # noaddr
    assert table["coverage"]["calls"]["disagree"] < 10
