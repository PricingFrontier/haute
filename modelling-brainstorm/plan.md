# Plan

How the Modelling UI gets built: one aspect at a time. I build a phase, you play with it in
the demo and decide what you think, and only then do we plan the next one. Nothing here
decides a concern (A to H) or a question (Q1 to Q6) from `overview.md`. Each phase says
which of them it lets you judge. There are no durations: I would be guessing.

Frame is built. Only the next two phases are detailed. The rest are a line or two each, and
each gets re-planned after the review before it.

## How a phase runs

- **Agree.** I write the phase out in a few lines: what is in, what is out. You say go.
- **Build.** Small steps under Haute's own rules: a line or two in the specs, targeted
  tests, then the rebuild so it is in the demo. Nothing is committed unless you say.
- **Play.** You restart `haute serve` and try it. Each phase lists what to try.
- **Decide.** What you decide goes into Settled in `overview.md`. The rest stays parked.

**Containment.** Only an interactive build opens in the Modelling UI and takes the new
config. Every other Model Training node keeps its panel and its config as they are, until
the last phase.

## The phases

**Staged t-boost proposal (20 Nov).** The detailed [implementation plan](staged-tboost.md)
covers building the full GBM with its pruning path, choosing pruning afterwards without
reboosting, then banding/smoothing and comparing stage predictions. It pulls the necessary
checkpoint persistence and evaluation provenance into that work. This is a proposed scope
and delivery sequence, not a claim that the feature is built or that the remaining phases
have been reordered.
Ralph's follow-up puts the stage hyperparameter boxes and separate action buttons in a
right-hand Training pane and removes the top-toolbar Train button. The proposed layout
uses Features and Training tabs, with Training replacing Parameters.

**Frame.** Built. The screen's skeleton, on today's one t-boost model: the top row with the
model's entry, the settings column as two tabs (All models and t-boost), and the pane tabs. Beyond
the plan, the chosen factor's row is highlighted and scrolled to. Left out: "out of date" on the
entry, and the panes are flat, not grouped. *Lets you judge:* concern A, and proposals 6 and 52.

**Every factor.** Built, waiting for your play. A factor's chart shows even when the model
left the factor out.

- *You get:* every run also bins actual against its prediction for each column it left out,
  worked out at training time. The columns charted are those reaching the node that hold no role,
  so not the target, weight, offset, ids, fold or split columns. The Factor pane charts a left-out
  factor under the line "Not in this run".
- *Built:* in the backend's diagnostics and the Factor pane, for an interactive build only. Every
  other Model Training node keeps its panel, config and results as they are.
- *Left out:* ranges, and splits by a second factor (still Kept predictions).
- *To try:* train with some factors left out, then choose them on the left.
- *Lets you judge:* the settled line "A factor's chart shows for every factor, even the ones a
  model did not fit."

**Several models, first cut.** Built 20 Nov, pulled forward at your request, waiting for your
play. Factors and directions are in each model's own tab, and All models keeps the target and
the objective. Add a t-boost model (it starts as the chosen model, proposal 59), and Duplicate,
Rename and Remove from an entry's menu (Duplicate copies settings, never the run, proposal 60).
There is an entry per named model, and each model trains and keeps its latest run on its own.
The config holds the shared settings once and the models under `models`, and the demo's
`modelling.json` was converted to it. *Left out:* runs still go on a restart, the Factor chart
draws the chosen model only, there is no second model type, no queue (a second Train while one
runs is refused), and no Compare. *To try:* add a model, change its factors, train both, and
switch between their entries. *Lets you judge:* concern G, proposals 59 and 60, and the
config's shape.

**Layout and comparison.** Built 20 Nov from your review, waiting for your play. The toolbar
has Model Setup, Training Split and Train (the chosen model), which becomes Cancel while
that model runs; Undo, Redo, the source and
Pipeline are gone (undo stays on the keyboard). The right-hand column is the chosen model's
Parameters. The condensed feature list in the right-hand Features pane expands each name into its fitting controls. Entries show a colour dot, the name and a tick box, with no words;
a line under them says why the model cannot train or why it failed. The Factor chart draws
every ticked model's expected over the actual, the chosen one heavier, and a model that left
the factor out dashed. Training Split makes validation and test rows reachable, which also
offers tuning in Parameters. *Left out:* the Train pane's RAM estimate, row-limit warning and
detailed failure; no out-of-date mark; tuning's trial progress has no home yet. *Lets you
judge:* the new settled lines, proposal 6.

**Runs that last.** A run survives a restart, and can be kept.

- *You get:* a trained model is still there after `haute serve` restarts. `Keep as a
  reference model` works, and a reference model is drawn dashed on the factor chart. The
  old run stays on screen until a new training finishes.
- *Built:* a run store in the project's cache, keyed by node, model and run: a model, not a
  model type. The factor chart learns to draw more than one line.
- *Why now:* every review ends in a restart, and until this phase each one means training
  again. Also, today a newer run releases the older run's files, which would lose a
  reference model or a second model.
- *Left out:* kept predictions, and the history.
- *Lets you judge:* concern B.

**Same rows.** One split per node, made once: training, validation and test rows. Charts
and scores come from validation rows the fit never saw, and nothing reads the test rows.
Every pane says which rows it uses. Much of this is settings Haute already has.
*Lets you judge:* proposals 2 and 20 to 24.

**Several models, the rest.** The first cut is above. A second model type, a queue, and a
shape per model type (the colour per model is built). Gradient boosting stops early on rows held back
from its training rows. *Lets you judge:* proposals 28, 55 and 61, and which model types come
first.

**Compare.** The standings, gain over the floor, the two yardsticks, `vs`, and lift and
calibration overlaid. *Lets you judge:* proposals 25, 31 and 54, and Q5.

**Kept predictions.** Each run keeps one prediction per row. That brings ranges on actual and
on differences, a true double lift, and splits by a second factor. *Lets you judge:* Q3 and
proposal 26.

**The loop.** Try and Apply, the history, the factor list sorted by misfit, the stopping
questions and the checks. *Lets you judge:* proposals 32 and 38 to 41.

**Model types in depth.** t-boost's pruning path, tuning for gradient boosting, EBM, and
the GLM's own workflow, built forward on its own factors. *Lets you judge:* concern F, and
proposals 45 and 46.

**Logging.** The refit, refit against reviewed, the test rows scored once, the note, the
model card and the node's card. *Lets you judge:* Q2 and Q6, and proposals 47 to 49.

**By hand.** Hand edits and fixed values. *Lets you judge:* concern E, Q1, and proposals 14
to 19, 43 and 44.

**Afterwards.** Refreshing a logged model, handover, scheduling, and moving every other
Model Training node into the Modelling UI. *Lets you judge:* concerns D and H, and proposals
50 and 51.

Q4 (claim counts and claim sizes checked together) is in no phase.

## Why this order

The team's order in its reports was foundations first. This one puts something on screen in
every phase and pulls each foundation in where it is first needed. What fixes the order:

- **Every factor comes first,** because it was the first thing you asked for after Frame, and
  it needs only today's one model and the diagnostics worked out at training time.
- **Runs are keyed by node today,** so the run store comes before a reference model or a
  second model.
- **Models must be judged on the same rows** before two of them share a chart, so Same rows
  comes before Several models.
- **Only one training runs at a time,** so the queue arrives with Several models, before
  train-all and what-if runs.
- **Kept predictions come before** ranges on differences, a true double lift, misfit and the
  hand-edit preview.
- **Logging needs the run's files,** so the run store comes long before it.

## Before starting

Everything so far is uncommitted on the branch `modelling-library-moves`, on top of a large
earlier change. A commit before Frame would let each phase be reviewed, and backed out, by
itself. That is your call.

Latest layout decision (20 Nov): Main Menu in the top toolbar replaces the back arrow;
models and Add are in the left palette; the complete animated feature list is in Features
on the right, beside the Parameters tab. This supersedes earlier locations in the sketches.
