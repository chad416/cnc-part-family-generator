"""Post-processors (F05-F07): FANUC-style (explicit or canned cycles) and Sinumerik-style text."""

from __future__ import annotations

from . import __version__
from .motion import Move
from .parts import Part
from .planner import Op
from .tooling import Machine

DIALECTS = ("fanuc", "fanuc_cycles", "sinumerik")


def num(v: float) -> str:
    if abs(v) < 5e-4:
        v = 0.0
    return f"{v:.3f}"


def _dia(x: float) -> str:
    return num(2.0 * x)


class _Writer:
    def __init__(self, dialect: str, m: Machine):
        self.d = dialect
        self.m = m
        self.lines: list[str] = []
        self.feed: float | None = None

    @property
    def fanuc(self) -> bool:
        return self.d.startswith("fanuc")

    def comment(self, text: str) -> None:
        self.lines.append(f"({text.upper()})" if self.fanuc else f"; {text}")

    def emit(self, text: str) -> None:
        self.lines.append(text)

    def move(self, mv: Move, extra: str = "", label: int | None = None) -> None:
        n = f"N{label} " if label is not None else ""
        if mv.kind == "rapid":
            s = f"G0 X{_dia(mv.x)} Z{num(mv.z)}"
        elif mv.kind == "feed":
            s = f"G1 X{_dia(mv.x)} Z{num(mv.z)}"
        elif mv.kind == "arc":
            g = "G3" if mv.ccw else "G2"
            rad = f"R{num(mv.r)}" if self.fanuc else f"CR={num(mv.r)}"
            s = f"{g} X{_dia(mv.x)} Z{num(mv.z)} {rad}"
        elif mv.kind == "thread":
            s = f"G32 Z{num(mv.z)} F{num(mv.f)}" if self.fanuc else f"G33 Z{num(mv.z)} K{num(mv.f)}"
            self.feed = None
            self.lines.append(n + s + extra)
            return
        else:
            raise ValueError(mv.kind)
        if mv.kind in ("feed", "arc") and mv.f is not None and mv.f != self.feed:
            s += f" F{mv.f:g}"
            self.feed = mv.f
        self.lines.append(n + s + extra)


def _spindle(w: _Writer, sp: tuple) -> None:
    if sp[0] == "css":
        w.emit(f"G50 S{sp[2]:.0f}" if w.fanuc else f"LIMS={sp[2]:.0f}")
        w.emit(f"G96 S{sp[1]:.0f} M3")
    else:
        w.emit(f"G97 S{sp[1]:.0f} M3")


def _cycle(w: _Writer, op: Op) -> None:
    c = op.cycle
    if c["type"] == "G71":
        p, q = c["labels"]
        w.emit(f"G71 U{num(c['d'])} R{num(c['e'])}")
        w.emit(f"G71 P{p} Q{q} U{num(2 * c['u'])} W{num(c['w'])} F{c['f']:g}")
        w.feed = c["f"]
        contour = c["contour"]
        w.feed = None
        for i, mv in enumerate(contour):
            if i == 0:
                w.emit(f"N{p} G0 X{_dia(mv.x)}")
            elif i == len(contour) - 1:
                w.move(mv, label=q)
            else:
                w.move(mv)
    elif c["type"] == "G70":
        p, q = c["labels"]
        w.emit(f"G70 P{p} Q{q}")
    elif c["type"] == "G76":
        k_um = round(c["k"] * 1000)
        d1_um = round(c["d1"] * 1000)
        dmin_um = round(c["dmin"] * 1000)
        w.emit(f"G76 P{c['m']:02d}0060 Q{dmin_um} R{num(c['fin'])}")
        w.emit(f"G76 X{_dia(c['x_minor'])} Z{num(c['z_end'])} P{k_um} Q{d1_um} F{num(c['lead'])}")
    else:
        raise ValueError(c["type"])


def write_program(part: Part, ops: list[Op], dialect: str, m: Machine, prog_no: int = 1000) -> str:
    if dialect not in DIALECTS:
        raise ValueError(dialect)
    w = _Writer(dialect, m)
    tcx, tcz = m.tc_x, m.tc_z
    if w.fanuc:
        w.emit("%")
        w.emit(f"O{prog_no:04d} ({part.part_id} {part.family.upper().replace('_', ' ')})")
    else:
        w.comment(f"{part.part_id} {part.family.upper().replace('_', ' ')}")
    w.comment(f"CNCGEN {__version__} - SIMULATED OUTPUT - NOT PROVEN ON A MACHINE")
    w.comment(part.description)
    w.comment(f"STOCK D{part.stock_d:g} {part.material}")
    w.comment("TOOL REFERENCE = NOSE CENTRE (NO G41/G42), GROOVE = -Z CORNER, DRILL = TIP")
    w.emit("G21 G18 G40 G99 G97" if w.fanuc else "G18 G71 G90 G95 DIAMON G40 G97")
    prev_tool = None
    prev_sp = None
    for op in ops:
        w.comment(op.name)
        new_tool = op.tool.number != prev_tool
        if new_tool:
            if prev_tool is not None:
                w.emit(f"G0 X{_dia(tcx)} Z{num(tcz)} M9")
                w.emit("M5")
            w.emit(f"G0 X{_dia(tcx)} Z{num(tcz)}")
            n = op.tool.number
            if w.fanuc:
                w.emit(f"T{n:02d}{n:02d} ({op.tool.desc})")
            else:
                w.emit(f"T{n} D1 ; {op.tool.desc}")
        if new_tool or op.spindle != prev_sp:
            _spindle(w, op.spindle)
        w.feed = None
        for i, mv in enumerate(op.approach):
            w.move(mv, extra=" M8" if (i == 0 and new_tool) else "")
        if dialect == "fanuc_cycles" and op.cycle is not None:
            _cycle(w, op)
        else:
            for mv in op.body:
                w.move(mv)
        prev_tool = op.tool.number
        prev_sp = op.spindle
    w.emit(f"G0 X{_dia(tcx)} Z{num(tcz)} M9")
    w.emit("M5")
    w.emit("M30")
    if w.fanuc:
        w.emit("%")
    return "\n".join(w.lines) + "\n"
