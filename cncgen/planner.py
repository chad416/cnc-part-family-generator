"""Operation planning (F03): Part -> list of Op with tool moves in (radius, z)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import cycles
from .geometry import offset_profile
from .motion import Move, elements_to_moves
from .parts import Groove, Part
from .tooling import Machine, Tool, rpm


@dataclass
class Op:
    name: str
    tool: Tool
    spindle: tuple  # ('css', vc, n_max) or ('rpm', n)
    approach: list[Move]
    body: list[Move]
    cycle: dict | None = None  # canned-cycle form for the FANUC cycles post
    coolant: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def moves(self) -> list[Move]:
        return self.approach + self.body


def _tool(tools: dict[int, Tool], kind: str) -> Tool:
    for t in tools.values():
        if t.kind == kind:
            return t
    raise ValueError(kind)


def _css(tool: Tool, part: Part, m: Machine) -> tuple:
    return ("css", round(tool.vc * m.materials[part.material]), m.n_max)


def _const(tool: Tool, part: Part, m: Machine, d: float, n_cap: float | None = None) -> tuple:
    cap = min(m.n_max, n_cap) if n_cap else m.n_max
    return ("rpm", round(rpm(tool.vc * m.materials[part.material], d, cap)))


def face_op(part: Part, tools, m: Machine) -> Op:
    t = _tool(tools, "turn")
    sr, rn = part.stock_r, t.rn
    xa = sr + rn + 1.0
    za = m.planner["start_z"]
    fa = part.face_allowance
    approach = [Move("rapid", xa, za)]
    body = []
    n = max(1, math.ceil(fa / t.doc - 1e-9))
    for i in range(1, n + 1):
        zc = fa - fa * i / n
        body += [
            Move("rapid", xa, zc + rn),
            Move("feed", 0.0, zc + rn, f=t.f),
            Move("rapid", 0.0, zc + rn + 1.0),
            Move("rapid", xa, zc + rn + 1.0),
        ]
    return Op("FACE", t, _css(t, part, m), approach, body)


def _od_contour(part: Part, finish: Tool, za: float) -> list[Move]:
    prof = [(part.od[0][0] - 1.0, 0.0)] + list(part.od) + [(part.stock_r + 1.0, part.od[-1][1])]
    elems = offset_profile(prof, finish.rn, "ext")
    p0 = elems[0].p0
    return [Move("rapid", p0[0], za), Move("feed", p0[0], p0[1], f=finish.f)] + elements_to_moves(elems, finish.f)


def _id_contour(part: Part, bar: Tool, za: float, r_drill: float) -> list[Move]:
    prof = [(part.bore[0][0] + 1.0, 0.0)] + list(part.bore) + [(r_drill - 1.0, part.bore[-1][1])]
    elems = offset_profile(prof, bar.rn, "int")
    p0 = elems[0].p0
    return [Move("rapid", p0[0], za), Move("feed", p0[0], p0[1], f=bar.f)] + elements_to_moves(elems, bar.f)


def od_ops(part: Part, tools, m: Machine) -> list[Op]:
    rough, fin = [t for t in tools.values() if t.kind == "turn"][:2]
    pl = m.planner
    za = pl["start_z"]
    allow = pl["finish_allowance"]
    contour = _od_contour(part, fin, za)
    xa = part.stock_r + rough.rn + 1.0
    shift = allow + (rough.rn - fin.rn)
    start = (xa, za)
    body = cycles.g71(start, contour, rough.doc, pl["retract"], shift, shift, rough.f, "ext")
    r_op = Op(
        "OD ROUGH",
        rough,
        _css(rough, part, m),
        [Move("rapid", xa, za)],
        body,
        cycle={"type": "G71", "start": start, "contour": contour, "d": rough.doc, "e": pl["retract"],
               "u": shift, "w": shift, "f": rough.f, "side": "ext", "labels": (100, 200)},
    )
    f_op = Op(
        "OD FINISH",
        fin,
        _css(fin, part, m),
        [Move("rapid", xa, za)],
        cycles.g70(start, contour),
        cycle={"type": "G70", "start": start, "labels": (100, 200)},
    )
    return [r_op, f_op]


def drill_op(part: Part, tools, m: Machine) -> Op:
    t = part.drill
    rd = t.d / 2
    tip = rd / math.tan(math.radians(59.0))
    z_end = -(part.length + _tool(tools, "groove_ext").width + m.planner["part_off_extra_bore"]) - tip
    peck = t.d
    body = [Move("rapid", 0.0, 1.0)]
    z = 0.0
    first = True
    while z > z_end + 1e-9:
        zn = max(z - peck, z_end)
        if not first:
            body.append(Move("rapid", 0.0, z + 0.5))
        body.append(Move("feed", 0.0, zn, f=t.f))
        body.append(Move("rapid", 0.0, 5.0))
        z = zn
        first = False
    return Op("DRILL", t, _const(t, part, m, t.d), [Move("rapid", 0.0, 5.0)], body)


def id_ops(part: Part, tools, m: Machine) -> list[Op]:
    bar = _tool(tools, "bore")
    pl = m.planner
    za = pl["start_z"]
    r_drill = part.drill.d / 2
    contour = _id_contour(part, bar, za, r_drill)
    xa = r_drill - bar.rn - 0.5
    u = -pl["finish_allowance"]
    w = pl["finish_allowance"]
    start = (xa, za)
    body = cycles.g71(start, contour, bar.doc, pl["retract"], u, w, bar.f, "int")
    r_op = Op(
        "ID ROUGH",
        bar,
        _css(bar, part, m),
        [Move("rapid", xa, za)],
        body,
        cycle={"type": "G71", "start": start, "contour": contour, "d": bar.doc, "e": pl["retract"],
               "u": u, "w": w, "f": bar.f, "side": "int", "labels": (300, 400)},
    )
    f_op = Op(
        "ID FINISH",
        bar,
        _css(bar, part, m),
        [Move("rapid", xa, za)],
        cycles.g70(start, contour),
        cycle={"type": "G70", "start": start, "labels": (300, 400)},
    )
    return [r_op, f_op]


def _surface_r(part: Part, g: Groove) -> float:
    prof = part.od if g.side == "ext" else part.bore
    for a, b in zip(prof, prof[1:]):
        if abs(a[0] - b[0]) < 1e-9 and a[1] >= g.z_right - 1e-9 and b[1] <= g.z_left + 1e-9:
            return a[0]
    raise ValueError(f"{part.part_id}: groove {g.name} not on a cylinder")


def _groove_moves(g: Groove, t: Tool, surf: float) -> list[Move]:
    """Moves for one groove; the tool is already at the clearance radius."""
    w = t.width
    sgn = 1.0 if g.side == "ext" else -1.0
    clear = surf + sgn * 1.0
    zl, zr = g.z_left, g.z_right
    out: list[Move] = []
    if g.width - w <= 1e-6:
        zp = zl
        out += [Move("rapid", clear, zp), Move("feed", g.r_bottom, zp, f=t.f), Move("rapid", clear, zp)]
        return out
    lo, hi = zl + 0.1, zr - w - 0.1
    span = hi - lo
    n = max(1, math.ceil(span / (0.8 * w) - 1e-9)) + 1 if span > 1e-9 else 1
    positions = [hi - span * i / (n - 1) for i in range(n)] if n > 1 else [(lo + hi) / 2]
    rough_x = g.r_bottom + sgn * 0.1
    for zp in positions:
        out += [Move("rapid", clear, zp), Move("feed", rough_x, zp, f=t.f), Move("rapid", clear, zp)]
    out += [
        Move("rapid", clear, zr - w),
        Move("feed", g.r_bottom, zr - w, f=t.f),
        Move("feed", g.r_bottom, zl, f=t.f),
        Move("feed", clear, zl, f=t.f),
    ]
    return out


def groove_ops(part: Part, tools, m: Machine, side: str) -> list[Op]:
    gs = sorted((g for g in part.grooves if g.side == side), key=lambda g: -g.z_right)
    if not gs:
        return []
    t = _tool(tools, "groove_ext" if side == "ext" else "groove_int")
    body: list[Move] = []
    if side == "ext":
        x_trav = max(p[0] for p in part.od) + 1.0
        approach = [Move("rapid", part.stock_r + 2.0, gs[0].z_left)]
        for g in gs:
            surf = _surface_r(part, g)
            body.append(Move("rapid", x_trav, g.z_left))
            body.append(Move("rapid", surf + 1.0, g.z_left))
            body += _groove_moves(g, t, surf)
            body.append(Move("rapid", x_trav, body[-1].z))
        name = "OD GROOVES"
    else:
        rb = part.bore[-1][0]
        x_in = rb - 1.0
        za = m.planner["start_z"]
        approach = [Move("rapid", x_in, za)]
        for g in gs:
            surf = _surface_r(part, g)
            body.append(Move("rapid", x_in, g.z_left))
            body += _groove_moves(g, t, surf)
            body.append(Move("rapid", x_in, body[-1].z))
        body.append(Move("rapid", x_in, za))
        name = "ID GROOVES"
    return [Op(name, t, _css(t, part, m), approach, body)]


def part_off_op(part: Part, tools, m: Machine) -> Op:
    t = _tool(tools, "groove_ext")
    z = -(part.length + t.width)
    x_clear = max(p[0] for p in part.od) + 1.0
    rb = part.bore[-1][0] if part.bore else 0.0
    x_end = rb - 0.5 if part.bore else -0.5
    x_slow = x_end + 2.0
    approach = [Move("rapid", part.stock_r + 2.0, z)]
    body = [
        Move("rapid", x_clear, z),
        Move("feed", x_slow, z, f=t.f),
        Move("feed", x_end, z, f=t.f / 2),
        Move("rapid", x_clear, z),
        Move("rapid", part.stock_r + 2.0, z),
    ]
    return Op("PART-OFF", t, _css(t, part, m), approach, body)


def thread_op(part: Part, tools, m: Machine) -> Op:
    t = _tool(tools, "thread")
    th = part.thread
    pl = m.planner
    # G76 carries depths in whole microns; use the same values for the explicit passes
    k = round(th.depth, 3)
    x_minor = round(th.major_d / 2 - k, 4)
    xs = th.major_d / 2 + 1.0
    zs = 3.0 * th.pitch
    d1 = round(pl["thread_first_depth_per_pitch"] * th.pitch, 3)
    dmin = pl["thread_min_depth"]
    fin = pl["thread_finish_allowance"]
    mrep = pl["thread_spring_passes"]
    body = cycles.g76((xs, zs), x_minor, th.z_end, k, d1, dmin, fin, mrep, th.pitch)
    return Op(
        "THREAD",
        t,
        _const(t, part, m, th.major_d, t.n_max or None),
        [Move("rapid", xs, zs)],
        body,
        cycle={"type": "G76", "start": (xs, zs), "x_minor": x_minor, "z_end": th.z_end, "k": k,
               "d1": d1, "dmin": dmin, "fin": fin, "m": mrep, "lead": th.pitch},
    )


def plan(part: Part, tools, m: Machine) -> list[Op]:
    ops = [face_op(part, tools, m)] + od_ops(part, tools, m)
    if part.bore is not None:
        ops.append(drill_op(part, tools, m))
        ops += id_ops(part, tools, m)
        ops += groove_ops(part, tools, m, "int")
    ops += groove_ops(part, tools, m, "ext")
    if part.thread is not None:
        ops.append(thread_op(part, tools, m))
    if part.part_off:
        ops.append(part_off_op(part, tools, m))
    return ops
