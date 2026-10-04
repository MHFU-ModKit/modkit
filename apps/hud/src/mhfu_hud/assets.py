# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Artwork from an assets folder the HUD does not ship, loaded lazily with a scale cache.

    <assets>/monsters/     monster icons, manifest.json maps slug -> file
    <assets>/items/        item icons, manifest.json likewise
    <assets>/maps/         map images, manifest.json likewise
    <assets>/backgrounds/  village background

The folder is `--assets DIR`, else $MHFU_HUD_ASSETS, else
`${XDG_DATA_HOME:-~/.local/share}/mhfu-hud/assets`. Missing art resolves to None and the layouts
draw placeholders.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pygame


def assets_dir(given: Path | None = None) -> Path:
    if given is not None:
        return given
    if env := os.environ.get("MHFU_HUD_ASSETS"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "mhfu-hud" / "assets"


def _manifest(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


class AssetLibrary:
    """Construct after the display exists, so convert_alpha has a format to convert to."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = assets_dir(root)
        self._raw: dict[str, pygame.Surface | None] = {}
        self._scaled: dict[tuple[int, int, int], pygame.Surface] = {}
        self._manifests = {
            kind: _manifest(self.root / kind / "manifest.json")
            for kind in ("monsters", "items", "maps")
        }

    def _load(self, key: str, path: Path) -> pygame.Surface | None:
        if key not in self._raw:
            surf = None
            if path.is_file():
                try:
                    surf = pygame.image.load(str(path)).convert_alpha()
                except (pygame.error, OSError):
                    surf = None
            self._raw[key] = surf
        return self._raw[key]

    def _image(self, kind: str, slug: str | None) -> pygame.Surface | None:
        if not slug:
            return None
        name = self._manifests[kind].get(slug, f"{slug}.png")
        return self._load(f"{kind}:{slug}", self.root / kind / name)

    def monster_icon(self, slug: str | None) -> pygame.Surface | None:
        return self._image("monsters", slug)

    def item_icon(self, slug: str | None) -> pygame.Surface | None:
        return self._image("items", slug)

    def map_image(self, slug: str | None) -> pygame.Surface | None:
        return self._image("maps", slug)

    def background(self, name: str = "village") -> pygame.Surface | None:
        for ext in (".png", ".jpg", ".jpeg"):
            p = self.root / "backgrounds" / f"{name}{ext}"
            if p.is_file():
                return self._load(f"bg:{name}", p)
        return None

    def scaled(
        self, surf: pygame.Surface | None, size: tuple[float, float]
    ) -> pygame.Surface | None:
        """`surf` scaled to `size`, cached by identity and size."""
        if surf is None:
            return None
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            return None
        key = (id(surf), w, h)
        if key not in self._scaled:
            self._scaled[key] = pygame.transform.smoothscale(surf, (w, h))
        return self._scaled[key]

    def has_assets(self) -> bool:
        return self.root.is_dir() and any(self.root.rglob("*.png"))

    def _slugs(self, kind: str) -> list[str]:
        slugs = [k for k in self._manifests[kind] if not k.startswith("_")]
        if not slugs and (self.root / kind).is_dir():
            slugs = sorted(p.stem for p in (self.root / kind).glob("*.png"))
        return slugs

    def map_slugs(self) -> list[str]:
        return self._slugs("maps")

    def monster_slugs(self) -> list[str]:
        return self._slugs("monsters")
