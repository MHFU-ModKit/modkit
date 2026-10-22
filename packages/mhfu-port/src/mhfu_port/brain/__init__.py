# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHP3rd's monster AI scripts: a species' lists of routines read from the game's bytes into a
tree (`p3`), written back to the same bytes, shown as text and checked over every monster. The
opcode table (`p3ops`) is the one every part reads.

    from mhfu.files import Extracted
    from mhfu_port.brain import p3

    extracted = Extracted.find(env="MHP3RD_DATA")
    brain = p3.lift(extracted, 23)           # Brute Tigrex: its lists and routines
    brain.routines["call_12"]                # a Routine: name, va, body, problems
    p3.encode(brain.routines["call_12"])     # its bytes, identical to the game's
    p3.render(brain)                         # the text lines
    p3.check(extracted).ok                   # every id and slot lifts and encodes back

    mhfu-port brain 23 | head                # the same as text
    mhfu-port brain --check                  # the check, numbers and known quirk
"""

from . import p3, p3ops

__all__ = ["p3", "p3ops"]
