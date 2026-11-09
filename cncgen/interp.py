"""G-code reader (V01) for the FANUC-style and Sinumerik-style subsets this project writes.

The verifier never sees the planner's moves. It rebuilds the motion from the program text, so a
program edited by hand (or broken on purpose by `inject`) is checked the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import cycles
from .motion import Move
from .tooling import Machine, Tool

WORD = re.compile(r"([A-Z]+)\s*=?\s*([-+]?(?:\d+\.?\d*|\.\d+))")


@dataclass
class Block:
    line: int
    raw: str
    label: int | None
    g: list[float]
    m: list[int]
    w: dict[str, float]


@dataclass
class SimMove:
    kind: str  # rapid | feed | arc | thread
    tool: int | None
    x0: float
    z0: float
    x1: float
    z1: float
    f: float | None
    ccw: bool
    r: float
    css: bool
    s: float
    n_max: float | None
    spindle_on: bool
    line: int


@dataclass
class Issue:
    rule: str
    line: int
    msg: str
    severity: str = "error"


@dataclass
class Program:
    moves: list[SimMove] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    tool_changes: int = 0
    ended: bool = False


def parse(text: str, dialect: str) -> list[Block]:
    fanuc = dialect.startswith("fanuc")
    blocks = []
    for i, raw in enumerate(text.splitlines(), start=1):
        s = raw.upper()
        s = re.sub(r"\([^)]*\)", "", s) if fanuc else s.split(";", 1)[0]
        s = s.strip()
        if not s or s == "%":
            continue
        label = None
        g: list[float] = []
        m: list[int] = []
        w: dict[str, float] = {}
        for key, val in WORD.findall(s):
            v = float(val)
            if key == "N":
                label = int(v)
            elif key == "G":
                g.append(v)
            elif key == "M":
                m.append(int(v))
            else:
                w[key] = v
        blocks.append(Block(i, raw, label, g, m, w))
    return blocks


class _State:
    def __init__(self, m: Machine):
        self.x = m.tc_x
        self.z = m.tc_z
        self.tool: int | None = None
        self.css = False
        self.s = 0.0
        self.n_max: float | None = None
        self.on = False
        self.motion: float | None = None
        self.f: float | None = None
        self.per_rev = False


def run(text: str, dialect: str, tools: dict[int, Tool], m: Machine) -> Program:
    fanuc = dialect.startswith("fanuc")
    blocks = parse(text, dialect)
    by_label = {b.label: i for i, b in enumerate(blocks) if b.label is not None}
    st = _State(m)
    prog = Program()
    g71_first: dict | None = None
    g76_first: dict | None = None

    def issue(rule: str, b: Block, msg: str, sev: str = "error") -> None:
        prog.issues.append(Issue(rule, b.line, msg, sev))

    def add(mv: Move, b: Block) -> None:
        kind = mv.kind
        x0, z0 = st.x, st.z
        if kind in ("feed", "arc", "thread"):
            if not st.on:
                issue("L12", b, "feed move with the spindle stopped")
            if kind != "thread" and (mv.f is None or mv.f <= 0):
                issue("L8", b, "feed move without a feed rate")
            if kind != "thread" and not st.per_rev:
                issue("L8", b, "feed per revolution (G99/G95) not active")
            tool = tools.get(st.tool) if st.tool is not None else None
            if kind == "thread" and st.css:
                issue("L2", b, "threading under constant surface speed (G96); use G97")
            if tool is not None and tool.kind == "drill" and st.css:
                issue("L7", b, "drilling under constant surface speed (G96); use G97")
        prog.moves.append(
            SimMove(kind, st.tool, x0, z0, mv.x, mv.z, mv.f, mv.ccw, mv.r, st.css, st.s, st.n_max, st.on, b.line)
        )
        st.x, st.z = mv.x, mv.z

    def block_move(b: Block, motion: float | None, pos: tuple[float, float], feed: float | None) -> Move | None:
        if "X" not in b.w and "Z" not in b.w:
            return None
        x = b.w["X"] / 2.0 if "X" in b.w else pos[0]
        z = b.w["Z"] if "Z" in b.w else pos[1]
        if motion == 0:
            return Move("rapid", x, z)
        if motion == 1:
            return Move("feed", x, z, f=b.w.get("F", feed))
        if motion in (2, 3):
            r = b.w.get("R") if fanuc else b.w.get("CR")
            if r is None:
                raise ValueError(f"line {b.line}: arc without radius")
            return Move("arc", x, z, f=b.w.get("F", feed), ccw=(motion == 3), r=r)
        if (fanuc and motion == 32) or (not fanuc and motion == 33):
            lead = b.w.get("F") if fanuc else b.w.get("K")
            return Move("thread", x, z, f=lead)
        raise ValueError(f"line {b.line}: unsupported motion G{motion:g}")

    def contour(i0: int, i1: int, start: tuple[float, float]) -> list[Move]:
        out = []
        pos = start
        motion = None
        f = st.f
        for b in blocks[i0 : i1 + 1]:
            for gv in b.g:
                if gv in (0, 1, 2, 3):
                    motion = gv
            if "F" in b.w:
                f = b.w["F"]
            mv = block_move(b, motion, pos, f)
            if mv is None:
                continue
            out.append(mv)
            pos = (mv.x, mv.z)
        return out

    i = 0
    while i < len(blocks):
        b = blocks[i]
        i += 1
        w = b.w
        if fanuc and "T" in w:
            tno = int(w["T"]) // 100
        elif not fanuc and "T" in w:
            tno = int(w["T"])
        else:
            tno = None
        if tno is not None:
            if tno not in tools:
                issue("L10", b, f"tool T{tno} is not in the tool library")
            st.tool = tno
            prog.tool_changes += 1
        if "LIMS" in w and not fanuc:
            st.n_max = w["LIMS"]
        handled_cycle = False
        for gv in b.g:
            if gv in (0, 1, 2, 3, 32, 33):
                st.motion = gv
            elif gv == 96:
                st.css = True
            elif gv == 97:
                st.css = False
            elif (fanuc and gv == 99) or (not fanuc and gv == 95):
                st.per_rev = True
            elif (fanuc and gv == 98) or (not fanuc and gv == 94):
                st.per_rev = False
            elif fanuc and gv == 50:
                if "S" in w:
                    st.n_max = w["S"]
                handled_cycle = True
            elif fanuc and gv == 71:
                if "P" in w:
                    p, q = int(w["P"]), int(w["Q"])
                    if g71_first is None:
                        raise ValueError(f"line {b.line}: G71 second block without first block")
                    cont = contour(by_label[p], by_label[q], (st.x, st.z))
                    u = w.get("U", 0.0) / 2.0
                    side = "int" if u < 0 else "ext"
                    f = w.get("F", st.f)
                    st.f = f
                    for mv in cycles.g71((st.x, st.z), cont, g71_first["d"], g71_first["e"], u, w.get("W", 0.0), f, side):
                        add(mv, b)
                    i = by_label[q] + 1
                else:
                    g71_first = {"d": w["U"], "e": w.get("R", 0.0)}
                handled_cycle = True
            elif fanuc and gv == 70:
                p, q = int(w["P"]), int(w["Q"])
                cont = contour(by_label[p], by_label[q], (st.x, st.z))
                for mv in cycles.g70((st.x, st.z), cont):
                    add(mv, b)
                handled_cycle = True
            elif fanuc and gv == 76:
                if "X" in w:
                    if g76_first is None:
                        raise ValueError(f"line {b.line}: G76 second block without first block")
                    k = w["P"] / 1000.0
                    d1 = w["Q"] / 1000.0
                    x_minor = w["X"] / 2.0
                    for mv in cycles.g76((st.x, st.z), x_minor, w["Z"], k, d1, g76_first["dmin"], g76_first["fin"], g76_first["m"], w["F"]):
                        add(mv, b)
                else:
                    pw = int(w.get("P", 10060))
                    g76_first = {"m": pw // 10000, "dmin": w.get("Q", 0.0) / 1000.0, "fin": w.get("R", 0.0)}
                handled_cycle = True
            elif not fanuc and gv in (70, 71):
                if gv == 70:
                    issue("L8", b, "inch mode (G70) not supported")
            elif gv in (18, 21, 40, 90):
                pass
            else:
                issue("L11", b, f"G{gv:g} is not supported by the verifier", "warning")
        if "S" in w and not (fanuc and 50 in b.g):
            st.s = w["S"]
        if st.css and st.n_max is None and ("S" in w or 96 in b.g):
            issue("L1", b, "G96 without a spindle speed clamp (G50 S / LIMS)")
        for mv_code in b.m:
            if mv_code in (3, 4):
                st.on = True
            elif mv_code == 5:
                st.on = False
            elif mv_code in (30, 2):
                prog.ended = True
        if "F" in w and not handled_cycle and st.motion not in (32,):
            st.f = w["F"]
        if handled_cycle:
            continue
        mv = block_move(b, st.motion, (st.x, st.z), st.f)
        if mv is not None:
            add(mv, b)
        if prog.ended:
            break
    if not prog.ended:
        prog.issues.append(Issue("L9", blocks[-1].line if blocks else 0, "program does not end with M30"))
    if st.on:
        prog.issues.append(Issue("L9", blocks[-1].line if blocks else 0, "spindle still running at program end", "warning"))
    return prog
