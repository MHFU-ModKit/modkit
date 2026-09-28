# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""DATA.BIN, the archive holding every game file: a table of block offsets, then the files, each
padded to a 2048-byte block. The disc stores it encrypted; `decrypt` and `encrypt` need the `iso`
extra (mhef)."""

import array
import importlib
import types
from dataclasses import dataclass, field
from enum import Enum
from itertools import accumulate, pairwise
from typing import Any, Self

from construct import Array, Int32ul

from ._base import FormatError

BLOCK = 2048


class Game(Enum):
    """A game by its product code, which picks the cipher of its DATA.BIN."""

    MHFU = "ULES-01213"
    MHP3RD = "ULJM-05800"


@dataclass
class DataBin:
    """The decrypted DATA.BIN. `files[i]` is engine file id `i` as stored, padded to whole blocks;
    a replacement may have any length: `to_bytes` pads it with zeros and moves every later file."""

    files: list[bytes] = field(default_factory=list)
    sizes: dict[int, int] = field(default_factory=dict)
    """Exact byte size of the files that record one, by file id. `replace` keeps it current."""
    toc_fill: bytes = b""
    """What fills the table's last block after the sizes (the games leave junk there), cut or
    zero-extended to fit."""

    @staticmethod
    def sniff(data: bytes) -> bool:
        return _toc(data) is not None

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        toc = _toc(data)
        if toc is None:
            raise FormatError("not a decrypted DATA.BIN: its table does not describe the file")
        starts, sizes, used = toc
        files = [data[a * BLOCK : b * BLOCK] for a, b in pairwise(starts)]
        return cls(files, sizes, data[used : starts[0] * BLOCK])

    def replace(self, file_id: int, data: bytes) -> None:
        """Swap in a file; one that records its exact size records the new one."""
        self.files[file_id] = data
        if file_id in self.sizes:
            self.sizes[file_id] = len(data)

    def to_bytes(self) -> bytes:
        if self.files and not self.files[-1]:
            raise ValueError("DATA.BIN cannot end on an empty file: its table would lose it")
        lengths = [_blocks(len(f)) for f in self.files]
        for file_id, size in self.sizes.items():
            if not 0 <= file_id < len(lengths):
                raise ValueError(f"a size is recorded for file {file_id}, which does not exist")
            if not _in_last_block(size, lengths[file_id]):
                raise ValueError(f"file {file_id}: size {size} is not in its last block")
        used = 4 * (len(self.files) + 1 + 2 * len(self.sizes))
        toc_len = _blocks(used) * BLOCK
        starts = accumulate(lengths, initial=toc_len // BLOCK)
        words = [*starts, *(v for record in sorted(self.sizes.items()) for v in record)]
        fill = self.toc_fill[: toc_len - used]
        out = [Array(len(words), Int32ul).build(words), fill, bytes(toc_len - used - len(fill))]
        for f in self.files:
            out += (f, bytes(-len(f) % BLOCK))
        return b"".join(out)


def decrypt(data: bytes, game: Game) -> bytes:
    """DATA.BIN as the disc stores it, decrypted."""
    return _crypt(data, game, encrypting=False)


def encrypt(data: bytes, game: Game) -> bytes:
    """A decrypted DATA.BIN, encrypted as the disc stores it."""
    return _crypt(data, game, encrypting=True)


def _iso_extra(module: str) -> Any:
    """Import a dependency of the `iso` extra."""
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise ImportError(f"{module} is missing: install mhp-formats[iso]") from e


def _crypt(data: bytes, game: Game, encrypting: bool) -> bytes:
    # mhef's own loop (DataCipher.decrypt_file) works on paths; this is the same loop in memory
    cipher = _cipher(game)
    run = cipher.encrypt if encrypting else cipher.decrypt

    def plain(toc: bytes) -> bytes:
        return toc if encrypting else bytes(cipher.decrypt(toc, 0))

    total, rest = divmod(len(data), BLOCK)
    toc_len = int.from_bytes(plain(data[:4]), "little") * BLOCK if total else 0
    starts = _starts(_words(plain(data[:toc_len])), total) if 0 < toc_len <= len(data) else None
    if rest or starts is None:
        raise FormatError("not a DATA.BIN: its table does not describe the file")
    out = bytearray(data)
    out[:toc_len] = run(data[:toc_len], 0)
    for file_id, (a, b) in enumerate(pairwise(starts)):
        if file_id not in cipher._exceptions:  # the files each game stores plain
            out[a * BLOCK : b * BLOCK] = run(data[a * BLOCK : b * BLOCK], a)
    return bytes(out)


class _Words(array.array):  # type: ignore[type-arg]
    tostring = array.array.tobytes  # mhef calls the name Python 3.9 removed


def _cipher(game: Game) -> Any:
    psp = _iso_extra("mhef.psp")
    if not hasattr(psp.array.array, "tostring"):
        psp.array = types.SimpleNamespace(array=_Words)
    return psp.DataCipher({Game.MHFU: psp.MHP2G_EU, Game.MHP3RD: psp.MHP3_JP}[game])


def _blocks(n: int) -> int:
    return -(-n // BLOCK)


def _in_last_block(size: int, blocks: int) -> bool:
    return (blocks - 1) * BLOCK < size <= blocks * BLOCK


def _words(toc: bytes) -> list[int]:
    return list(Array(len(toc) // 4, Int32ul).parse(toc))


def _starts(words: list[int], total: int) -> list[int] | None:
    """Each file's first block and then the end, `total`: the words up to the first reaching it."""
    end = next((i for i, w in enumerate(words) if w >= total), None)
    if end is None or words[end] != total or any(a > b for a, b in pairwise(words[: end + 1])):
        return None
    return words[: end + 1]


def _toc(data: bytes) -> tuple[list[int], dict[int, int], int] | None:
    """The file starts, the size records after them, and the table bytes the two use."""
    total, rest = divmod(len(data), BLOCK)
    toc_len = int.from_bytes(data[:4], "little") * BLOCK
    if rest or not 0 < toc_len <= len(data):
        return None
    words = _words(data[:toc_len])
    starts = _starts(words, total)
    if starts is None:
        return None
    # the records have no count: they run while ids ascend and each size fits its file
    sizes: dict[int, int] = {}
    k, last = len(starts), -1
    while k + 1 < len(words):
        file_id, size = words[k], words[k + 1]
        if not last < file_id < len(starts) - 1:
            break
        if not _in_last_block(size, starts[file_id + 1] - starts[file_id]):
            break
        sizes[file_id] = size
        k, last = k + 2, file_id
    if _blocks(4 * k) != toc_len // BLOCK:
        return None  # a spare block, which `to_bytes` would not write
    return starts, sizes, 4 * k
