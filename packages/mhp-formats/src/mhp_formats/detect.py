# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which format a blob is, from each format's own `sniff`."""

from ._base import Format
from .databin import DataBin
from .fu.anim import Anim as FuAnim
from .fu.stage import Stage
from .p3rd.anim import Anim as P3rdAnim
from .p3rd.pmo import Pmo as P3rdPmo
from .pac import Pac
from .pmo import Pmo
from .skeleton import Skeleton
from .tmh import Tmh

# a stage is also a PAC; an MHFU clip tag also passes the looser MHP3rd check
_ORDER: tuple[type[Format], ...] = (
    Stage,
    Pmo,
    P3rdPmo,
    Tmh,
    Skeleton,
    FuAnim,
    P3rdAnim,
    Pac,
    DataBin,
)


def detect(data: bytes) -> type[Format] | None:
    return next((fmt for fmt in _ORDER if fmt.sniff(data)), None)
