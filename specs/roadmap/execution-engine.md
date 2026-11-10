# Execution engine roadmap

## Scope

Running a graph, as [the execution engine
specification](../execution-engine/high-level.md) describes it: which source a
run reads, how a node's settings become the frame it produces, and how a
failure before any node runs is reported. The packages below come from the
9 November 2026 codebase review. `EXE-01` and `EXE-02` were reproduced against
`HEAD` on that day by calling the named functions; `EXE-03` is read from the
source and was not run. `EXE-04` was reproduced on 10 November 2026 through
the editor's preview route, `haute run` and the deploy scorer.

`EXE-01` also changes what the optimiser runs on, so it is read together with
[the optimiser specification](../optimiser/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXE-01 | Planned | P2 | A pipeline with more than one non-live source can be optimised and written from, on the source the analyst chose. |
| EXE-02 | Planned | P2 | A Constant's value runs as the type its settings file holds: one rule decides what reads as a number. |
| EXE-03 | Planned | P3 | A preamble failure is reported at the preamble's own line, wherever the project folder is. |
| EXE-04 | Planned | P2 | A join that previews in the editor runs from the command line and in a deployment, whatever Polars options it carries. |

## Planned improvements

### EXE-01 — A second non-live source no longer stops the optimiser and a write
**Why:** Reproduced. A Data Output written while the toolbar's source is
`live`, and every optimiser solve, run under "the" non-live source, found by
scanning the Source Switch maps. As soon as the maps name two different
non-live sources, in two switches or in one, the scan raises a bare
`ValueError`: "Conflicting batch scenarios across live_switch nodes: 'batch'
vs 'backtest'. All live_switch nodes must use the same non-live scenario
name." Nothing else has that rule: the settings check accepts any names, the
editor lets the analyst add any number of sources and offers them all on a
switch, and preview, trace and a write under a named source all work. With
the example switch mapped to `live`, `batch` and `backtest`, a write under
`batch` or `backtest` resolved, and a write under `live` and the optimiser
raised. The optimiser's solve request carries no source at all, so with two
non-live sources it cannot run, and with one the analyst cannot see which
source it used. The message names an internal node type and a rule the
product never states. How the route answers the bare `ValueError` was not
traced.

**Plan:** The optimiser and a Data Output write take the source the analyst
chose: the toolbar's active source when it is not `live`, carried on the
request as preview and trace carry it. Under `live` with exactly one non-live
source, keep today's behaviour. Under `live` with several, refuse with a
message that names the sources and says to choose one in the toolbar. Remove
the scan's "same name" rule, or state it in the settings check where the
analyst meets it.

**Acceptance:** On a pipeline whose switch maps `live`, `batch` and
`backtest`: a solve started under `backtest` runs on the backtest input and
records `backtest` as its data source; a write under `live` is refused naming
`batch` and `backtest`; no path raises the "Conflicting batch scenarios"
error.

**Dependencies:** A decision on the source a solve uses when the toolbar is
on `live` and the pipeline has one non-live source (today: that source).

**Evidence:** `src/haute/executor.py::_resolve_batch_scenario`;
`src/haute/executor.py::_data_output_scenario`;
`src/haute/routes/_optimiser_service.py::_resolve_batch_scenario` (imported at
its three call sites); `src/haute/_node_config_models.py::_check_live_switch`
accepts any source names; `frontend/src/stores/useSettingsStore.ts::addSource`;
`frontend/src/panels/editors/LiveSwitchEditor.tsx::setMapping`.

### EXE-02 — A Constant's value runs as the type its settings file holds
**Why:** Reproduced. The Constant node builds its frame with `float(value)`
and keeps the raw value only when that raises. So text that Python's `float`
accepts becomes a number whatever it was meant as: `"0123"` runs as `123.0`,
`"1E5"` as `100000.0`, `"1_000"` as `1000.0`, `"nan"` as NaN, `"Infinity"` as
infinity, `True` as `1.0`, and a whole number `5` as the float `5.0`. The
node's own settings codec answers the same question strictly: it writes a
number only for a canonical integer or a finite float whose `repr` is the
typed text, so `"0123"` and `"1E5"` are kept as text on disk and then run as
numbers. A branch code with a leading zero silently becomes a different
value, and `nan` or `inf` puts a non-finite number into every row the
constant joins.

**Plan:** One function decides what reads as a number, shared by the codec
and the run: canonical finite decimal text is a number, a whole number stays
an integer, a boolean stays a boolean, and everything else is text. A
non-finite value is refused by the settings check, naming the constant.

**Acceptance:** A Constant holding `"0123"`, `"1E5"`, `True`, `5` and `2.5`
produces columns of String, String, Boolean, Int64 and Float64 with those
values, in preview, in a run and in the deployed scorer; `"nan"` is refused
before the node runs. The codec's round-trip tests and the run's tests read
their cases from one table.

**Dependencies:** None. Haute has no users, so existing settings files need
no migration.

**Evidence:** `src/haute/_node_apply.py::constant_frame`;
`src/haute/_constant_config.py::_as_written`;
`src/haute/_node_config_models.py::ConstantValue`.

### EXE-03 — A preamble failure is attributed by the utility package, not a path substring
**Why:** Read from the source, not run. When a preamble fails, the executor
walks the traceback from the innermost frame and reports the first frame
whose file name contains the text `utility` as "Error in <file> line N". A
project folder whose path contains `utility` (and so its `.venv`) makes every
library frame match, so a preamble failure inside Polars is reported as an
error in a site-packages file instead of the preamble line. The fallback
takes the innermost frame named `<string>`, which code generated by a
library (a dataclass's `__init__`) also uses, so the line can be the
library's.

**Plan:** Compare each frame's file with the directory of the utility
package the project bound, and take the outermost `<string>` frame, which is
the preamble's own.

**Acceptance:** In a project under a folder named `utility-pricing`, a
preamble line that raises inside a library is reported as that preamble
line. A failure inside a real utility module is still reported at the
module's line.

**Dependencies:** None.

**Evidence:** `src/haute/executor.py::_exec_preamble_namespace`;
`src/haute/_sandbox.py::compile_project_code` compiles the preamble as
`<string>`.

### EXE-04 — A join option the planner does not read does not stop a run
**Why:** Reproduced. A Polars node whose join carries `coalesce=True` or
`nulls_equal=True`, both ordinary Polars options, previews in the editor and
cannot be run anywhere else. With a two-row request and a two-row lookup,
the editor's preview route answered the node's two rows. `haute run` on the
same file exited 1: "Materialisation of 'join' could not be admitted for
this execution ... remediation=Provide readable source/schema metadata so
Haute can estimate the full 'join' materialisation boundary before
execution. Estimator reported:
joined:row_cardinality_unavailable:joined:unsupported_join_option". A deploy
to the default target refused it too, in words that call the join "the
group-by at node 'joined' (operator 'join')". For a container target the
dry run answered "Validation passed", and scoring one quote through the
deploy scorer was then refused. The specification prescribes the refusal
where a row count cannot be bounded and the surface has no hard memory cap;
the editor's worker has one, which is why the preview runs. The defect is
what counts as unbounded. The planner's reading of a join accepts seven
keywords and fails on any other, so `coalesce`, which cannot change how many
rows a join returns, makes the row count "unavailable". The remedy the
message gives cannot help: no metadata makes the option readable, and the
cause appears only as the code at the message's end.

**Plan:** Where the planner reads a join for its row count alone, accept the
options that cannot change the count: `coalesce` and the two parallelism
switches. Bound `nulls_equal` as a join that also matches null keys, or
refuse it by name. Where a join is still refused, say which option the
planner cannot read and what works instead: dropping it, or running on a
surface with a memory cap. Make the deploy refusal name the operator it is
about. Keep the column-lineage reading strict, since `coalesce` does change
the columns.

**Acceptance:** `haute run` on a pipeline whose join carries `coalesce=True`
returns the rows the editor previews, for a left and for a full join. A join
carrying an option the planner cannot bound is refused in one message naming
the node, the option and the two remedies, by `haute run`, by the deploy and
by scoring. The deploy refusal of a join does not say "group-by". A test
holds one pipeline to the same outcome in the editor's preview, `haute run`
and the deploy scorer for each join option Polars accepts.

**Dependencies:** None.

**Evidence:** `src/haute/_column_lineage.py::_parse_join` fails on any
keyword it does not list;
`src/haute/execution.py::_plan_unproven_materialisation` raises the refusal;
`src/haute/errors.py::GroupByExecutionUnsupportedError`;
`src/haute/deploy/_schema.py::infer_deploy_execution_policy` words the
deploy refusal; `tests/test_column_lineage.py` holds the parse failure for
an unknown option.
