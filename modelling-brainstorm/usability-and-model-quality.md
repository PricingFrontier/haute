# Making modelling intuitive and effective

21 November 2026. Brainstorm requested by Ralph after accepting the general structure.
These are proposals, not settled decisions or implementation specifications. No product
changes were made for this review.

Two agents independently examined task flow and modelling decision quality; the lead
reviewed the current code/screenshots, challenged the recommendations and synthesised them.
A second usability pass challenged priorities and clutter. This was a design walkthrough,
not a study with users, so the recommendations still need task-based validation.

## Keep the structure

Keep Models on the left; Features, Parameters and Training on the right; Factor, Learning
curve, Tables and Pruning path in the centre. Keep warnings inside the right column, the
quiet model palette, compact tabs and separate configuration/action panes. Do not introduce
a wizard, a second feature list, automatic tab changes or a permanent status banner.

The central opportunity is to make each decision answer:

1. What model am I looking at, and which rows support this evidence?
2. What did this change gain or sacrifice?
3. Which result am I keeping, and what is the next useful thing to inspect?

## Findings from the current implementation

- `TrainingPane.tsx` exposes stage actions, running state and pending-settings text, but
  little completed-result context. In the browser screenshot the path says Applied: 6
  tables while the Pruning card still gives a generic instruction to preview a choice.
- Stage snapshots already contain diagnostic metrics and table reports. A first comparison
  surface can reuse these rather than introducing a new evaluator.
- `PruningPath.tsx` labels training OOB evidence and its development reference fit under CV.
  `ModelsAveChart.tsx` labels actual versus expected but does not keep validation/development
  context equally prominent. It already rejects incompatible series identities.
- The GBM parameters and tuning search space are primarily JSON editors. Ordinary users
  must know parameter names and useful values before making a controlled experiment.
- New models currently start without validation or a reserved test set. Training therefore
  being possible is not the same as having evidence of performance on unseen rows.
- Pruning has preview/apply/discard. Banding and smoothing currently apply immediately.
  Matching their interaction to pruning would require real candidate-retention work.
- Stage visibility is a display choice. It does not select the recipe that finalisation
  refits or the artifact that export saves; the UI should make this distinction obvious.
- The public modelling guide still has an older “What the view does not do yet” section
  contradicting its later staged export instructions. Reconcile that documentation in the
  next implementation pass. Older brainstorm analytics likewise contain historical claims
  superseded by staged checkpoints; they are not proof that proposed analytics exist.

## 1. Make the evaluation basis clear from the first fit

Use the existing empty canvas and GBM card to summarise target, selected feature count and
evaluation mode, with direct links to Model Setup and Training Split. Let users deliberately
choose an evaluation split or development-only exploration. Do not silently assume random
holdout is suitable for time-dependent observations or repeated entities.

Put a small Validation, OOF validation or Development label in the existing chart metadata.
Expose row counts, weights/exposure and split details on demand. Keep the pruning path's
Training OOB label distinct. Under CV, table complexity/path evidence belongs to the
reference fit; it should not pretend to describe one bank used for every OOF prediction.

Development-only results remain useful for fitting/debugging, but must not look like an
assessment of generalisation. A repeated final-test review also needs honest provenance;
persisting test-use history across rebuilds is a later feature, not something current stage
manifests already guarantee. The reason for separating model selection and final testing is
described in [scikit-learn's evaluation guidance](https://scikit-learn.org/stable/modules/cross_validation.html).

## 2. Give Training cards a useful state

Use their existing headings and metadata space. Show one salient state rather than a row of
badges. Keep detailed saved/draft differences behind a disclosure.

| Situation | What the card should communicate | Useful action |
|---|---|---|
| No full model | What must be configured first | Choose target / Review split / Train GBM |
| Full model built | Completed full fit and evaluation basis | Inspect pruning path, or continue with full model |
| Pruning preview ready | Candidate exists; it is not applied | Compare, Apply pruning, Discard preview |
| Pruning applied | Applied cutoff and retained table count | Optional banding, another preview, or finalise |
| Parameters edited | Changes pending; saved result still shown | Inspect changes, rerun the affected stage |
| Rebuild cancelled/failed | Previous applied result remains available | Retry or continue inspecting it |

Make unavailable actions explain their actual prerequisite locally, with a link to it.
Never launch a missing earlier stage implicitly. Keep Cancel tied to its running operation.
Completion can offer an inspection link without moving the user's selected chart.

This is an application of visible system state, not a claim that extra badges alone improve
usability. [NN/g's guidance](https://www.nngroup.com/articles/visibility-system-status/)
supports making the state and outcome of actions apparent.

## 3. Turn a preview into an informed decision

Beside the keep/discard actions, show a compact before/after comparison for the selected
decision, not a permanent dashboard for every model and stage.

- Name the reference explicitly: Full GBM, the chosen parent, or a deliberately pinned model.
- Show one primary metric, its direction, the evaluation basis and the observed difference.
  Secondary metrics can be expanded. Compare only compatible row/target/weight identities.
- Show the complexity that actually changes: retained tables for pruning; levels/cells for
  banding where reports support it; changed table shapes for smoothing. Table count alone
  is not an adequate measure of banding or smoothing.
- Offer Compare with parent / Compare with full as an explicit chart-view action. Temporarily
  isolate the relevant lines and restore the user's previous visibility choices afterward.
- Offer Inspect changed factors instead of automatically switching the chosen factor.
- Distinguish the OOB rule that proposes a cutoff from the preview's validation outcome.

Illustrative fields, not measured results:

> Pruning preview compared with Full GBM  
> Validation deviance: before → after · observed difference  
> Retained tables: before → after  
> Compare · Apply pruning · Discard preview

Do not turn a tiny numerical gain into a green “better model” verdict. Do not infer
uncertainty from the separation of curves. Proper uncertainty, paired comparisons and
segment/stability analysis need their own definitions and data-retention work.

## 4. Make Parameters approachable — declined

**Ralph declined this proposal on 21 November: keep the JSON parameter editor.**
The following paragraph is retained as the historical proposal, not planned work.

Expose a small set of commonly changed GBM controls as labelled fields: tree count, learning
rate, interaction order and bagging, with applicable early-stopping controls. Retain the
complete JSON form under Advanced. The form and JSON must edit one canonical configuration,
preserve advanced keys and obtain effective defaults from the same authoritative definitions.

Collapsed stage headings can summarise Automatic cutoff, a table budget or Automatic
smoothing. A small settings link in Training should open the matching Parameters section.
Keep technical explanation in contextual help/docs rather than adding paragraphs to cards.

Avoid “best model” presets. A future quick exploration preset must disclose its actual
settings and label its result accordingly; automatic tuning is not a substitute for a
meaningful split, valid input features and inspection of the resulting model.

## 5. Make experimentation recoverable and output explicit

Extend try → compare → keep/discard to banding and smoothing in a later increment. This is
new backend and UI behaviour: changing button labels alone would be misleading. Keep the
parent while the candidate is inspected, and preserve it on failure/cancellation.

Later, let users retain a named model checkpoint before replacing a fit. Current Duplicate
copies settings, not trained results, and ordinary configuration undo is not result rollback.
Make those distinctions clear before encouraging unrestricted experimentation.

The existing Final model card should name the exact applied output and recipe being refitted,
for example Full → Pruned → Banded, independently of visible curves and pending settings.
Separate exploration metrics from the final refit's test metrics. Export should clearly name
the final artifact it will save. A smoother or simpler stage is optional, not a mandatory
upgrade over its parent.

## Quality considerations without a wall of warnings

Make exposure/support information easy to inspect beside factor charts. Sparse levels should
remain visible; any low-support indicator needs an objective-appropriate definition. A
universal claim-count threshold or exposure alone does not establish confidence.

Give analysts a way to review whether features are available at prediction time. Do not
declare a strong predictor a leak or automatically delete it. An evaluation split inside the
training node also does not prove that learned upstream preprocessing excluded held-out data.
See [scikit-learn's leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

Model quality involves useful validation performance, appropriate complexity and credible
behaviour in relevant populations. Do not compress these into an unexplained quality score,
or automatically choose the highest score after repeated trials.

## Suggested order

1. **Orientation:** intentional evaluation setup, consistent evidence labels and clear
   current/applied/pending states inside existing space.
2. **Decision support:** one compact stage comparison plus explicit chart-comparison links.
   Much of the numerical evidence already exists in saved stage diagnostics.
3. **Declined:** replacing the JSON editor with common parameter fields.
4. **Safer experiments:** genuine band/smooth candidates, named retained checkpoints and
   durable final-test-use provenance. These require additional persistence contracts.

The adversarial pass moved evaluation labels/setup ahead of friendly parameter controls:
a convenient control cannot make an in-sample comparison a trustworthy validation result.
It also cut new permanent strips, mandatory stage completion, automatic chart navigation,
invented significance rules and generic quality badges.

## Validate before expanding the feature set

Give a first-time analyst these tasks without explaining the controls first:

1. Start a model and identify which rows will judge its performance.
2. Produce a pruned candidate, compare it with its parent, and keep or discard it.
3. Edit a pruning setting and identify the saved applied result versus the pending change.
4. Return to the project and identify exactly what would be finalised/exported without
   training again.

Observe wrong turns, requests for help, accidental rebuilds and whether the user can explain
their decision. Repeat with an experienced analyst. Successful completion and correct
interpretation matter more than subjective claims that the interface feels simpler.

Ralph’s subsequent requested change: per-phase enable checkboxes replace the parent-model
dropdowns. Enabled phases use the nearest earlier enabled phase; Parameters stays separate
from Training, and GBM configuration remains JSON.

## Implementation follow-up

Ralph approved the other workflow ideas on 21 November, excluding set 4. Stage states,
intentional first-fit evaluation choice, evidence labels, compatible preview comparisons,
explicit factor comparison, reversible band/smooth candidates, named checkpoints and exact
applied/final output are now implemented and reviewed. See `handover.md` for verification.
The common-fields parameter proposal remains declined. Final-test exposure history,
uncertainty, leakage review and segment/stability analyses remain later ideas.
