# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The flags every `studio render` command shares, and what to do with its images.

Imports nothing heavy, so a `commands.py` can use it at module level.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_studio.shell.findings import worst

if TYPE_CHECKING:
    from mhfu_studio.harness.render import Shots
    from mhfu_studio.harness.stats import Tolerance

#: set by the container image to the mounted repository; output must land inside it
MOUNT_ENV = "STUDIO_MOUNT"
SAMPLES = 4


def size(text: str) -> tuple[int, int]:
    """`WxH`, as argparse's `type`."""
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"want WxH, got {text!r}") from None
    if w < 1 or h < 1:
        raise argparse.ArgumentTypeError(f"want a positive size, got {text!r}")
    return w, h


def add_flags(p: argparse.ArgumentParser, default_size: tuple[int, int] = (1280, 800)) -> None:
    """-o, --size, --samples, --golden, --update and --sheet."""
    p.add_argument("-o", "--out", type=Path, help="directory for the PNGs")
    p.add_argument(
        "--size", type=size, default=default_size, metavar="WxH", help="default %(default)s"
    )
    p.add_argument("--samples", type=int, default=SAMPLES, help="MSAA samples, 0 for none")
    p.add_argument("--golden", type=Path, help="statistics JSON to compare against")
    p.add_argument("--update", action="store_true", help="rewrite --golden from this render")
    p.add_argument("--sheet", action="store_true", help="also write OUT/sheet.png, all tiles")


def check_out(path: Path) -> Path:
    """`path`, refused when it would be written outside the container's mounted repository.

    Anything else is written inside the container and vanishes with it, while the command
    reports success.
    """
    mount = os.environ.get(MOUNT_ENV)
    if mount and not path.resolve().is_relative_to(Path(mount).resolve()):
        raise ValueError(f"{path} is outside the mounted repository {mount}: it would vanish")
    return path


def check_args(args: argparse.Namespace) -> None:
    """Refuses a render with nowhere to go, before anything is drawn."""
    if args.out is None and args.golden is None:
        raise ValueError("nothing to do: give -o, --golden, or both")
    if args.update and args.golden is None:
        raise ValueError("--update needs --golden")
    if args.out is not None:
        check_out(args.out)
    if args.update:
        check_out(args.golden)


def finish(args: argparse.Namespace, shots: Shots, tolerance: Tolerance | None = None) -> int:
    """Writes the PNGs and the sheet, compares or updates the golden; the exit status."""
    from mhfu_studio.harness.render import contact_sheet
    from mhfu_studio.harness.stats import compare_all, load_golden, measure, save_golden
    from mhfu_studio.shell.target import write_png

    check_args(args)
    images = shots.images
    if args.out is not None:
        out = check_out(args.out)
        for name, img in images.items():
            write_png(out / f"{name}.png", img)
        if args.sheet and images:
            write_png(out / "sheet.png", contact_sheet(list(images.values()), list(images)))
        print(f"wrote {len(images)} image(s) to {out}")
    if args.golden is None:
        return 0
    stats = {name: measure(img, shots.clear, shots.renderer) for name, img in images.items()}
    if args.update:
        print(f"wrote {save_golden(check_out(args.golden), stats)}")
        return 0
    findings = compare_all(stats, load_golden(args.golden), tolerance)
    for f in findings:
        print(f, file=sys.stderr if f.level == "error" else sys.stdout)
    if worst(findings) == "error":
        return 1
    print(f"{len(images)} image(s) match {args.golden}")
    return 0
