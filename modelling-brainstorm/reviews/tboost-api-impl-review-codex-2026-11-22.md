1. **P2 — Stored pruning evidence is not fully validated against its settings.**  
   [_checkpoint.py:402](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:402) checks `main_effects`, but never checks the schedule against `steps` or OOB availability against `min_rows`. I reproduced checksum-valid archives that loaded successfully after changing:
   - `steps` to 2 while retaining `[0, 1, 2, 3, 4]`, instead of the actual `[0, 1, 4]`;
   - `min_rows` to 100000 despite only 778 OOB rows.

   Both subsequently accepted pruning. Validate these relationships before installing cache entries, including entries without an applied stage, and add corruption tests.

2. **P2 — Prepared candidates discard the live estimators intended to seed results.**  
   [_paths.py:311](/home/ralph/suite/t-boost/python/t_boost/_paths.py:311) and [_paths.py:336](/home/ralph/suite/t-boost/python/t_boost/_paths.py:336) construct candidates without passing `model`. First model access therefore rebuilds every prepared pruning, banding, and smoothing candidate. This also affects `session.apply(Smooth(..., strength=2))`: its returned result rebuilds the estimator the operation just produced. I confirmed the extra `from_json` calls. Transfer these estimators through the existing seed/pop mechanism and test prepared candidates as well as restored ones.

3. **P2 — The promised public operation decoders are missing.**  
   [_stage_types.py:171](/home/ralph/suite/t-boost/python/t_boost/_stage_types.py:171) supplies decoding only through an internal-module helper. `Prune`, `Band`, and `Smooth` have `to_plain_data()` but no `from_plain_data()`, contrary to the approved design. Clients cannot round-trip standalone operations through the specified public API. Add the class methods, delegating to shared validation, with JSON round-trip tests.

4. **P3 — The spec still documents removed methods and dictionary metadata.**  
   [staged-training.md:171](/home/ralph/suite/t-boost/specs/roadmap/staged-training.md:171) advertises `model(stage)`, `prune`, `band`, and `smooth`, and describes `stages` as JSON-safe metadata. Replace this paragraph with `stage(...).model`, typed `apply(...)`, and `StageInfo`.

I found no weakened numerical assertions in the ported tests. Focused in-memory probes passed for eager inference validation with recomputed revisions, restored-candidate seed isolation, result lifetime, and recipe interruption at every operation. Chain loading follows the required order; its validation completeness is the issue above.

No files modified. I did not rerun the reported full suite or oracle.

REQUEST_CHANGES