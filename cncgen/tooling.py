"""Tool library and machine data (JSON)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tool:
    number: int
    kind: str  # turn | bore | groove_ext | groove_int | thread | drill
    desc: str
    vc: float
    f: float = 0.0
    rn: float = 0.0
    doc: float = 0.0
    width: float = 0.0
    d: float = 0.0
    min_bore: float = 0.0
    max_depth: float = 0.0
    n_max: float = 0.0


@dataclass(frozen=True)
class Machine:
    n_max: float
    rapid_x: float  # mm/min (radial slide)
    rapid_z: float
    tool_change_s: float
    tc_x: float  # radius
    tc_z: float
    jaw_height: float
    materials: dict
    planner: dict


def load_tools(path: Path) -> dict[int, Tool]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tools = {}
    for t in data["tools"]:
        tool = Tool(**t)
        if tool.number in tools:
            raise ValueError(f"duplicate tool number {tool.number}")
        tools[tool.number] = tool
    return tools


def load_machine(path: Path) -> Machine:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    return Machine(
        n_max=m["n_max"],
        rapid_x=m["rapid_x_mm_min"],
        rapid_z=m["rapid_z_mm_min"],
        tool_change_s=m["tool_change_s"],
        tc_x=m["tool_change_x"],
        tc_z=m["tool_change_z"],
        jaw_height=m["jaw_height"],
        materials=m["materials"],
        planner=m["planner"],
    )


def rpm(vc: float, d: float, n_max: float) -> float:
    """Spindle speed for cutting speed vc (m/min) at diameter d (mm), clamped."""
    if d <= 1e-9:
        return n_max
    return min(1000.0 * vc / (math.pi * d), n_max)
