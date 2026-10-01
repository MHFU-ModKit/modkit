# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Shader programs, compiled once per context and kept by source; workspaces add their own."""

from __future__ import annotations

from typing import cast

import moderngl

#: flat-coloured lines and points; colour rides on the vertex so one draw carries many tints
LINE_VS = """
#version 330 core
uniform mat4 u_mvp;
uniform float u_point_size;
in vec3 in_pos;
in vec4 in_color;
out vec4 v_color;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_PointSize = u_point_size;
    v_color = in_color;
}
"""

LINE_FS = """
#version 330 core
uniform float u_alpha;
in vec4 v_color;
out vec4 f_color;
void main() {
    f_color = vec4(v_color.rgb, v_color.a * u_alpha);
    if (f_color.a < 0.004) discard;
}
"""

#: GLSL `vec3 hue(float i)`: a stable, well-spread colour per integer (groups, parts)
HUE = """
vec3 hue(float i) {
    float h = fract(i * 0.6180339887);
    vec3 k = fract(vec3(h) + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0));
    return 0.35 + 0.55 * abs(fract(k * 3.0) * 2.0 - 1.0);
}
"""

#: the one attribute the cache lives in; a dict beside the context would keep it alive
_ATTR = "_mhfu_studio_programs"
Programs = dict[tuple[str, str], moderngl.Program]


def program(ctx: moderngl.Context, vertex: str, fragment: str) -> moderngl.Program:
    """Compiles once per context; a failure carries the GLSL log and the renderer."""
    store = cast("Programs | None", getattr(ctx, _ATTR, None))
    if store is None:
        store = {}
        setattr(ctx, _ATTR, store)
    prog = store.get((vertex, fragment))
    if prog is None:
        try:
            prog = ctx.program(vertex_shader=vertex, fragment_shader=fragment)
        except Exception as e:
            renderer = ctx.info.get("GL_RENDERER", "?")
            raise RuntimeError(f"shader failed to compile on {renderer}:\n{e}") from e
        store[(vertex, fragment)] = prog
    return prog


def line_program(ctx: moderngl.Context) -> moderngl.Program:
    return program(ctx, LINE_VS, LINE_FS)


def uniform(prog: moderngl.Program, name: str) -> moderngl.Uniform:
    """`prog[name]`, typed; a name that is not an active uniform is a KeyError."""
    u = prog[name]
    if not isinstance(u, moderngl.Uniform):
        raise KeyError(f"{name} is not a uniform of this program")
    return u
