# Frontend UI quality roadmap

**Status:** Active

**Current as of:** 2026-07-20

**Owning queue:** [Frontend and canvas](components/frontend-canvas/README.md)

## Outcome

The browser UI protects the user journeys and persisted configuration shapes
most likely to block pricing work, hide a meaningful choice, or make a saved
project appear to have changed. The suite remains deterministic and useful:
it makes important failures observable without promising to prove every visual
or interaction detail on every commit.

## Verified baseline

The reported Rating Step issue is fixed. The typed Banding-level utility now
recognises `assignment` levels for continuous and categorical rules and
`label` levels for breakpoint rules. Focused utility and Rating Step component
tests cover the original age, vehicle-age, and channel example.

The frontend already has a substantial Vitest suite; deterministic,
single-worker Playwright infrastructure with isolated projects and retained
failure artifacts; real training and optimiser workflows; save/reload
coverage; PR Chromium E2E plus a Firefox smoke project; nightly shuffled
Vitest execution; and scheduled browser performance checks. This roadmap must
extend those foundations rather than introduce a competing test harness.

The current optimiser browser test starts from a complete seeded config, runs
the real solver, selects one frontier point, saves a local artifact, and opens
an Apply Optimisation node that can preview it. Component tests separately
cover constraint controls, individual/frontier switching, auto-range results
and failures, frontier navigation, summary/rates displays, local save, MLflow
actions, and Apply Optimisation source modes. No browser test currently edits
or persists optimiser constraints/frontier settings, exercises auto-range or
validation feedback, or proves an MLflow-backed apply flow. Likewise, the
Playwright suite has no checked-in visual snapshots and no keyboard/focus
journey for these editors; existing accessibility assertions are primarily
component-local.

The remaining gap is not a lack of test layers. It is the absence of a
maintained, risk-based contract tying real configuration variants to the
smallest suitable utility, component, browser, visual, and accessibility
checks. In particular, no browser journey yet proves the complete Banding to
Rating Step persistence flow, and a configured Banding output with no detected
levels can still disappear from the factor choices without a dedicated,
named warning.

## Remaining milestones

### 1. Maintain the user-journey and configuration-shape matrix

**Scope:** Establish one reviewed matrix of high-risk user journeys, their
cross-node contracts, their fixture source, and their test tier. Start with
Banding to Rating Step: continuous, categorical, and breakpoint outputs;
mixed output columns; zero-level factors; malformed and partial Banding
configuration; and persisted Rating Step tables. Expand it only when an audit
identifies an adjacent high-risk flow, rather than producing an unowned list
of every component.

The matrix is a maintained engineering asset, not a second roadmap. Each row
must identify the user-visible failure, exact persisted shape, fixture path,
owning test, layer, and owner. Rows owned by another roadmap, such as Edge Join
insertion, are references to that owner rather than duplicate test work here.

**Tests first:**

- Turn every initial matrix row into a named test at the lowest layer that can
  prove its contract, and add a component or browser assertion when the user
  can observe the result there.
- Introduce a fixture convention with minimal hand-written shape fixtures,
  frozen production-shaped fixtures, and deterministic browser project
  fixtures. Factories may support these fixtures but cannot be the sole proof
  of a production configuration variant.
- Review fixtures whenever a format, code generator, or user-found bug changes
  a supported shape; add the new shape to the matrix before closing the fix.

**Acceptance criteria:**

- The matrix identifies an owner, fixtures, and the intended test tier for
  every initial Banding-to-Rating variant.
- Continuous, categorical, and breakpoint levels are independently proved
  through their real persisted shapes, including mixed inputs and partial or
  malformed input that the editor must render safely.
- A reviewer can determine why a fixture is representative without reading a
  test factory's implementation.

### 2. Add deterministic cross-node browser journeys

**Scope:** Add a Banding-to-Rating browser workflow that creates or opens
continuous, categorical, and breakpoint outputs; adds factors to a Rating
Step; rebuilds the table; saves; reloads; and verifies the same factors,
entries, and editor state. Then audit existing browser coverage and add only
the remaining high-risk cross-node slices.

The browser work starts from this evidence map:

| Contract | Browser evidence today | Remaining browser proof |
| --- | --- | --- |
| Banding to Rating Step | None | Open a production-shaped mixed-mode fixture; discover all three factor types; rebuild; save; reload |
| Optimiser solve and local apply | Real seeded solve, one frontier selection, local artifact save, and Apply Optimisation preview | Prove point changes update the visible result identity and summary/detail values; retain the saved-point/apply identity assertion |
| Optimiser configuration | None; controls are component-tested | Edit a constraint; expose and recover from a named invalid state; switch Individual/Efficient frontier; run auto-range; save; reload |
| Optimiser MLflow apply | None; actions and source modes are component-tested | With deterministic local route/service fixtures, prove selected-point log identity, run or registered-model selection, Apply Optimisation metadata, and save/reload agreement |

The optimiser browser slice must therefore cover configuration and persistence,
not repeat every component assertion. One configuration journey should edit a
constraint, switch from Individual to Efficient frontier, confirm missing
range feedback, run auto-range, and verify the returned per-constraint bounds
before and after save/reload. Extend the existing real result journey so a
frontier chart or stepper selection changes a named point and visible values,
then prove the saved local artifact and apply preview refer to that same point.
Use a deterministic MLflow boundary fixture for the MLflow-backed flow; browser
CI must not depend on a live remote service.

**Tests first:**

- Use a small, local, production-shaped fixture and stable semantic locators;
  do not depend on coordinates, live services, or a shared project state.
- Assert the actual factor options, table entries after rebuild, saved config,
  and state after reload rather than private component implementation.
- For each optimiser gap, add an independently diagnosable workflow assertion
  that proves the visible result and the persisted artifact/config agreement.

**Acceptance criteria:**

- The Banding-to-Rating workflow runs reliably in the normal browser suite and
  fails on missing breakpoint factors, failed table rebuild, or persistence
  drift.
- The Banding-to-Rating fixture contains one continuous `assignment`, one
  categorical `assignment`, and one breakpoint `label` output; the test names
  all three options, asserts the expected Cartesian entries after rebuild, and
  proves an edited relativity survives reload.
- The optimiser configuration journey proves constraint editing, missing-range
  feedback, Individual/Efficient frontier switching, auto-range values, and
  save/reload. The result journey proves selected-point identity across the
  chart or stepper, visible result values, local artifact save, and apply
  preview. The deterministic MLflow journey proves the selected run/model and
  point identity across log, apply metadata, and reload.
- Browser tests clean up through the established project-isolation mechanism
  and remain repeatable locally and in CI.

### 3. Make important missing data visible

**Scope:** Define an explicit visible-failure contract for editors whose
upstream configuration is incomplete or cannot produce a required choice.
Begin with Rating Step: when a configured Banding output contributes no usable
levels, show a named warning that identifies the output and explains the next
action. Do not show that warning merely because the project has no Banding
node.

Classify the condition from the typed Banding config rather than inferring it
from a shortened dropdown. A warning is warranted only for a loaded Banding
factor with a non-blank `outputColumn` and no valid rule levels, or for a
selected Rating Step factor whose previously resolvable source is confirmed
missing. No warning is warranted for a project with no Banding node, an empty
new-factor draft, a blank output-column draft, a factor that still has usable
levels, or a transient loading state. Multiple affected outputs should be
reported together without hiding healthy choices.

**Tests first:**

- Add utility and component tests for zero-level, malformed, partial, and
  absent Banding situations, loading state, a stale selected factor, and
  multiple configured outputs.
- Assert an accessible, named warning for configured outputs with no levels;
  assert no warning and no false problem state for each normal/draft/loading
  case above.
- Add the browser assertion only after the component contract is stable.

**Acceptance criteria:**

- A user never has to infer a missing configured Banding output from a
  shortened dropdown alone.
- Warning text distinguishes a genuine upstream configuration problem from the
  normal absence of Banding, a draft, and loading; it names every confirmed
  affected output and does not hide otherwise usable factors.
- The contract is reusable for comparable cross-node choice loss without
  creating silent fallback values.

### 4. Add targeted visual and interaction assurance

**Scope:** Establish stable visual regression checks for a deliberately small
set of high-value panels, starting with the dense Banding and Rating Step
editing states and the optimiser result/apply surface after the workflow
contracts settle. Capture desktop plus a deliberately selected narrow viewport
that reflects supported use; do not label an arbitrary viewport as mobile
support. Define a browser keyboard/focus/label journey for the same controls
and make a deliberate decision on the automated accessibility method.

Limit the initial visual set to three stable states: a mixed-mode Banding
editor, a rebuilt three-factor Rating Step table, and an optimiser result with
a selected frontier point (including the apply state only if it is stable in
the same fixture). Use the existing Desktop Chrome viewport plus one documented
supported narrow width. The keyboard journey should protect the controls used
by those same workflows rather than attempt a generic crawl of the whole app.

**Tests first:**

- Stabilise data, fonts, animations, viewport, and screenshots before adding a
  visual baseline; review each snapshot for user-significant change rather
  than accepting it mechanically.
- Exercise tab order, visible focus, labels, keyboard activation, and escape
  or cancellation where the chosen workflow exposes them.
- Record the accessibility approach and its coverage boundary before adding a
  generic scanner. This roadmap does not mandate Axe or any other tool without
  that decision.

**Acceptance criteria:**

- Visual checks are deterministic and cover stable, high-value states at both
  selected viewport widths, whose pixel dimensions and support intent are
  recorded with the baselines.
- The chosen keyboard journey can complete the protected editor actions and
  exposes focus and labels to assistive technology.
- Accessibility automation has an explicit, reviewable purpose; it is not a
  broad, flaky substitute for interaction tests.

### 5. Keep CI risk-based and regressions cumulative

**Scope:** Keep the full PR browser E2E suite as the authoritative normal
workflow gate. Add a separate smoke, visual, or nightly cross-browser lane
only when its measured risk reduction justifies its cost and ownership. Retain
the existing nightly shuffle and browser-performance lanes. Adopt a UI-bug
regression policy: every user-found issue gets the smallest useful regression,
an observable user-level check where appropriate, a fixture/matrix review, and
a decision on whether the UI should have failed visibly.

**Tests first:**

- Before changing a tier, measure the candidate tests' duration, stability,
  failure diagnostics, and overlap with the full PR E2E lane.
- Add a regression test before a UI bug fix and prove it fails for the original
  defect; add a broader workflow test only when the defect crossed that
  boundary.
- Test tier configuration itself where practical, including selection rules
  that keep visual and cross-browser jobs intentional rather than accidental.

**Acceptance criteria:**

- Every UI test has a clear owner and tier rationale; no reduced lane claims to
  replace the existing full PR E2E gate.
- Any new smoke, visual, nightly, or cross-browser lane has a documented
  failure owner and a reproducible artifact path.
- User-found UI regressions leave behind durable coverage and an updated
  fixture/matrix entry instead of a one-off assertion.

## Non-goals

- Guaranteeing that automation catches every possible UI, browser, or layout
  defect.
- Replacing established Vitest, Playwright, project-isolation, or CI
  infrastructure with a parallel harness.
- Mandating a blanket accessibility scanner, a device matrix, or snapshots for
  every component without a demonstrated user-risk case.
- Duplicating Edge Join insertion, role-handle, and feature E2E work; that is
  owned by the [Edge Join completion roadmap](edge-join-completion.md).
- Owning generic backend contract, persistence, execution, or artifact
  hardening; those cross-cutting non-UI concerns belong to the
  [test-suite hardening roadmap](test-suite-hardening.md).

## Dependencies and sequencing

Start by publishing and testing the matrix and fixture convention, because it
defines the contract for the Banding-to-Rating browser slice. Complete the
visible-failure contract before asserting it end to end. Add visual and
accessibility coverage only after the core workflow has stable fixtures and
semantic locators. Tier any new CI lane last, using evidence from the
implemented tests rather than estimates. Coordinate with Edge Join only where
the shared user-journey inventory needs an entry; its specialized insertion
workflow remains separate.

## Completion and retirement criteria

Retire this roadmap when the maintained risk matrix and fixture convention
cover the initial Banding-to-Rating and audited optimiser journeys; the full
Banding-to-Rating browser flow proves factor discovery, table rebuild, save,
and reload; configured zero-level Banding outputs produce a named visible
warning without false warnings in projects with no Banding; selected panels
have deterministic visual and keyboard/focus coverage; accessibility strategy
and CI ownership are explicit; and the regression policy is applied to new
UI bugs. Thereafter, ordinary feature and regression tests, not this roadmap,
own the continuing coverage.
