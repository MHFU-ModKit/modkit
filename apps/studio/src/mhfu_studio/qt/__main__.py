# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import sys

from mhfu_studio.cli import main

sys.exit(main(["qt", *sys.argv[1:]]))
