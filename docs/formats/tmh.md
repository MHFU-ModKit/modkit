# TMH

The texture bank of both games, byte-compatible between them: a list of images, each GE pixel
data plus, for an indexed format, its own palette (CLUT). A monster PAC's entry 2 and a stage's
entry 1 are TMH banks. Read and written by `mhp_formats.tmh.Tmh`, colours by
`mhp_formats.psp.color`, the swizzle by `mhp_formats.psp.swizzle`. Derived in part from
[mhff](https://github.com/svanheulen/mhff) by Seth VanHeulen.

## Layout

Everything is contiguous; there is no offset table.

**Bank header**, 0x10:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `char[8]` | `.TMH0.14` |
| 0x08 | `u32` | Image count |
| 0x0C | `u32` | Zero |

The images follow. Bytes after the last image are the tail: a standalone bank runs on to the end
of its 2048-byte block.

**Image**:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | Size of the whole image, this header to the end of its CLUT |
| 0x04 | `u32` | Zero |
| 0x08 | `u32` | 1 |
| 0x0C | `u32` | 1 when a CLUT block follows the pixels, else 0 |
| 0x10 | `u32` | Pixel block size, these 16 bytes included |
| 0x14 | `u32` | 1 (the pixel block's tag) |
| 0x18 | `u32` | Pixel format, below |
| 0x1C | `u16` | Width |
| 0x1E | `u16` | Height |
| 0x20 | bytes | Pixel data, possibly padded past the image |

**CLUT block**, when the image has one:

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | `u32` | Block size, these 16 bytes included |
| 0x04 | `u32` | 2 (the CLUT block's tag) |
| 0x08 | `u32` | Colour format of the entries, 0 to 3 |
| 0x0C | `u32` | Entry count |
| 0x10 | bytes | The entries |

## Pixel formats

The GE's texture formats:

| Format | Name | Bits per pixel | |
|---|---|---|---|
| 0 | BGR5650 | 16 | direct colour |
| 1 | ABGR5551 | 16 | direct colour |
| 2 | ABGR4444 | 16 | direct colour |
| 3 | ABGR8888 | 32 | direct colour |
| 4 | CLUT4 | 4 | palette index |
| 5 | CLUT8 | 8 | palette index |
| 6 | CLUT16 | 16 | palette index |
| 7 | CLUT32 | 32 | palette index |
| 8 | DXT1 | 4 | compressed |
| 9 | DXT3 | 8 | compressed, not decoded |
| 10 | DXT5 | 8 | compressed, not decoded |

Nearly every shipped image is CLUT4 or CLUT8. MHP3rd ships some DXT1; neither game ships DXT3 or
DXT5.

A CLUT4 byte holds two pixels, the left one in the low nibble. An image with format 4 or 5 uses
16 or 256 entries per palette, and a CLUT may hold several palettes back to back (some MHP3rd
images do); `TmhImage.decode(palette=p)` picks one.

## Colour formats

Shared with vertex colours ([pmo.md](pmo.md#vertex-type)). Red sits in the low bits, then green,
blue, alpha. On decode a 5-bit channel widens to 8 bits by bit replication (`v << 3 | v >> 2`),
a 6-bit one likewise (`v << 2 | v >> 4`), a 4-bit one times 17; the 5551 alpha bit becomes 0 or
255. Encoding rounds each channel, and sets the 5551 alpha bit from 128.

## Swizzle

An image is swizzled exactly when it is not DXT and one row is a whole number of 16-byte blocks
(width times bits per pixel a multiple of 128); there is no flag. A swizzled image is cut into
blocks of 16 bytes by 8 rows, stored block after block, left to right, then top to bottom. In
pixels a block is 32x8 at 4 bits, 16x8 at 8, 8x8 at 16 and 4x8 at 32.

When the height is not a multiple of 8 the last band of blocks is short, and the stored data may
stop inside it; the GE reads on past the image, which decodes as zeros here.

## DXT1

Not swizzled. 8-byte blocks of 4x4 pixels, row-major. The PSP stores the four index rows first
(one byte per row, 2 bits per pixel, the left pixel in the low bits), then the two BGR565 end
colours at +4 and +6. The ends widen by a plain shift, not by replication. When the first end is
greater, indices 2 and 3 are the 2:1 and 1:2 blends; otherwise index 2 is the average and index 3
transparent black.

## Writing

- A bank's size is fixed by its images' formats and dimensions alone, so replacing an image's
  pixels keeps every byte offset. `TmhImage.encode(rgba)` does that at the image's own format and
  size: an indexed image is quantised (`quantize`, a median cut weighted by how often each colour
  occurs) and that palette is written over the CLUT, or the pixels are mapped onto given colours.
  On a palette that holds one colour twice the encoder takes the last index.
- DXT is not encoded. An image cannot change format or dimensions without moving every later
  image.
- A stage's bank is read by the GE out of the resident PAC every frame, so a same-size write
  shows on the next frame ([stage.md](stage.md#editing-a-running-game)).
