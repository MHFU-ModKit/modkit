# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The stage's GLSL. No lighting: a stage vertex has no normal, its colour is the light the
artists baked, multiplied into the texture; two-sided, as the engine draws."""

from __future__ import annotations

import moderngl

from mhfu_studio.shell.shaders import HUE, program

#: per-vertex `in_flag` (0 none, 1 hovered, 2 selected) rides in its own buffer; per-group
#: state (hidden, backdrop, selected) is an R8 texture indexed by `in_group`
MESH_VS = """
#version 330 core
uniform mat4 u_mvp;
in vec3 in_pos;
in vec2 in_uv;
in vec4 in_color;
in float in_group;
in float in_flag;
out vec2 v_uv;
out vec4 v_color;
out float v_group;
out float v_flag;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_uv = in_uv;
    v_color = in_color;
    v_group = in_group;
    v_flag = in_flag;
}
"""

#: `u_mode`: 0 texture x colour, 1 colour, 2 texture, 3 group hue, 4 flat; group flag bits:
#: 1 hidden, 2 backdrop (hazed), 4 selected
MESH_FS = (
    """
#version 330 core
uniform sampler2D u_tex;
uniform sampler2D u_group_flags;
uniform int   u_n_groups;
uniform int   u_mode;
uniform int   u_has_tex;
uniform float u_gain;
uniform float u_alpha;
uniform float u_backdrop_alpha;
uniform float u_alpha_cut;
in vec2 v_uv;
in vec4 v_color;
in float v_group;
in float v_flag;
out vec4 f_color;
"""
    + HUE
    + """
int group_flags() {
    if (u_n_groups <= 0) return 0;
    float f = texture(u_group_flags, vec2((v_group + 0.5) / float(u_n_groups), 0.5)).r;
    return int(f * 255.0 + 0.5);
}

void main() {
    int gf = group_flags();
    if ((gf & 1) != 0) discard;
    vec4 tex = vec4(1.0);
    if (u_has_tex != 0 && (u_mode == 0 || u_mode == 2)) {
        tex = texture(u_tex, v_uv);
        if (tex.a < u_alpha_cut) discard;
    }
    vec3 base;
    float a = u_alpha;
    if (u_mode == 0)      base = tex.rgb * v_color.rgb * u_gain;
    else if (u_mode == 1) base = v_color.rgb;
    else if (u_mode == 2) base = tex.rgb;
    else if (u_mode == 3) base = hue(v_group) * (0.5 + 0.5 * v_color.r);
    else                  base = vec3(0.70, 0.71, 0.74) * (0.4 + 0.6 * v_color.r);
    if ((gf & 2) != 0) {
        base = mix(base, vec3(0.62, 0.66, 0.74), 0.25);
        a *= u_backdrop_alpha;
    }
    if ((gf & 4) != 0) base = mix(base, vec3(1.00, 0.72, 0.20), 0.35);
    if (v_flag > 1.5)      base = mix(base, vec3(1.00, 0.55, 0.10), 0.65);
    else if (v_flag > 0.5) base = mix(base, vec3(0.45, 0.85, 1.00), 0.45);
    f_color = vec4(base, a);
}
"""
)

#: collision: one flat colour per triangle, blended over the mesh
FLAT_VS = """
#version 330 core
uniform mat4 u_mvp;
in vec3 in_pos;
in vec4 in_color;
out vec4 v_color;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_color = in_color;
}
"""

FLAT_FS = """
#version 330 core
uniform float u_alpha;
in vec4 v_color;
out vec4 f_color;
void main() {
    if (v_color.a < 0.004) discard;
    f_color = vec4(v_color.rgb, v_color.a * u_alpha);
}
"""


def mesh_program(ctx: moderngl.Context) -> moderngl.Program:
    return program(ctx, MESH_VS, MESH_FS)


def flat_program(ctx: moderngl.Context) -> moderngl.Program:
    return program(ctx, FLAT_VS, FLAT_FS)
