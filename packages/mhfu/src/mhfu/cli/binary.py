# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu dis|xref|switch|reloc`: the game's code, read from BOOT.BIN or an overlay file."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from .. import addresses, files, reloc
from ..eboot import Eboot
from ..mips import Code, instructions
from ..overlay import Overlay

if TYPE_CHECKING:
    from . import Subparsers

_NOT_DATA = {".text", ".sceStub.text", ".bss"}


def register(sub: Subparsers) -> None:
    source = argparse.ArgumentParser(add_help=False)
    source.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    source.add_argument(
        "--overlay", metavar="OVL", help="a file id, game_task, game_sub, emNN or stageNNN"
    )

    p = sub.add_parser("dis", parents=[source], help="disassemble BOOT.BIN or an overlay")
    p.add_argument("address", type=_address, metavar="ADDR")
    p.add_argument("count", type=int, nargs="?", default=32, metavar="COUNT")
    p.set_defaults(run=dis)

    p = sub.add_parser("xref", parents=[source], help="calls, lui/lo pairs and data words to ADDR")
    p.add_argument("address", type=_address, metavar="ADDR")
    p.set_defaults(run=xref)

    p = sub.add_parser("switch", parents=[source], help="the jump table a jr dispatches through")
    p.add_argument("address", type=_address, metavar="ADDR", help="the jr, or its table")
    p.set_defaults(run=switch)

    p = sub.add_parser(
        "reloc",
        help="move an overlay by DELTA bytes (a multiple of 0x10000)",
        epilog="A negative DELTA goes after --: mhfu reloc em75 -o out.bin -- -0x60000",
    )
    p.add_argument(
        "overlay", metavar="OVL", help="a file id, game_task, game_sub, emNN or stageNNN"
    )
    p.add_argument("delta", type=lambda s: int(s, 0), metavar="DELTA")
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    p.set_defaults(run=move)


def _address(text: str) -> int:
    """A number, or a name from the address table."""
    try:
        return int(text, 0)
    except ValueError:
        if text.isupper() and text.isidentifier() and text in addresses.table().addresses:
            return int(getattr(addresses, text))
        raise argparse.ArgumentTypeError(
            f"{text!r} is neither a number nor an address name"
        ) from None


def _overlay_id(spec: str) -> int:
    fixed = {"game_task": files.GAME_TASK, "game_sub": files.GAME_SUB}
    if spec in fixed:
        return fixed[spec]
    for prefix, file_id, known in (
        ("em", files.em_overlay, files.EM_SPECIES),
        ("stage", files.stage_overlay, files.STAGES),
    ):
        number = spec.removeprefix(prefix)
        if number != spec and number.isdigit():
            if int(number) not in known:
                raise ValueError(f"no {prefix} overlay {number}")
            return file_id(int(number))
    return int(spec, 0)


def _image(args: argparse.Namespace) -> Eboot | Overlay:
    game = files.Extracted.find(args.data)
    return game.eboot() if args.overlay is None else game.overlay(_overlay_id(args.overlay))


def _data(image: Eboot | Overlay) -> list[range]:
    """The ranges holding data words."""
    if isinstance(image, Overlay):
        return [image.initialised]
    return [
        r
        for name, r in image.sections.items()
        if name not in _NOT_DATA and r and r.start in image and r.stop <= image.end
    ]


def _in(code: Code, va: int) -> str:
    return f"  in 0x{code.function(va).start:08X}" if va in code.text else ""


def dis(args: argparse.Namespace) -> int:
    image = _image(args)
    if args.address not in image:
        raise ValueError(f"0x{args.address:08X} is outside {image!r}")
    stop = min(args.address + 4 * args.count, image.end)
    for ins in instructions(image, args.address, stop):
        print(f"0x{ins.vram:08X}  {ins.getRaw():08X}  {ins.disassemble()}")
    return 0


def xref(args: argparse.Namespace) -> int:
    image = _image(args)
    code, target = Code(image, image.text), args.address
    found = 0
    for ins in code:
        if ins.isJumpWithAddress() and ins.getInstrIndexAsVram() == target:
            found += 1
            print(f"{'call' if ins.doesLink() else 'jump'}   0x{ins.vram:08X}{_in(code, ins.vram)}")
    for c in code.calls:
        if c.target == target and not code.at(c.site).isJumpWithAddress():
            found += 1
            print(f"call   0x{c.site:08X}{_in(code, c.site)}  through a register")
    for p in code.pairs:
        if p.value == target:
            found += 1
            print(f"hi/lo  0x{p.hi:08X} 0x{p.lo:08X}{_in(code, p.lo)}")
    for r in _data(image):
        for a in range(r.start, r.stop - 3, 4):
            if image.u32(a) == target:
                found += 1
                print(f"data   0x{a:08X}")
    if not found:
        print(f"no references to 0x{target:08X}")
    return 0


def switch(args: argparse.Namespace) -> int:
    image = _image(args)
    code = Code(image, image.text)
    s = code.switch(args.address) or next(
        (s for s in code.switches.values() if s.table == args.address), None
    )
    if s is None:
        raise ValueError(f"0x{args.address:08X} is neither a jump-table jr nor its table")
    operand = (
        "" if s.operand is None else f", on {s.operand.disassemble()} at 0x{s.operand.vram:08X}"
    )
    print(f"jr 0x{s.jr:08X}  table 0x{s.table:08X}  {len(s.targets)} cases from {s.first}{operand}")
    for i, target in enumerate(s.targets):
        print(f"  {s.first + i:4d}  0x{target:08X}")
    return 0


def move(args: argparse.Namespace) -> int:
    ovl = files.Extracted.find(args.data).overlay(_overlay_id(args.overlay))
    where = reloc.sites(ovl)
    args.output.write_bytes(reloc.relocate(ovl, args.delta, where))
    print(
        f"{ovl.name} 0x{ovl.load:08X} -> 0x{ovl.load + args.delta:08X}: {len(where.jumps)} jumps, "
        f"{len(where.his)} luis, {len(where.words)} data words -> {args.output}"
    )
    return 0
