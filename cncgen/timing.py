"""Cycle-time estimate (F08) from the interpreted motion."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import arc_centre, arc_points
from .interp import Program, SimMove
from .tooling import Machine


@dataclass
class Timing:
    total_s: float = 0.0
    cutting_s: float = 0.0
    rapid_s: float = 0.0
    tool_change_s: float = 0.0
    per_tool: dict = field(default_factory=dict)


def _n(css: bool, s: float, n_max: float | None, x: float, machine_max: float) -> float:
    cap = min(n_max, machine_max) if n_max else machine_max
    if not css:
        return min(s, cap)
    d = 2.0 * abs(x)
    if d < 1e-6:
        return cap
    return min(1000.0 * s / (math.pi * d), cap)


def move_time(mv: SimMove, m: Machine) -> float:
    if mv.kind == "rapid":
        return 60.0 * max(abs(mv.x1 - mv.x0) / m.rapid_x, abs(mv.z1 - mv.z0) / m.rapid_z)
    if mv.kind == "thread":
        n = _n(False, mv.s, mv.n_max, mv.x1, m.n_max)
        return 60.0 * abs(mv.z1 - mv.z0) / (mv.f * n)
    if mv.kind == "arc":
        c = arc_centre((mv.x0, mv.z0), (mv.x1, mv.z1), mv.r, mv.ccw)
        pts = arc_points((mv.x0, mv.z0), (mv.x1, mv.z1), c, mv.ccw, 1e-3)
    else:
        pts = [(mv.x0 + (mv.x1 - mv.x0) * i / 32, mv.z0 + (mv.z1 - mv.z0) * i / 32) for i in range(33)]
    t = 0.0
    for a, b in zip(pts, pts[1:]):
        ds = math.hypot(b[0] - a[0], b[1] - a[1])
        xm = 0.5 * (a[0] + b[0])
        n = _n(mv.css, mv.s, mv.n_max, xm, m.n_max)
        t += 60.0 * ds / (mv.f * n)
    return t


def cycle_time(prog: Program, m: Machine) -> Timing:
    tm = Timing()
    tm.tool_change_s = prog.tool_changes * m.tool_change_s
    for mv in prog.moves:
        dt = move_time(mv, m)
        if mv.kind == "rapid":
            tm.rapid_s += dt
        else:
            tm.cutting_s += dt
        tm.per_tool[mv.tool] = tm.per_tool.get(mv.tool, 0.0) + dt
    tm.total_s = tm.cutting_s + tm.rapid_s + tm.tool_change_s
    return tm
