# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `studio` commands of this area."""

from __future__ import annotations

import argparse

from mhfu_studio.cli import Groups
from mhfu_studio.harness import flags


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
    p = groups.render.add_parser(
        "selftest", help="draw the harness's synthetic scene: proves GL, MSAA and the goldens"
    )
    flags.add_flags(p, default_size=(480, 320))
    p.set_defaults(run=_selftest)


def _selftest(args: argparse.Namespace) -> int:
    from mhfu_studio.harness import selftest

    flags.check_args(args)
    return flags.finish(args, selftest.render(size=args.size, samples=args.samples))
