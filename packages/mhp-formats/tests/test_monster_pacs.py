# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from mhp_formats import Pac, detect

MONSTER_PACS = range(6111, 6160)


def test_decoded_round_trip(mhfu_data):
    """Every entry of every big-monster model PAC through its own format, then the PAC again."""
    for n in MONSTER_PACS:
        data = (mhfu_data / f"file_{n:05d}.bin").read_bytes()
        pac = Pac.from_bytes(data)
        for i, entry in enumerate(pac.entries):
            if entry:
                fmt = detect(entry)
                assert fmt is not None, f"file_{n:05d} entry {i}"
                pac.entries[i] = fmt.from_bytes(entry).to_bytes()
        assert pac.to_bytes() == data, f"file_{n:05d}"
