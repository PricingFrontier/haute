# Execution engine roadmap

## Scope

Running a graph, as [the execution engine
specification](../execution-engine/high-level.md) describes it: which source a
run reads, how a node's settings become the frame it produces, and how a
failure before any node runs is reported. The packages below come from the
9 November 2026 codebase review. `EXE-01` and `EXE-02` were reproduced against
`HEAD` on that day by calling the named functions; `EXE-03` is read from the
source and was not run. `EXE-04` and `EXE-05` were reproduced on 10 November
2026 through the editor's preview route, `haute run` and the deploy. `EXE-06`
comes from the review plan's phase 5, a reader from a second model family
(Codex) over the code changed since the 31 October audit, and was reproduced
on 13 November by running one pipeline file three ways. `EXE-07` and
`EXE-08` come from the same reader; a worker built each run and the main
session ran it again on 13 November.

`EXE-01` also changes what the optimiser runs on, so it is read together with
[the optimiser specification](../optimiser/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXE-01 | Planned | P2 | A pipeline with more than one non-live source can be optimised and written from, on the source the analyst chose. |
| EXE-02 | Planned | P2 | A Constant's value runs as the type its settings file holds: one rule decides what reads as a number. |
| EXE-03 | Planned | P3 | A preamble failure is reported at the preamble's own line, wherever the project folder is. |
| EXE-04 | Planned | P2 | A join that previews in the editor runs from the command line and in a deployment, whatever Polars options it carries. |
| EXE-05 | Planned | P2 | The Constant page's own example, a Transform that cross-joins a Constant, runs from the command line and deploys. |
| EXE-06 | Planned | P2 | A pipeline run as Python, by `pipeline.run()` or `pipeline.score()`, applies each node's column selection and renames, as `haute run`, the preview and a deployment do. |
| EXE-07 | Planned | P2 | A preview worker that died while idle is replaced whatever the size of the next request; previews and the server's shutdown no longer hang until a restart. |
| EXE-08 | Planned | P3 | A column replaced under its own name is estimated at its new width, so work that cannot fit is refused before it runs. |

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

### EXE-05 — A Transform that cross-joins a Constant runs outside the editor
**Why:** Reproduced. The Constant's page gives two ways to use one: an Edge
Join set to Cross, or a Transform with a "Join another input" step set to
cross. Its worked example is the second. With two quotes and a one-value
Constant, both previewed two rows in the editor. The Edge Join then ran from
`haute run`, and `haute deploy --dry-run` passed it as "one row per quote in
request order". The Transform exited 1 from `haute run`: "Materialisation of
'join' could not be admitted for this execution ... Estimator reported:
with_rate:cross_join_unmeasured". The deploy dry run exited 1 as well:
"Resolution failed: Deployed batch scoring cannot be admitted". The
execution-engine specification prescribes that refusal for every cross join
in a Transform: nobody has measured what a cross join costs, so it is warned
under a worker's memory cap and refused without one. The deploy
specification and the deployment guide say the opposite of this one: a cross
join with a single-row Constant in a "Join another input" step keeps one row
per quote. The low-level specification keeps the Edge Join out of the
planner's boundaries so that a Constant's cross join is not refused on a
host without a cap; the Transform has no such exemption. The refusal's
remedy, "Provide readable source/schema metadata", cannot help, and the
deploy text calls the join a group-by, both as in `EXE-04`.

**Plan:** Read a cross join whose other side is proved to hold one row, as a
Constant is, as what it is: the dominant side's rows with the constant's
columns added. Admit it on every surface without the unmeasured-cross-join
rule. Keep that rule for a cross join whose other side may hold more rows,
and make its message name the join, say that a cross join's memory cannot be
estimated, and give what works instead: an Edge Join set to Cross, or a
surface with a memory cap. Bring the two specifications and the Constant
page into agreement.

**Acceptance:** The Constant page's example (quotes, a Constant, a Transform
with a cross "Join another input" step and a formula that uses the constant)
gives the same rows in the editor's preview, from `haute run` and from the
deploy scorer, and its deploy dry run passes. A Transform that cross-joins
two many-row inputs is still refused where there is no memory cap, in one
message naming the node, the join and the remedies. A test runs the page's
example through all three.

**Dependencies:** None. `EXE-04` rewords the same refusal.

**Evidence:**
`src/haute/_ram_estimate.py::_estimate_materialisation_boundary_from_index`
answers `cross_join_unmeasured` for any cross join; `tests/test_ram_estimate.py`
and `tests/test_polars_backend_strategy_contract.py` hold that answer;
`docs/building-models/nodes/constant.md` (Connecting to other nodes, and its
example); `docs/deployment/index.md`; `specs/deploy/high-level.md`;
`specs/execution-engine/low-level.md`.

### EXE-06 — `pipeline.run()` applies a node's column selection and renames
**Why:** Reproduced on 13 November 2026 on two small pipelines, each run
three ways. A Banding's settings keep two columns and rename one, as its
Columns tab writes them: `selected_columns` of `quote_id` and `ncd_band`,
and `column_renames` of `ncd_band` to `band`. `haute run` and the editor's
preview return `quote_id` and `band`. The same file run as Python,
`pipeline.run(source="batch")`, returns `quote_id`, `ncd`,
`internal_margin` and `ncd_band`: nothing selected and nothing renamed. A
Data Input with `selected_columns` in its settings and a Transform with
`selected_columns` on its decorator do the same: two columns from
`haute run`, all four from `pipeline.run()`.

`pipeline.run()` and `pipeline.score()` walk the nodes with a loop of their
own. The graph walker shapes each node's output after its work; the loop
does not, and neither it nor the code that runs a configured node reads
either setting. A column the analyst removed therefore comes back in the
standalone result, and a later node that reads a renamed column does not
find it, in the one way of running a pipeline that needs no editor.

**Plan:** Shape a node's output in the standalone loop by the functions the
walker uses, so the two cannot differ, for every node type.

**Acceptance:** For each node type that can carry a column selection or a
rename, a pipeline gives the same columns and rows from `pipeline.run()`,
`pipeline.score()`, `haute run` and the preview. A test runs the shipped
examples the standalone way beside the others.

**Dependencies:** None.

**Evidence:** `src/haute/pipeline.py`;
`src/haute/_standalone_nodes.py::run_configured_node`;
`src/haute/_graph_walker.py`;
`src/haute/_execute_lazy.py::_apply_selected_columns`;
`src/haute/_execute_lazy.py::_apply_column_renames`.

### EXE-07 — A preview worker that died is replaced, whatever the next request's size
**Why:** Reproduced on 13 November 2026 with the real worker pool and
through the editor's preview route. The editor keeps a worker process for
previews. That process can die while it is idle: ended in Task Manager, or
killed by the system when memory is short. The next request finds it dead.
With a request of 4,000 bytes the pool reports a crashed worker in 3.5
seconds, starts another, and answers the request after that. With a request
of 16,000 bytes or more the call never returns. The pool closes the dead
worker's request queue and waits for the queue's feeder thread, which is
still writing the request into a pipe that nothing reads. The next request
times out waiting for the slot, and the pool's own close, which the server
calls when it shuts down, does not return either.

Through the editor, the shipped rating example's graph is 2,224 bytes as
sent to the worker. With the worker killed, the next preview answers 500,
"Operation failed. Check the server logs for details.", and the one after
it works. With the example's rating table grown to 5,000 rows the graph is
151,156 bytes: after the worker is killed the preview has no answer after
60 seconds, and the next preview of the shipped graph has none after 30.
Previews stay dead until the server is restarted. The execution
specification has a request never wait "unbounded".

**Plan:** When a worker is known to be dead, do not wait for its queue's
feeder: abandon what was not yet sent, and bound the wait that remains.
Answer the first request after a worker's death by saying that the worker
stopped and was started again.

**Acceptance:** With the idle worker killed, a preview whose graph is 151
kB as sent is answered, with rows or with a named failure, within the
request's own time limit; the preview after it has rows; and the server
shuts down. No preview after a worker's death says "Check the server logs".

**Dependencies:** `SRV-01` decides how much a failure's answer says.

**Evidence:** `src/haute/_interactive_workers.py`;
`specs/execution-engine/low-level.md`.

### EXE-08 — A column replaced under its own name is estimated at its new width
**Why:** Reproduced on 13 November 2026 through the executor with an
admitted preview. 100,000 quotes hold a three-byte `risk_text` and a
`description` of 4,097 bytes, every one distinct. A Transform selects
`pl.col("description").alias("risk_text")`, and the next node keeps the
distinct rows. The memory estimate for the distinct step is 8.0 MiB. With
the alias changed to a name the frame does not have, `risk_text_new`, the
estimate for the same data is 4,107.6 MiB. An output column that takes the
name of an upstream column is given that column's width, not the width of
what it is computed from. The execution specification asks the estimate to
account "conservatively for variable-width columns".

With the preview's budget set to 0.2 GB, the new name is refused before
anything runs, by name: "Materialisation of 'unique' could not be
admitted". The old name is admitted, runs, and is stopped part way:
"Execution 'preview' exceeded its memory growth budget". The cap held and
nothing crashed; the estimate that should have refused the work first did
not.

**Plan:** Take a replaced column's width from the columns its expression
reads, as for a column with a new name.

**Acceptance:** The two aliases above get the same estimate, and with the
0.2 GB budget both are refused before running.

**Dependencies:** None.

**Evidence:** `src/haute/_ram_estimate.py`;
`specs/execution-engine/low-level.md`.
