# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The one place that knows whether a window exists: a GL context made here or attached to.

Everything downstream draws the same way through either door, so a headless PNG and the
window's viewport are the same pixels. 3.3 core is the floor and the ceiling: macOS tops out at
4.1 (through Metal) with no compatibility profile, so every draw needs a VAO and a program.
"""

from __future__ import annotations

import os
import sys
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes.util import find_library
from typing import Any, cast

import moderngl

GL_VERSION = 330

#: standalone contexts made here, newest last, so `standalone` can hand the current one back;
#: never a toolkit's, which may be gone by then (re-entering a dead GLX one segfaults)
_made: list[weakref.ref[moderngl.Context]] = []


class ContextError(RuntimeError):
    """No GL context could be made; the message says what to install or where to run."""


def headless(version: int = GL_VERSION) -> moderngl.Context:
    """A standalone context; on Linux without a display it goes through EGL (Mesa llvmpipe)."""
    errors: list[str] = []
    for backend in _backends():
        settings: dict[str, Any] = {} if backend is None else {"backend": backend}
        try:
            return _remember(moderngl.create_standalone_context(require=version, **settings))
        except Exception as e:
            errors.append(f"{backend or 'default'}: {e}")
    raise ContextError(_diagnose("; ".join(errors), version))


@contextmanager
def standalone(version: int = GL_VERSION) -> Iterator[moderngl.Context]:
    """A context of its own for one job; the newest one made before is current again after.

    Making a standalone context makes it current, and releasing it leaves none current, which
    silently breaks whoever was drawing before (a test session's shared context).
    """
    with borrowed():
        ctx = headless(version)
        try:
            yield ctx
        finally:
            ctx.release()


def attached(version: int = GL_VERSION) -> moderngl.Context:
    """moderngl's view of the context the window toolkit made current; call inside a frame."""
    errors: list[str] = []
    for settings in _attach_settings():
        try:
            return moderngl.create_context(require=version, **settings)
        except Exception as e:
            errors.append(f"{settings.get('backend', 'default')}: {e}")
    raise ContextError(
        f"could not attach to the window's GL context (wanted {_pretty(version)} core); "
        f"attach from inside the frame callback, where it is current: {'; '.join(errors)}"
    )


def _attach_settings() -> list[dict[str, Any]]:
    """Always some: without settings moderngl hands back its one cached context, and on Linux
    its own loader opens the unversioned libGL.so only the -dev packages install."""
    if sys.platform.startswith("linux"):
        return [{"libgl": find_library("GL") or "libGL.so.1"}, {"backend": "egl"}]
    if sys.platform == "win32":
        return [{"libgl": "opengl32.dll"}]
    return [{"libgl": ""}]  # macOS ignores it


@contextmanager
def borrowed() -> Iterator[None]:
    """For code that makes another context current (a toolkit's, a standalone one): the newest
    standalone one made here, and not released, is current again after."""
    before = _newest()
    try:
        yield
    finally:
        if before is not None:
            cast(Any, before).__enter__()  # current again; never exited, so it stays


def describe(ctx: moderngl.Context) -> str:
    """The renderer in one line; also the key that decides whether two renders must match."""
    info = ctx.info
    return " | ".join(str(info.get(k, "?")) for k in ("GL_VERSION", "GL_RENDERER", "GL_VENDOR"))


def max_samples(ctx: moderngl.Context, wanted: int) -> int:
    """`wanted` clamped to GL_MAX_SAMPLES; above it a framebuffer is incomplete at bind time."""
    if wanted <= 1:
        return 0
    try:
        cap = int(ctx.info.get("GL_MAX_SAMPLES", 0))
    except (TypeError, ValueError):
        cap = 0
    return max(0, min(int(wanted), cap))


def _remember(ctx: moderngl.Context) -> moderngl.Context:
    _made[:] = [r for r in _made if r() is not None]
    _made.append(weakref.ref(ctx))
    return ctx


def _newest() -> moderngl.Context | None:
    """The newest context made here and not yet released."""
    for ref in reversed(_made):
        ctx = ref()
        if ctx is not None and not isinstance(ctx.mglo, moderngl.InvalidObject):
            return ctx
    return None


def _backends() -> list[str | None]:
    if not sys.platform.startswith("linux"):
        return [None]
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return [None, "egl"]
    return ["egl"]


def _pretty(version: int) -> str:
    return f"{version // 100}.{version % 100 // 10}"


def _diagnose(error: str, version: int) -> str:
    head = f"no standalone GL {_pretty(version)} context on this machine ({error})"
    if sys.platform == "darwin":
        return head + (
            "\n  macOS supplies GL 4.1 through Metal; a failure here usually means the process"
            " has no window server session (a bare ssh login, or a daemon)."
        )
    if sys.platform == "win32":
        return head + (
            "\n  Windows renders through the display driver's WGL; a session without a driver"
            " (some CI images, some RDP setups) has no GL at all."
        )
    return head + (
        "\n  On Linux the studio renders through EGL; Mesa's llvmpipe needs no GPU:"
        "\n      apt install libegl1 libegl-mesa0 libgl1 libgl1-mesa-dri  # Debian, Ubuntu"
        "\n      dnf install mesa-libEGL mesa-libGL mesa-dri-drivers        # Fedora"
        "\n  A host with only libvulkan and /dev/dri has no GL path at all: use the container"
        " in apps/studio/docker."
    )
