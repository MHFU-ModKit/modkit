# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`python -m mhfu_studio.ui [path]`: the same as `studio qt [path]`."""

from __future__ import annotations

import sys

from mhfu_studio.cli import main

sys.exit(main(["qt", *sys.argv[1:]]))
