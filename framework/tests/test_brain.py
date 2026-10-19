# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The memory-stick AI scripts' pure parts (src/core/brain_logic.h) on the host, against the
Python producer: stick containers come from `mhfu.em.script.inject.container`, from a tiny
synthetic program and, with MHFU_DATA, from em75's and em01's lifted ones."""

import ctypes
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from mhfu import addresses as a
from mhfu import files
from mhfu.em import script
from mhfu.em.script import asm, ir, lift
from mhfu.em.script.inject import Container, container

FRAMEWORK = Path(__file__).parents[1]
ERRORS = {
    m[1]: int(m[2])
    for m in re.finditer(
        r"MHFU_BRAIN_E_(\w+)\s*=\s*(-\d+)",
        (FRAMEWORK / "include" / "mhfu" / "brain.h").read_text(encoding="utf-8"),
    )
}
LOAD = int(a.INJECT_XRAM) + 0x1040
OTHER = LOAD + 0x20000
WORDS = int(a.SCRIPT_HEADERS.count or 0)
JUNK = 0x12345678


class View(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in "row id image size relocs nrelocs patches npatches".split()
    ]


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("tests/brain_host.cpp")
    u8p, u32, u32p = (
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    view, chars = ctypes.POINTER(View), ctypes.c_char_p
    lib.brain_host_check.argtypes = [chars, u32, u32, view]
    lib.brain_host_relocate.argtypes = [chars, view, u32]
    lib.brain_host_patch.argtypes = [chars, view, u32, u8p, u32, u32, u32, u32p]
    lib.brain_host_unpatch.argtypes = [chars, view, u32, u8p]
    lib.brain_host_in_image.restype = u32
    lib.brain_host_in_image.argtypes = [u8p, u32, u32]
    lib.brain_host_error.restype = chars
    return lib


# ---- stick containers from Python's producer

ROW = 0x2A
N = int(a.AI_OVERLAY) + 0x1000  # the synthetic native region


def u32s(*words: int) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


NATIVE = (
    bytes.fromhex("0500010005000200ff00")  # r0: two enters and an end
    + bytes(6)
    + u32s(N, 0)  # base_l: r0 and an empty slot
    + u32s(N + 0x10, N + 0x10)  # h0 and h1
)


def tiny() -> tuple[ir.Program, asm.Image]:
    """Two headers over one list: map types 0-4 and 5-16."""

    def read(pc: int) -> int:
        return NATIVE[pc - N] if 0 <= pc - N < len(NATIVE) else script.SENTINEL

    body, end = lift.routine(read, N, ROW)
    items: list[ir.Item] = [
        ir.Routine("r0", body, NATIVE[end - N : 0x10], N),
        ir.Table("base_l", ["r0", None], N + 0x10),
        ir.Header("h0", ["base_l"], tuple(range(5)), N + 0x18),
        ir.Header("h1", ["base_l"], tuple(range(5, WORDS)), N + 0x1C),
    ]
    p = ir.Program(ROW, N, items, N + len(NATIVE))
    return p, asm.assemble(p)


@dataclass(frozen=True)
class Built:
    prog: ir.Program
    stick: bytes
    box: Container

    @property
    def at(self) -> int:
        """Where the game keeps the row's header words."""
        return int(a.SCRIPT_HEADERS) + self.prog.row * int(a.SPECIES.stride)

    def native(self) -> list[int]:
        """The row as the game loads it: each map type's native header."""
        words = [0] * WORDS
        for h in self.prog.headers():
            for k in h.variants:
                words[k] = h.va
        return words

    def placed(self, load: int) -> Container:
        return Container.read(self.box.place(load))


@pytest.fixture(scope="module", params=["tiny", 75, 1])
def built(request: pytest.FixtureRequest) -> Built:
    if request.param == "tiny":
        prog, img = tiny()
    else:
        game = files.Extracted.find(request.getfixturevalue("mhfu_data"))
        prog = lift.lift(script.Scripts(game, request.param), request.param)
        img = asm.assemble(prog)
    stick = container(prog, img, 0, origin=False)
    return Built(prog, stick, Container.read(stick))


def check(lib: ctypes.CDLL, raw: bytes, rows: int = files.SPECIES_ROWS) -> tuple[int, View]:
    v = View()
    return lib.brain_host_check(raw, len(raw), rows, ctypes.byref(v)), v


def relocated(lib: ctypes.CDLL, b: Built, load: int) -> tuple[bytes, View]:
    buf = ctypes.create_string_buffer(b.stick, len(b.stick))
    rc, v = check(lib, b.stick)
    assert rc == 0
    assert lib.brain_host_relocate(buf, ctypes.byref(v), load) == 0
    return buf.raw, v


def row_of(words: list[int]) -> "ctypes.Array[ctypes.c_uint8]":
    return (ctypes.c_uint8 * (4 * WORDS)).from_buffer_copy(u32s(*words))


def words_of(row: "ctypes.Array[ctypes.c_uint8]") -> list[int]:
    return list(struct.unpack(f"<{WORDS}I", bytes(row)))


def patch(
    lib: ctypes.CDLL,
    b: Built,
    load: int,
    row: "ctypes.Array[ctypes.c_uint8]",
    chunk: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """C's patch of `row` for the container at `load`, over `chunk` (default: the row): how many
    words and which."""
    raw, v = relocated(lib, b, load)
    buf, size = chunk or (b.at, 4 * WORDS)
    mask = ctypes.c_uint32(0)
    n = lib.brain_host_patch(raw, ctypes.byref(v), load, row, b.at, buf, size, ctypes.byref(mask))
    assert n == mask.value.bit_count()
    return n, mask.value


def expected(b: Built, words: list[int], load: int) -> list[int]:
    """Python's answer: each native header word becomes the container's address for it."""
    headers = b.placed(load).headers
    return [headers.get(w, w) for w in words]


# ---- accepting and relocating


def test_accepts(lib: ctypes.CDLL, built: Built) -> None:
    rc, v = check(lib, built.stick)
    head = built.box.head
    assert rc == 0
    assert (v.row, v.id, v.image, v.size) == (head.row, head.id, *head.image)
    assert (v.relocs, v.nrelocs) == head.relocs
    assert (v.patches, v.npatches) == head.patches


def test_the_counts_are_the_addresses(lib: ctypes.CDLL) -> None:
    assert lib.brain_host_rows() == files.SPECIES_ROWS
    assert lib.brain_host_words() == script.VARIANTS == WORDS


@pytest.mark.parametrize("load", [0, LOAD, OTHER])
def test_relocation_is_pythons(lib: ctypes.CDLL, built: Built, load: int) -> None:
    raw, _ = relocated(lib, built, load)
    assert raw == built.box.place(load)
    assert Container.read(raw).image == built.box.place(load)[0x40 : 0x40 + len(built.box.image)]


def test_a_placed_file_is_not_placed_again(lib: ctypes.CDLL, built: Built) -> None:
    raw, v = relocated(lib, built, LOAD)
    buf = ctypes.create_string_buffer(raw, len(raw))
    assert lib.brain_host_relocate(buf, ctypes.byref(v), OTHER) == ERRORS["LOAD"]
    assert buf.raw == raw


# ---- patching the row


def test_the_row_takes_the_headers(lib: ctypes.CDLL, built: Built) -> None:
    words = built.native()
    words[3], words[WORDS - 1] = JUNK, 0  # words that are no header stay as they are
    row = row_of(words)
    n, mask = patch(lib, built, LOAD, row)
    want = expected(built, words, LOAD)
    assert words_of(row) == want
    assert n == sum(w != x for w, x in zip(words, want, strict=True)) > 0
    assert mask == sum(1 << k for k, (w, x) in enumerate(zip(words, want, strict=True)) if w != x)
    assert not mask >> 3 & 1 and not mask >> (WORDS - 1) & 1


def test_what_the_row_holds_after(lib: ctypes.CDLL, built: Built) -> None:
    row = row_of(built.native())
    size = len(built.box.image)
    assert lib.brain_host_in_image(row, LOAD, size) == 0
    patch(lib, built, LOAD, row)
    assert lib.brain_host_in_image(row, LOAD, size) == (1 << WORDS) - 1
    assert lib.brain_host_in_image(row, OTHER, size) == 0


def test_a_second_pass_changes_nothing(lib: ctypes.CDLL, built: Built) -> None:
    row = row_of(built.native())
    patch(lib, built, LOAD, row)
    done = words_of(row)
    assert patch(lib, built, LOAD, row)[0] == 0
    assert words_of(row) == done


def test_only_the_chunk_is_patched(lib: ctypes.CDLL, built: Built) -> None:
    words = built.native()
    row = row_of(words)
    n, _ = patch(lib, built, LOAD, row, (built.at + 4 * 4, 4 * 5))  # words 4 to 8
    want = expected(built, words, LOAD)
    assert words_of(row) == words[:4] + want[4:9] + words[9:]
    assert n == sum(w != x for w, x in zip(words[4:9], want[4:9], strict=True))
    before, after = built.at - 0x800, built.at + 4 * WORDS
    for chunk in ((before, 0x800), (after, 0x800), (built.at, 3), (built.at + 2, 4)):
        assert patch(lib, built, LOAD, row_of(words), chunk)[0] == 0


def test_a_row_over_two_chunks(lib: ctypes.CDLL, built: Built) -> None:
    words = built.native()
    row = row_of(words)
    split = built.at + 4 * 6
    first = patch(lib, built, LOAD, row, (built.at - 0x100, split - built.at + 0x100))
    assert words_of(row)[6:] == words[6:] and words_of(row)[:6] == expected(built, words, LOAD)[:6]
    second = patch(lib, built, LOAD, row, (split, 0x200))
    assert words_of(row) == expected(built, words, LOAD)
    whole = patch(lib, built, LOAD, row_of(words))
    assert first[0] + second[0] == whole[0] and first[1] | second[1] == whole[1]


def test_a_replaced_install_gives_the_row_back(lib: ctypes.CDLL, built: Built) -> None:
    words = built.native()
    words[3] = JUNK
    row = row_of(words)
    patch(lib, built, LOAD, row)
    raw, v = relocated(lib, built, LOAD)
    n = lib.brain_host_unpatch(raw, ctypes.byref(v), LOAD, row)
    assert words_of(row) == words and n > 0
    patch(lib, built, OTHER, row)
    assert words_of(row) == expected(built, words, OTHER)


def test_unpatching_leaves_another_images_words(lib: ctypes.CDLL, built: Built) -> None:
    row = row_of(built.native())
    patch(lib, built, OTHER, row)
    placed = words_of(row)
    raw, v = relocated(lib, built, LOAD)
    assert lib.brain_host_unpatch(raw, ctypes.byref(v), LOAD, row) == 0
    assert words_of(row) == placed


# ---- refusing


def poke(raw: bytes, at: int, value: int, size: int = 4) -> bytes:
    out = bytearray(raw)
    out[at : at + size] = value.to_bytes(size, "little")
    return bytes(out)


@pytest.mark.parametrize(
    ("name", "mend"),
    [
        ("MAGIC", lambda s, h: b"XHAI" + s[4:]),
        ("VERSION", lambda s, h: poke(s, 4, 2, 2)),
        ("SHORT", lambda s, h: s[:0x3F]),
        ("SECTION", lambda s, h: s[:-8]),
        ("SECTION", lambda s, h: poke(s, 0x10, 0x20)),  # the image starts inside the header
        ("SECTION", lambda s, h: poke(s, 0x14, 0xFFFFFFF0)),  # an image larger than the file
        ("SECTION", lambda s, h: poke(s, 0x1C, 0xFFFFFFFF)),  # a count that wraps
        ("LOAD", lambda s, h: poke(s, 0x0C, 0x1000)),
        ("ROW", lambda s, h: poke(s, 6, files.SPECIES_ROWS, 2)),
        ("RELOC", lambda s, h: poke(s, h.relocs[0], h.image[1])),  # the first word past the image
        ("RELOC", lambda s, h: poke(s, h.relocs[0], h.image[1] - 2)),  # a word that is not aligned
        ("PATCH", lambda s, h: poke(s, h.patches[0] + 4, h.image[1])),  # a header past the image
        ("PATCH", lambda s, h: poke(s, h.patches[0] + 4, 2)),  # not aligned
        ("PATCH", lambda s, h: poke(s, h.patches[0], 0)),  # no native address
        ("PATCH", lambda s, h: poke(s, 0x24, 0)),  # no header at all
    ],
)
def test_refuses(lib: ctypes.CDLL, built: Built, name: str, mend: Callable[..., bytes]) -> None:
    rc, _ = check(lib, mend(built.stick, built.box.head))
    assert rc == ERRORS[name]


def test_the_row_is_below_the_table(lib: ctypes.CDLL, built: Built) -> None:
    assert check(lib, built.stick, built.prog.row + 1)[0] == 0
    assert check(lib, built.stick, built.prog.row)[0] == ERRORS["ROW"]


def test_every_error_has_words(lib: ctypes.CDLL) -> None:
    texts = {lib.brain_host_error(rc) for rc in ERRORS.values()}
    assert len(texts) == len(ERRORS) and b"unknown error" not in texts
    assert lib.brain_host_error(1) == b"unknown error"
