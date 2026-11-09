# Requirement → evidence

| ID | Requirement (short) | Evidence | Status |
|---|---|---|---|
| F01 | Part from one CSV row, 3 families | `test_families_load`; `cncgen/parts.py` builders | met |
| F02 | Reject rows that cannot be made | `test_validation_rejects` (7 cases), `test_profile_step_shorter_than_nose_rejected` | met |
| F03 | Plan face, rough, finish, drill, bore, grooves, thread, part-off | `cncgen/planner.py`; `test_offset_path_is_exactly_one_nose_radius_away`; backplots in `docs/img/` | met |
| F04 | Tool library and material factor | `data/tools.json`, `data/machine.json`; spindle words in `examples/programs/` (e.g. `G96 S170` = 200 × 0.85 for 42CrMo4) | met |
| F05 | FANUC-style explicit output | `examples/programs/fanuc/` | met |
| F06 | FANUC-style canned cycles G71/G70/G76 | `examples/programs/fanuc_cycles/` | met |
| F07 | Sinumerik-style output | `examples/programs/sinumerik/` | met |
| F08 | Cycle time with CSS, clamp, rapids, tool changes | `test_cycle_time_by_hand`, `test_constant_surface_speed_is_clamped`; `examples/summary.md` | met |
| F09 | Backplot PNG with verification result | `docs/img/RE-40_backplot.png`, `PI-63_backplot.png`, `GL-40_backplot.png`, `fault_gouge.png` | met |
| V01 | Read both dialects, expand cycles | `test_reader_arcs_fanuc_and_sinumerik_agree`, `test_outputs_produce_the_same_motion` | met |
| V02 | Gouge check | `test_all_examples_pass_every_check`; fault 5 in `docs/fault_injection.md` | met |
| V03 | Leftover check | `test_all_examples_pass_every_check`; faults 6 and 13 | met |
| V04 | Rapid into material | fault 7 | met |
| V05 | Chuck jaws | fault 8 | met |
| V06 | Spindle rules (clamp, G97 for thread/drill, spindle on, known tool) | faults 1, 2, 3, 4, 11, 12 | met |
| V07 | Thread depth, relief, run-in, lead | `test_iso_thread_geometry`, `test_thread_is_iso_depth_and_ends_in_relief`; faults 9, 10 | met (run-in rule L15 has no injected fault) |
| V08 | All 27 example programs pass | `test_all_examples_pass_every_check`; `examples/summary.md` | met |
| V09 | Every injected fault caught by its rule | `test_fault_injection_all_caught`; `docs/fault_injection.md` (13/13) | met |
| V10 | Same motion from all 3 outputs (≤ 0.002 mm) | `test_outputs_produce_the_same_motion` | met |
| N01 | Python + numpy + matplotlib, CI regenerates examples | `.github/workflows/ci.yml` (`build --check`) | met when CI is green |
| N02 | Limits stated | README "Limits" | met |
