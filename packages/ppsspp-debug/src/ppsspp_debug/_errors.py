# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
class DebuggerError(Exception):
    """PPSSPP answered a request with an error."""

    def __init__(self, event: str, message: str) -> None:
        super().__init__(f"{event}: {message}")
        self.event = event
        self.message = message


class Unsupported(DebuggerError):
    """This PPSSPP does not know the event, such as a patched-build command on stock PPSSPP."""


class Disconnected(ConnectionError):
    """The debugger connection is closed."""
