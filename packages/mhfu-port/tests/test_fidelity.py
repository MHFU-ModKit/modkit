# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port import fidelity, manifest
from mhfu_port.cli import main

PORTS = Path(__file__).parents[3] / "ports"


def test_normalised():
    assert fidelity.normalised([(3, 0.25), (-1, 0.0), (3, 0.25), (5, 0.5), (7, 0.0)]) == {
        3: 0.5,
        5: 0.5,
    }


def test_expected():
    want = fidelity.expected([[(0, 0.5), (9, 0.5)], [(1, 0.2), (0, 0.8)]], {0: 4, 1: 6})
    assert want == [{4: 1.0}, {6: pytest.approx(0.2), 4: pytest.approx(0.8)}]


def test_compare():
    want = [{4: 1.0}, {6: 0.25, 4: 0.75}]
    same = fidelity.compare(want, [[(4, 1.0)], [(4, 0.75), (6, 0.25), (2, 0.0)]])
    assert same.ok and same.max_error == 0.0
    step = fidelity.compare(want, [[(4, 1.0)], [(4, 0.745), (6, 0.255)]])
    assert step.ok and step.max_error == pytest.approx(0.005)
    off = fidelity.compare(want, [[(4, 1.0)], [(4, 0.7), (6, 0.3)]])
    assert not off.within_step and not off.ok
    wrong = fidelity.compare(want, [[(5, 1.0)], [(4, 1.0)]])
    assert (wrong.wrong_set, wrong.ok) == (2, False)
    short = fidelity.compare(want, [[(4, 1.0)]])
    assert (short.expected, short.vertices, short.ok) == (2, 1, False)


@pytest.mark.parametrize("name", ["brute_tigrex", "zinogre"])
def test_ports_keep_the_donor_skin(data, name):
    r = fidelity.of_build(manifest.load(PORTS / f"{name}.toml"), data)
    assert r.skin == "source" and r.fidelity.ok and r.fidelity.vertices > 0
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["fidelity", str(PORTS / f"{name}.toml"), *games]) == 0
