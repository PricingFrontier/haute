# Execution engine roadmap

## Scope

Running a graph, as [the execution engine
specification](../execution-engine/high-level.md) describes it: which source a
run reads, how a node's settings become the frame it produces, and how a
failure before any node runs is reported. The packages below come from the
9 November 2026 codebase review. `EXE-04` and `EXE-05` were reproduced on 10 November
2026 through the editor's preview route, `haute run` and the deploy. `EXE-07`
and `EXE-08` come from the review plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit; a worker
built each run and the main session ran it again on 13 November.

`tests/test_run_parity.py` runs every pipeline each way the specification's
**Ways of running a pipeline** names and holds `EXE-05`'s difference as an
expected failure naming it, so its delivery turns the case green.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXE-04 | Planned | P2 | A join that previews in the editor runs from the command line and in a deployment, whatever Polars options it carries. |
| EXE-05 | Planned | P2 | The Constant page's own example, a Transform that cross-joins a Constant, runs from the command line and deploys. |
| EXE-07 | Planned | P2 | A preview worker that died while idle is replaced whatever the size of the next request; previews and the server's shutdown no longer hang until a restart. |
| EXE-08 | Planned | P3 | A column replaced under its own name is estimated at its new width, so work that cannot fit is refused before it runs. |

## Planned improvements

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

Since 18 November 2026 the execution-engine specification's **Ways of running
a pipeline** states the difference that is meant: work whose memory nobody has
measured is admitted with a warning under a worker's memory cap and refused
without one. What is left here is reading a cross join whose other side holds
one row as no such work, and the refusal's words.

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
message naming the node, the join and the remedies.
`tests/test_run_parity.py::test_a_transform_crossing_a_constant_answers_every_way`
passes with its expected-failure mark removed.

**Dependencies:** None. `EXE-04` rewords the same refusal.

**Evidence:**
`src/haute/_ram_estimate.py::_estimate_materialisation_boundary_from_index`
answers `cross_join_unmeasured` for any cross join; `tests/test_ram_estimate.py`
and `tests/test_polars_backend_strategy_contract.py` hold that answer;
`docs/building-models/nodes/constant.md` (Connecting to other nodes, and its
example); `docs/deployment/index.md`; `specs/deploy/high-level.md`;
`specs/execution-engine/low-level.md`.

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
sent to the worker. With the worker killed, the next preview answers 500
with an incident ("Haute hit a failure it does not recognise"), and the one
after it works. With the example's rating table grown to 5,000 rows the graph is
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
shuts down. The first preview after a worker's death says that the worker
stopped and was started again, not that the failure is unrecognised.

**Dependencies:** None.

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
