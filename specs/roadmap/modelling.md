# Modelling roadmap

## Scope

Training a model from a pipeline node or an exported script, as [the
modelling specification](../modelling/high-level.md) describes it: how the
evaluation plan is built from the data, what a failed run tells the analyst,
and the files a run writes. The packages below come from the 9 November 2026
codebase review. Each entry says whether it was reproduced, measured or read
from the source against `HEAD` on that day.

`MDL-01` and `MDL-02` are both a message the analyst needs arriving as
"unexpected internal error"; they share the worker's failure mapping and go
together. `MDL-05` removes code and has no behaviour of its own.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MDL-01 | Planned | P2 | A temporal split whose date column has rows without a date, or is not a date, is refused naming the column and what to do. |
| MDL-02 | Planned | P3 | A tuning trial's own validation message reaches the analyst as written. |
| MDL-03 | Planned | P3 | An exported training script can be run again; when it cannot write, it says which files stand in the way before it prepares any data. |
| MDL-04 | Planned | P3 | Building a group or stratified evaluation plan no longer walks the key column row by row in Python. |
| MDL-05 | Planned | P3 | Training has one split pipeline: the constructor-only `split` seam and its code are gone. |

## Planned improvements

### MDL-01 — A date column that cannot split is refused by name
**Why:** Reproduced at the plan builder; the route's answer is read from the
source. A temporal evaluation collects the date column and hands its values
to the plan. A value that is neither text nor a date, a null included, raises
a bare `TypeError`: "date values must be ISO strings or native date/datetime
values". Nothing checks the column first: the target's nulls are counted and
filtered, the date column's are not. The training worker recognises only
Haute's own validation errors as messages for the analyst, so the `TypeError`
is answered as "Training of ... failed with an unexpected internal error
(TypeError). The full error is recorded in the job's error details." A
missing inception date on one row of the training data is enough. With 24
rows: all dates planned; one null date raised the `TypeError`; an integer
`yyyymmdd` column raised it too; a zone-aware datetime column raised Haute's
validation error "date values must not mix timezone awareness", which names
neither the column nor a way out, although a zone-aware column can never meet
the plain ISO boundaries the settings hold.

**Plan:** Check the date column before planning: its type (Date, Datetime or
ISO text) and its null count. Refuse naming the column, the number of rows
without a date and the choice the analyst has: remove those rows upstream or
split by another column. Read a zone-aware column by its dates, or refuse it
by name with the conversion to apply.

**Acceptance:** Training with a temporal split on a column holding one null
is refused before any fit, the job's message names the column and counts the
rows, and its terminal reason is the contract error, not the internal one.
An integer column and a zone-aware column are each refused, or planned, with
a message naming the column.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_evaluation.py::_date_codes`;
`src/haute/modelling/_training_job.py::TrainingJob._build_evaluation_plan`;
`src/haute/routes/_training_worker.py::_known_training_worker_failure`;
`src/haute/routes/_training_worker.py::_friendly_error`.

### MDL-02 — A tuning trial's validation message is not wrapped
**Why:** Read from the source. The tuning loop re-raises cancellation, memory
errors and `HauteError`, and wraps every other exception in
`RuntimeError("Tuning trial N failed for sampled parameters ...")`.
`HauteValidationError` derives from `ValueError`, not from `HauteError`, so a
validation message Haute wrote for the analyst (a family's parameter check, a
class-label or metric refusal raised by the trial's own fit) loses the type
the worker's failure mapping looks for. The job ends as an internal error
naming `RuntimeError` where the same message outside tuning ends as a
contract error with its text.

**Plan:** Re-raise `HauteValidationError` unwrapped, adding the trial number
and its sampled parameters to the message.

**Acceptance:** A tuning run whose sampled parameters the family refuses ends
with terminal reason `contract_error` and the family's message, naming the
trial. A test raises each of the two validation types inside a trial and
checks the job's message.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_training_job.py::TrainingJob._run_tuning_trials`;
`src/haute/_validation_error.py::HauteValidationError`;
`src/haute/routes/_training_worker.py::_known_training_worker_failure`.

### MDL-03 — An exported training script can be run twice
**Why:** Read from the source. A run refuses to start its evaluation when the
evaluation's plan, results or report file already exists, and its tuning when
the tuning files exist: "evaluation artifact paths already exist". The
exported script writes a fixed output folder and name, so the second
`haute train` of the same script always fails. The refusal names no file and
no remedy, and it comes after the data was prepared and the whole source
hashed for the plan. The model file itself is overwritten without a word.
Training from the canvas is unaffected: it stages each job in its own folder.

**Plan:** Decide at the start of the run, before any data is read: a run
replaces the files it owns (model, evaluation, tuning) together, or refuses
naming each file that stands in the way and saying to delete them or change
the output folder or name. Whichever is chosen applies to the model file
too.

**Acceptance:** Running one exported script twice either succeeds both times
with the second run's files, or the second run stops within a second with a
message listing the files and the two remedies. The model file and the
evaluation files of one folder always come from the same run.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_training_job.py::TrainingJob._run_evaluation`;
`src/haute/modelling/_training_job.py::TrainingJob._run_tuning_trials`;
`src/haute/modelling/_training_job.py::TrainingJob._save_artifacts`;
`src/haute/modelling/_export.py`; `src/haute/cli/_train.py`.

### MDL-04 — Evaluation planning does not walk the key column in Python
**Why:** Measured. The plan builder collects the group, date or target column
and converts it to a Python list; a group plan and a stratified plan then run
one `json.dumps` per row to key each value. A group plan took 1.7 seconds on
0.5 million rows and 8.4 on 2 million; a stratified plan 5.1 seconds on 2
million; a temporal plan 0.5 seconds on 2 million. The cost is linear, so
about 40 seconds at 10 million rows, and the list is ten million Python
objects (several hundred megabytes for a string key) inside the
memory-limited training worker, which the training estimate does not count.

**Plan:** Key groups and strata in Polars: take the distinct values with
their row codes from the frame, key only the distinct values in Python, and
map codes to memberships with arrays. Keep the plan's bytes identical for
the existing fixtures.

**Acceptance:** A group plan over 2 million rows with 50,000 groups builds in
under a second on the review machine and allocates no per-row Python
objects, asserted by a peak-allocation bound; every existing plan fixture
hashes as before.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_training_job.py::TrainingJob._build_evaluation_plan`;
`src/haute/modelling/_evaluation.py::_json_key`;
`src/haute/modelling/_evaluation.py::generate_evaluation_plan`.

### MDL-05 — The constructor-only split seam is removed
**Why:** Reproduced. `TrainingJob` still accepts `split=` in place of
`evaluation=`; its docstring calls it a constructor-only seam for direct test
callers, and node settings and exported scripts always pass `evaluation`.
The seam carries a second split pipeline with a silent fallback in it: the
group split keys groups by Python's `str()` of the distinct values but looks
rows up by Polars' text cast, and a row whose key is not found is put in
training. On 1,000 rows asked for 30% validation and 20% holdout, an integer
group column split 491, 318 and 191, while a datetime and a boolean group
column put all 1,000 rows in training with nothing said. No product path
reaches it, so no result is wrong today; the defect is some four hundred
lines of production code, with a silent fallback, kept for tests.

**Plan:** Remove `split=`, `SplitConfig`, the `exclude` handling and the
`split_config` branches, and point the tests that use them at the evaluation
path.

**Acceptance:** `TrainingJob` has no `split` parameter, `_split.py` holds
nothing the evaluation path does not call, and the suite passes with the
tests moved.

**Dependencies:** None. Haute has no users, so nothing needs migrating.

**Evidence:** `src/haute/modelling/_split.py::_group_mask`;
`src/haute/modelling/_split.py::SplitConfig`;
`src/haute/modelling/_training_job.py::TrainingJob`.
