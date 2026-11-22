# Modelling roadmap

## Scope

Training a model from a pipeline node or an exported script, as [the
modelling specification](../modelling/high-level.md) describes it: how the
evaluation plan is built from the data, what a failed run tells the analyst,
and the files a run writes. The packages below come from the 9 November 2026
codebase review. Each entry says whether it was reproduced, measured or read
from the source against `HEAD` on that day.

`MDL-01` and `MDL-02` are both a message the analyst needs arriving as an
incident ("Haute hit a failure it does not recognise"); they share the worker's
failure mapping and go together. `MDL-05` removes code and has no behaviour of its own.

`MDL-07` and `MDL-08` come from the review plan's sweep of 10 November 2026,
which trained each model family through Haute and compared every way of
scoring it with the family's own library. Where a family scored at all,
every way agreed with the library row for row; these two are the places
where it did not score.

`MDL-09` comes from the plan's phase 5, a reader from a second model family
(Codex) over the code changed since the 31 October audit. It is about a
model trained outside Haute, which the earlier sweep did not score, and was
reproduced on 13 November against LightGBM's own `predict`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MDL-01 | Planned | P2 | A temporal split whose date column has rows without a date, or is not a date, is refused naming the column and what to do. |
| MDL-02 | Planned | P3 | A tuning trial's own validation message reaches the analyst as written. |
| MDL-03 | Planned | P3 | An exported training script can be run again; when it cannot write, it says which files stand in the way before it prepares any data. |
| MDL-04 | Planned | P3 | Building a group or stratified evaluation plan no longer walks the key column row by row in Python. |
| MDL-05 | Planned | P3 | Training has one split pipeline: the constructor-only `split` seam and its code are gone. |
| MDL-06 | Planned | P3 | `haute train` runs any script that plain Python runs: a script's own classes can be dataclasses and can be pickled. |
| MDL-07 | Planned | P3 | A GLM asked to classify is refused by name, when it is trained from a script and when a Model Scoring node scores it. |
| MDL-08 | Planned | P2 | A null in a GLM's numeric feature, or an infinity reaching an XGBoost model, is refused naming the column instead of failing the whole run with the library's own error. |
| MDL-09 | Planned | P2 | An imported LightGBM model whose categorical feature has Boolean levels scores a False and a True as its own `predict` does, not both as missing. |

## Planned improvements

### MDL-01 — A date column that cannot split is refused by name
**Why:** Reproduced at the plan builder; the route's answer is read from the
source. A temporal evaluation collects the date column and hands its values
to the plan. A value that is neither text nor a date, a null included, raises
a bare `TypeError`: "date values must be ISO strings or native date/datetime
values". Nothing checks the column first: the target's nulls are counted and
filtered, the date column's are not. The training worker recognises only
Haute's own validation errors as messages for the analyst, so the `TypeError`
is answered as an incident: "Haute hit a failure it does not recognise
(incident …)", its text recorded under the incident. A
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
`src/haute/routes/_training_worker.py::_training_failure_answer`.

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
hashes as before. A temporal group value stays keyed by its exact text
(`TemporalKey`).

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

### MDL-06 — A training script is loaded as an ordinary module
**Why:** Reproduced on 10 November 2026. `haute train` loads the script as a
module named `training_script` and runs it without registering that module,
so code that looks its own module up fails. A script holding
`from __future__ import annotations` and one `@dataclass` ran under plain
Python and, under `haute train`, ended in a traceback: "AttributeError:
'NoneType' object has no attribute '__dict__'", raised inside the standard
library's `dataclasses`. A class the script defines also cannot be pickled,
which a worker process needs, because its module cannot be imported by name.
The exported script holds neither, so this reaches a script an analyst has
extended by hand. The 28 October audit recorded it as an unverified lead.

**Plan:** Register the module under its name before running it, and remove
it afterwards, as the import system does for any file it loads.

**Acceptance:** The script above runs under `haute train` as it does under
Python. A script whose `job` is built from a dataclass it defines trains. A
script that raises still reports its own error, and nothing of it is left in
`sys.modules` after the command.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_train.py::handle_train`.

### MDL-07 — A GLM asked to classify is refused by name
**Why:** Reproduced on 10 November 2026. The specification says the GLM
trains regression only, and that a task a family does not support fails
before data is read. The node's settings are held to that: "GLM does not
support the classification task." `TrainingJob` itself is not: built with
`algorithm="glm"` and `task="classification"`, as a hand-edited training
script can build it, it trains a binomial GLM without a word. Scoring has
the same gap. A Model Scoring node whose task disagrees with a CatBoost
model is refused well: "This CatBoost model was trained for regression (loss
RMSE) but the node scores it as classification. Set the node's task to
regression." Set to classification over a binomial GLM trained the ordinary
way, the node's preview fails with RustyStats' own text: "Prediction
produced non-finite linear predictors. nonfinite_eta_count=1. Use
predict_linear() or predict_diagnostics() to inspect eta." A binomial GLM is
the model an analyst is most likely to think of as a classifier.

**Plan:** Check the task against the family's list where `TrainingJob` is
built, with the message the settings check gives. Give the Model Scoring
node the same task check for a GLM that the tree families have, saying that
a binomial GLM predicts a probability and the node's task is regression.

**Acceptance:** `TrainingJob(algorithm="glm", task="classification")` is
refused before any data is read, naming the family and its tasks. A Model
Scoring node set to classification over a GLM file is refused in the
preview, in a run and in the deploy check with a message that names the
node and says to set its task to regression. A test holds every family to a
named refusal for each task it does not support, at training and at
scoring.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_descriptors.py::GLM` lists regression
alone; `src/haute/modelling/_train_config.py::build_training_job_kwargs`
makes the check the constructor lacks;
`src/haute/modelling/_training_job.py::TrainingJob`;
`src/haute/_model_scorer.py::ModelScorer`.

### MDL-08 — A null or an infinity a family cannot take is refused by column
**Why:** Reproduced on 10 November 2026. The specification and the Model
Scoring page promise that a missing value never stops scoring: "every family
scores it through its own missing-value handling". A GLM has none for a
numeric term. One null in a numeric feature made the whole frame fail with
RustyStats' own error, "Prediction produced non-finite linear predictors.
nonfinite_eta_count=1", naming neither the column nor the row; the same
frame without that row scored. NaN and the infinities do the same. An
XGBoost model does it for an infinity: one `inf` in a float feature fails
the whole frame with "XGBoostError: ... data.cc:1196: Check failed: valid:
Input data contains `inf` or a value too large", a C++ source path included,
while CatBoost, LightGBM and EBM score the same row. A ratio whose
denominator is zero is enough to make one. The sweep met both on all ten
scoring paths it ran, the deploy scorer among them, where the caller of a
deployed model is told only that the request failed. Training has the same
gap for a GLM: nulls in a numeric feature raise RustyStats' "design matrix
(X) contains 8 NaN values (0.7%)", which names no column, and the training
worker passes on only Haute's own validation messages, so by the source the
analyst is told only of an incident. The route was not run.

**Plan:** Before a GLM is fitted or scored, count the nulls and non-finite
values in each numeric feature its terms use and refuse naming the column,
the number of rows and what to do: fill or drop them upstream. Before an
XGBoost model is scored, do the same for infinities, or pass them as the
missing value the model was trained with; decide which. Say in the
specification and on the Model Scoring page what a GLM does with a null in
a numeric term.

**Acceptance:** A GLM scored on a frame with one null in a numeric feature
is refused before prediction in a message that names the node, the column
and the count, in the preview, a run and the deploy scorer. GLM training on
such data is refused the same way, as a contract error and not an internal
one. An XGBoost model scored on a frame holding an infinity either scores it
or is refused by column; no scoring path shows a library's source path. The
specification's sentence on nulls is true of every family or names the
exception.

**Dependencies:** A decision on a GLM's null in a numeric term: refuse by
name, as proposed, or answer null for that row and carry on.

**Evidence:** `src/haute/_mlflow_io.py::native_predictions`;
`src/haute/modelling/_xgboost.py::XGBoostModel.matrix`;
`src/haute/modelling/_rustystats.py::_rustystats_call`;
`src/haute/routes/_training_worker.py::_training_failure_answer`;
`specs/modelling/high-level.md` (Unseen categorical values);
`docs/building-models/nodes/model-score.md` (Values the model was not
trained on).

### MDL-09 — An imported LightGBM model's Boolean categories score as its own predict scores them
**Why:** Reproduced on 13 November 2026 through the editor's preview. A
LightGBM model trained outside Haute has a categorical feature `flag`
whose levels are the Booleans False and True, as pandas gives a yes/no
column. Scored by a Model Scoring node on a Boolean column, both rows get
5.7288. The booster's own `predict` gives 2.5293 for False and 5.7288 for
True. False is scored as a missing value and nothing says so. The same
values sent as the texts "False" and "True" score 2.5293 and 5.7288. An
integer-coded category is refused by name: "model expects categorical
(String), got Int".

Haute reads the model's recorded levels with Python's `str`, which gives
"False" and "True", and turns the incoming column to text with Polars,
which gives "false" and "true". The two never match, so every Boolean
value falls outside the levels. The modelling specification has a model
trained outside Haute "priced by LightGBM's own `predict`" from the levels
LightGBM records.

**Plan:** Turn a recorded level and an incoming value to text by one rule,
so a Boolean, like any other level, matches itself. Or refuse a Boolean
column for a categorical feature by name, as an integer one is refused.

**Acceptance:** The model above scores 2.5293 for False and 5.7288 for
True through the preview, `haute run` and the deploy scorer, or the node
is refused naming the feature and its type. A test scores an imported
model with a level of each type LightGBM records against the booster's own
`predict`.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_native_encoding.py::native_categories`;
`src/haute/modelling/_native_encoding.py::encode_frame`;
`src/haute/modelling/_lightgbm.py`; `specs/modelling/low-level.md`.

