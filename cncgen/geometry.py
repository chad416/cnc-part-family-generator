"""Plane geometry in the lathe half-section.

A point is (x, z): x = radius, z = axial position. The drawing view has Z to the right and X up.
Counter-clockwise (ccw) in that view is G3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Pt = tuple[float, float]


@dataclass(frozen=True)
class Line:
    p0: Pt
    p1: Pt


@dataclass(frozen=True)
class Arc:
    p0: Pt
    p1: Pt
    c: Pt
    ccw: bool

    @property
    def r(self) -> float:
        return math.hypot(self.p0[0] - self.c[0], self.p0[1] - self.c[1])


def cross_zx(a: Pt, b: Pt) -> float:
    """z-component of a x b in the (Z right, X up) frame. a, b are (x, z) vectors."""
    return a[1] * b[0] - a[0] * b[1]


def outward_normal(d: Pt, side: str) -> Pt:
    """Unit normal pointing away from the material for a profile walked front -> back.

    side 'ext': outer profile (material towards the axis); 'int': bore (material away from the axis).
    """
    dx, dz = d
    length = math.hypot(dx, dz)
    if side == "ext":
        return (-dz / length, dx / length)
    if side == "int":
        return (dz / length, -dx / length)
    raise ValueError(side)


def _intersect(a0: Pt, a1: Pt, b0: Pt, b1: Pt) -> Pt:
    """Intersection of the infinite lines a0-a1 and b0-b1."""
    ax, az = a1[0] - a0[0], a1[1] - a0[1]
    bx, bz = b1[0] - b0[0], b1[1] - b0[1]
    den = ax * bz - az * bx
    if abs(den) < 1e-14:
        raise ValueError("parallel offset segments")
    t = ((b0[0] - a0[0]) * bz - (b0[1] - a0[1]) * bx) / den
    return (a0[0] + t * ax, a0[1] + t * az)


def offset_profile(pts: list[Pt], rn: float, side: str) -> list[Line | Arc]:
    """Path of a round nose of radius rn that touches the profile from the free side.

    Convex corners get an arc around the vertex; concave corners are trimmed to the intersection.
    Raises ValueError if a profile segment is shorter than the nose can follow.
    """
    segs = []
    for a, b in zip(pts, pts[1:]):
        d = (b[0] - a[0], b[1] - a[1])
        if math.hypot(*d) < 1e-12:
            continue
        n = outward_normal(d, side)
        segs.append(
            {
                "a": a,
                "b": b,
                "d": d,
                "n": n,
                "s": (a[0] + rn * n[0], a[1] + rn * n[1]),
                "e": (b[0] + rn * n[0], b[1] + rn * n[1]),
            }
        )
    arcs_after = [False] * len(segs)
    for i in range(len(segs) - 1):
        s, t = segs[i], segs[i + 1]
        same = abs(s["n"][0] - t["n"][0]) < 1e-12 and abs(s["n"][1] - t["n"][1]) < 1e-12
        if same:
            continue
        dot = s["n"][0] * t["d"][0] + s["n"][1] * t["d"][1]
        if dot < 0:
            arcs_after[i] = True
        else:
            p = _intersect(s["s"], s["e"], t["s"], t["e"])
            s["e"] = p
            t["s"] = p
    out: list[Line | Arc] = []
    for i, s in enumerate(segs):
        nd = (s["e"][0] - s["s"][0], s["e"][1] - s["s"][1])
        if nd[0] * s["d"][0] + nd[1] * s["d"][1] <= 1e-12:
            raise ValueError(
                f"profile segment {s['a']}->{s['b']} is too short for nose radius {rn}"
            )
        out.append(Line(s["s"], s["e"]))
        if arcs_after[i]:
            v = s["b"]
            p0, p1 = s["e"], segs[i + 1]["s"]
            ccw = cross_zx((p0[0] - v[0], p0[1] - v[1]), (p1[0] - v[0], p1[1] - v[1])) > 0
            out.append(Arc(p0, p1, v, ccw))
    return out


def fillet_concave(
    pts: list[Pt], rho: float, side: str, skip_z: list[tuple[float, float]] = (), max_err: float = 1e-4
) -> list[Pt]:
    """Round every concave (inside) corner of a front->back profile with radius rho.

    A round tool nose of radius rho cannot make a sharp inside corner; the drawing must allow it.
    Corners whose z lies in one of the skip_z ranges (z_lo, z_hi) stay sharp (a groove cuts them).
    """
    if rho <= 0 or len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for a, v, b in zip(pts, pts[1:], pts[2:]):
        if any(lo - 1e-9 <= v[1] <= hi + 1e-9 for lo, hi in skip_z):
            out.append(v)
            continue
        d1 = (v[0] - a[0], v[1] - a[1])
        d2 = (b[0] - v[0], b[1] - v[1])
        n1 = outward_normal(d1, side)
        n2 = outward_normal(d2, side)
        if n1[0] * d2[0] + n1[1] * d2[1] <= 1e-12:  # convex or straight
            out.append(v)
            continue
        c = _intersect(
            (a[0] + rho * n1[0], a[1] + rho * n1[1]),
            (v[0] + rho * n1[0], v[1] + rho * n1[1]),
            (v[0] + rho * n2[0], v[1] + rho * n2[1]),
            (b[0] + rho * n2[0], b[1] + rho * n2[1]),
        )
        t1 = (c[0] - rho * n1[0], c[1] - rho * n1[1])
        t2 = (c[0] - rho * n2[0], c[1] - rho * n2[1])
        ccw = cross_zx((t1[0] - c[0], t1[1] - c[1]), (t2[0] - c[0], t2[1] - c[1])) > 0
        out.extend(arc_points(t1, t2, c, ccw, max_err))
    out.append(pts[-1])
    return out


def arc_centre(p0: Pt, p1: Pt, r: float, ccw: bool) -> Pt:
    """Centre of the minor arc of radius r from p0 to p1 (as G2/G3 with R)."""
    vz, vx = p1[1] - p0[1], p1[0] - p0[0]
    c = math.hypot(vz, vx)
    if c < 1e-12:
        raise ValueError("zero-length arc")
    h = math.sqrt(max(r * r - c * c / 4.0, 0.0))
    mz, mx = (p0[1] + p1[1]) / 2.0, (p0[0] + p1[0]) / 2.0
    lz, lx = -vx / c, vz / c  # left perpendicular in the (Z, X) frame
    s = 1.0 if ccw else -1.0
    return (mx + s * h * lx, mz + s * h * lz)


def arc_points(p0: Pt, p1: Pt, c: Pt, ccw: bool, max_err: float = 1e-4) -> list[Pt]:
    """Points along the arc, chord error <= max_err. First point p0, last point p1."""
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    a0 = math.atan2(p0[0] - c[0], p0[1] - c[1])
    a1 = math.atan2(p1[0] - c[0], p1[1] - c[1])
    if ccw:
        while a1 <= a0:
            a1 += 2 * math.pi
    else:
        while a1 >= a0:
            a1 -= 2 * math.pi
    span = abs(a1 - a0)
    if r <= max_err:
        n = 2
    else:
        step = 2 * math.acos(max(-1.0, 1 - max_err / r))
        n = max(2, math.ceil(span / step) + 1)
    pts = []
    for i in range(n):
        a = a0 + (a1 - a0) * i / (n - 1)
        pts.append((c[0] + r * math.sin(a), c[1] + r * math.cos(a)))
    pts[0] = p0
    pts[-1] = p1
    return pts


def arc_length(p0: Pt, p1: Pt, c: Pt, ccw: bool) -> float:
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    a0 = math.atan2(p0[0] - c[0], p0[1] - c[1])
    a1 = math.atan2(p1[0] - c[0], p1[1] - c[1])
    if ccw:
        while a1 <= a0:
            a1 += 2 * math.pi
    else:
        while a1 >= a0:
            a1 -= 2 * math.pi
    return r * abs(a1 - a0)
