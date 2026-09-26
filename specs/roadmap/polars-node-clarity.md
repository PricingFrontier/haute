# Polars node clarity roadmap

## Scope

The low-code step editor on the Polars node, as a first-time pricing analyst
meets it: the `Add step` menu, the step cards and their forms, the column
completion boxes, the formula box and the generated code panel. Current
behaviour is specified in
[the frontend node editors specification](../frontend-node-editors/high-level.md)
and its [low-level specification](../frontend-node-editors/low-level.md);
the server renders the steps to code in `src/haute/_polars_steps.py`. The
canvas, the node palette, the node panel's layout and the data preview are out
of scope.

This roadmap improves the experience of what the editor already does: how
intuitive it is, how it looks, and where it tells the analyst what happened.
The step kinds and what each can compute stay as they are. Proposals that add
capability, such as new formula functions, multi-branch values, per-step row
counts or data previews, switching a step off and step notes, are out of scope,
and `PNC-07` is deferred for the same reason. Labels use plain English, with
the Polars name beside them where a technical name helps, since the generated
code already teaches those names; SAS and Excel terms are not used.

The packages come from two sources on `ui-changes-5`. The first is a scripted
walkthrough of the editor on 25 September 2026, made while recording a
marketing clip of a claims node built from the quote's `proposer_claims`
table and judged from screenshots. The second is a read-only review of the
step editor's code on 26 September 2026 from five angles: first-hour analyst
tasks, visual design, where feedback appears, prior art in step-based data
tools, and keyboard and formula mechanics. Claims of faulty behaviour were
checked against the code; visual judgements come from the components and
theme tokens, not from screenshots. None of this is user research. Each
package states the behaviour an analyst meets today, the change, and the test
that proves it.

The structure of the editor already works: data reads top to bottom (input,
`Start from`, the numbered steps, then the generated code), the step menu
uses plain English grouped into Rows, Columns, Combine, Values and Code,
column suggestions come from the real upstream schema, and the generated code
with the one-way switch to code makes the result easy to trust and easy to
leave. The packages below remove friction inside that structure; none of them
changes it.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PNC-01 | Planned | P2 | A suggestion is taken only when the analyst chose it, from a list whose highlight is visible and shows each column's type. |
| PNC-08 | Planned | P2 | Keys pressed in the step editor act on the steps, never on the canvas, and a new step's first field takes focus. |
| PNC-09 | Planned | P2 | A failed Polars transform run names the step that caused it, on that step's card, with the run's message. |
| PNC-10 | Planned | P2 | The code panel never presents code the steps no longer produce, and an unfinished step says what it needs. |
| PNC-11 | Planned | P2 | A column name that does not exist at its step is marked where it was typed, with the closest name offered. |
| PNC-12 | Planned | P2 | Join keys complete from the table they belong to and are entered as pairs. |
| PNC-02 | Decision | P2 | `Add column` opens in the mode most analysts want. |
| PNC-13 | Planned | P3 | Step cards have an aligned, readable header whose actions stay in place. |
| PNC-14 | Planned | P3 | A collapsed card reads as the step it describes, with the columns it adds or removes. |
| PNC-15 | Planned | P3 | The generated code is coloured like code mode and linked to its cards both ways. |
| PNC-16 | Planned | P3 | Forms read as sentences, in plain words, with quiet secondary buttons. |
| PNC-17 | Planned | P3 | A value is one control, with its type shown as a marker. |
| PNC-18 | Planned | P3 | The formula box shows its example, its functions and where a formula went wrong. |
| PNC-19 | Planned | P3 | `Add step` can be searched, shows what each kind does, and helps an empty node start. |
| PNC-03 | Planned | P3 | An aggregation reads as one sentence, name = function of column, and names itself. |
| PNC-04 | Planned | P3 | A condition's value starts in the column's type. |
| PNC-05 | Decision | P3 | Options the simple case never needs sit behind a disclosure. |
| PNC-06 | Planned | P3 | The generated code lays long calls out one argument per line. |
| PNC-07 | Deferred | P3 | A formula can compare, so a flag such as `amount_paid > 5000` is one formula. |

`PNC-01` and `PNC-08` to `PNC-11` remove traps an analyst meets in their
first minutes and are each small. `PNC-13` to `PNC-15` make the largest
visible difference to how the editor looks.

## Planned improvements

### PNC-01 — A suggestion is taken only when the analyst chose it
**Why:** The column boxes in the step editor (the chip lists such as
`Keep only` and `Group by`, and the single-column pickers such as an
aggregation's `of column`) open their suggestion list on focus. An empty box
lists every column with the first one active, and Tab accepts the active entry
whenever the list has one, so tabbing through an empty `Group by` adds the
first column as a chip. The list leaves out an exact match but keeps longer
names, so tabbing past a picker that holds `premium` turns it into
`premium_net` when such a column exists. The formula box lists names only once
a word is typed before the caret, with the same exact-match rule, so typing
`premium` and pressing Tab to move on turns the word into `premium_net`. In
the column boxes Enter, the usual way to accept an autocomplete, instead
commits what was typed: `quo` and Enter, with `quote_id` listed, adds a chip
named `quo`, and leaving the box does the same. The mistake surfaces later as an
unknown-column error, away from where it was made. The active row is hard to
see: it is drawn in `--chrome-hover` (#161a26) on the list's `--bg-elevated`
(#1a1d2b), one shade darker than the list. Suggestions are bare names,
although the upstream schema passed to the editor carries each column's type;
`PolarsStepsEditor` keeps only the names.

**Plan:**
- No row is active until the analyst types or presses Down; typing makes the
  first match active. Focusing a column box may still show the list, with
  nothing active.
- Tab and Enter accept the active row. With no row active, Tab moves focus on
  and Enter commits the typed text (in the formula box, the formula), so a
  name that is not in the schema yet (for example a column an unsaved upstream
  step will create) can still be entered deliberately. Leaving the box keeps
  committing the draft.
- A draft, or in the formula box the word being completed, that exactly
  matches a known name shows that name as matched with no row active, so Tab
  and Enter leave it unchanged.
- The active row has the `--accent-soft` background with an accent bar at its
  left edge, and the typed part of each name is bold.
- Each suggestion shows its column's type at the right, coloured by
  `getDtypeColor` as the data preview colours types; a column whose type is
  not known shows none, and a column made by an earlier step shows `new`.
- The types follow one rule. They come from the start input's own columns
  (the edge that feeds it), not from the merged list of every input, so two
  inputs sharing a column name cannot disagree; in frame mode they come from
  the node's frame. `columnsBeforeStep` carries them forward: a rename keeps a
  column's type, a cast sets it, a group-by key keeps it, and a step that
  creates or replaces a column leaves that column untyped. Columns from
  another input stay untyped until `PNC-12` tracks them. `StepFormContext`
  carries the resulting type beside each name.
- Update the completion text in both specifications (the high-level "Tab or a
  click completes the name ... Enter or leaving the box keeps what was typed"
  and the low-level "Tab accepts").

**Acceptance:** Component tests on a chip list, a single-column picker and the
formula box: focusing an empty column box and pressing Tab adds nothing and
moves focus; typing `quo` with `quote_id` listed and pressing Enter commits
`quote_id`; a picker holding `premium`, with `premium_net` available, keeps
`premium` through focus and Tab; typing `premium` in the formula box and
pressing Tab or Enter leaves `premium`; with the list closed, Enter commits the
typed text unchanged. Unit tests on `columnsBeforeStep`: a column's type
follows the start input when another input has the same name with a different
type, changes when the start input changes, is set by a cast and kept by a
rename, and is absent after an `Add column` that replaces it. Up, Down and
Escape behave as before.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/fields.tsx::CompletingInput`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::CompletionList`;
`frontend/src/panels/editors/polarsSteps/useCompletionList.ts::useCompletionList`;
`frontend/src/panels/editors/polarsSteps/completion.ts::completionMatches`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::FormulaField`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::StepFormContext`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`;
`frontend/src/panels/editors/polarsSteps/derivedColumns.ts::columnsBeforeStep`;
`frontend/src/panels/NodePanel.tsx::collectColumnsFromEdges`;
`frontend/src/utils/dtypeColors.ts::getDtypeColor`.

### PNC-08 — Keys pressed in the step editor act on the steps
**Why:** The canvas shortcut handler counts only text inputs, text areas and
the code editor as typing. Delete or Backspace pressed while a step card's
select, disclosure button or move button has focus therefore reaches the
canvas handler, which deletes the selected nodes: when the panel was opened by
selecting the node, that is the node being edited. Ctrl+C, Ctrl+V, Ctrl+A and
Ctrl+G pressed there copy, paste, select and group nodes the same way. This
was read from the code, so the first step is a failing test that confirms the
node is still selected while its panel is open. Focus also does not follow a new step: the
new card opens, but the `Add step` menu's close effect returns focus to the
`Add step` button, and `StepFormContext.firstFieldId` is set by three forms and
never focused. Reordering by keyboard means tabbing to a card's move buttons;
Rating tables and Banding factors already move with Alt+Up and Alt+Down.

**Plan:**
- Canvas shortcuts that act on the graph stop at the step editor whatever
  control has focus: Delete and Backspace (delete nodes), Ctrl+C and Ctrl+V
  (copy and paste nodes), Ctrl+A (select every node) and Ctrl+G (group into a
  submodel). The shortcuts that act on the document or the window stay global:
  Ctrl+S, undo and redo, Ctrl+K, Ctrl+1, `?`, and Escape once no open card has
  handled it.
- Adding a step moves focus to its first field; every form names that field
  through `firstFieldId`. For `Add column` that stays `Column name`.
- Alt+Up and Alt+Down on a card header move the step and keep focus on it, as
  `SearchableItemList` does for its rows.
- Describe the keys in the high-level specification.

**Acceptance:** Tests with the real canvas shortcut handler mounted and a step
card's select focused, each failing against today's code first:
- with the node selected, Delete, Backspace and Ctrl+A leave the graph and the
  selection unchanged;
- Ctrl+C leaves an already-populated node clipboard unchanged and shows no
  toast;
- with a populated clipboard, Ctrl+V adds no node;
- with two nodes selected, Ctrl+G opens no dialog and shows no toast;
- Ctrl+S still saves and Ctrl+Z still undoes.
Adding `Filter rows` leaves focus in its column box. Alt+Down on a card header
moves the step down and keeps focus on its header. Escape still collapses an
open card.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useKeyboardShortcuts.ts::useKeyboardShortcuts`;
`frontend/src/panels/editors/polarsSteps/StepCard.tsx::StepCard`;
`frontend/src/panels/editors/polarsSteps/AddStepMenu.tsx::AddStepMenu`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::StepFormContext`;
`frontend/src/panels/editors/shared/SearchableItemList.tsx::SearchableItemList`.

### PNC-09 — A failed run names the step that caused it
**Why:** A stepped node's code builds a lazy Polars plan, so a missing column
or a type mismatch is raised when the plan's schema is resolved, after the
code has run. `_exec_user_code` records a line only for an exception raised
while the code runs, and `_extract_error_line` finds no line in a Polars
message, so the run reports no error line. The editor then badges no card,
"Go to error" never appears, and the message is shown only in the data
preview. When a line is known, the card's badge says "Failed when the pipeline
ran" rather than what failed.

**Plan:**
- The work happens only when the walker records a failure with no error line
  for a Polars transform in input mode, and only when its steps contain no
  `Free code` step. The steps of a transform start from its input frames,
  which the run has already built. Frame-mode surfaces (Data Input, External
  File, Scenario Expander, Rating Step, Model Score, Explore's pane) are left
  out. Their steps start from a frame the node builds itself, such as the
  opened source or the rated frame, and replaying would mean rebuilding it.
- Free code can replace the frame, so an earlier step's broken plan need not
  be what failed, and replaying it would run authored code a second time.
- The executor first resolves the schema of the node's full plan. Only if that
  also fails is the error a schema error. It then renders each prefix of the
  steps with `render_polars_steps`, executes it through `_exec_user_code` with
  the input frames the run already built, and resolves its schema. These are
  the renderer's own closed-vocabulary statements; no rows are read. The first
  prefix whose schema fails names the step, and the error line becomes the
  first line of that step's range in the node's code.
- The original exception and its message are kept unchanged; only the line is
  added. A successful run does no extra work. An error that only data can
  raise, such as a strict cast meeting a bad value, resolves as a schema and
  keeps today's behaviour.
- The card's badge shows the run's message in the warning tone, and the code
  panel's "Go to error" opens that card. Update the runtime-error sentences of
  the high-level specification.

**Acceptance:** Backend tests:
- a stepped transform whose filter names a missing column fails with the same
  message as today and an error line inside that step's range;
- a strict-cast data error is still reported without a line;
- a node with a `Free code` step gets no located line and runs no prefix;
- a Data Input whose steps name a missing column gets no located line and runs
  no prefix;
- a successful run resolves no prefix schema, checked by counting renders or
  executions.

A frontend test: a run error with a line badges its card with the message text
and "Go to error" opens it.

**Dependencies:** None.

**Evidence:** `src/haute/_user_exec.py::_exec_user_code`;
`src/haute/_graph_walker.py::_record_failure`;
`src/haute/_execute_lazy.py::_extract_error_line`;
`src/haute/_builders.py::_build_transform`;
`src/haute/_polars_steps.py::render_polars_steps`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`.

### PNC-10 — The code panel never presents stale code as current
**Why:** When a render fails, `useRenderedSteps` keeps the previous code and
the code panel shows it at full strength; it fades only while a render is
pending. Render problems stay hidden until a run has failed on the node, so an
analyst who adds a group-by and leaves an aggregation unnamed sees code
without that step and nothing saying why. The high-level specification already
says a validation failure's generated code is unavailable, and the panel does
not do that.

**Plan:** Keep the rule that a step being built is not an error yet: nothing
turns red before a run fails. While the latest render fails, the code panel
dims the last good code and labels it as out of date. What the note says
depends on the failure:
- A failure that names a step gets a neutral note with the step and the
  server's message ("Step 3 isn't finished: an aggregation needs a name"), and
  that card shows the same need as a muted line under its summary.
- A failure that names no step (a list-level validation message, or a render
  request that could not reach the server) gets a neutral note with the
  message ("The code could not be rendered: …") and is attached to no card.

After a run has failed, today's red display applies. Update the high-level
specification's "a render problem is not shown at all" and its
unavailable-code sentence to describe this.

**Acceptance:** Component tests:
- a render failure naming a step dims the code, shows the note naming that
  step and the card's muted line, and shows no alert or danger colour while no
  run has failed;
- a failed render request keeps the last code dimmed, shows the unattributed
  note and marks no card;
- once a run has failed, the existing error display appears;
- the next successful render clears the note and the dimming.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts::useRenderedSteps`;
`frontend/src/panels/editors/polarsSteps/GeneratedCodePanel.tsx::GeneratedCodePanel`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`;
`frontend/src/panels/editors/polarsSteps/StepCard.tsx::StepCard`.

### PNC-11 — An unknown column name is marked where it was typed
**Why:** Chips and column boxes accept any text, and a chip holding a typo
looks exactly like a real one; the formula box reads any unknown name as a
column. Moving a step above the step that creates its column, or deleting that
step, is just as silent. Each mistake surfaces as a failed run.

**Plan:**
- `columnsBeforeStep` also reports whether the list is complete, meaning every
  column that can exist at the step is in it. The list is incomplete while the
  upstream schema has not loaded, and after any of these earlier steps:
  - `Free code`;
  - `Append inputs`;
  - an aggregation over every column of a type, whose output names are only
    known when the plan runs;
  - a join, until `PNC-12` defines its output columns.
- Where the list is complete, a name not in it gets a dashed amber border and
  a note: "`quot_id` isn't in the data at this step. Did you mean
  `quote_id`?", with a button that replaces it with the closest name. The
  formula box lists its unknown names in its note.
- A collapsed card shows the note too, so a reorder marks the affected card at
  once.
- Amber rather than red, because a step being built is not an error yet.
  Nothing is marked where the list is incomplete.

**Acceptance:** Unit tests on `columnsBeforeStep`: the list is complete for
plain steps, and incomplete after each earlier step listed above and before
the upstream schema loads. Component tests:
- a `Group by` chip `quot_id` with `quote_id` upstream is marked and the
  button replaces it;
- no mark appears on the same chip after a `Free code` step, or on
  `premium_sum` after an aggregation over every `Float64` column;
- after a reorder puts a step above the step that creates its column, the
  collapsed card shows the note;
- a formula naming an unknown column lists it in the note.

**Dependencies:** `PNC-01`, which changes the same boxes. `PNC-12` makes the
list complete after a join.

**Evidence:** `frontend/src/panels/editors/polarsSteps/fields.tsx::ColumnListField`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::ColumnPicker`;
`frontend/src/panels/editors/polarsSteps/derivedColumns.ts::columnsBeforeStep`;
`frontend/src/panels/editors/polarsSteps/formula.ts::parseFormula`;
`frontend/src/panels/editors/polarsSteps/summary.ts::summarizeStep`.

### PNC-12 — Join keys come from the table they belong to
**Why:** The join form's `Right keys (joined input)` suggests the same list as
the left keys: the node's merged column list, into which
`collectColumnsFromEdges` puts every connected input's columns. The right keys
can therefore suggest columns the joined table does not have, and every step
before the join already suggests the joined table's columns. The keys are two
separate chip lists paired only by position. The join types are listed by
their Polars names alone, with no hint of which rows each keeps. With one
input connected, a new join silently picks the start input, a join of the
frame with itself.

**Plan:**
- The form context carries each input's columns. Left keys complete from the
  frame at this step and right keys from the chosen input. Steps before the
  join suggest only their own frame's columns.
- After the join, the suggestions are the join's real output columns, which
  follow Polars' rules for the renderer's call (`left_on`, `right_on`, `how`,
  `suffix`). Checked on Polars 1.44:
  - `inner` and `left` drop the right keys;
  - `full` keeps both sets of keys, the right ones suffixed when they clash;
  - `right` keeps the right keys and drops this frame's;
  - `semi` and `anti` keep only this frame's columns;
  - `cross` keeps every column;
  - any other clashing name takes the suffix.

  The low-level specification gets the table for every join kind, with the
  same key names on both sides and different ones.
- Keys are entered as pairs, `[left key] = [right key]`, with the right key
  filled in when the joined input has a column of the same name. The step
  still saves `leftOn` and `rightOn`.
- Each join type keeps its Polars name and says which rows it keeps, for
  example `left: every row here, with matches added` and `anti: rows here
  with no match`.
- With one input connected, the join input starts unset with the hint
  "Connect the table to join on the canvas".

**Acceptance:** One shared fixture lists the output columns for every join
kind, with same-named and differently named keys and a clashing non-key
column. A backend test asserts the fixture equals the schema Polars gives the
rendered code. A frontend test asserts `columnsBeforeStep` after the join
equals the fixture exactly, including the columns that are absent. Component
tests:
- right-key suggestions list only the joined input's columns;
- a key with the same name on both sides is paired automatically;
- each join type option carries its description;
- with one input connected, the join input starts unset with the hint;
- a saved join round-trips unchanged.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/forms.tsx::JoinForm`;
`frontend/src/panels/NodePanel.tsx::collectColumnsFromEdges`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::JOIN_HOW`;
`frontend/src/panels/editors/polarsSteps/derivedColumns.ts::columnsBeforeStep`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`;
`src/haute/_polars_steps.py::_render_join`.

### PNC-02 — `Add column` opens in the mode most analysts want
**Why:** A new `Add column` step opens with `Computed as` set to `Value`
and the value set to an empty column reference, which copies an existing
column. Deriving a column, the common reason to add one, needs `Computed as`
changed to `Formula` (or `If-then`) first. An analyst who starts typing
arithmetic into the `Value` column box gets a column-name completion list
instead of a formula box, with no hint that another mode exists. The formula
box has no example placeholder today: its example is a tooltip on the box and
on an info icon.

**Plan:** Decide between two options. (a) Open new steps in `Formula`, whose
empty box shows immediately what to type once `PNC-18` gives it a placeholder;
`Value` stays one choice away. (b) Replace the closed select with a small
segmented control showing the four common modes (`Value`, `Formula`,
`Function`, `If-then`) and a `More` menu holding `Window` and `Join text`, so
the common choice is visible and every existing mode stays reachable.
Recommendation: (a), since a formula that is only a column name already
behaves as `Value`, and it is the smaller change. Persisted steps are
unaffected either way; only the default for a newly created step changes.
Either way, focus on a new card goes to `Column name` first (`PNC-08`), since
the name comes first in the form.

**Acceptance:** Creating an `Add column` step from the menu opens its card
with `Column name` focused and, below it, the formula box showing its
placeholder (option a) or all six modes reachable, four of them visible
(option b); a test on the step factory pins the new default, and existing
saved steps render as before.

**Dependencies:** `PNC-18` for option (a); `PNC-08` for focus.

**Evidence:** `frontend/src/panels/editors/polarsSteps/catalogue.ts::createStep`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::defaultExpr`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::WithColumnForm`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::EXPR_TYPES`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::FORMULA_EXAMPLE`.

### PNC-13 — Step cards have an aligned, readable header
**Why:** The card header puts a white 10 px number on the Transform accent
(#56B4E9), a contrast of about 2.3:1 against the 4.5:1 WCAG AA asks of text.
The collapsed summary starts at the card's edge, under the number rather than
under the title. A card renders only the move buttons that apply, so the
chevron and the delete button shift between the first, middle and last cards.
The header's icons come in four sizes and the chevron has a heavier stroke.
Neither the card nor its header reacts to hover. The open card's accent border
sits beside the focus ring's different blue. Cards and every control inside
them share the `--bg-input` surface, so fields show only as faint outlines.
The trace panel's step card lays out the same kind of header more cleanly.

**Plan:** Rebuild the header on the trace card's pattern: the chevron at the
left, the step number in muted monospace instead of a filled badge, an icon
for the step kind (shared with `PNC-19`), then the label; a fixed-width action
group (move up, move down, delete) that keeps its slots on every card, with an
inapplicable move disabled rather than removed, and shows fully on hover or
focus; and a grip that marks the drag area. The summary aligns with the label.
Cards sit on `--bg-elevated`, so their inputs read as set into them, and the
open card's border uses the focus-ring colour. The accessible names ("Step 3:
Filter rows", "Move Step 3: Filter rows up") and the keyboard behaviour stay as
they are.

**Acceptance:**
- Component tests: a card keeps its accessible name; the first, a middle and
  the last card each render the same three action slots, with the
  inapplicable move disabled; the number is text in a muted token, not a
  filled badge; the summary sits in the same column container as the label,
  so they align by construction; the existing step editor tests pass
  unchanged.
- A unit test on the theme tokens: the number, the label and the summary each
  reach 4.5:1 against `--bg-elevated`.
- A Playwright screenshot of a three-step node in the narrow canvas-assurance
  viewport, with one card open, one hovered and one focused, joins the visual
  baselines.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/StepCard.tsx::StepCard`;
`frontend/src/trace/StepCard.tsx::StepCard`;
`frontend/src/theme/colors.ts::NODE_GROUP_COLORS`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`.

### PNC-14 — A collapsed card reads as the step it describes
**Why:** `summarizeStep` returns one muted string in which column names,
values and operator words look the same. Unset parts print as `?`, so a new
group-by reads `whole frame: ? = sum(?)` and a new filter reads `? equals 0`.
Nothing on a collapsed card says what the step does to the columns.

**Plan:** A summary becomes a short list of parts the card renders: column
names as small monospace chips, values in the colours code mode uses for text
and numbers, operator words muted, and an unset part as a dashed placeholder
naming what is missing ("column", "name"). A step whose main column is not
chosen yet reads "Choose a column…". A small right-aligned note gives the
change the step makes to the columns, from `columnsBeforeStep` before and after
it: `+claim_flag`, `−3 columns`, or `→ 3 columns` after a group-by. The note
needs the exact columns, not just a list that contains every possible one. It
appears only where the lists before and after the step are complete, as
`PNC-11` defines it, and no step up to and including this one keeps or drops
columns by type. The aggregation part follows
`PNC-03`'s order, so the collapsed card and the open form say the same
sentence. The plain text of the summary stays available as its title.

**Acceptance:** Unit tests: each step kind's parts join to today's summary
text, except that `?` becomes the placeholder wording. Component tests: a fresh
filter shows "Choose a column…"; `Add column`, `Drop columns` by name and
`Group and aggregate` show their exact column-change notes; no note appears on
a step after `Free code`, on `Drop columns` or `Keep columns` by type, or on a
later step after either of those.

**Dependencies:** `PNC-13`, which lays out the line this sits on, and `PNC-11`,
which says when the columns are complete.

**Evidence:** `frontend/src/panels/editors/polarsSteps/summary.ts::summarizeStep`;
`frontend/src/panels/editors/polarsSteps/summary.ts::aggregationText`;
`frontend/src/panels/editors/polarsSteps/StepCard.tsx::StepCard`;
`frontend/src/panels/editors/polarsSteps/derivedColumns.ts::columnsBeforeStep`.

### PNC-15 — The generated code is coloured and linked to its cards
**Why:** The code panel prints its code in one colour, while code mode and
the `Free code` step highlight Python, so the one-way switch changes how the
same code looks. Every edit fades the code to 60% opacity for at least the
render's debounce, a flicker while typing. The render response already maps
each step to its line range, but only runtime-error mapping uses it, so nothing
shows which lines a card produced.

**Plan:** Highlight the panel's code with the Python highlighting and colour
tokens code mode uses, read-only. Hovering or focusing a card tints its line
range in the panel; hovering a line outlines its card, and clicking a line
opens that card. The code fades only when a render is still pending after
about 400 ms. Ranges from a pending or failed render are never used, as the
specification already requires for errors.

**Acceptance:** Component tests: the code lines carry highlighting; hovering a
card marks exactly its range; clicking a line in a step's range opens that
step's card; a render that finishes within 400 ms never fades the code; a range
spanning several lines is marked whole.

**Dependencies:** None. After `PNC-06`, a step's range spans several lines.

**Evidence:** `frontend/src/panels/editors/polarsSteps/GeneratedCodePanel.tsx::GeneratedCodePanel`;
`frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts::useRenderedSteps`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`;
`frontend/src/panels/editors/CodeMirrorEditor.tsx::CodeMirrorEditor`.

### PNC-16 — Forms read as sentences, in plain words
**Why:** Every form stacks a 10 px bold capital-letter label over each
full-width control, including long notes such as "GROUP BY (EMPTY =
SUMMARISE THE WHOLE FRAME)" and "CHECK KEY CARDINALITY (FAILS THE RUN WHEN
VIOLATED)". The labels share the style of the menu and section headings, so a
card has no hierarchy inside it, and a simple step such as `Limit rows` takes
two lines to say "keep the first 100 rows". Some options show raw values: the
fill strategies (`forward`, `backward`, `min` and so on) and fifteen cast types,
eight of them integer widths. "Keep rows matching" heads the conditions of an
`If-then` and of an aggregation's row filter, where no rows are kept. `Add
step`, `Add condition` and `Only some rows…` look alike, so a group-by shows
several grey full-width bars.

**Plan:**
- Lay the short forms out as wrapping sentences with muted linking words, such
  as `Keep the first [100] rows`, `Rename [from] to [to]` and `Sort by
  [column] [descending]`. Controls keep their accessible names, so the stacked
  labels can go where the sentence carries the meaning.
- Where a label stays, it is short and in sentence case, with any note beside
  it in normal weight, as the Banding editor labels its default.
- Options in plain words with the Polars name where it helps: `previous row's
  value (forward)`; the common cast types first (`Int64`, `Float64`, `String`,
  `Boolean`, `Date`, `Datetime`, `Categorical`) and the other widths after
  them; "When [all] of these are true", with the match select in place of
  `all` so it reads "any" when chosen, wherever conditions do not filter rows.
- Secondary actions become quiet text buttons (`+ Add condition`), and
  `Only some rows…` a link-style toggle.
- No step schema change; saved steps render as before.

**Acceptance:** Component tests: the `Limit rows`, `Rename columns` and `Sort
rows` forms read in sentence order and keep their accessible names; the
condition lists of an `If-then` and of an aggregation's row filter read "When
all of these are true" with `all` chosen and "When any of these are true" with
`any`, and write the same match value as before; the fill strategy
and cast options show the new labels and write the same values as before; the
existing form tests pass.

**Dependencies:** `PNC-03` and `PNC-05` settle the aggregation row and the
disclosure; this package applies the same approach to the other forms.

**Evidence:** `frontend/src/panels/editors/polarsSteps/fields.tsx::FieldLabel`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::ConditionList`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::AddRow`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::LimitForm`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::FillNullForm`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::FILL_STRATEGIES`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::CAST_DTYPES`.

### PNC-17 — A value is one control
**Why:** A value in a condition or an expression shows up to three controls
of equal weight: a source select (value, column, variable, expression), a type
select (number, text, true/false, date) and the input itself. One condition
row becomes five boxes over two lines at 440 px, and the two selects hold the
least-used choices.

**Plan:** One input with a small type marker at its start: `#` for a number,
`"` for text, a calendar mark for a date, a tick for true/false, a column mark,
a variable mark, and `ƒ` for an expression. Clicking the marker, or pressing
Alt+Down on it, opens a menu of the sources and types allowed there. The
choices are the same as today's; the marker also shows the type `PNC-04`
chose.

**Acceptance:** Component tests: a condition row renders one value control
with its marker; switching from number to text through the marker writes the
same step the type select writes today; each field offers the same sources and
types as before.

**Dependencies:** `PNC-04`.

**Evidence:** `frontend/src/panels/editors/polarsSteps/fields.tsx::OperandField`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::ConditionRow`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::LiteralValueInput`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::LITERAL_TYPES`.

### PNC-18 — The formula box shows what it can do
**Why:** The formula box is a single-line input with no placeholder; its
example is a tooltip on the box and on an info icon. It completes columns and
variables but not the catalogue's functions: the `Function` mode's select
lists them, but inside a formula they can be found only by typing an unknown
name and reading the error that lists them all. Function
names must match their case exactly, so `ROUND(premium, 2)` fails. A parse
error shows only on commit, as a muted "Not understood" note, and only errors
from reading the characters give a position; a missing bracket does not. A
long formula scrolls sideways in the panel's width.

**Plan:**
- Show the example as a muted monospace placeholder in the empty box.
- Let the box grow onto more lines as the formula lengthens; Enter still
  commits.
- List the functions after the columns and variables in completion, each
  marked `ƒ` and shown with its label. A name that is both a column and a
  function appears twice: the column entry inserts the name as today (in
  backticks where needed), and the function entry inserts `name()` with the
  caret between the brackets.
- With the caret inside a call, a line under the box shows the function's
  arguments with the current one bold, from the existing argument labels.
- Accept a function name in any case and write it in the catalogue's case on
  commit.
- Say where a formula went wrong for every parse error, with the position
  marked under the box ("expected `)` after `sum_insured`").
- Commit, round-trip and depth rules are unchanged.

**Acceptance:** Form tests: the empty box shows the placeholder; typing `rou`
lists `round` with its label, and choosing it leaves `round(|)` with the caret
between the brackets; typing `premium, 2` and committing stores a `round`
function expression; with a column named `round` upstream, its column entry
inserts the name exactly as today and commits as a column; with the caret in `round(premium, |)` the argument line
bolds the second argument; `ROUND(premium, 2)` commits as
`round(premium, 2)`; a missing closing bracket reports its position; formula
text round-trips as before.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/forms.tsx::FormulaField`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::FORMULA_EXAMPLE`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::completionsFor`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::ARG_LABELS`;
`frontend/src/panels/editors/polarsSteps/formula.ts::parseFormula`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::FUNCTIONS`.

### PNC-19 — `Add step` can be searched and says what each kind does
**Why:** The chooser shows seventeen pill buttons that look alike. What each
kind does is only a tooltip, which keyboard users never see, so nothing tells
`Pivot to columns`, `Unpivot to rows` and `Group and aggregate` apart at a
glance, and there is no way to type to find a kind. The chooser's close button
turns red on hover, as a destructive action does, although closing discards
nothing. A node with no operation steps yet shows the start card, a dashed
`Add step` and a code panel holding only `df = <input>` (the start step is
written as soon as the input is known), with no hint of what a step is.

**Plan:**
- A filter box at the top of the chooser takes focus when it opens. It matches
  each kind's label, its description and its Polars method (`group_by`,
  `with_columns`, `unique`, `fill_null`, `unpivot` and so on); Enter adds the
  first match, and the arrow keys still move between kinds.
- The focused or hovered kind's description shows as a line at the foot of the
  chooser, and each entry shows its Polars method in muted monospace.
- Each kind has an icon, shared with the card header (`PNC-13`). If kinds get
  colours, only the icon is tinted, since node colours already mark node
  families on the canvas.
- The close button uses the neutral hover style.
- While a node has no operation cards (only the start step in input mode, an
  empty list in frame mode), one line says that steps run top to bottom on
  the start input (in frame mode, on this node's data), with buttons for
  `Filter rows`, `Add column` and `Group and aggregate`.

**Acceptance:** Component tests: the filter box has focus when the chooser
opens; typing `group_by` leaves only `Group and aggregate` and Enter adds it;
the description line follows focus; kinds the surface withholds stay withheld
while filtering; in the settled state of a connected Transform, with only its
start step written, and in an empty frame-mode list, the empty state offers
the three kinds and each adds its step; it disappears once a card exists.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/AddStepMenu.tsx::AddStepMenu`;
`frontend/src/panels/editors/polarsSteps/AddStepMenu.tsx::GROUPS`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::STEP_CATALOGUE`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::PolarsStepsEditor`.

### PNC-03 — An aggregation reads as one sentence
**Why:** In `Group and aggregate`, each aggregation row lays out
`[a column] [output name] =` and then `[sum] [of column]`, which wraps onto
two lines in a narrow panel (it did at 440 px in the walkthrough). The leading
`a column` select chooses between one column and every column of a type, but
it sits where the column being aggregated would be expected, so it reads as the
input column. The output name comes before the function, and the `=` falls at
the end of the first line. A new aggregation also starts at `sum` with no
column and no name, so every row needs a typed name before the step renders,
and the common count needs the function changed to `row count`, whose meaning
differs from `count (non-null)` in a way the labels do not explain.

**Plan:** Lay each row out as it reads aloud: `total_incurred = sum of
amount_paid`, with the name, function and column on one line where the width
allows and wrapping after the `=` when it does not. Move the column-or-type
choice onto the column control itself (for example an `every Float64 column`
entry at the end of its suggestions) instead of a separate leading select.
Give `row count` and `count (non-null)` short descriptions in the function
list. When the function and column are chosen and the name is empty, or still
the name suggested for the previous choice, fill it in as `<column>_<function>`
(`amount_paid_sum`); a typed name is never replaced. A new row starts from the
previous row's column. No step schema change is needed.

**Acceptance:** A component test renders a group-by with two aggregations and
asserts the reading order name, function, column in each row; the type-wide
aggregation is still reachable and round-trips through save; the function
list shows descriptions for the two counts; choosing `sum` of `amount_paid`
names a new row `amount_paid_sum`, changing the function to `max` renames it
`amount_paid_max`, and a typed name survives both.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/forms.tsx::AggregationRow`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::AggregationTargetSelect`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::GroupByForm`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::AGGREGATIONS`.

### PNC-04 — A condition's value starts in the column's type
**Why:** A new condition (in `Filter rows`, an aggregation's `Only some
rows…` or an `If-then`) defaults its value to the number `0`. Filtering a
text column, such as `fault equals at_fault`, needs the value's literal type
changed from number to text before the text can be typed, a second select
on top of the operator. The step forms cannot do better today: their context
carries the available column names but not their types.

**Plan:** Use the column types `PNC-01` adds to the step form context
(derived columns whose type is not known stay untyped). When the condition's
column is set and the value is still the untouched default, switch the value's
literal type to match: text for strings, a date box for dates, true/false for
booleans, number for numeric columns, and the current number default when the
type is unknown. Never change a value the analyst has already edited. Choosing
a text operator such as `contains text` keeps its current behaviour.

**Acceptance:** A component test picks a string, a date, a boolean, a numeric
and an untyped column in a fresh condition and asserts the value control's
type follows (number for the untyped one); a value typed before the column is
picked is left unchanged.

**Dependencies:** `PNC-01` for the column types in the form context.

**Evidence:** `frontend/src/panels/editors/polarsSteps/catalogue.ts::defaultCondition`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::ConditionRow`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::OperandField`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::StepFormContext`.

### PNC-05 — Options the simple case never needs sit behind a disclosure
**Why:** Every `Keep columns` and `Drop columns` card shows a second field,
`And every other column of type`, every aggregation row shows an `Only some
rows…` button, and every join shows its output row order, its suffix for
clashing names and, for inner, left and full joins, its key cardinality check.
All are useful, but they appear on every card in the simplest case, so a
first step presents more controls than the task needs and the eye has to find
the one field that matters.

**Plan:** Decide which options move behind a per-card `More options`
disclosure, opened automatically when a saved step already uses one. The
candidates are the type-wide selection on `Keep columns` and `Drop columns`,
the per-aggregation row filter, and the join's row order, suffix and
cardinality check. Recommendation: move all of them, since each is used far
less than the plain case and each stays one click away. The disclosure uses
the quiet style of `PNC-16`. An option counts as used when its saved value
differs from what a new step gets:
- a `dtypes` list is present;
- a `where` filter is present;
- `maintainOrder` or `validate` is present;
- the suffix is not `_right`.

**Acceptance:** A component test shows a fresh `Keep columns` step without
the type field, a fresh aggregation without its row filter and a fresh join
without its row order, suffix and check. A saved join with the default suffix
and no order or check opens collapsed. A saved step with any used option,
including a join whose suffix is `_r`, opens with the disclosure expanded and
its values intact.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/forms.tsx::ColumnsAndTypesForm`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::AggregationRow`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::JoinForm`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts::createStep`.

### PNC-06 — The generated code lays long calls out one argument per line
**Why:** The generated code panel is the editor's proof of what the steps do,
and it teaches Polars as a side effect. A group-by renders as one line, for
example `df = df.group_by(['quote_id'], maintain_order=True).agg([...])`
with every aggregation inside it, which runs well past the panel's width at
its default size and is read by scrolling sideways. Long `select` lists do the
same.

**Plan:** Render a call whose single-line form exceeds a fixed width with one
argument per line, indented, in the layout `ruff format` would produce, so the
panel reads like a formatted pipeline file. The steps' behaviour, the
line mapping used by "Go to error" and the saved code's semantics do not
change; only its layout does.

**Acceptance:** Render tests for a group-by with two aggregations and a long
`select` assert the multi-line layout and that `ruff format` leaves the
output unchanged; the existing render-to-step line mapping tests still pass.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_steps.py::_render_group_by`;
`frontend/src/panels/editors/polarsSteps/GeneratedCodePanel.tsx`.

### PNC-07 — A formula can compare
**Why:** The `Formula` box parses arithmetic, functions, columns, variables
and literals, but not comparisons or boolean logic. A flag such as
`amount_paid > 5000`, or `claim_type == 'windscreen'`, cannot be written as
a formula and has to be rebuilt as an `If-then` with a condition row and
explicit `then` and `otherwise` values, a much longer path for a very common
pricing derivation. The package is deferred because it adds capability to the
formula language, which this roadmap leaves out of scope; the flag can already
be built with `If-then`.

**Plan:** If reopened, decide whether comparisons (`== != > >= < <=`) and
`and`, `or`, `not` join the formula grammar, producing a boolean expression,
and whether `if(cond, then, otherwise)` joins it as a function. Leaving
multi-branch logic to `If-then` would not cover it, since `If-then` has one
branch. The step schema and the server's closed vocabulary would gain the new
operators, held equal by the catalogue parity test.

**Acceptance:** Parser tests for each comparison and boolean operator with
Python precedence, `formulaText` round trips, a server render test producing
the matching Polars expression, and the catalogue parity test updated for the
new operators.

**Dependencies:** A decision to extend the formula language.

**Evidence:** `frontend/src/panels/editors/polarsSteps/formula.ts::parseFormula`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::EXPR_TYPES`.
