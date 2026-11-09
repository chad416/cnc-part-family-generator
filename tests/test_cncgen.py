"""Tests. Requirement IDs refer to docs/SPEC.md and docs/traceability.md."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from cncgen import cycles
from cncgen.geometry import Arc, arc_centre, arc_points, offset_profile
from cncgen.interp import run
from cncgen.parts import Thread, build_part, load_all
from cncgen.planner import plan
from cncgen.post import DIALECTS, write_program
from cncgen.timing import cycle_time, move_time
from cncgen.tooling import load_machine, load_tools
from cncgen.verify import verify

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture(scope="module")
def env():
    tools = load_tools(DATA / "tools.json")
    m = load_machine(DATA / "machine.json")
    parts = {p.part_id: p for p in load_all(DATA, tools, m)}
    return tools, m, parts


def _row(env, family, pid):
    return copy.deepcopy(env[2][pid].params)


# ---------------------------------------------------------------- F01 / F02
def test_families_load(env):
    """F01: 3 families x 3 sizes from the CSV files."""
    fams = sorted({p.family for p in env[2].values()})
    assert fams == ["gland", "piston", "rod_end"]
    assert len(env[2]) == 9


@pytest.mark.parametrize(
    "family,pid,key,value,msg",
    [
        ("piston", "PI-63", "guide_w", "2.5", "width"),  # narrower than the 3 mm grooving tool
        ("rod_end", "RE-40", "chamfer", "1.0", "chamfer"),  # chamfer < thread depth 1.227
        ("rod_end", "RE-40", "spigot_d", "26", "spigot"),  # spigot not larger than thread
        ("piston", "PI-63", "inner_d", "15", "drill"),  # no drill between bar minimum 16 and 15-2
        ("gland", "GL-40", "wiper_z", "1.5", "closer"),  # groove touching the bore chamfer
        ("gland", "GL-40", "seal_bottom_d", "56", "depth"),  # deeper than the ID grooving tool allows
        ("piston", "PI-63", "material", "Inconel", "material"),
    ],
)
def test_validation_rejects(env, family, pid, key, value, msg):
    """F02: rows that cannot be made are rejected with a message."""
    tools, m, _ = env
    row = _row(env, family, pid)
    row[key] = value
    with pytest.raises(ValueError, match=msg):
        build_part(family, row, tools, m)


def test_profile_step_shorter_than_nose_rejected():
    """F02: a convex/concave step smaller than the nose cannot be followed."""
    prof = [(10.0, 0.0), (10.0, -5.0), (10.3, -5.0), (10.3, -5.2), (12.0, -5.2), (12.0, -10.0)]
    with pytest.raises(ValueError, match="too short"):
        offset_profile(prof, 0.8, "ext")


# ---------------------------------------------------------------- geometry
def _dist_to_poly(p, poly):
    best = math.inf
    for a, b in zip(poly, poly[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dz * dz
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz))
    return best


@pytest.mark.parametrize("pid", ["RE-25", "RE-56", "PI-80", "GL-40"])
def test_offset_path_is_exactly_one_nose_radius_away(env, pid):
    """F03: every point of the finishing path is rn from the profile (no gouge, no gap)."""
    part = env[2][pid]
    prof = [(part.od[0][0] - 1.0, 0.0)] + list(part.od) + [(part.stock_r + 1.0, part.od[-1][1])]
    for rn in (0.4, 0.8):
        for e in offset_profile(prof, rn, "ext"):
            pts = arc_points(e.p0, e.p1, e.c, e.ccw) if isinstance(e, Arc) else [e.p0, e.p1]
            for p in pts:
                assert _dist_to_poly(p, prof) == pytest.approx(rn, abs=1e-9)


def test_arc_centre_matches_both_directions():
    c = arc_centre((1.0, 0.0), (0.0, 1.0), 1.0, ccw=False)
    assert c == pytest.approx((0.0, 0.0), abs=1e-12)
    c = arc_centre((1.0, 0.0), (0.0, 1.0), 1.0, ccw=True)
    assert c == pytest.approx((1.0, 1.0), abs=1e-12)


def test_iso_thread_geometry():
    """V07: ISO 68-1 external thread: h3 = 0.61343 P, d3 = d - 1.22687 P."""
    th = Thread(27.0, 2.0, 26.85, -27.5)
    assert th.depth == pytest.approx(1.22686, abs=1e-9)
    assert th.minor_d == pytest.approx(24.54626, abs=1e-9)


def test_thread_depth_sequence():
    """SPEC section 5: d1*sqrt(n), step >= dmin, then the finish allowance and spring passes."""
    d = cycles.thread_depths(1.227, 0.4, 0.05, 0.02, 1)
    assert d[-1] == pytest.approx(1.227)
    assert d[-2] == pytest.approx(1.207)
    assert all(b >= a for a, b in zip(d, d[1:]))
    assert all(b - a >= 0.05 - 1e-12 for a, b in zip(d[:-3], d[1:-2]))


# ---------------------------------------------------------------- V01 reader
def test_reader_arcs_fanuc_and_sinumerik_agree(env):
    tools, m, _ = env
    fan = "G21 G18 G40 G99 G97\nT0202\nG50 S3000\nG96 S200 M3\nG0 X20. Z2.\nG1 Z0. F0.1\nG3 X22. Z-1. R1.\nM5\nM30\n"
    sin = "G18 G71 G90 G95 DIAMON G40 G97\nT2 D1\nLIMS=3000\nG96 S200 M3\nG0 X20 Z2\nG1 Z0 F0.1\nG3 X22 Z-1 CR=1\nM5\nM30\n"
    a = run(fan, "fanuc", tools, m)
    b = run(sin, "sinumerik", tools, m)
    assert not a.issues and not b.issues
    assert [(x.kind, x.x1, x.z1, x.ccw, x.r) for x in a.moves] == [(x.kind, x.x1, x.z1, x.ccw, x.r) for x in b.moves]
    arc = a.moves[-1]
    assert arc_centre((arc.x0, arc.z0), (arc.x1, arc.z1), arc.r, arc.ccw) == pytest.approx((10.0, -1.0), abs=1e-12)


# ---------------------------------------------------------------- F08 timing
def test_cycle_time_by_hand(env):
    """F08: 50 mm at 0.2 mm/rev and 1000 rpm = 15 s; rapids and one tool change on top."""
    tools, m, _ = env
    text = "G21 G18 G40 G99 G97\nT0101\nG97 S1000 M3\nG0 X40. Z2.\nG1 Z-48. F0.2\nG0 X200. Z100.\nM5\nM30\n"
    tm = cycle_time(run(text, "fanuc", tools, m), m)
    assert tm.cutting_s == pytest.approx(15.0, rel=1e-9)
    rap = 60 * max(80 / m.rapid_x, 98 / m.rapid_z) + 60 * max(80 / m.rapid_x, 148 / m.rapid_z)
    assert tm.rapid_s == pytest.approx(rap, rel=1e-9)
    assert tm.tool_change_s == pytest.approx(m.tool_change_s)


def test_constant_surface_speed_is_clamped(env):
    """F08: G96 rpm = 1000*Vc/(pi*D) until the G50 clamp."""
    tools, m, _ = env
    text = "G21 G18 G40 G99 G97\nT0101\nG50 S1000\nG96 S200 M3\nG0 X80. Z0.\nG1 X60. F0.2\nG1 X20.\nM5\nM30\n"
    prog = run(text, "fanuc", tools, m)
    face1, face2 = prog.moves[-2], prog.moves[-1]
    # 80 -> 60 mm: rpm 796..1061 -> partly clamped; 60 -> 20 mm: clamped at 1000 rpm all the way
    assert move_time(face2, m) == pytest.approx(60 * 20 / (0.2 * 1000), rel=1e-9)
    t1 = move_time(face1, m)
    assert 60 * 10 / (0.2 * 1000) < t1 < 60 * 10 / (0.2 * 795.7)


# ---------------------------------------------------------------- V02-V08, V10
@pytest.fixture(scope="module")
def results(env):
    tools, m, parts = env
    out = {}
    for pid, part in parts.items():
        ops = plan(part, tools, m)
        for d in DIALECTS:
            text = write_program(part, ops, d, m)
            prog = run(text, "sinumerik" if d == "sinumerik" else "fanuc", tools, m)
            res = verify(part, prog, tools, m)
            # keep the counts, drop the grids (about 20 MB per program)
            res.material = res.sd = res.check = res.band = res.gouge_mask = None
            out[(pid, d)] = (prog, res)
    return out


def test_all_examples_pass_every_check(results):
    """V08: 9 parts x 3 outputs, 0 errors, 0 gouge, 0 leftover, 0 rapid or jaw hits."""
    assert len(results) == 27
    for key, (prog, res) in results.items():
        assert res.errors == [], (key, res.errors[:3])
        assert res.gouge_cells == 0 and res.leftover_cells == 0, key
        assert res.rapid_hits == 0 and res.jaw_hits == 0, key


def test_outputs_produce_the_same_motion(results, env):
    """V10: canned cycles, explicit FANUC and Sinumerik give the same motion (<= 0.002 mm)."""
    for pid in env[2]:
        a = results[(pid, "fanuc")][0].moves
        for d in ("fanuc_cycles", "sinumerik"):
            b = results[(pid, d)][0].moves
            assert len(a) == len(b), (pid, d)
            for x, y in zip(a, b):
                assert (x.kind, x.tool, x.f, x.css, x.s) == (y.kind, y.tool, y.f, y.css, y.s), (pid, d, x.line, y.line)
                assert abs(x.x1 - y.x1) <= 0.002 and abs(x.z1 - y.z1) <= 0.002, (pid, d, x.line, y.line)


def test_thread_is_iso_depth_and_ends_in_relief(results, env):
    """V07: the final pass reaches h3 and every pass ends inside the relief groove."""
    for pid in ("RE-25", "RE-40", "RE-56"):
        part = env[2][pid]
        prog = results[(pid, "fanuc_cycles")][0]
        passes = [mv for mv in prog.moves if mv.kind == "thread"]
        assert min(mv.x1 for mv in passes) == pytest.approx(part.thread.major_d / 2 - part.thread.depth, abs=0.001)
        relief = [g for g in part.grooves if g.name == "relief"][0]
        assert all(relief.z_left < mv.z1 < relief.z_right for mv in passes)
        assert all(mv.f == part.thread.pitch for mv in passes)
        assert all(not mv.css for mv in passes)


# ---------------------------------------------------------------- V09
def test_fault_injection_all_caught(tmp_path):
    """V09: every injected fault is reported by its rule."""
    from cncgen.inject import run_all

    assert run_all(DATA, tmp_path / "fi.md", 0.05, None) == 0
    text = (tmp_path / "fi.md").read_text(encoding="utf-8")
    assert "**NO**" not in text
