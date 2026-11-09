"""Canned-cycle model (SPEC section 5).

The planner and the verifier share these functions. In explicit mode the planner writes out the
moves this module produces. In canned-cycle mode the program holds G71/G70/G76, and the verifier
expands them with the same model. The material-removal simulation is the independent check of the
result.
"""

from __future__ import annotations

import math

from .geometry import Pt
from .motion import Move, polyline, shift


def _first_crossing(pts: list[Pt], level: float, side: str) -> float | None:
    """z where the contour first reaches radius `level`, walking from its start."""
    for a, b in zip(pts, pts[1:]):
        if side == "ext":
            if b[0] >= level - 1e-12:
                if a[0] >= level - 1e-12:
                    return a[1]
                t = (level - a[0]) / (b[0] - a[0])
                return a[1] + t * (b[1] - a[1])
        else:
            if b[0] <= level + 1e-12:
                if a[0] <= level + 1e-12:
                    return a[1]
                t = (level - a[0]) / (b[0] - a[0])
                return a[1] + t * (b[1] - a[1])
    return None


def g71(start: Pt, contour: list[Move], d: float, e: float, u: float, w: float, f: float, side: str) -> list[Move]:
    """Type-I stock removal. contour[0] is the X-only move to A'.

    u: radial finishing allowance incl. sign (+ OD, - ID); w: axial allowance.
    """
    xa, za = start
    pts = polyline(start, contour)[1:]
    sh = [(x + u, z + w) for x, z in pts]
    sgn = 1.0 if side == "ext" else -1.0
    levels = []
    k = 1
    if side == "ext":
        lim = min(p[0] for p in sh)
        while xa - k * d > lim + 1e-6:
            levels.append(xa - k * d)
            k += 1
    else:
        lim = max(p[0] for p in sh)
        while xa + k * d < lim - 1e-6:
            levels.append(xa + k * d)
            k += 1
    out: list[Move] = []
    for xk in levels:
        zi = _first_crossing(sh, xk, side)
        if zi is None:
            zi = sh[-1][1]
        if zi >= za - 1e-6:
            continue
        out.append(Move("rapid", xk, za))
        out.append(Move("feed", xk, zi, f=f))
        out.append(Move("rapid", xk + sgn * e, zi + e))
        out.append(Move("rapid", xk + sgn * e, za))
    out.append(Move("rapid", sh[0][0], sh[0][1]))
    for m in contour[1:]:
        out.append(shift(m, u, w, kind="arc" if m.kind == "arc" else "feed", f=f))
    out.append(Move("rapid", xa, za))
    return out


def g70(start: Pt, contour: list[Move]) -> list[Move]:
    """Finish along blocks P..Q, then return to the start point."""
    first = contour[0]
    out = [Move("rapid", first.x, start[1])]
    out += list(contour[1:])
    out.append(Move("rapid", start[0], start[1]))
    return out


def thread_depths(k: float, d1: float, dmin: float, fin: float, m: int) -> list[float]:
    """Cumulative radial depths: d1*sqrt(n) (step >= dmin) up to k - fin, then m passes at k."""
    depths: list[float] = []
    prev = 0.0
    n = 1
    while True:
        dn = d1 * math.sqrt(n)
        if dn - prev < dmin:
            dn = prev + dmin
        if dn >= k - fin - 1e-6:
            break
        depths.append(dn)
        prev = dn
        n += 1
    if fin > 0 and (not depths or depths[-1] < k - fin - 1e-6):
        depths.append(k - fin)
    depths += [k] * max(1, m)
    return depths


def g76(start: Pt, x_minor: float, z_end: float, k: float, d1: float, dmin: float, fin: float, m: int, lead: float) -> list[Move]:
    """Thread passes with radial infeed (model; a real G76 uses flank infeed)."""
    xs, zs = start
    x_major = x_minor + k
    out: list[Move] = []
    for dn in thread_depths(k, d1, dmin, fin, m):
        xp = x_major - dn
        out.append(Move("rapid", xp, zs))
        out.append(Move("thread", xp, z_end, f=lead))
        out.append(Move("rapid", xs, z_end))
        out.append(Move("rapid", xs, zs))
    return out
