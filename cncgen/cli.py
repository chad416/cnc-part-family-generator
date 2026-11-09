"""Command line: build all example programs, verify one program, or run the fault injections."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .interp import run
from .parts import load_all
from .planner import plan
from .post import DIALECTS, write_program
from .timing import cycle_time
from .tooling import load_machine, load_tools
from .verify import verify

EXT = {"fanuc": ".nc", "fanuc_cycles": ".nc", "sinumerik": ".mpf"}
PLOT_IDS = ("RE-40", "PI-63", "GL-40")


def _load(data: Path):
    tools = load_tools(data / "tools.json")
    m = load_machine(data / "machine.json")
    return tools, m, load_all(data, tools, m)


def build(args) -> int:
    data, out = Path(args.data), Path(args.out)
    tools, m, parts = _load(data)
    rows = []
    changed = []
    failed = []
    for no, part in enumerate(parts):
        ops = plan(part, tools, m)
        for d in DIALECTS:
            text = write_program(part, ops, d, m, prog_no=1000 + no)
            path = out / "programs" / d / f"{part.part_id}{EXT[d]}"
            if args.check:
                old = path.read_text(encoding="utf-8") if path.exists() else None
                if old != text:
                    changed.append(str(path))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            prog = run(text, d, tools, m)
            res = verify(part, prog, tools, m, h=args.grid)
            tm = cycle_time(prog, m)
            errs = res.errors
            warns = [i for i in res.issues if i.severity != "error"]
            rows.append(
                {
                    "part": part.part_id,
                    "family": part.family,
                    "dialect": d,
                    "lines": len(text.splitlines()),
                    "tool_changes": prog.tool_changes,
                    "cycle_time_s": round(tm.total_s, 1),
                    "cutting_s": round(tm.cutting_s, 1),
                    "rapid_s": round(tm.rapid_s, 1),
                    "gouge_cells": res.gouge_cells,
                    "leftover_cells": res.leftover_cells,
                    "rapid_hits": res.rapid_hits,
                    "jaw_hits": res.jaw_hits,
                    "errors": len(errs),
                    "warnings": len(warns),
                }
            )
            if errs:
                failed.append((part.part_id, d, errs))
            if not args.check and args.plots and d == "fanuc_cycles" and part.part_id in PLOT_IDS:
                from .plot import backplot

                backplot(part, prog, res, tools, Path(args.plots) / f"{part.part_id}_backplot.png",
                         title=f"{part.part_id}  {part.description}  -  {tm.total_s:.0f} s, {len(errs)} errors")
    summary_csv = out / "summary.csv"
    summary_md = out / "summary.md"
    md = _summary_md(rows)
    if args.check:
        for path, new in ((summary_md, md),):
            if not path.exists() or path.read_text(encoding="utf-8") != new:
                changed.append(str(path))
    else:
        out.mkdir(parents=True, exist_ok=True)
        with summary_csv.open("w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
            wr.writeheader()
            wr.writerows(rows)
        summary_md.write_text(md, encoding="utf-8", newline="\n")
    print(md)
    for pid, d, errs in failed:
        print(f"FAIL {pid} {d}:")
        for e in errs[:10]:
            print(f"   line {e.line} {e.rule}: {e.msg}")
    if changed:
        print("Generated output differs from the committed files:")
        for c in changed:
            print("   " + c)
    return 1 if (failed or changed) else 0


def _summary_md(rows: list[dict]) -> str:
    lines = [
        "| Part | Family | Output | Lines | Tool changes | Cycle time (s) | Gouge | Leftover | Rapid hits | Jaw hits | Errors |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['part']} | {r['family']} | {r['dialect']} | {r['lines']} | {r['tool_changes']} | "
            f"{r['cycle_time_s']:.1f} | {r['gouge_cells']} | {r['leftover_cells']} | {r['rapid_hits']} | "
            f"{r['jaw_hits']} | {r['errors']} |"
        )
    return "\n".join(lines) + "\n"


def verify_cmd(args) -> int:
    data = Path(args.data)
    tools, m, parts = _load(data)
    part = {p.part_id: p for p in parts}.get(args.id)
    if part is None:
        print(f"unknown part id {args.id}")
        return 2
    text = Path(args.file).read_text(encoding="utf-8")
    prog = run(text, args.dialect, tools, m)
    res = verify(part, prog, tools, m, h=args.grid)
    tm = cycle_time(prog, m)
    report = {
        "file": args.file,
        "part": part.part_id,
        "dialect": args.dialect,
        "cycle_time_s": round(tm.total_s, 1),
        "gouge_cells": res.gouge_cells,
        "gouge_max_mm": round(res.gouge_max, 4),
        "leftover_cells": res.leftover_cells,
        "leftover_max_mm": round(res.leftover_max, 4),
        "issues": [{"rule": i.rule, "line": i.line, "severity": i.severity, "msg": i.msg} for i in res.issues],
    }
    print(json.dumps(report, indent=2))
    if args.png:
        from .plot import backplot

        backplot(part, prog, res, tools, Path(args.png), title=f"{part.part_id} {Path(args.file).name}")
    return 1 if res.errors else 0


def inject_cmd(args) -> int:
    from .inject import run_all

    return run_all(Path(args.data), Path(args.out), args.grid, Path(args.png) if args.png else None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cncgen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate and verify all example programs")
    b.add_argument("--data", default="data")
    b.add_argument("--out", default="examples")
    b.add_argument("--plots", default="docs/img")
    b.add_argument("--grid", type=float, default=0.05)
    b.add_argument("--check", action="store_true", help="fail if the output differs from the committed files")
    b.set_defaults(fn=build)
    v = sub.add_parser("verify", help="verify one program against a part definition")
    v.add_argument("file")
    v.add_argument("--dialect", choices=["fanuc", "sinumerik"], required=True)
    v.add_argument("--id", required=True)
    v.add_argument("--data", default="data")
    v.add_argument("--grid", type=float, default=0.05)
    v.add_argument("--png")
    v.set_defaults(fn=verify_cmd)
    i = sub.add_parser("inject", help="break example programs on purpose and check that each fault is caught")
    i.add_argument("--data", default="data")
    i.add_argument("--out", default="docs/fault_injection.md")
    i.add_argument("--grid", type=float, default=0.05)
    i.add_argument("--png", default="docs/img/fault_gouge.png")
    i.set_defaults(fn=inject_cmd)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
