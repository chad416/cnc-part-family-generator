# Changelog

## 0.1.0 (9 Nov 2026)
- Part families: piston-rod end, piston and gland, built from CSV rows with validation.
- Planner: face, G71-model roughing, nose-radius-offset finishing, drilling, boring, OD and ID grooves, ISO metric thread, part-off.
- Output: FANUC-style (explicit or G71/G70/G76) and Sinumerik-style.
- Read-back verifier: material-removal simulation (gouge, leftover, rapid, chuck) plus spindle and thread rules.
- Cycle-time estimate, backplots, 13 fault injections, 23 tests, CI.
