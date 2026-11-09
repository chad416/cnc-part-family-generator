"""Part families: one CSV row -> one Part (profile, grooves, thread), with validation (F01, F02)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Pt, fillet_concave
from .tooling import Machine, Tool

FAMILIES = ("rod_end", "piston", "gland")

# ISO 68-1 basic profile, external thread: h3 = 0.61343 P, d3 = d - 1.22687 P
H3_PER_P = 0.61343
D3_PER_P = 1.22687


@dataclass(frozen=True)
class Groove:
    name: str
    side: str  # 'ext' | 'int'
    z_right: float  # wall towards the front face (less negative)
    width: float
    r_bottom: float

    @property
    def z_left(self) -> float:
        return self.z_right - self.width


@dataclass(frozen=True)
class Thread:
    major_d: float
    pitch: float
    turned_d: float
    z_end: float  # programmed thread end (inside the relief groove)

    @property
    def depth(self) -> float:
        return H3_PER_P * self.pitch

    @property
    def minor_d(self) -> float:
        return self.major_d - D3_PER_P * self.pitch


@dataclass
class Part:
    part_id: str
    family: str
    description: str
    material: str
    stock_d: float
    od: list[Pt]  # front -> back, turned zone only
    bore: list[Pt] | None
    grooves: list[Groove]
    thread: Thread | None
    part_off: bool
    length: float  # finished length (part-off parts) or checked length (rod end)
    face_allowance: float
    z_chuck: float
    drill: Tool | None = None
    params: dict = field(default_factory=dict)
    od_corner_r: float = 0.0  # inside-corner radius the drawing must allow (finishing nose radius)
    bore_corner_r: float = 0.0

    @property
    def stock_r(self) -> float:
        return self.stock_d / 2.0

    @property
    def z_check_min(self) -> float:
        return -self.length

    def outline(self) -> list[Pt]:
        """Closed half-section of the finished part (x = radius), grooves included."""
        zb = -self.length
        ext = [g for g in self.grooves if g.side == "ext"]
        intg = [g for g in self.grooves if g.side == "int"]
        od_f = fillet_concave(self.od, self.od_corner_r, "ext", [(g.z_left, g.z_right) for g in ext])
        od = _notch(_truncate(od_f, zb), ext)
        if self.bore is None:
            # solid: the OD runs on at the last radius to the checked length, then to the axis
            last_r = od[-1][0]
            pts = list(od)
            if pts[-1][1] > zb:
                pts.append((last_r, zb))
            pts += [(0.0, zb), (0.0, 0.0)]
            return pts
        bore_f = fillet_concave(self.bore, self.bore_corner_r, "int", [(g.z_left, g.z_right) for g in intg])
        bore = _notch(_truncate(bore_f, zb), intg)
        return list(od) + list(reversed(bore))


def _truncate(poly: list[Pt], zb: float) -> list[Pt]:
    """Cut a front->back polyline at z = zb (keeps z >= zb)."""
    out = [poly[0]]
    for a, b in zip(poly, poly[1:]):
        if b[1] >= zb - 1e-12:
            out.append(b)
            continue
        if a[1] > zb:
            t = (zb - a[1]) / (b[1] - a[1])
            out.append((a[0] + t * (b[0] - a[0]), zb))
        break
    return out


def _notch(poly: list[Pt], grooves: list[Groove]) -> list[Pt]:
    """Insert rectangular grooves into horizontal (cylindrical) segments of a front->back polyline."""
    pts = list(poly)
    for g in grooves:
        done = False
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if abs(a[0] - b[0]) > 1e-9:
                continue
            if a[1] >= g.z_right - 1e-9 and b[1] <= g.z_left + 1e-9:
                r = a[0]
                new = [(r, g.z_right), (g.r_bottom, g.z_right), (g.r_bottom, g.z_left), (r, g.z_left)]
                pts = pts[: i + 1] + new + pts[i + 1 :]
                done = True
                break
        if not done:
            raise ValueError(f"groove {g.name} is not on a cylindrical surface of the profile")
    # drop zero-length steps
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-12 or abs(p[1] - clean[-1][1]) > 1e-12:
            clean.append(p)
    return clean


def _f(row: dict, key: str) -> float:
    try:
        return float(row[key])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{row.get('id', '?')}: bad or missing value for '{key}'") from exc


def _finish_rn(tools: dict[int, Tool]) -> float:
    turn = [t for t in tools.values() if t.kind == "turn"]
    return min(t.rn for t in turn)


def _tool_of(tools: dict[int, Tool], kind: str) -> Tool:
    for t in tools.values():
        if t.kind == kind:
            return t
    raise ValueError(f"tool library has no '{kind}' tool")


def _check_groove(pid: str, g: Groove, tool: Tool, surface_r: float) -> None:
    if g.width < tool.width - 1e-9:
        raise ValueError(f"{pid}: groove {g.name} width {g.width} < tool width {tool.width}")
    depth = abs(surface_r - g.r_bottom)
    if depth > tool.max_depth + 1e-9:
        raise ValueError(f"{pid}: groove {g.name} depth {depth:.2f} > tool limit {tool.max_depth}")
    if g.side == "ext" and g.r_bottom >= surface_r:
        raise ValueError(f"{pid}: groove {g.name} bottom is not below the surface")
    if g.side == "int" and g.r_bottom <= surface_r:
        raise ValueError(f"{pid}: groove {g.name} bottom is not outside the bore")


def _check_no_overlap(pid: str, grooves: list[Groove], z_front: float, z_back: float, gap: float) -> None:
    for side in ("ext", "int"):
        gs = sorted((g for g in grooves if g.side == side), key=lambda g: -g.z_right)
        prev_left = z_front
        for g in gs:
            if g.z_right > prev_left - gap + 1e-9:
                raise ValueError(f"{pid}: groove {g.name} is closer than {gap} mm to the previous feature")
            prev_left = g.z_left
        if gs and gs[-1].z_left < z_back + gap - 1e-9:
            raise ValueError(f"{pid}: groove {gs[-1].name} is closer than {gap} mm to the back face")


def _pick_drill(pid: str, bore_d: float, tools: dict[int, Tool], bar: Tool) -> Tool:
    drills = sorted((t for t in tools.values() if t.kind == "drill"), key=lambda t: t.d)
    ok = [t for t in drills if t.d <= bore_d - 2.0 + 1e-9 and t.d >= bar.min_bore - 1e-9]
    if not ok:
        raise ValueError(
            f"{pid}: no drill between boring-bar minimum {bar.min_bore} and bore {bore_d} - 2 mm"
        )
    return ok[-1]


def build_rod_end(row: dict, tools: dict[int, Tool], m: Machine) -> Part:
    pid = row["id"]
    D, dt, p = _f(row, "rod_d"), _f(row, "thread_d"), _f(row, "pitch")
    lt, rw = _f(row, "thread_len"), _f(row, "relief_w")
    ds, ls, c = _f(row, "spigot_d"), _f(row, "spigot_len"), _f(row, "chamfer")
    pl = m.planner
    if not dt < ds < D - 2.0:
        raise ValueError(f"{pid}: need thread_d < spigot_d < rod_d - 2")
    turned = dt - pl["thread_turned_undersize"]
    rt, rs, rr = turned / 2, ds / 2, D / 2
    c2 = 1.0
    z_sh = -(lt + ls)
    od = [
        (rt - c, 0.0),
        (rt, -c),
        (rt, -lt),
        (rs, -lt),
        (rs, z_sh),
        (rr - c2, z_sh),
        (rr, z_sh - c2),
    ]
    minor = dt - D3_PER_P * p
    relief = Groove("relief", "ext", -(lt - rw), rw, minor / 2 - 0.1)
    thread = Thread(dt, p, turned, relief.z_left + rw / 2)
    if c < thread.depth - 1e-9:
        raise ValueError(f"{pid}: chamfer {c} < thread depth {thread.depth:.3f}")
    if lt - rw < 3 * p:
        raise ValueError(f"{pid}: usable thread length {lt - rw} < 3 x pitch")
    _check_groove(pid, relief, _tool_of(tools, "groove_ext"), rt)
    n_thread = [t for t in tools.values() if t.kind == "thread"]
    if not n_thread:
        raise ValueError("tool library has no thread tool")
    length = lt + ls + c2 + 3.0
    return Part(
        pid,
        "rod_end",
        f"PISTON ROD END M{dt:g}x{p:g} ROD D{D:g}",
        row["material"],
        D,
        od,
        None,
        [relief],
        thread,
        False,
        length,
        pl["face_allowance"],
        z_chuck=-(lt + ls + c2) - 15.0,
        params=dict(row),
        od_corner_r=_finish_rn(tools),
    )


def build_piston(row: dict, tools: dict[int, Tool], m: Machine) -> Part:
    pid = row["id"]
    bore, clear, di, L = _f(row, "bore_d"), _f(row, "od_clear"), _f(row, "inner_d"), _f(row, "length")
    sw, sb = _f(row, "seal_w"), _f(row, "seal_bottom_d")
    gw, gb, gi = _f(row, "guide_w"), _f(row, "guide_bottom_d"), _f(row, "guide_inset")
    c, stock = _f(row, "chamfer"), _f(row, "stock_d")
    pl = m.planner
    gt = _tool_of(tools, "groove_ext")
    w_po = gt.width
    od_d = bore - clear
    if stock < od_d + 1.0:
        raise ValueError(f"{pid}: stock {stock} too small for OD {od_d}")
    ro, rb = od_d / 2, di / 2
    z_od = -(L + w_po + pl["part_off_extra_od"])
    z_id = -(L + w_po + pl["part_off_extra_bore"])
    od = [(ro - c, 0.0), (ro, -c), (ro, z_od)]
    bar = _tool_of(tools, "bore")
    drill = _pick_drill(pid, di, tools, bar)
    cid = 1.0
    bore_p = [(rb + cid, 0.0), (rb, -cid), (rb, z_id)]
    grooves = [
        Groove("guide_front", "ext", -gi, gw, gb / 2),
        Groove("seal", "ext", -(L / 2 - sw / 2), sw, sb / 2),
        Groove("guide_back", "ext", -(L - gi - gw), gw, gb / 2),
    ]
    for g in grooves:
        _check_groove(pid, g, gt, ro)
        if g.r_bottom - rb < 3.0:
            raise ValueError(f"{pid}: groove {g.name} leaves < 3 mm wall to the bore")
    _check_no_overlap(pid, grooves, -c, -L, 1.0)
    return Part(
        pid,
        "piston",
        f"PISTON BORE D{bore:g} ROD SEAT D{di:g}",
        row["material"],
        stock,
        od,
        bore_p,
        grooves,
        None,
        True,
        L,
        pl["face_allowance"],
        z_chuck=-(L + w_po + 6.0),
        drill=drill,
        params=dict(row),
        od_corner_r=_finish_rn(tools),
        bore_corner_r=_tool_of(tools, "bore").rn,
    )


def build_gland(row: dict, tools: dict[int, Tool], m: Machine) -> Part:
    pid = row["id"]
    rod, clear, odd, L = _f(row, "rod_d"), _f(row, "id_clear"), _f(row, "od"), _f(row, "length")
    c, stock = _f(row, "chamfer"), _f(row, "stock_d")
    pl = m.planner
    gt_e = _tool_of(tools, "groove_ext")
    gt_i = _tool_of(tools, "groove_int")
    w_po = gt_e.width
    bore_d = rod + clear
    if stock < odd + 1.0:
        raise ValueError(f"{pid}: stock {stock} too small for OD {odd}")
    ro, rb = odd / 2, bore_d / 2
    z_od = -(L + w_po + pl["part_off_extra_od"])
    z_id = -(L + w_po + pl["part_off_extra_bore"])
    od = [(ro - c, 0.0), (ro, -c), (ro, z_od)]
    bar = _tool_of(tools, "bore")
    drill = _pick_drill(pid, bore_d, tools, bar)
    if bore_d < gt_i.min_bore - 1e-9:
        raise ValueError(f"{pid}: bore {bore_d} < ID grooving bar minimum {gt_i.min_bore}")
    cid = 1.0
    bore_p = [(rb + cid, 0.0), (rb, -cid), (rb, z_id)]
    grooves = [
        Groove("oring", "ext", -_f(row, "oring_z"), _f(row, "oring_w"), _f(row, "oring_bottom_d") / 2),
        Groove("wiper", "int", -_f(row, "wiper_z"), _f(row, "wiper_w"), _f(row, "wiper_bottom_d") / 2),
        Groove("rod_seal", "int", -_f(row, "seal_z"), _f(row, "seal_w"), _f(row, "seal_bottom_d") / 2),
        Groove("guide", "int", -_f(row, "guide_z"), _f(row, "guide_w"), _f(row, "guide_bottom_d") / 2),
    ]
    for g in grooves:
        _check_groove(pid, g, gt_e if g.side == "ext" else gt_i, ro if g.side == "ext" else rb)
    _check_no_overlap(pid, grooves, -c, -L, 1.0)
    return Part(
        pid,
        "gland",
        f"GLAND ROD D{rod:g} OD D{odd:g}",
        row["material"],
        stock,
        od,
        bore_p,
        grooves,
        None,
        True,
        L,
        pl["face_allowance"],
        z_chuck=-(L + w_po + 6.0),
        drill=drill,
        params=dict(row),
        od_corner_r=_finish_rn(tools),
        bore_corner_r=_tool_of(tools, "bore").rn,
    )


BUILDERS = {"rod_end": build_rod_end, "piston": build_piston, "gland": build_gland}


def build_part(family: str, row: dict, tools: dict[int, Tool], m: Machine) -> Part:
    if family not in BUILDERS:
        raise ValueError(f"unknown family {family}")
    if row.get("material") not in m.materials:
        raise ValueError(f"{row.get('id')}: unknown material {row.get('material')}")
    return BUILDERS[family](row, tools, m)


def load_family(data_dir: Path, family: str, tools: dict[int, Tool], m: Machine) -> list[Part]:
    path = Path(data_dir) / f"{family}.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return [build_part(family, row, tools, m) for row in csv.DictReader(fh)]


def load_all(data_dir: Path, tools: dict[int, Tool], m: Machine) -> list[Part]:
    parts = []
    for fam in FAMILIES:
        parts += load_family(data_dir, fam, tools, m)
    return parts
