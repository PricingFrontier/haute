# Re-verification of the engineering audit against HEAD

> **Point-in-time status evidence.** Component ownership and current queue state
> live in the [component improvement catalogue](../../roadmap/index.md).
> Re-check a finding against the present `HEAD` before implementation.

> Every one of the audit's **881 findings** was independently re-checked against the current
> code (branch `code-fixes`), which carries **145 commits of drift** since the audit base
> (`1b8eb150`, 2026-06-22). Each verdict cites current `file:line` evidence; 78 backend repros
> were re-run live. Read-only — no source was changed during verification.

## Verdict summary

| Verdict | Count | Meaning |
|---|---:|---|
| **STILL_VALID** | 864 | defect/opportunity present at HEAD |
| **FIXED** | 11 | deliberately fixed by drift work |
| **OBSOLETE** | 6 | code removed/rewritten; finding no longer applies |
| **total** | 881 | |

The drift was overwhelmingly *feature* work (P6/P7 git & version-control, multi-frame, path-grammar), 
so the audit holds almost entirely: **864/881 findings (98%) still stand.** 
Verification confidence was high — 78 repros re-executed and still demonstrate their defect; only 1 verdict is low-confidence.

## Actionable cut (still-valid, by the program's must-fix framework)

| Bucket | Count | Definition |
|---|---:|---|
| **MUST-FIX** | 63 | live-price / silent-wrongness / security / their guards (Waves 0–5 spine) |
| **SHOULD-FIX** | 292 | real but bounded — scheduled correctness/prevention |
| **TRACKED DEBT** | 509 | type-vocabulary, a11y, sim, low tail — opportunistic burn-down |

Must-fix severity mix: 4 critical · 51 high · 4 medium · 3 low · 1 sim.

---
## Already handled by drift (FIXED / OBSOLETE)

These 17 findings need **no work** — verified closed:

| id | verdict | sev | file | what happened |
|---|---|---|---|---|
| F406 | FIXED | low | `frontend/src/panels/editors/OutputEditor.tsx` | Preview rewritten to JsonPreview component using JSON.stringify, exactly the recommended fix. |
| F810 | FIXED | high | `frontend/src/utils/__tests__/sanitizeName.test` | Keyword-prefix rule added to sanitizeName.ts and mirrored by dedicated keyword-parity tests in sanitizeName.te |
| F197 | FIXED | medium | `frontend/src/utils/sanitizeName.ts` | Keyword-prefix guard added after the leading-digit prefix (line 82), matching backend _sanitize_func_name. |
| F298 | FIXED | low | `src/haute/_api_input_schema.py` | Fixed by the shared _jsonpath grammar rewrite; no silent-None leaf path remains. |
| F473 | FIXED | sim | `src/haute/_contracts.py` | Deduped onto _contracts.py in commit dfe580c2. |
| F074 | FIXED | low | `src/haute/_execute_lazy.py` | Removed in commit dfe580c2 'Remove confirmed dead code; dedup _builders.Contract onto _contracts'. |
| F685 | FIXED | low | `src/haute/_expression_parser.py` | Fixed by adopting scale-by-10**n with banker's rounding as recommended. |
| F007 | FIXED | high | `src/haute/_json_shred.py` | Fixed by the walk rewrite to _emit_at/_walk_array which no longer recurses into non-dict list elements. |
| F528 | FIXED | medium | `src/haute/routes/_optimiser_service.py` | Fixed via HTTPException(400) in _validate_config + ValueError->400 mapping in workers rather than a named Conf |
| F752 | FIXED | high | `tests/test_apiinput_multi_port_runtime.py` | Design diverged from finding's proposal: columns stays [] by design (line 536 asserts it) and per-port schema  |
| F794 | FIXED | high | `tests/test_deploy.py` | Fixed around commit 3ad2fbdc (Address PR23 review findings). The bundler silent-skip tests remain but the serv |
| F479 | OBSOLETE | sim | `frontend/src/panels/GitPanel.tsx` | Code the finding targeted was removed. |
| F336 | OBSOLETE | low | `frontend/src/panels/GitPanel.tsx` | Underlying submit/push feature removed. |
| F337 | OBSOLETE | low | `frontend/src/panels/GitPanel.tsx` | Component rewritten; empty-state scenario no longer applies. |
| F255 | OBSOLETE | low | `src/haute/_builders.py` | Rewritten in the outputMapping migration; contract changed from _passthrough_columns to _output_columns. |
| F723 | OBSOLETE | low | `src/haute/_cache.py` | No action: single sort via json.dumps(sort_keys=True); the claimed second sort in _canonicalise does not exist |
| F279 | OBSOLETE | low | `src/haute/_git.py` | Code rewritten; finding no longer applies. |

---
## MUST-FIX backlog by wave (Phase F execution order)

### Wave 0 — Criticals & near-free fail-loud quick wins — 25 items

| id | sev | file | finding |
|---|---|---|---|
| F573 | critical | `(other)` | mlflow |
| F597 | critical | `(other)` | vitest |
| F598 | critical | `(other)` | @vitest/coverage-v8 |
| F737 | critical | `src/haute/_sandbox.py` | RestrictedUnpickler allowlist admits real RCE gadgets via whole-package numpy entry â€” arbitra 🔴live |
| F574 | high | `(other)` | pyjwt |
| F575 | high | `(other)` | gitpython |
| F576 | high | `(other)` | pillow |
| F577 | high | `(other)` | starlette |
| F578 | high | `(other)` | python-multipart |
| F599 | high | `(other)` | vite |
| F600 | high | `(other)` | undici |
| F131 | high | `src/haute/cli/_init_cmd.py` | Dependency strings are re-emitted with naive double-quote wrapping; any dependency value contai |
| F056 | high | `src/haute/deploy/_schema.py` | infer_output_schema cache key (graph_fingerprint) excludes model-artifact bytes/version, so a s 🔴live |
| F055 | high | `src/haute/deploy/_validators.py` | Test-before-live gate scores test quotes WITHOUT bundled artifact_paths, so validate-time loads |
| F510 | high | `src/haute/pipeline.py` | run()/score() silently return the LAST topological node, not a declared output â€” fan-outs ret |
| F511 | high | `src/haute/pipeline.py` | Node.__call__ silently drops extra wired inputs (single-param node fed two edges uses only the  |
| F513 | high | `src/haute/pipeline.py` | `@pipeline.api_input` decorator does NOT mark a node as the live API input â€” you must also pa |
| F601 | moderate | `(other)` | postcss |
| F602 | moderate | `(other)` | js-yaml |
| F514 | medium | `src/haute/pipeline.py` | score() silently seeds EVERY source with the input df when no api_input is marked â€” a guess-f |
| F516 | medium | `src/haute/pipeline.py` | `@pipeline.instance` is a public decorator whose core semantics (instanceOf/inputMapping) the s |
| F469 | sim | `src/haute/pipeline.py` | run() and score() duplicate the non-source node execution block (input resolution + missing-inp |
| F059 | low | `src/haute/_sandbox.py` | safe_unpickle/safe_joblib_load allow the entire numpy/sklearn/scipy/pandas/joblib trees, so __r |
| F208 | low | `src/haute/_sandbox.py` | safe_joblib_load captures original_find_class OUTSIDE the lock, permanently leaking a restricte |
| F290 | low | `src/haute/_sandbox.py` | Allowlist entries ('builtins','True'), ('builtins','False'), ('builtins','None') are unreachabl |

### Wave 1 — Cache-spine integrity (json-shred / fingerprint / chunking) — 5 items

| id | sev | file | finding |
|---|---|---|---|
| F563 | high | `src/haute/_cache.py` | Fingerprint-COMPLETENESS is asserted ad hoc per cache, not as a single 'every output-affecting  |
| F132 | high | `src/haute/_json_shred.py` | A literal JSON key named "$value" collides with the reserved scalar-array sentinel, so inferenc 🔴live |
| F640 | high | `src/haute/_json_shred.py` | Header comment asserts a JSON key 'can't be $value ... so there's no collision' â€” false, and  🔴live |
| F015 | high | `src/haute/chunking.py` | Byte-budget chunk sizing estimates target row width from SOURCE schema only, so downstream-crea 🔴live |
| F713 | high | `src/haute/chunking.py` | Per-chunk collect_schema() storm: chunk-invariant node schemas are re-resolved O(nodes x chunks |

### Wave 2 — Codegen/executor equivalence (apply_*_from_config) — 8 items

| id | sev | file | finding |
|---|---|---|---|
| F000 | high | `src/haute/_codegen_builders.py` | Generated liveSwitch body hard-wires the 'live' input; standalone pipeline.run() (source=batch) 🔴live |
| F001 | high | `src/haute/_codegen_builders.py` | Generated optimiserApply body is a pure passthrough (return first); standalone pipeline.run() n 🔴live |
| F005 | high | `src/haute/_codegen_builders.py` | Passthrough-body node types (optimiser, modelling, scenarioExpander, optimiserApply) generate b 🔴live |
| F134 | high | `src/haute/_codegen_builders.py` | _gen_constant emits columns for empty/missing-name constant entries (default name 'col'/''), di |
| F558 | high | `src/haute/_registry.py` | No registry invariant that every stateful NodeType shares one apply_*_from_config helper betwee |
| F852 | high | `src/haute/_registry.py` | Passthrough-vs-stateful-apply is unencoded in NodeRegistryEntry; the two sides can disagree by  |
| F853 | high | `src/haute/_registry.py` | The 'single shared apply helper per stateful node' invariant exists only by convention; it shou |
| F743 | high | `src/haute/codegen.py` | Submodel name / file field code-injected into emitted pipeline.submodel("...") call via bare-qu |

### Wave 3 — Parser structure-conservation + expression numerical fidelity — 7 items

| id | sev | file | finding |
|---|---|---|---|
| F135 | high | `src/haute/_code_extraction.py` | _match_source silently drops the first user statement when a DataSource body has no recognized  🔴live |
| F679 | high | `src/haute/_expression_parser.py` | Integer arithmetic computed in unbounded Python int, not Int64 â€” evaluator shows a wildly dif |
| F680 | high | `src/haute/_expression_parser.py` | Boolean & / / do not implement Kleene three-valued logic: `False & null` and `True / null` retu |
| F703 | high | `src/haute/_expression_parser.py` | evaluate_expression re-parses the same code 2-3x per call and discards the AST + located target |
| F704 | high | `src/haute/_expression_parser.py` | Chain enrichment is O(N^2) in node width x chain depth: parse_expression_chain re-parses per ch |
| F027 | high | `src/haute/_parser_regex.py` | Regex fallback parser silently discards all submodels (and their nodes/edges) when the main fil |
| F137 | high | `src/haute/_parser_submodels.py` | Direct edge between children of two different submodels loses its boundary handle: dropped on f |

### Wave 4 — Rating-key & trace-correlation fidelity — 4 items

| id | sev | file | finding |
|---|---|---|---|
| F865 | high | `src/haute/_model_scorer.py` | Model flavour is stringly-typed and its valid set is duplicated across 4 independent sites |
| F866 | high | `src/haute/_model_scorer.py` | `task` is `str` across the scorer/algorithm surface even though a `Task` literal alias already  |
| F667 | high | `src/haute/_rating.py` | Float32 factor columns canonicalise non-dyadic decimals at f32 precision, so the SAME nominal v |
| F136 | high | `src/haute/_rating_step_config.py` | compact_rating_step_config_for_sidecar can emit a sidecar that expand_rating_step_config_from_s |

### Wave 5 — Frontend/backend contract + remaining verified highs — 14 items

| id | sev | file | finding |
|---|---|---|---|
| F139 | high | `frontend/src/panels/UtilityPanel.tsx` | Switching utility files discards (does not flush) the pending debounced save, losing the last e |
| F526 | high | `src/haute/_config_builder.py` | Generic 'Failed to load node config; check that the path exists and contains valid JSON' headli |
| F532 | high | `src/haute/_config_builder.py` | Sidecar-required error never names the actual config folder and omits how to fix it |
| F133 | high | `src/haute/_scaffold.py` | Azure DevOps scaffold emits invalid YAML: production-deploy env: secrets are under-indented, br |
| F539 | high | `src/haute/_types.py` | Node-config keys mix camelCase and snake_case with no rule -- the same concept (output column)  |
| F138 | high | `src/haute/deploy/_scorer.py` | Misconfigured modelScore node deploys and serves as a SILENT passthrough â€” the 'no model arti |
| F525 | high | `src/haute/errors.py` | errors.py docstring promises 'catch the whole family with a single except', but ~15 domain exce |
| F533 | high | `src/haute/pipeline.py` | Decorator docstrings advertise inline kwargs that the parser rejects with ConfigError |
| F225 | high | `src/haute/projection.py` | Unordered demand walk omits filter keyword-constraint columns, under-demanding the parent for d |
| F140 | high | `src/haute/routes/_explore_service.py` | Binary column with non-UTF-8 bytes crashes the entire Explore materialisation via a strict cast |
| F540 | high | `src/haute/routes/_optimiser_service.py` | The optimiser's central output value has ~6 different names across the stack, including the act |
| F141 | high | `src/haute/routes/_supersession.py` | Semaphore double-release (permit leak) when limiter acquire and supersession complete in the sa |
| F738 | high | `src/haute/routes/pipeline.py` | Route input-path guard omits modelScore feature_contract_path / artifact_path, so even guarded  |
| F541 | high | `src/haute/schemas.py` | `test_rows`/`test_mb` actually carry the VALIDATION-set counts -- colliding with the train/vali |

---
## SHOULD-FIX (scheduled, by wave)

| wave | count |
|---|---:|
| Wave 1 — Cache-spine integrity (json-shred / fingerprint / chunking) | 33 |
| Wave 2 — Codegen/executor equivalence (apply_*_from_config) | 16 |
| Wave 3 — Parser structure-conservation + expression numerical fidelity | 46 |
| Wave 4 — Rating-key & trace-correlation fidelity | 39 |
| Wave 5 — Frontend/backend contract + remaining verified highs | 10 |
| Wave 5 (docs) — Documentation truth pass | 9 |
| W6-prevention | 21 |
| W6-tests | 22 |
| WT | 96 |

Full per-finding list in `status.json` (filter `bucket=="should"`).

---
## TRACKED DEBT (opportunistic)

| class | count |
|---|---:|
| medium/low long tail | 276 |
| type-safety / perf tail | 71 |
| simplifications | 70 |
| test-debt | 47 |
| frontend a11y | 24 |
| docs overstatements | 21 |

Per the program's cut line: fix opportunistically when touching the file; never a blocking wave. Full list in `status.json`.
