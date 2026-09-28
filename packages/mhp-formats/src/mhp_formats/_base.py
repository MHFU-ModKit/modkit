# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The contract every format in the package keeps."""

from typing import Protocol, Self


class FormatError(ValueError):
    """Bytes that are not the format they were read as."""


class Format(Protocol):
    """A file format: `from_bytes(data).to_bytes() == data` for every file the games ship."""

    @classmethod
    def from_bytes(cls, data: bytes) -> Self: ...

    def to_bytes(self) -> bytes: ...
