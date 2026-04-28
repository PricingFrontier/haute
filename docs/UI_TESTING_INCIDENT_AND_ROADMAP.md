# UI Testing Incident And Roadmap Notes

Date: 2026-04-27

## Purpose

This note records a UI issue found manually in the Rating Step editor, explains why the existing test suite did not catch it, and outlines how to approach a future roadmap for catching important UI issues before users do.

The goal is not to claim that a test suite can catch every possible UI issue. That is not realistic. The goal is to build a suite that catches the important classes of UI regressions:

- Broken core workflows.
- Real project/config shape mismatches.
- Silent data loss or disappearing controls.
- Cross-node contract failures.
- Layout or interaction failures that make the app feel broken.

## Incident Summary

In the `adjustments` Rating Step node, the "Add factor" dropdown only showed:

```text
channel_band
```

It should also have shown breakpoint-derived banding outputs from the upstream Banding node, including:

```text
proposer_age_band
vehicle_age_band
channel_band
```

The issue was visible immediately when using the app because the user expected to add rating factors against age and vehicle age bands, but the editor only exposed the categorical channel band.

## Root Cause

The Rating Step editor builds its add-factor dropdown from banding levels extracted by:

```text
frontend/src/utils/banding.ts
```

Before the fix, the extraction helper only collected rule levels from a property named:

```text
assignment
```

That works for continuous and categorical banding rules, for example:

```json
{
  "value": "broker",
  "assignment": "broker"
}
```

However, breakpoint banding rules store their level names as:

```text
label
```

For example:

```json
{
  "boundary": "27",
  "label": "20-27"
}
```

So the extractor correctly found `channel_band`, because it came from categorical rules with `assignment`, but it silently dropped `proposer_age_band` and `vehicle_age_band`, because they came from breakpoint rules with `label`.

## Why The Test Suite Did Not Catch It

The tests had a high-level shape that looked correct, but they did not include the specific production data shape that failed.

### 1. Synthetic Fixtures Were Too Tidy

The existing Rating Step tests used helper-generated Banding nodes. Those helpers represented levels using `assignment`, even when the test was only trying to assert that "banding levels exist."

That meant the tests encoded the developer's simplified model of banding rules, not the full set of shapes the real app produces.

The important missed variant was:

```text
breakpoint rules use label, not assignment
```

### 2. Variant Coverage Was Incomplete

Banding supports multiple rule modes:

- Continuous.
- Categorical.
- Breakpoints.

The Rating Step dropdown depends on all of them producing factor levels. The test suite covered the general concept of extracting levels, but it did not require each supported banding mode to prove it works end to end in the Rating Step UI.

The missing contract was:

```text
Every completed Banding output, regardless of banding mode, must be available as a Rating Step factor when it has finite levels.
```

### 3. The Tests Were Mostly Component-Local

The component tests checked that the Rating Step editor renders a dropdown when given suitable node data.

They did not test the real user journey:

```text
Open a real pipeline
Click the adjustments Rating Step node
Open the Add factor dropdown
Verify proposer_age_band, vehicle_age_band, and channel_band are present
Add a factor
Verify the table populates
```

The failure happened at a cross-node boundary:

```text
Banding config shape -> extracted levels -> Rating Step dropdown
```

Component tests can miss that if they mock or simplify the upstream node shape.

### 4. Silent Disappearance Was Treated As Acceptable

The UI did not fail loudly. It simply showed fewer options.

That is dangerous because:

- The dropdown still rendered.
- It still had one valid-looking option.
- No warning explained that other banding outputs had no detected levels.
- Tests had no reason to fail unless they explicitly expected the missing factors.

For important UI data contracts, silently shrinking the available choices should be treated as suspicious.

### 5. TypeScript Did Not Protect The Contract

The extraction code effectively treated rules as generic records:

```ts
(r as Record<string, string>).assignment
```

That bypassed the useful distinction in the type system between rule shapes:

- Continuous/categorical rules use `assignment`.
- Breakpoint rules use `label`.

The test suite therefore needed to catch the runtime behavior, because the type system had been sidestepped.

## Fix That Was Applied

The fix made the banding level extractor understand both supported level fields:

- `assignment` for continuous and categorical rules.
- `label` for breakpoint rules.

Regression coverage was added at two layers:

1. Utility-level tests for extracting breakpoint labels as levels.
2. Rating Step render-level tests proving that breakpoint banding columns appear in the add-factor dropdown alongside categorical banding columns.

This means the specific reported case is now covered:

```text
proposer_age_band
vehicle_age_band
channel_band
```

## Testing Lesson

The issue was not "no tests." The issue was that the tests used representative-looking fake data that was not representative enough.

A strong UI suite for this app needs to test:

- Real production-shaped config.
- Every supported config variant.
- Cross-node contracts.
- Full user workflows.
- Error and empty states.
- Save/reload behavior.
- Layout and interaction quality.

The practical rule should be:

```text
If a user can create it in the UI, at least one test should use that exact config shape.
```

## Roadmap Framing

If we later write a roadmap to "capture all UI issues," it should be framed carefully.

The feasible target is:

```text
Catch the important classes of UI issues before users do.
```

The infeasible target is:

```text
Guarantee that every possible UI issue is caught.
```

The roadmap should be risk-based. It should focus first on the workflows where a broken UI can block users, corrupt configuration, hide available actions, or produce misleading output.

## Roadmap Approach

### Step 1: Inventory Core User Journeys

List the workflows users rely on repeatedly. For Haute, the first set should include:

- Create and edit Banding nodes.
- Create and edit Rating Step nodes.
- Use Banding outputs as Rating Step factors.
- Add and remove tables in a Rating Step.
- Rebuild rating tables from factor levels.
- Save, reload, and verify the same config renders correctly.
- Connect nodes and verify downstream editors see upstream data.
- Configure model training.
- Configure model scoring.
- Configure optimiser and optimiser apply nodes.

Each journey should be written from the user's point of view, not from the component tree's point of view.

Example:

```text
As a pricing user, I can create age and vehicle age bands, then use both as rating factors in an adjustments table.
```

### Step 2: Define UI Invariants

For each journey, define what must always be true.

For the Banding to Rating Step journey:

```text
Every completed banding output with finite levels appears in the Rating Step add-factor dropdown.
```

```text
Adding a factor rebuilds table entries using all known levels for that factor.
```

```text
Removing a factor preserves existing values for matching remaining combinations where possible.
```

```text
If a factor cannot provide levels, the UI shows a visible warning instead of silently hiding it.
```

These invariants become test assertions.

### Step 3: Build A Config Shape Matrix

For each editor, document the supported config shapes.

For Banding:

| Mode | Rule level field | Example output |
| --- | --- | --- |
| Continuous | `assignment` | `driver_age_band` |
| Categorical | `assignment` | `channel_band` |
| Breakpoints | `label` | `vehicle_age_band` |

For Rating Step:

| Factor source | Expected behavior |
| --- | --- |
| Banded categorical output | Appears as a factor with known levels |
| Banded breakpoint output | Appears as a factor with known levels |
| Banded continuous output | Appears as a factor with known levels |
| Existing selected factor no longer found | Shows visible warning |
| Factor selected with zero levels | Fails visibly or blocks rebuild |

Every row in the matrix should have at least one test.

### Step 4: Use Production-Shape Fixtures

Create a small set of frozen fixtures that mirror real project configs.

Do not rely only on test factories. Factories are useful, but they can accidentally simplify away the exact edge case that matters.

Recommended fixture types:

- Minimal hand-written fixtures for each config mode.
- Production-shaped fixtures copied from real generated configs.
- Golden pipeline fixtures for full workflow tests.
- Malformed or partial fixtures for visible failure tests.

For this incident, the test fixture should include:

```json
{
  "banding": "breakpoints",
  "outputColumn": "proposer_age_band",
  "rules": [
    { "boundary": "27", "label": "20-27" },
    { "boundary": "34", "label": "28-34" }
  ]
}
```

### Step 5: Test At Multiple Layers

No single test layer is enough.

#### Unit And Utility Tests

Purpose:

- Validate pure logic.
- Cover config variants.
- Keep failures small and easy to diagnose.

Example:

```text
extractBandingLevels returns breakpoint labels as levels.
```

#### Component Contract Tests

Purpose:

- Render one editor with realistic props.
- Verify controls, warnings, and emitted updates.

Example:

```text
RatingStepEditor shows breakpoint banding outputs in the Add factor dropdown.
```

#### Cross-Node Integration Tests

Purpose:

- Verify one node editor can consume outputs from another node's real config shape.

Example:

```text
Banding config with categorical and breakpoint factors feeds RatingStepEditor factor options.
```

#### Browser Workflow Tests

Purpose:

- Exercise the actual app shell, graph, panel selection, and user interactions.

Example:

```text
Open pipeline -> select adjustments -> add proposer_age_band -> table entries appear -> save -> reload -> factor remains.
```

#### Visual Regression Tests

Purpose:

- Catch clipped controls, hidden buttons, unreadable dropdowns, broken layout, and responsive regressions.

These should focus on high-value panels rather than every screen.

#### Accessibility And Keyboard Tests

Purpose:

- Catch controls that cannot be reached or operated without a mouse.
- Verify labels, roles, tab order, and focus states.

This matters especially for dense editor panels.

### Step 6: Make Silent Failures Visible

Tests are much stronger when the UI fails visibly.

For this class of bug, the app should prefer:

```text
Some banding outputs have no detected levels: proposer_age_band, vehicle_age_band
```

over:

```text
Only show channel_band and say nothing.
```

The roadmap should include UI changes that turn important impossible states into visible warnings. Then the tests can assert those warnings exist.

### Step 7: Add Save/Reload Assertions

Because Haute's source of truth is disk-backed config/code, many UI tests should include a persistence step.

Example:

```text
Open node
Change editor config
Save or wait for sync
Reload pipeline
Open same node
Verify editor still renders correctly
```

This catches a different class of issues:

- UI writes one shape.
- Backend/codegen writes another.
- Reloaded UI expects a third.

### Step 8: Define CI Tiers

The roadmap should avoid making every commit painfully slow.

Recommended tiers:

#### Fast PR Gate

Run on every change:

- Typecheck.
- Lint.
- Unit tests.
- Component tests for touched areas.
- A small set of golden browser smoke tests.

#### Full PR Or Merge Gate

Run before merge, or when relevant labels/files change:

- Full frontend test suite.
- Core Playwright workflows.
- Save/reload tests.
- Selected visual regression tests.

#### Nightly Gate

Run once per day:

- Full browser suite.
- Broader visual snapshots.
- Cross-browser checks if needed.
- Larger fixture matrix.

### Step 9: Add A Regression Policy

When a user finds a UI issue, the fix should include:

1. A low-level regression test at the smallest useful layer.
2. A user-journey or component-level regression if the issue was user-visible.
3. A check for whether the failure should have been visible in the UI.
4. An update to the config shape matrix if a missing variant caused the issue.

For this incident:

- Low-level test: breakpoint labels are extracted.
- UI-level test: Rating Step dropdown shows breakpoint outputs.
- Matrix update: breakpoints use `label`.
- Possible future UI improvement: warn when completed banding outputs have no detected levels.

## Example Roadmap Structure

The later roadmap could be written in phases like this.

### Phase 0: Incident Backfill

Goal:

Cover known gaps discovered from recent UI issues.

Deliverables:

- Add regression tests for Rating Step breakpoint factors.
- Add a config shape matrix for Banding and Rating Step.
- Identify other editors with similar "synthetic fixture only" coverage.

### Phase 1: Golden Workflow Slice

Goal:

Build one complete test slice from real user action to persisted config.

Recommended first slice:

```text
Banding -> Rating Step -> save/reload
```

Deliverables:

- Real-shape pipeline fixture.
- Playwright workflow for opening the pipeline and editing `adjustments`.
- Assertions for dropdown options, table generation, and reload behavior.
- Screenshot baseline for the Rating Step editor.

### Phase 2: Node Editor Contract Matrix

Goal:

Each node editor has explicit config variant coverage.

Deliverables:

- Matrix for every editor.
- Component tests for each supported config shape.
- Tests for partial/invalid configs where the UI should show warnings.

### Phase 3: Cross-Node Integration Tests

Goal:

Protect the contracts between adjacent node types.

Deliverables:

- Banding to Rating Step.
- Data Source to Transform editor schema display.
- Model Training to Model Scoring.
- Scenario Expander to Optimiser.
- Optimiser to Apply Optimisation.

### Phase 4: Browser Workflow Suite

Goal:

Catch issues that only appear in the full app shell.

Deliverables:

- Add node.
- Connect nodes.
- Select node.
- Edit config.
- Save.
- Reload.
- Verify graph and panel state.

### Phase 5: Visual And Accessibility Regression

Goal:

Catch UI issues that are technically functional but visually or interactively broken.

Deliverables:

- Screenshot coverage for key panels.
- Desktop and mobile viewport checks.
- Keyboard navigation checks for dense editors.
- Basic accessibility assertions for form controls.

### Phase 6: Ongoing Quality Loop

Goal:

Keep the suite aligned with real user behavior.

Deliverables:

- Regression policy for every user-found UI bug.
- Test review checklist.
- Fixture review checklist.
- CI tier ownership.
- Periodic audit of skipped or brittle tests.

## Roadmap Writing Checklist

When writing the future roadmap, answer these questions for each planned area:

1. What user workflow are we protecting?
2. What would a user see if this broke?
3. What real config shapes are involved?
4. What data contracts cross component or node boundaries?
5. What should fail loudly instead of silently disappearing?
6. What is the smallest useful unit test?
7. What component test proves the editor behavior?
8. What browser test proves the user journey?
9. Does the workflow need save/reload coverage?
10. Should this have visual regression coverage?
11. Which CI tier should run it?
12. How will future bugs update the matrix?

## Key Principle

The test suite should not only prove that components render with valid props. It should prove that the app's real user workflows continue to work with the real config shapes Haute produces.

For this project, the highest-value improvement is to connect tests to production-shaped fixtures and cross-node workflows. That is the layer most likely to catch the kind of issue that a user finds quickly in the UI.
