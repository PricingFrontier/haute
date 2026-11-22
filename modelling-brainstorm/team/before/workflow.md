# Workflow

How an analyst gets from a new node to a logged model. Loose, and proposed unless
`overview.md` lists it as settled.

## The journey

1. **Open the node.** From the pipeline, the node opens in the Modelling UI.
2. **Set up once, for all models.** The target, exposure or weight, offset, loss, split and
   metrics. The tool suggests where it can: a loss from the shape of the target, a split by
   time when there is a date, a split by group when there is a policy id. It never applies
   a suggestion by itself.
3. **Look before fitting.** Before any model exists, the factor chart already shows actual
   by level, with exposure. Go through the factors, leave out what should not be rated on,
   and set the directions the business requires. The checks flag likely leaks, identifiers,
   constants and near-duplicates. These charts and checks leave the test rows out, so
   looking at the data does not unseal them.
4. **First fits.** Add model types and train them all on their defaults. A sample run first
   gives charts in seconds. Two yardsticks come free: a model with no factors (the
   floor), and a gradient-boosting model on defaults (roughly how much signal there is).
5. **Read the standings.** The comparison shows where each model sits between the floor and
   the strongest model.
6. **Improve, in a loop.** See "The loop".
7. **Refine within a model type.** See "Workflows by model type".
8. **Adjust by hand,** where judgement overrides the fit. See "Hand edits".
9. **Stress the favourite.** Run the thorough evaluation. Check it holds across years, by
   segment and in calibration. Run a double lift against the last logged run and against
   the strongest model.
10. **Log.** Pick the run. The test rows are scored now. Go through the checklist, add a
    note, and log to MLflow.
11. **Afterwards.** The node shows what was logged. The logged run is kept as a reference
    model, so the next round of work is drawn against it.

## The loop

    pick the worst-fitting factor
      -> read its chart for every model
      -> change one thing
      -> retrain what is out of date
      -> read what changed
      -> keep it, or undo it

- **Pick.** The factor list can sort by where the chosen model fits worst, so the list is a
  worklist.
- **Read.** The factor chart shows every model's expected against actual, how each model
  treats the factor, and the same split by year.
- **Change one thing.** A shared setting (in, out, direction), a model's own setting, or
  the data itself in a pre-processing node.
- **Retrain what is out of date.** A shared change marks every model. A model's own change,
  a hand edit included, marks only that model. One button trains whatever is out of date.
- **Read what changed.** Each new run is set against the run it replaced: what changed in
  the settings, the change in each score with its range, and which factors' fit moved.
- **Keep or undo.** Undo takes the settings back, as it does everywhere in Haute. A run
  worth returning to later is kept as a reference model before moving on.

**What-if runs.** These ask a question without committing to the change:

- "without this factor"
- "with this direction"
- "each factor left out in turn"

The run trains in the background, for the chosen model or for all of them. It reports the
difference with its range and offers to apply the change. It is dropped afterwards unless it
is kept as a reference model. Because factors are shared, this
is how a factor gets tested without putting every model out of date.

## Checks

A short list the tool keeps current. Each check links to the place that fixes it.

- a factor that predicts the target suspiciously well (a likely leak)
- a wide gap between the fit on training rows and on validation rows (overfitting)
- a factor whose pattern flips between years
- a level too thin to trust
- two factors that carry the same information
- a model whose total expected is off its total actual
- an unconstrained fit that runs against the direction the business would expect
- a hand edit the data has moved away from: left free, the fit would choose a clearly
  different value
- a hand edit whose levels no longer exist, after the factor changed in pre-processing. It
  is not applied.
- runs that are out of date

Checks inform and never block training. Each can be acknowledged with a note.

## Feature engineering

Settled: feature engineering stays in the pre-processing nodes.

- From a factor, one click opens the node that makes its column.
- A new or changed column shows in the factor list, marked as new, and puts the models out
  of date.
- The Modelling UI can suggest (a banding, a grouping of levels, two factors worth
  combining), but the change is made in pre-processing.
- A GLM term, such as bands or a spline, says how a factor enters that one model. It makes
  no new column, so it stays in the GLM's workflow.

## Hand edits

Settled: an analyst may edit a fitted table or relativity by hand before logging. The rules
are in `overview.md`. In the workflow:

- **Last, not first.** Edit once the fit is settled. Improve the model through its settings
  for as long as that works. A setting applies to every model and an edit to one, and
  every later fit has to work around the edits already made.
- **One edit, one reason.** Each edit gets a note. Typical reasons: a thin level the fit
  cannot be trusted on, a curve that should be smooth, a value the business or a regulator
  fixes, a tail that should carry on in a straight line.
- **Read the preview, then the refit.** The preview shows the edit laid on the current
  fit. After training, read the scores with their ranges and which other tables moved to
  make room.
- **Test an edit before keeping it.** A what-if run can try an edit, or the model without
  one, and report the difference.
- **Stress the edited model.** The stress step and the logging checklist apply to the model
  with its edits, since that is what gets logged.

## Workflows by model type

**t-boost.** Boost, then make the tables readable in steps: prune the interactions, band,
graduate.

- Each step shows what it costs as a curve of fit against simplicity, with the chosen point
  marked. The analyst picks the trade on the curve instead of typing a tolerance. t-boost
  already scores each point on its pruning path.
- The tables are the model, so the model's own panes are the tables, with the interactions
  listed by importance.
- Ideally each step applies to the boosted model without boosting again, so trying another
  point on a curve is quick.
- The boosted, pruned and banded results can each be kept as a reference model, so they
  compare like any others.

**Gradient boosting (LightGBM, XGBoost, CatBoost).** The work is mostly parameters: defaults
first, then tuning within a time budget.

- Its own panes: the learning curve, importance, SHAP, the tuning trials.
- Its usual role is the yardstick. It shows how much signal a simpler model is missing, and
  the comparison shows where.

**EBM.** Its terms and its chosen interactions. Its own panes are the term shapes.

**GLM.** The GLM shares the factors and monotonicity, but it needs its own workflow. A tree
model takes a factor as it comes, while a GLM has to be told how each factor enters the
model.

- **A term per factor:** categorical levels (grouped where they are thin or alike), bands
  for a continuous factor, or a spline or polynomial.
- **Interactions are added explicitly,** not found by the fit.
- **Built factor by factor:** add or change a term, refit, look at the result, repeat. This
  is the classic actuarial loop, so refitting should feel instant.
- **Its own diagnostics:** relativities with confidence intervals, coefficients and their
  standard errors, significance tests for adding or dropping a term, and AIC or BIC.
- **Monotonicity in a GLM** means constraining the term, for example a monotone spline or
  ordered bands, rather than a flag passed to the fit.
- **It trains with no setup.** Each factor that is in starts with a default term: its
  levels for a category, a smooth curve for a number. The GLM is then in the comparison
  from the first training, and is refined from there.
- **It borrows from the other models.** Bands from where t-boost banded a factor, a curve
  from another model's effect, interactions from the ones t-boost kept. Each is offered
  with its evidence and applied by the analyst.

The GLM still gets the shared diagnostics, so it compares with every other model on the same
factor chart.

**Clustering.** Parked for now.

## Before logging

A checklist, shown at logging and recorded with the run. It does not block.

- every factor that is in has been reviewed (a tick and an optional note per factor)
- the checks have been read: each one fixed or acknowledged
- every hand edit has a note
- the thorough evaluation has run on the settings being logged
- the test score has been seen, once
- the run is compared with the last logged run: the scores, and how far predictions move
- a note says what changed and why

The notes, the reviews and the run's history go into the model card logged with the run.

## Working fast

- **By size.** On small data the thorough evaluation is quick enough to run every time. On
  large data it waits until before logging.
- **Sample first.** A run on a sample of rows answers most questions in seconds. The full
  run confirms. (Haute has a row limit today.)
- **Train only what is out of date,** and several models at once where memory allows.
  (Haute estimates memory today.)
- **No waiting to look.** Charts come from kept predictions, so another factor, split or
  pair of runs needs no training.
- **Carry on where possible.** Where a model type allows it, continue from the last run
  rather than start again: more boosting rounds, or one changed GLM term.
- **Keyboard.** Arrow keys move through the factors, so reviewing forty factors is forty
  key presses.

## The Assistant

Haute has an Assistant panel. In modelling it could explain a chart, run a what-if, or
draft the logging note. It would work through the same settings and runs as the analyst, so
everything it does is visible and can be undone.
