# WS-06 — Pipeline config, expression parsing & reference pipeline

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-06 · Status: delivered in PR #135.

**Branch:** `opus5/ws-06-config-parsing-reference`

## Mission

How a pipeline is defined, resolved and parsed: the `Pipeline` builder and decorator surface,
`haute.toml`/discovery resolution, the AST + regex-fallback parser and expression evaluator,
and the checked-in `rating/` reference pipeline that doubles as executable documentation.
The evaluator's silent identity-fallback is the review's flagship fail-loud violation in this
area.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| pipeline-config | 0 | 3 | 4 | 8 |
| expression-parsing | 0 | 3 | 5 | 4 |
| reference-pipeline | 0 | 0 | 3 | 5 |
| cross-cutting (assigned) | 0 | 0 | 0 | 1 |
| **Total** | **0** | **6** | **12** | **18** |

## Priorities

**P1 — fabricated / silent results (review Wave 3, but high-value):**

- `expression-parsing-1` (H): evaluator returns the receiver's value for any unsupported
  method (`floor`, `ceil`, `exp`, …) — confident wrong trace output. Replace the trailing
  identity fallback with an explicit unsupported result (`None`) or raise.
- `expression-parsing-2` (H): regex fallback drops pipeline name/description when `haute` is
  imported under an alias; the next save overwrites the user's authored name. Make
  `_recover_pipeline_meta` alias-aware or raise `ParseError`.
- `expression-parsing-3` (H): control-flow poisoning misses `select()` and `except*` — a
  branch-dependent column gets a confident wrong formula.
- `pipeline-config-3` (H): zero-parameter `@pipeline.instance` executes its stub silently
  instead of raising `ExecutionError`.

**P1 — resolver correctness:**

- `pipeline-config-1` (H): three different pipeline resolvers; `haute deploy`/GUI can bind a
  different file than `haute run`, and a broken `[project].pipeline` is loud on one surface
  and silent on another. Unify on `_project.resolve_pipeline_file` or document all three with
  a `> NOTE:`. The `deploy/_config.py` edit is in WS-14's file — coordinate the delegation
  hunk (distinct from WS-14's `[server]` schema hunk).

**P2 — bugs:** `expression-parsing-4/-5` (bare `SyntaxError`/`ConfigError` vs documented
`ParseError`), `expression-parsing-6/-7/-8` (chain vs evaluate divergence, live observed-value
fallback, missing `"window"` enum value), `expression-parsing-9` (path-escaping submodel ref
bare `ValueError` — coordinate the `_submodel_paths.py` fix with WS-05), `pipeline-config-7`
(malformed `haute.toml` silently drops tier 1), `pipeline-config-8` (apiInput columns writable
but stripped on read), `pipeline-config-10` (wrong folder in docstring example),
`pipeline-config-6` (kwonly params do become edges).

**P3 — spec truth and consolidation:** decorator-list drift `data_source`/`data_sink`
(`pipeline-config-2`, `contracts-a-5`, `seam-io-9`, `pipeline-config-9`),
`over-complication-8` (`Pipeline.to_graph()` is a second graph builder with different
node-type and edge inference and zero production callers — delete it or make it delegate to
`_graph_builders.py`), fold shipped contract
(`pipeline-config-4`), ownership rows for `_builders.py`/`_registry.py`/`_contracts.py`/
`_node_builder.py` (`pipeline-config-5`, coordinate the ownership decision with WS-03's
`execution-engine-12`), dead helper (`pipeline-config-11`), test counts
(`pipeline-config-12`, `testing-credibility-11` cross-cutting — pipeline-config half here,
modelling half noted to WS-07). reference-pipeline: API-input return-type drift
(`reference-pipeline-2`), stale codegen output (`reference-pipeline-3`, `reference-pipeline-9`),
removed sidecar format shown as valid (`reference-pipeline-4`), layout/marker and haute.toml
omissions (`reference-pipeline-11`, `-13`, `-14`, `-6`).

## Finding inventory

High (6): `expression-parsing-1`, `expression-parsing-2`, `expression-parsing-3`,
`pipeline-config-1`, `pipeline-config-3`, `contracts-a-5`.
Medium (12): `expression-parsing-4`, `expression-parsing-5`, `expression-parsing-6`,
`expression-parsing-7`, `expression-parsing-8`, `pipeline-config-2`, `pipeline-config-9`,
`over-complication-8`, `seam-io-9`, `reference-pipeline-2`, `reference-pipeline-3`,
`reference-pipeline-9`.
Low (18): `expression-parsing-9`, `expression-parsing-11`, `expression-parsing-12`,
`expression-parsing-13`, `pipeline-config-4`, `pipeline-config-5`, `pipeline-config-6`,
`pipeline-config-7`, `pipeline-config-8`, `pipeline-config-10`, `pipeline-config-11`,
`pipeline-config-12`, `reference-pipeline-4`, `reference-pipeline-6`,
`reference-pipeline-11`, `reference-pipeline-13`, `reference-pipeline-14`,
`testing-credibility-11`.

## File ownership (exclusive)

- `src/haute/pipeline.py`, `_types.py` (decorator/node-type maps), `_project.py`,
  `discovery.py`, `_config_builder.py`, `_config_validation.py`,
  `_expression_parser.py`, `_parser_regex.py`
- `src/haute/_builders.py`, `_registry.py`, `_contracts.py`, `_node_builder.py` **ownership
  decision** (module-map row + `ownership.toml` entry) — coordinate with WS-03/WS-04, which
  only read these files
- `rating/**` (reference pipeline assets)
- `docs/specs/pipeline-config/**`, `docs/specs/expression-parsing/**`,
  `docs/specs/reference-pipeline/**`
- Their tests (`tests/test_pipeline.py`, `test_expression_parser*.py`, `test_parser_*.py`,
  `test_config_validation.py`, reference-pipeline contract tests)

## Cross-stream touchpoints

- `_submodel_paths.py` / `parser.py` are WS-05's files — `expression-parsing-9` and the
  submodel-path `ValueError` fix must be one coordinated typed-error change.
- `deploy/_config.py` resolver delegation (`pipeline-config-1`) is WS-14's file — coordinate.
- `_builders.py` is read by WS-04 (OUTPUT) and WS-08 (optimiser apply runtime); settle its
  documented owner here and append the `ownership.toml` entry.
- `_types.py` node-type enum is also referenced by frontend guards (WS-09) — no shared edit,
  but keep the 19-type count consistent in specs.

## Definition of done

- Evaluator no longer fabricates values; alias-aware meta recovery; zero-param instance and
  branch cases raise/mark-opaque — all with regression tests.
- One documented resolver story; `haute run`/`haute deploy`/GUI agree or the divergence is
  specced with a `> NOTE:`.
- Decorator lists and node-type counts consistent across pipeline-config docs and code;
  reference-pipeline specs match the checked-in `rating/` tree.
- `_builders.py` et al. have a documented owner and ledger entry.
- Baseline entries deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_expression_parser.py tests/test_pipeline.py tests/test_config_validation.py -q`
- `uv run pytest tests/test_output_nest_example_contract.py -q` (reference sidecars)
- `uv run pytest tests/test_docs_accuracy.py -q`; quick preflight near completion.
