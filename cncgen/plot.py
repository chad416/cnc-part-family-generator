"""Backplot PNG (F09): toolpath over the half-section, and the verification map."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Polygon  # noqa: E402

from .geometry import arc_centre, arc_points  # noqa: E402
from .interp import Program  # noqa: E402
from .parts import Part  # noqa: E402
from .tooling import Tool  # noqa: E402
from .verify import Result  # noqa: E402

TOOL_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#e377c2", "#17becf", "#7f7f7f", "#bcbd22"]


def _view(part: Part) -> tuple[float, float, float, float]:
    z0 = -(part.length + 6.0)
    return z0, part.face_allowance + 6.0, -1.0, part.stock_r + 6.0


def backplot(part: Part, prog: Program, res: Result, tools: dict[int, Tool], path: Path, title: str = "") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    zlo, zhi, xlo, xhi = _view(part)
    fig, axes = plt.subplots(2, 1, figsize=(11, 8.2), dpi=110, sharex=True)
    ax = axes[0]
    outline = np.array(part.outline())
    ax.add_patch(Polygon(outline[:, ::-1], closed=True, facecolor="#dfe7f2", edgecolor="#34495e", lw=1.2, zorder=1))
    ax.plot([zlo, part.face_allowance, part.face_allowance], [part.stock_r, part.stock_r, 0], color="#888", lw=1, ls="--", zorder=1)
    used = []
    for mv in prog.moves:
        if mv.tool is None:
            continue
        col = TOOL_COLORS[(mv.tool - 1) % len(TOOL_COLORS)]
        if mv.tool not in used:
            used.append(mv.tool)
        if mv.kind == "arc":
            c = arc_centre((mv.x0, mv.z0), (mv.x1, mv.z1), mv.r, mv.ccw)
            pts = np.array(arc_points((mv.x0, mv.z0), (mv.x1, mv.z1), c, mv.ccw, 1e-3))
            ax.plot(pts[:, 1], pts[:, 0], color=col, lw=1.1, zorder=3)
        elif mv.kind == "rapid":
            ax.plot([mv.z0, mv.z1], [mv.x0, mv.x1], color=col, lw=0.6, ls=":", alpha=0.6, zorder=2)
        else:
            ax.plot([mv.z0, mv.z1], [mv.x0, mv.x1], color=col, lw=1.1, zorder=3)
    handles = [Line2D([0], [0], color=TOOL_COLORS[(t - 1) % len(TOOL_COLORS)], lw=2, label=f"T{t} {tools[t].desc}") for t in used if t in tools]
    handles.append(Line2D([0], [0], color="#555", lw=1, ls=":", label="rapid (dotted)"))
    ax.legend(handles=handles, loc="upper left", fontsize=7, ncol=2, framealpha=0.9)
    ax.set_ylim(xlo, xhi)
    ax.set_xlim(zlo, zhi)
    ax.set_ylabel("radius x (mm)")
    ax.set_title(title or part.part_id, fontsize=10)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.25)

    ax = axes[1]
    # 0 removed/air, 1 finished part, 2 scrap stock outside the checked zone, 3 leftover, 4 gouge
    img = np.zeros(res.material.shape, dtype=np.uint8)
    inside = res.sd < 0
    img[res.material & inside] = 1
    img[res.material & ~inside] = 2
    left = res.material & (res.sd > 0.01) & res.check & ~res.band
    img[left] = 3
    img[res.gouge_mask] = 4
    cmap = ListedColormap(["#ffffff", "#5b8fd1", "#c8c8c8", "#ff8c1a", "#e01b24"])
    z_min, z_max, r_min, r_max = res.extent
    ax.imshow(img, origin="lower", extent=(z_min, z_max, r_min, r_max), cmap=cmap, vmin=0, vmax=4, interpolation="nearest", aspect="equal")
    ax.add_patch(Polygon(outline[:, ::-1], closed=True, fill=False, edgecolor="#1b2631", lw=0.8))
    errs = len(res.errors)
    ax.set_title(
        f"material-removal check: gouge {res.gouge_cells} cells, leftover {res.leftover_cells} cells, "
        f"rapid hits {res.rapid_hits}, jaw hits {res.jaw_hits}  ->  {'PASS' if errs == 0 else f'{errs} ERRORS'}",
        fontsize=9,
    )
    ax.legend(
        handles=[
            Patch(color="#5b8fd1", label="finished part"),
            Patch(color="#c8c8c8", label="stock left outside the part (scrap / bar)"),
            Patch(color="#ff8c1a", label="leftover > 0.01 mm"),
            Patch(color="#e01b24", label="gouge > 0.01 mm"),
        ],
        loc="upper left",
        fontsize=7,
        framealpha=0.9,
    )
    bad = res.gouge_mask | left
    if bad.any():
        ii, jj = np.nonzero(bad)
        r = (np.arange(img.shape[0]) + 0.5) * (r_max - r_min) / img.shape[0] + r_min
        z = (np.arange(img.shape[1]) + 0.5) * (z_max - z_min) / img.shape[1] + z_min
        zc, rc = float(np.median(z[jj])), float(np.median(r[ii]))
        ins = ax.inset_axes([0.60, 0.08, 0.38, 0.55])
        ins.imshow(img, origin="lower", extent=(z_min, z_max, r_min, r_max), cmap=cmap, vmin=0, vmax=4, interpolation="nearest")
        ins.add_patch(Polygon(outline[:, ::-1], closed=True, fill=False, edgecolor="#1b2631", lw=0.8))
        ins.set_xlim(zc - 2.0, zc + 2.0)
        ins.set_ylim(rc - 1.2, rc + 1.2)
        ins.set_title("zoom", fontsize=7)
        ins.tick_params(labelsize=6)
        ax.indicate_inset_zoom(ins, edgecolor="#e01b24")
    ax.set_ylim(xlo, xhi)
    ax.set_xlim(zlo, zhi)
    ax.set_xlabel("z (mm)   - Z0 = finished front face")
    ax.set_ylabel("radius x (mm)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
