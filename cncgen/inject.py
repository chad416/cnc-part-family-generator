"""Fault injection (V09): break a correct program on purpose and check that the verifier catches it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .interp import run
from .parts import load_all
from .planner import plan
from .post import write_program
from .tooling import load_machine, load_tools
from .verify import verify


def _section(lines: list[str], name: str, fanuc: bool = True) -> tuple[int, int]:
    tag = f"({name})" if fanuc else f"; {name}"
    start = lines.index(tag)
    end = start + 1
    while end < len(lines) and not (lines[end].startswith("(") or lines[end].startswith("; ")):
        end += 1
    return start, end


def _replace_first(text: str, pattern: str, repl: str, after: str | None = None) -> str:
    lines = text.splitlines()
    i0 = lines.index(after) if after else 0
    for i in range(i0, len(lines)):
        if re.search(pattern, lines[i]):
            lines[i] = re.sub(pattern, repl, lines[i], count=1)
            return "\n".join(lines) + "\n"
    raise ValueError(f"pattern {pattern} not found")


def m_no_clamp(t: str) -> str:
    return "\n".join(line for line in t.splitlines() if not line.startswith("G50 S")) + "\n"


def m_no_lims(t: str) -> str:
    return "\n".join(line for line in t.splitlines() if not line.startswith("LIMS=")) + "\n"


def m_css_thread(t: str) -> str:
    return _replace_first(t, r"^G97 S\d+ M3$", "G96 S100 M3", after="(THREAD)")


def m_css_drill(t: str) -> str:
    return _replace_first(t, r"^G97 S\d+ M3$", "G96 S150 M3", after="(DRILL)")


def m_finish_deep(t: str) -> str:
    lines = t.splitlines()
    s, e = _section(lines, "OD FINISH")
    for i in range(s, e):
        mt = re.match(r"^G1 X(-?[\d.]+) Z(-?[\d.]+)", lines[i])
        if mt and float(mt.group(2)) < -5.0:
            x = float(mt.group(1)) - 0.4
            lines[i] = re.sub(r"X-?[\d.]+", f"X{x:.3f}", lines[i], count=1)
            return "\n".join(lines) + "\n"
    raise ValueError("no finishing line found")


def m_skip_g70(t: str) -> str:
    return "\n".join(line for line in t.splitlines() if not line.startswith("G70 P100")) + "\n"


def m_rapid_cut(t: str) -> str:
    return _replace_first(t, r"^G1 ", "G0 ", after="(OD ROUGH)")


def m_partoff_chuck(t: str) -> str:
    lines = t.splitlines()
    s, e = _section(lines, "PART-OFF")
    zs = {re.search(r"Z(-?[\d.]+)", lines[i]).group(1) for i in range(s, e) if re.search(r"Z(-?[\d.]+)", lines[i])}
    z = sorted(zs, key=float)[0]
    znew = f"{float(z) - 12.0:.3f}"
    for i in range(s, e):
        lines[i] = lines[i].replace(f"Z{z}", f"Z{znew}")
    return "\n".join(lines) + "\n"


def m_thread_shallow(t: str) -> str:
    # G76 ends each thread at X (the minor diameter); P only sets where the first pass starts
    mt = re.search(r"G76 X(-?[\d.]+) Z", t)
    x = float(mt.group(1)) + 0.4
    return t.replace(f"G76 X{mt.group(1)} Z", f"G76 X{x:.3f} Z", 1)


def m_thread_long(t: str) -> str:
    return _replace_first(t, r"(G76 X[-\d.]+ )Z[-\d.]+", r"\g<1>Z-31.000")


def m_unknown_tool(t: str) -> str:
    return _replace_first(t, r"^T0202", "T1111")


def m_spindle_off(t: str) -> str:
    # the tool change before finishing stops the spindle (M5); drop the M3 that restarts it
    return _replace_first(t, r"^G96 S(\d+) M3$", r"G96 S\1", after="(OD FINISH)")


def m_no_groove_finish(t: str) -> str:
    lines = t.splitlines()
    s, e = _section(lines, "OD GROOVES")
    # drop the finishing bottom traverse of the first groove: the 2nd consecutive G1 in the section
    run_len = 0
    for i in range(s, e):
        if lines[i].startswith("G1 "):
            run_len += 1
            if run_len == 2:
                del lines[i]
                return "\n".join(lines) + "\n"
        else:
            run_len = 0
    raise ValueError("no groove finish found")


@dataclass
class Fault:
    name: str
    part: str
    dialect: str
    fn: Callable[[str], str]
    expect: str
    what: str


FAULTS = [
    Fault("no spindle clamp", "RE-40", "fanuc", m_no_clamp, "L1", "all G50 S lines removed"),
    Fault("no LIMS", "PI-63", "sinumerik", m_no_lims, "L1", "all LIMS= lines removed"),
    Fault("CSS while threading", "RE-40", "fanuc_cycles", m_css_thread, "L2", "G97 before G76 changed to G96"),
    Fault("CSS while drilling", "PI-63", "fanuc", m_css_drill, "L7", "G97 before drilling changed to G96"),
    Fault("finish pass 0.2 mm deep", "PI-63", "fanuc", m_finish_deep, "L5", "one OD finishing X reduced by 0.4 (diameter)"),
    Fault("finishing skipped", "GL-40", "fanuc_cycles", m_skip_g70, "L6", "G70 P100 Q200 removed"),
    Fault("rapid instead of feed", "PI-80", "fanuc", m_rapid_cut, "L3", "first roughing G1 changed to G0"),
    Fault("part-off into the chuck", "PI-63", "fanuc", m_partoff_chuck, "L4", "part-off Z moved 12 mm towards the chuck"),
    Fault("thread too shallow", "RE-40", "fanuc_cycles", m_thread_shallow, "L13", "G76 minor diameter X +0.4 (diameter)"),
    Fault("thread past the relief", "RE-40", "fanuc_cycles", m_thread_long, "L14", "G76 end Z moved past the relief groove"),
    Fault("unknown tool", "RE-25", "fanuc", m_unknown_tool, "L10", "T0202 -> T1111"),
    Fault("feed with spindle stopped", "RE-25", "fanuc", m_spindle_off, "L12", "M3 after the finishing tool change removed"),
    Fault("groove bottom not finished", "PI-40", "fanuc", m_no_groove_finish, "L6", "bottom traverse of the first groove removed"),
]


def run_all(data: Path, out: Path, grid: float, png: Path | None) -> int:
    tools = load_tools(data / "tools.json")
    m = load_machine(data / "machine.json")
    parts = {p.part_id: p for p in load_all(data, tools, m)}
    rows = []
    missed = 0
    for f in FAULTS:
        part = parts[f.part]
        good = write_program(part, plan(part, tools, m), f.dialect, m)
        bad = f.fn(good)
        if bad == good:
            raise ValueError(f"fault '{f.name}' did not change the program")
        prog = run(bad, "sinumerik" if f.dialect == "sinumerik" else "fanuc", tools, m)
        res = verify(part, prog, tools, m, h=grid)
        found = sorted({i.rule for i in res.errors})
        ok = f.expect in found
        missed += 0 if ok else 1
        rows.append((f, found, ok))
        if png is not None and f.name == "finish pass 0.2 mm deep":
            from .plot import backplot

            backplot(part, prog, res, tools, png, title=f"FAULT INJECTED: {f.what} -> caught as {', '.join(found)}")
    lines = [
        "# Fault injection results",
        "",
        "Generated by `python -m cncgen inject`. Each row takes a program that passes every check, breaks it",
        "in one way, reads it back and runs the verifier. The fault must be reported by the rule meant for it.",
        "",
        "| # | Fault | Part / output | Change made | Expected rule | Rules reported | Caught |",
        "|---:|---|---|---|---|---|---|",
    ]
    for n, (f, found, ok) in enumerate(rows, 1):
        lines.append(f"| {n} | {f.name} | {f.part} / {f.dialect} | {f.what} | {f.expect} | {', '.join(found) or '-'} | {'yes' if ok else '**NO**'} |")
    lines.append("")
    lines.append(f"{len(rows) - missed} of {len(rows)} faults caught by the expected rule.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 1 if missed else 0
