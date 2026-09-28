# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from mhp_formats import Bone, Pac, Skeleton, Tmh, detect
from mhp_formats.fu import Stage


def test_detect():
    skeleton = Skeleton([Bone(-1, -1, -1)]).to_bytes()
    assert detect(skeleton) is Skeleton
    assert detect(Tmh().to_bytes()) is Tmh
    assert detect(Pac([skeleton]).to_bytes()) is Pac
    assert detect(b"\xff" * 64) is None


def test_stage_before_pac():
    assert detect((6).to_bytes(4, "little") + bytes(48)) is Stage
