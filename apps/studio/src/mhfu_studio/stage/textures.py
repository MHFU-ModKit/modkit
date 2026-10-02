# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The texture half of an edit list: the stage's TMH bank with images replaced in place.

The GE reads the bank straight out of the resident PAC, so a texture edit lands on the next
frame. The bank sits between the two PMOs with no slack and every slot is worn, so an import
replaces a slot at its own size and pixel format.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from mhfu.files import Extracted
from mhp_formats.tmh import Tmh, TmhImage
from PIL import Image

from mhfu_studio.shell.findings import Finding

from . import ops as O
from .file import MESHES, TEXTURES, StageFile

UNTEXTURED = 0xFF
"""A material texture of 255 draws without one."""


@dataclass
class TextureEdit:
    stage: int
    original: bytes
    data: bytes
    applied: int = 0
    log: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


class Usage(NamedTuple):
    triangles: int
    """Drawn, degenerate ones left out."""
    groups: list[tuple[int, int]]
    """(sub, group) of each group wearing the slot."""


def usage(stage: StageFile) -> dict[int, Usage]:
    """What wears each bank slot: a group draws with its material's texture."""
    out: dict[int, Usage] = {}
    for sub in MESHES:
        pmo = stage.pmo(sub)
        for g, group in enumerate(pmo.groups() if pmo else []):
            mat = pmo.material(g) if pmo else None
            if mat is None or mat.texture == UNTEXTURED:
                continue
            n = sum(len(set(t)) == 3 for t in group.block.triangles())
            have = out.get(mat.texture, Usage(0, []))
            out[mat.texture] = Usage(have.triangles + n, [*have.groups, (sub, g)])
    return out


def build(stage: StageFile, ops: Sequence[O.Op], base_dir: Path) -> TextureEdit:
    """The bank with every texture op applied in order; a refused op is skipped."""
    original = stage.entry(TEXTURES)
    out = TextureEdit(stage.number, original, original)
    if not any(O.touches_textures(op) for op in ops):
        return out
    bank = Tmh.from_bytes(original)
    shipped: dict[int, bytes | None] = {}
    for i, op in enumerate(ops):
        if not O.touches_textures(op):
            continue
        where, target = O.place(stage.label, i), (stage.number, i)
        if why := O.malformed(op):
            out.findings.append(Finding("error", "refused", f"texture: {why}", where, target))
            continue
        slot = op["slot"]
        try:
            if not 0 <= slot < len(bank.images):
                raise ValueError(f"the bank holds {len(bank.images)} images, not slot {slot}")
            img = bank.images[slot]
            if slot not in shipped:
                shipped[slot] = _decoded(Tmh.from_bytes(original).images[slot])
            rgba, size = _source(stage, op, img, base_dir, shipped[slot])
            colours = img.palette_colours() if op.get("keep_palette") and img.clut else None
            bank.images[slot] = img.encode(rgba, colours)
        except (ValueError, OSError) as e:
            out.findings.append(
                Finding("error", "refused", f"texture slot {slot}: {e}", where, target)
            )
            continue
        out.applied += 1
        kept = ", palette kept" if colours else ""
        source = op.get("png") or op.get("from") or op.get("rgb")
        if size and size != (img.width, img.height):
            out.findings.append(
                Finding(
                    "info",
                    "resized",
                    f"{source} is {size[0]}x{size[1]}; slot {slot} is {img.width}x{img.height},"
                    " so it is resized",
                    where,
                    target,
                )
            )
        out.log.append(
            f"{where}: slot {slot} <- {source} ({img.width}x{img.height}, mode {img.mode}{kept})"
        )
    out.data = bank.to_bytes()
    if len(out.data) != len(original):
        raise AssertionError("a same-size encode changed the bank's size")
    return out


def _decoded(img: TmhImage) -> bytes | None:
    try:
        return img.decode()
    except (NotImplementedError, ValueError):
        return None


def _source(
    stage: StageFile, op: O.Op, img: TmhImage, base_dir: Path, current: bytes | None
) -> tuple[bytes, tuple[int, int] | None]:
    """The op's pixels at the slot's size, and the size of the image they came from."""
    if "png" in op:
        with Image.open(base_dir / op["png"]) as im:
            return _fit(im.convert("RGBA"), img), im.size
    if "from" in op:
        donor = stage.sibling(op["from"]["stage"]).entry(TEXTURES)
        images = Tmh.from_bytes(donor).images
        k = op["from"]["slot"]
        if not 0 <= k < len(images):
            raise ValueError(f"st{op['from']['stage']:03d} has no texture {k}")
        src = images[k]
        donated = Image.frombytes("RGBA", (src.width, src.height), src.decode())
        return _fit(donated, img), donated.size
    r, g, b = op["rgb"]
    n = img.width * img.height
    if current is None:
        return bytes((r, g, b, 255)) * n, None
    # a flat recolour keeps the alpha, so an alpha-masked card keeps its shape
    out = bytearray(current)
    out[0::4], out[1::4], out[2::4] = bytes([r]) * n, bytes([g]) * n, bytes([b]) * n
    return bytes(out), None


def _fit(im: Image.Image, img: TmhImage) -> bytes:
    if im.size != (img.width, img.height):
        im = im.resize((img.width, img.height), Image.Resampling.LANCZOS)
    return im.tobytes()


def export(stage: StageFile, out: Path, slot: int | None = None) -> list[Path]:
    """Every decodable image of the bank (or one) as `stNNN_texKK.png`."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for k, img in enumerate(Tmh.from_bytes(stage.entry(TEXTURES)).images):
        rgba = _decoded(img) if slot in (None, k) else None
        if rgba is None:
            continue
        path = out / f"{stage.label}_tex{k:02d}.png"
        Image.frombytes("RGBA", (img.width, img.height), rgba).save(path)
        written.append(path)
    return written


class Verified(NamedTuple):
    stages: int
    images: int
    exact: int
    """Re-encode to the shipped bytes."""
    pixels: int
    """Re-encode to the shipped picture (a palette with duplicate colours may index differently)."""
    failed: list[tuple[int, int, str]]


def verify(game: Extracted, stages: Sequence[int]) -> Verified:
    """Decode and re-encode every image of the banks, each through its own palette."""
    n = images = exact = pixels = 0
    failed: list[tuple[int, int, str]] = []
    for number in stages:
        data = StageFile.read(game, number).entry(TEXTURES)
        if not Tmh.sniff(data):
            continue
        n += 1
        for k, img in enumerate(Tmh.from_bytes(data).images):
            images += 1
            try:
                rgba = img.decode()
                again = img.encode(rgba, img.palette_colours() if img.clut else None)
            except (NotImplementedError, ValueError) as e:
                failed.append((number, k, str(e)))
                continue
            if again == img:
                exact += 1
                pixels += 1
            elif again.decode() == rgba:
                pixels += 1
            else:
                failed.append((number, k, "pixels differ"))
    return Verified(n, images, exact, pixels, failed)
