# Coverage baseline — Phase 0

- **Date:** 2026-06-19
- **Tree audited:** `origin/main` (byte-identical to `wave-2-cache-integrity` @ `1b8eb150`; local `main` is stale and was **not** used)
- **Command:** `pytest tests/ -n 4 --cov=src/haute --cov-branch` (default markers, `-m 'not perf'`)

## Suite health
- **11,087 passed**, 40 skipped, 3 xfailed, **14 warnings**, 0 failed — 285s (~4m45s), exit 0.
- The 14 warnings pass the `filterwarnings = error::RuntimeWarning` gate (so they are non-RuntimeWarning), but are worth a look in the test-quality pass.

## Coverage totals
| Metric | Value |
|---|---|
| Statements | **91.75%** (30,795 total, 1,908 missing) |
| Branches | **~92%** (11,224 total, 1,559 missing) |
| Global gate | `fail_under = 90` + per-file `[tool.haute.critical_coverage]` |

## Unexercised-code leads (top files by missing lines + branches)
> Low coverage is a **lead, not a verdict** — cross-reference against the risk heat-map in Phase 2. A high-risk file that is also under-exercised is a priority for the test-quality + mutation pass.

| Missing | Stmt % | Missing lines | Missing branches | File |
|--:|--:|--:|--:|---|
| 352 | 85.9% | 218 | 134 | `routes/_optimiser_service.py` |
| 270 | 87.9% | 130 | 140 | `_expression_parser.py` |
| 251 | 85.7% | 128 | 123 | `projection.py` |
| 213 | 82.0% | 122 | 91 | `_trace_enrichment.py` |
| 175 | 82.3% | 99 | 76 | `_builders.py` |
| 137 | 83.9% | 73 | 64 | `chunking.py` |
| 78 | 92.4% | 44 | 34 | `_execute_lazy.py` |
| 75 | 79.6% | 39 | 36 | `_rating_step_config.py` |
| 69 | **69.5%** | 45 | 24 | `_worker_isolation.py` (lowest) |
| 68 | 88.8% | 44 | 24 | `_execution_context.py` |
| 64 | 82.0% | 33 | 31 | `_optimiser_apply_explainability.py` |
| 63 | 83.3% | 37 | 26 | `execution.py` |
| 62 | 90.0% | 32 | 30 | `_code_extraction.py` |
| 61 | 84.4% | 35 | 26 | `cli/_init_cmd.py` |
| 60 | 81.1% | 32 | 28 | `_model_explainability.py` |
| 56 | 91.2% | 28 | 28 | `routes/_train_service.py` |
| 54 | 93.8% | 28 | 26 | `executor.py` |
| 48 | 78.8% | 25 | 23 | `_banding_config.py` |
| 47 | 94.2% | 23 | 24 | `_json_shred.py` |
| 47 | 87.6% | 26 | 21 | `_dataframe_execution_cache.py` |

Artifact source: `.cache/coverage/audit-backend.json`, `.cache/coverage/audit-pytest.log`.
