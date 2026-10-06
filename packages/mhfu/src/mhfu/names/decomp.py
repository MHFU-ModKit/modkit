# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A checkout of the MHP2G decomp (github.com/tclamb/mhp2g-decomp, CC0): its symbol files
(`config/**/<module>.symbol_addrs.txt`, JP addresses) and the checksums of the binaries it is
built from."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..overlay import Overlay

_LINE = re.compile(r"^\s*(\S+?)\s*=\s*(0x[0-9A-Fa-f]+)\s*;")
_PLACEHOLDER = re.compile(r"^(?:func|D|jtbl|ptmf|lbl)_\w+_[0-9A-F]{8}$|^\.")
SHARED = ("eboot", "game_task", "game_sub")


def reach(module: str) -> tuple[str, ...]:
    """The modules whose addresses `module`'s code means: itself, and what is loaded whenever it
    is. Task overlays share one address, so BOOT.BIN and game_sub calling there is ambiguous."""
    fixed = {"eboot": ("eboot",), "game_sub": ("game_sub", "eboot")}
    return fixed.get(module, (module, *(m for m in SHARED if m != module)))


def placeholder(symbol: str) -> bool:
    """A name the decomp made from an address (`func_eboot_08865CB0`), or a local label."""
    return bool(_PLACEHOLDER.match(symbol))


@dataclass(frozen=True)
class Decomp:
    root: Path

    @classmethod
    def find(cls, path: str | Path | None = None, env: str = "MHP2G_DECOMP") -> Decomp:
        """From that directory, by default from the variable `env`."""
        if path is None:
            path = os.environ.get(env)
            if not path:
                raise FileNotFoundError(f"no MHP2G decomp: pass its checkout or set {env}")
        root = Path(path).expanduser()
        if not (root / "config" / "eboot.symbol_addrs.txt").is_file():
            raise FileNotFoundError(f"{root} is not a checkout of the MHP2G decomp")
        return cls(root)

    @cached_property
    def commit(self) -> str:
        run = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return run.stdout.strip() or "unknown"

    def symbols(self, module: str) -> dict[int, str]:
        """The module's symbol file: address -> symbol, the first of several at one address."""
        found = list(self.root.glob(f"config/**/{module}.symbol_addrs.txt"))
        if len(found) != 1:
            return {}
        out: dict[int, str] = {}
        for line in found[0].read_text(encoding="utf-8").splitlines():
            if m := _LINE.match(line):
                out.setdefault(int(m[2], 16), m[1])
        return out

    @cached_property
    def sha1(self) -> dict[str, str]:
        """Module -> the SHA-1 the decomp expects of it (an overlay up to the end of its data)."""
        out = {}
        for name in ("eboot.sha1", "overlays.sha1"):
            for line in (self.root / "config" / name).read_text(encoding="utf-8").splitlines():
                digest, _, path = line.partition(" ")
                out[Path(path.strip()).stem] = digest
        return out

    def check(self, module: str, raw: bytes) -> None:
        """Raise unless `raw` (BOOT.BIN, or an overlay file) is the binary the decomp is built
        from."""
        if Overlay.sniff(raw):
            ovl = Overlay(raw)
            raw = raw[: ovl.initialised.stop - ovl.load]
        digest = hashlib.sha1(raw).hexdigest()
        if self.sha1.get(module) != digest:
            raise ValueError(f"JP {module} is not the decomp's (SHA-1 {digest})")

    def names(self, texts: Mapping[str, range]) -> dict[str, dict[int, str]]:
        """Module -> JP address -> symbol, for the named (not placeholder) symbols in each
        module's range. A module's own file is read first; the others add what they name in the
        modules they reach."""
        out: dict[str, dict[int, str]] = {m: {} for m in texts}
        files = {m: self.symbols(m) for m in texts}
        for own in (True, False):
            for module, symbols in files.items():
                for va, symbol in symbols.items():
                    if placeholder(symbol):
                        continue
                    for owner in reach(module)[:1] if own else reach(module)[1:]:
                        if owner in texts and va in texts[owner]:
                            out[owner].setdefault(va, symbol)
                            break
        return out
