"""Tool moves shared by the planner, the post-processors and the cycle model."""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import Arc, Line, Pt, arc_centre, arc_points


@dataclass(frozen=True)
class Move:
    """One programmed move to the end point (x = radius, z).

    kind: 'rapid' | 'feed' | 'arc' | 'thread'
    f: feed in mm/rev for 'feed'/'arc'; lead in mm for 'thread'
    """

    kind: str
    x: float
    z: float
    f: float | None = None
    ccw: bool = False
    r: float = 0.0


def elements_to_moves(elems: list[Line | Arc], f: float) -> list[Move]:
    """Feed moves along a path that starts at elems[0].p0 (the caller is already there)."""
    out = []
    for e in elems:
        if isinstance(e, Line):
            out.append(Move("feed", e.p1[0], e.p1[1], f=f))
        else:
            out.append(Move("arc", e.p1[0], e.p1[1], f=f, ccw=e.ccw, r=e.r))
    return out


def shift(m: Move, dx: float, dz: float, kind: str | None = None, f: float | None = None) -> Move:
    return Move(kind or m.kind, m.x + dx, m.z + dz, f=m.f if f is None else f, ccw=m.ccw, r=m.r)


def polyline(start: Pt, moves: list[Move], max_err: float = 1e-4) -> list[Pt]:
    """Points along a chain of moves, starting at start."""
    pts = [start]
    cur = start
    for m in moves:
        end = (m.x, m.z)
        if m.kind == "arc":
            c = arc_centre(cur, end, m.r, m.ccw)
            pts.extend(arc_points(cur, end, c, m.ccw, max_err)[1:])
        else:
            pts.append(end)
        cur = end
    return pts
