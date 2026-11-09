# SPEC: cnc-part-family-generator

Rev A, 9 Nov 2026.

## 1. Purpose

A job shop that makes hydraulic cylinders to order turns many small batches of similar parts: piston-rod ends, pistons and glands (rod guides). Each variant differs only in its sizes, yet each one normally gets a hand-written CNC program.

This project turns **one row of a parameter table into a complete 2-axis lathe program**. Then it **proves the program in simulation** before any machine sees it.

Everything is simulated. No real machine, control or part was used. All sizes in `data/*.csv` are illustrative. They are not taken from a seal maker's or a cylinder maker's catalogue.

## 2. Scope

| In | Out |
|---|---|
| 2-axis turning (X, Z), main spindle, one set-up (op 1) | Back-side op 2 (back chamfers), live tooling, Y axis, sub-spindle |
| Facing, OD rough and finish, drilling, ID rough and finish, OD and ID grooves, external metric thread, part-off | Internal threads, knurling, tapers other than chamfers, arcs in the part profile |
| FANUC-style (explicit moves, or G71/G70/G76 canned cycles) and Sinumerik-style (explicit moves) output | Machine-specific post-processors, G41/G42 nose-radius compensation |
| Material-removal check on an axisymmetric half-section | Cutting forces, chatter, chip control, tool wear |

## 3. Conventions

- Internal coordinates are `(x, z)`, with **x = radius** and z = axial. Z0 is the finished front face, and the part lies at z < 0. Programs use **diameter X** (FANUC diameter mode, Sinumerik `DIAMON`).
- Programmed point:
  - turning and boring tools: the **nose-radius centre** (FANUC tip 0/9 style), so no G41/G42 is needed;
  - grooving tools: the chuck-side (−Z) corner of the cutting edge;
  - drills: the point on the axis;
  - threading tools: the tip.
- Arcs are viewed with Z to the right and X up. In that view `G2` is clockwise. The radius is given as `R` (FANUC) or `CR=` (Sinumerik).
- Feeds are per revolution (FANUC `G99`, Sinumerik `G95`). Units are mm.

## 4. Requirements

### Function
| ID | Requirement |
|---|---|
| F01 | Build a part from one CSV row for the 3 families `rod_end`, `piston` and `gland` (`data/*.csv`). |
| F02 | Reject a row that cannot be made with the tool library, with a message. Cases: groove narrower than the grooving tool, thread chamfer smaller than the thread depth, relief groove not below the minor diameter, no drill or boring bar that fits, a groove that is not on a cylindrical surface, a profile step smaller than the nose radius. |
| F03 | Plan the operations in order: face, OD rough, OD finish, drill, ID rough, ID finish, ID grooves, OD grooves, thread, part-off (whichever the family needs). |
| F04 | Take tools and cutting data from `data/tools.json` (Vc, f, depth of cut, nose radius, width). Scale Vc by a material factor from `data/machine.json`. |
| F05 | Write a FANUC-style program with explicit G0/G1/G2/G3/G32 moves. |
| F06 | Write a FANUC-style program that uses the canned cycles G71 (rough), G70 (finish) and G76 (thread). |
| F07 | Write a Sinumerik-style program with explicit moves (G0/G1/G2/G3 with `CR=`, G33 threads, `LIMS=`). |
| F08 | Estimate the cycle time for each program. Account for the constant-surface-speed spindle (rpm limited by G50/LIMS), rapid rates and tool changes. |
| F09 | Draw a backplot PNG of each program with the verification result. |

### Verification (the program is checked by reading it back, not by trusting the planner)
| ID | Requirement |
|---|---|
| V01 | Interpret both dialects from the program text. G71/G70/G76 are expanded with the cycle model in §5. |
| V02 | **Gouge:** no tool may remove part material deeper than 0.01 mm. |
| V03 | **Leftover:** no material more than 0.01 mm outside the finished part may remain in the checked zone. The thread flanks are excluded. |
| V04 | **Rapid into material:** a G0 move must not touch remaining material. |
| V05 | **Chuck:** no move may enter the chuck jaws. |
| V06 | **Spindle rules:** G96 needs an active speed clamp (G50 S / LIMS). Threading and drilling run at constant rpm (G97). No feed move with the spindle stopped. Every T number must be in the tool library. |
| V07 | **Thread:** the final pass is at the ISO 68-1 depth h3 = 0.61343·P. The thread ends inside a relief groove. The run-in is ≥ 2·P. The lead equals the pitch. |
| V08 | All example programs (3 families × 3 sizes × 3 output styles) pass V02–V07 with 0 errors. |
| V09 | Fault injection: each injected fault is caught by the rule meant for it. |
| V10 | The canned-cycle program and the explicit program produce the same motion, within 0.001 mm. |

### Non-functional
| ID | Requirement |
|---|---|
| N01 | Pure Python 3.12 with numpy and matplotlib. CI runs the tests and regenerates the example programs; any difference fails the build. |
| N02 | Limits are stated in the README: not proven on a machine, generic cutting data, and a cycle model that may differ from a real control. |

## 5. Cycle model (used by the planner and by the verifier)

**G71 (type I, FANUC two-block form).**
- The contour is the blocks P..Q. The first block is an X-only move.
- The contour is shifted by (U/2, W). Passes are made at depth U(d) from the start point. Each pass ends where it meets the shifted contour, then retracts at 45° by R(e) and returns at rapid.
- After the last pass, one pass follows the shifted contour. The tool then returns to the start point.
- Real controls differ in details, such as the final contour pass and how the retract is done. Check against the control's manual.

**G70.** Runs blocks P..Q from the start point, then returns to the start point at rapid.

**G76.** First block P(m)(r)(a) Q(dmin) R(finish allowance). Second block X(minor diameter) Z(end) P(thread height, µm) Q(first depth, µm) F(lead).
- Depth of pass n is Δd·√n, with each step at least dmin, down to h − R. Then m passes at full depth h.
- **The model uses radial infeed.** A real G76 uses flank infeed at a/2, which only moves the start Z of each pass inside the run-in.

## 6. Acceptance
`python -m pytest` passes. `python -m cncgen build --check` reproduces `examples/` exactly. `python -m cncgen inject` reports every fault as detected.
