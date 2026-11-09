"""Material-removal check on the axisymmetric half-section (V02-V05, V07).

The stock is a grid of cells (x = radius, z). Each move removes the cells that its tool sweeps:
- a disk for turning and boring tools;
- a rectangle for grooving tools;
- a cone-tipped cylinder for drills.

The finished part is a polygon. Its signed distance field gives the gouge and leftover checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from matplotlib.path import Path as MplPath

from .geometry import arc_centre, arc_points
from .interp import Issue, Program, SimMove
from .parts import Part
from .tooling import Machine, Tool

DRILL_COT = 1.0 / math.tan(math.radians(59.0))  # 118 deg point


@dataclass
class Result:
    issues: list[Issue] = field(default_factory=list)
    gouge_cells: int = 0
    gouge_max: float = 0.0
    leftover_cells: int = 0
    leftover_max: float = 0.0
    rapid_hits: int = 0
    jaw_hits: int = 0
    material: np.ndarray | None = None
    sd: np.ndarray | None = None
    check: np.ndarray | None = None
    band: np.ndarray | None = None
    gouge_mask: np.ndarray | None = None
    extent: tuple = ()

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


class Sim:
    def __init__(self, part: Part, m: Machine, h: float = 0.05, tol: float = 0.01):
        self.part = part
        self.h = h
        self.tol = tol
        sr = part.stock_r
        self.r_max = sr + m.jaw_height + 5.0
        self.z_min = part.z_chuck - 20.0
        self.z_max = part.face_allowance + 5.0
        nr = int(math.ceil(self.r_max / h))
        nz = int(math.ceil((self.z_max - self.z_min) / h))
        self.r = (np.arange(nr) + 0.5) * h
        self.z = self.z_min + (np.arange(nz) + 0.5) * h
        R = self.r[:, None]
        Z = self.z[None, :]
        self.material = (R <= sr) & (Z <= part.face_allowance)
        self.jaws = (R >= sr) & (R <= sr + m.jaw_height) & (Z <= part.z_chuck)
        self.sd = self._signed_distance()
        self.check = np.broadcast_to((Z >= part.z_check_min) & (Z <= part.face_allowance), self.material.shape)
        if part.thread is not None:
            th = part.thread
            self.band = (Z >= th.z_end) & (Z <= 0.0) & (R >= th.minor_d / 2 - 0.01) & (R <= th.turned_d / 2 + 0.01)
        else:
            self.band = np.zeros_like(self.material)
        self.gouge = np.zeros_like(self.material)

    # ---- geometry helpers -------------------------------------------------
    def _signed_distance(self) -> np.ndarray:
        """Signed distance to the part outline (negative inside), capped at CAP mm (float32)."""
        cap = 1.0
        poly = np.array(self.part.outline())
        k = min(len(self.r), int(math.ceil((self.part.stock_r + 1.0) / self.h)) + 2)
        d2 = np.full((k, len(self.z)), cap * cap, dtype=np.float32)
        closed = np.vstack([poly, poly[:1]])
        for (ax, az), (bx, bz) in zip(closed[:-1], closed[1:]):
            dx, dz = bx - ax, bz - az
            L2 = dx * dx + dz * dz
            if L2 < 1e-18:
                continue
            rs = self._rs(min(ax, bx) - cap, max(ax, bx) + cap)
            rs = slice(rs.start, min(rs.stop, k))
            zs = self._zs(min(az, bz) - cap, max(az, bz) + cap)
            if rs.stop <= rs.start or zs.stop <= zs.start:
                continue
            R = self.r[rs][:, None]
            Z = self.z[zs][None, :]
            t = np.clip(((R - ax) * dx + (Z - az) * dz) / L2, 0.0, 1.0)
            dd = ((R - ax - t * dx) ** 2 + (Z - az - t * dz) ** 2).astype(np.float32)
            np.minimum(d2[rs, zs], dd, out=d2[rs, zs])
        path = MplPath(poly)
        sd = np.full((len(self.r), len(self.z)), cap, dtype=np.float32)
        sd[:k] = np.sqrt(d2)
        zcol = self.z
        for i in range(k):
            pts = np.column_stack([np.full(len(zcol), self.r[i]), zcol])
            inside = path.contains_points(pts)
            sd[i, inside] *= -1.0
        return sd

    def _rs(self, lo: float, hi: float) -> slice:
        i0 = max(0, int(math.ceil(lo / self.h - 0.5)))
        i1 = min(len(self.r) - 1, int(math.floor(hi / self.h - 0.5)))
        return slice(i0, i1 + 1)

    def _zs(self, lo: float, hi: float) -> slice:
        i0 = max(0, int(math.ceil((lo - self.z_min) / self.h - 0.5)))
        i1 = min(len(self.z) - 1, int(math.floor((hi - self.z_min) / self.h - 0.5)))
        return slice(i0, i1 + 1)

    # ---- tool sweeps --------------------------------------------------------
    def _capsule(self, p0, p1, rad):
        x0, z0 = p0
        x1, z1 = p1
        rs = self._rs(min(x0, x1) - rad, max(x0, x1) + rad)
        zs = self._zs(min(z0, z1) - rad, max(z0, z1) + rad)
        if rs.stop <= rs.start or zs.stop <= zs.start:
            return None
        R = self.r[rs][:, None]
        Z = self.z[zs][None, :]
        dx, dz = x1 - x0, z1 - z0
        L2 = dx * dx + dz * dz
        if L2 < 1e-18:
            d2 = (R - x0) ** 2 + (Z - z0) ** 2
        else:
            t = np.clip(((R - x0) * dx + (Z - z0) * dz) / L2, 0.0, 1.0)
            d2 = (R - x0 - t * dx) ** 2 + (Z - z0 - t * dz) ** 2
        return rs, zs, d2 <= rad * rad

    def _groove(self, p0, p1, w, side, shrink):
        x0, z0 = p0
        x1, z1 = p1
        zs = self._zs(min(z0, z1) + shrink, max(z0, z1) + w - shrink)
        if zs.stop <= zs.start:
            return None
        Zc = self.z[zs]
        dz = z1 - z0
        if abs(dz) < 1e-12:
            tlo = np.zeros_like(Zc)
            thi = np.ones_like(Zc)
            valid = np.ones_like(Zc, dtype=bool)
        else:
            ta = (Zc - w + shrink - z0) / dz
            tb = (Zc - shrink - z0) / dz
            lo, hi = np.minimum(ta, tb), np.maximum(ta, tb)
            valid = (hi >= 0.0) & (lo <= 1.0)
            tlo, thi = np.clip(lo, 0, 1), np.clip(hi, 0, 1)
        xa = x0 + tlo * (x1 - x0)
        xb = x0 + thi * (x1 - x0)
        if side == "ext":
            thr = np.minimum(xa, xb) + shrink
            # above the stock there is only air, except at the chuck jaws
            r_hi = self.r_max if min(z0, z1) <= self.part.z_chuck else self.part.stock_r + self.h
            rs = self._rs(min(x0, x1) + shrink, r_hi)
            if rs.stop <= rs.start:
                return None
            mask = self.r[rs][:, None] >= thr[None, :]
        else:
            thr = np.maximum(xa, xb) - shrink
            rs = self._rs(0.0, max(x0, x1) - shrink)
            if rs.stop <= rs.start:
                return None
            mask = self.r[rs][:, None] <= thr[None, :]
        return rs, zs, mask & valid[None, :]

    def _drill(self, p0, p1, rad, shrink):
        x0, z0 = p0
        x1, z1 = p1
        zt = min(z0, z1) + shrink
        rr = rad - shrink
        if abs(x0) < 1e-9 and abs(x1) < 1e-9:
            rs = self._rs(0.0, rr)
            zs = self._zs(zt, self.z_max)
            if rs.stop <= rs.start or zs.stop <= zs.start:
                return None
            R = self.r[rs][:, None]
            Z = self.z[zs][None, :]
            return rs, zs, (Z - zt) >= DRILL_COT * R
        rs = self._rs(0.0, max(abs(x0), abs(x1)) + rr)
        zs = self._zs(zt, self.z_max)
        if rs.stop <= rs.start or zs.stop <= zs.start:
            return None
        return rs, zs, np.ones((rs.stop - rs.start, zs.stop - zs.start), dtype=bool)

    def sweeps(self, mv: SimMove, tool: Tool, shrink: float = 0.0):
        p0, p1 = (mv.x0, mv.z0), (mv.x1, mv.z1)
        kind = tool.kind
        if kind in ("turn", "bore", "thread"):
            # insert = nose disk + the cutting-edge strip that runs from the nose away from the
            # axis (OD tools, thread tool) or towards the axis (boring bar), 2*rn wide in Z
            rn = tool.rn if kind != "thread" else 0.05
            rad = rn - shrink
            side = "int" if kind == "bore" else "ext"
            if mv.kind == "arc":
                c = arc_centre(p0, p1, mv.r, mv.ccw)
                pts = arc_points(p0, p1, c, mv.ccw, 1e-3)
            else:
                pts = [p0, p1]
            out = []
            for a, b in zip(pts, pts[1:]):
                out.append(self._capsule(a, b, rad))
                out.append(self._groove((a[0], a[1] - rn), (b[0], b[1] - rn), 2 * rn, side, shrink))
            return [s for s in out if s is not None]
        if kind in ("groove_ext", "groove_int"):
            s = self._groove(p0, p1, tool.width, "ext" if kind == "groove_ext" else "int", shrink)
            return [s] if s is not None else []
        if kind == "drill":
            s = self._drill(p0, p1, tool.d / 2, shrink)
            return [s] if s is not None else []
        raise ValueError(kind)

    # ---- run --------------------------------------------------------------
    def run(self, prog: Program, tools: dict[int, Tool]) -> Result:
        res = Result(issues=list(prog.issues))
        gtol = self.tol
        for mv in prog.moves:
            if mv.tool is None or mv.tool not in tools:
                if mv.kind != "rapid":
                    res.issues.append(Issue("L10", mv.line, "move without a known tool"))
                continue
            tool = tools[mv.tool]
            if mv.kind == "rapid":
                hits = 0
                jaw = 0
                for rs, zs, mask in self.sweeps(mv, tool, shrink=gtol):
                    hits += int(np.count_nonzero(mask & self.material[rs, zs]))
                    jaw += int(np.count_nonzero(mask & self.jaws[rs, zs]))
                if hits:
                    res.rapid_hits += hits
                    res.issues.append(Issue("L3", mv.line, f"rapid move cuts material ({hits} cells)"))
                if jaw:
                    res.jaw_hits += jaw
                    res.issues.append(Issue("L4", mv.line, f"move enters the chuck jaws ({jaw} cells)"))
            if mv.kind == "thread":
                continue
            jaw = 0
            for rs, zs, mask in self.sweeps(mv, tool):
                sub = self.material[rs, zs]
                cut = mask & sub
                g = cut & (self.sd[rs, zs] < -gtol)
                ng = int(np.count_nonzero(g))
                if ng:
                    self.gouge[rs, zs] |= g
                    res.gouge_cells += ng
                    res.gouge_max = max(res.gouge_max, float(-self.sd[rs, zs][g].min()))
                    res.issues.append(Issue("L5", mv.line, f"gouge: part material removed ({ng} cells)"))
                if mv.kind != "rapid":
                    jaw += int(np.count_nonzero(mask & self.jaws[rs, zs]))
                sub &= ~mask
            if jaw:
                res.jaw_hits += jaw
                res.issues.append(Issue("L4", mv.line, f"move enters the chuck jaws ({jaw} cells)"))
        left = self.material & (self.sd > gtol) & self.check & ~self.band
        res.leftover_cells = int(np.count_nonzero(left))
        if res.leftover_cells:
            res.leftover_max = float(self.sd[left].max())
            res.issues.append(
                Issue("L6", 0, f"leftover: {res.leftover_cells} cells up to {res.leftover_max:.3f} mm outside the part")
            )
        if self.part.thread is not None:
            res.issues += check_thread(self.part, prog)
        res.material = self.material
        res.sd = self.sd
        res.check = np.asarray(self.check)
        res.band = self.band
        res.gouge_mask = self.gouge
        res.extent = (self.z_min, self.z_max, 0.0, self.r_max)
        return res


def check_thread(part: Part, prog: Program) -> list[Issue]:
    """V07: depth, end inside the relief groove, run-in, lead."""
    th = part.thread
    out = []
    passes = [mv for mv in prog.moves if mv.kind == "thread"]
    if not passes:
        return [Issue("L13", 0, "no thread passes found")]
    final_x = min(mv.x1 for mv in passes)
    want = th.major_d / 2 - th.depth
    if abs(final_x - want) > 0.005:
        out.append(Issue("L13", passes[-1].line, f"thread final radius {final_x:.4f}, ISO 68-1 needs {want:.4f}"))
    relief = [g for g in part.grooves if g.name == "relief"]
    for mv in passes:
        if mv.f is None or abs(mv.f - th.pitch) > 1e-6:
            out.append(Issue("L16", mv.line, f"thread lead {mv.f} differs from pitch {th.pitch}"))
            break
    for mv in passes:
        ze = mv.z1
        if not any(g.z_left + 0.5 <= ze <= g.z_right - 0.5 for g in relief):
            out.append(Issue("L14", mv.line, f"thread ends at Z{ze:.3f}, not inside the relief groove"))
            break
    for mv in passes:
        if mv.z0 < 2 * th.pitch - 1e-9:
            out.append(Issue("L15", mv.line, f"thread run-in starts at Z{mv.z0:.3f} < 2 x pitch"))
            break
    return out


def verify(part: Part, prog: Program, tools: dict[int, Tool], m: Machine, h: float = 0.05) -> Result:
    return Sim(part, m, h=h).run(prog, tools)
