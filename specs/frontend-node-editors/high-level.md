# Frontend Node Editors — High-Level Specification

## Purpose

The node-editor surface turns the selected pipeline node into a type-specific, editable
configuration form. It keeps authoring consistent across simple scalar nodes, code nodes,
multi-frame API inputs, IO sources/sinks, banding, rating, model scoring, submodels, and
optimiser application.

## Scope

This component owns node-panel dispatch, the node palette, editor implementations and their
form, banding, rating, path, clipboard, and format helpers. It also owns the generic Columns
and grouped-columns configuration tabs. The graph canvas owns selection and graph mutation;
backend API modules own validation and persistence.

## Behaviour

- Selecting a node opens its matching editor through a lazy editor registry. An unknown type,
  malformed instance reference, or unsupported read-only configuration is shown as diagnostics
  instead of being guessed.
- Editor updates flow through the supplied configuration callbacks. Text and numeric drafts are
  committed on blur/Enter where their controls use the shared committed-input primitives, so a
  normal edit does not create a graph mutation for each keystroke.
- Connected inputs are listed by their **input name — the exact argument name in the node's
  code**, 1:1 with the generated function signature: an API-input frame edge's chip shows the
  frame label carried on the edge (`quotes` is displayed as `quotes` and callable as `quotes`),
  an ordinary source's chip shows the sanitised node label, and a submodel `out__` edge's input name
  is the sanitised public output port name, independent of the occurrence alias and
  number of output ports. Renaming an occurrence leaves its output frame names
  unchanged. Connections contributing duplicate input names are rejected explicitly.
  Polars input chips, their tooltips, and connection-removal controls always use
  this frame name. A source card's display label, occurrence alias, internal child
  label, or structural handle must never replace a declared frame name, including
  when only one frame is emitted or an additional output is added.
  Inside a
  drilled submodel, an edge from the composite Input resolves its row handle to that public
  input port's name; the literal boundary-card label
  `INPUT` is never presented as the child's argument name. The frame
  is named in the chip tooltip. Two frames connected from one API input render as two
  distinct, individually removable chips with two distinct names. Live-switch mapping rows and
  output frame blocks present the same names — there is no separate display identity anywhere.
- Editors retain incomplete persisted rows when they can be repaired (notably API schema and
  output mappings); fresh inference data may be normalised separately from persisted data.
- API Input preview browsing advertises and filters JSON, JSONL, NDJSON, and XML. Selecting any
  of those structured formats fetches its schema preview, and all four expose the cache/infer
  action. Directory rows remain navigable when the server reports a null size; only numeric file
  sizes are rendered.
- Banding exposes categorical/numeric rule editing, preview-derived suggestions and histogram
  context. The Numeric type is disabled, with the reason as its tooltip, while the selected
  input column's dtype is known and neither numeric nor a date: numeric bands compare the column
  against number or date boundaries, which a text or boolean column cannot satisfy. Banding lists its
  factors the way Rating Step lists its tables, with the same shared list: a search over each
  factor's output and input column names, an All/Issues filter (an issue is a factor without
  its input column, output column or rules), an Add button, and a scrollable box of rows with
  a health dot, the output name, a rule-count badge and, when there is more than one factor, a
  remove control. Banding's rows can also be dragged, or moved with Alt+Up/Down, to reorder
  the factors; list order is the order execution applies them in, so a factor that bands
  another's output must stay after it. A new node, or one whose only factor is still empty,
  shows the list with a placeholder "Column 1" row and the Type row as any other factor, so
  choosing the first column does not move the layout. A new factor is Numeric; choosing an
  input column whose dtype is not numeric switches it to Categorical. Numeric and Categorical
  are the only banding types. Numeric's Generate opens its options
  where it was pressed: under the Breakpoints heading and above the table, or in place of the
  "No breakpoints yet" prompt when there are none. Generate keeps its Start, End and Step
  options; on a factor with at least two "Up to" boundaries they start from what those
  boundaries imply, so regenerating begins from the settings last used: End is the highest
  boundary, Step spreads the lowest to the highest boundary over the bands (their count less
  one), and Start is one step below the lowest. Boundaries that are evenly spaced but for a
  shorter last band — Generate's output when the range is not a whole number of steps — give
  back that exact step instead. With fewer boundaries the options start from the data's range.
  Numeric also bands Date and Datetime columns, and is the type choosing one selects: its "Up
  to" then takes a date (`YYYY-MM-DD`) or a date and time (`YYYY-MM-DD HH:MM`), compared as the
  rating spec describes (a date by calendar day, so "up to 2024-02-29" includes that whole day;
  a date and time by wall-clock time; both in the column's own time zone). The grid shows the
  expected format, and warns on a boundary it cannot read, one out of order, or a mix of kinds;
  the histogram and its end labels read as dates. On a date column Generate asks for a Start and
  End date and a Step of so many days, weeks, months or years: each band starts where the last
  ended and ends the day before the next band starts (the last capped at End), and it is
  labelled by its first and last day (`2024-01-01–2024-01-31`). Its "Up to" is that last day,
  or the day after it when the factor's bands stop before their "Up to" (left-closed), so the
  bands hold the days their labels name either way. Reopening it on date breakpoints whose bands'
  last days step by a whole number of years, months, weeks or days (checked in that order, the
  last band possibly shorter) starts from that step; other date breakpoints start from the
  lowest and highest of those days and the band count in days, and a factor with fewer than two
  starts from the data's first and last dates in steps of one month. A band's last day is the
  last calendar day it reaches under the factor's closure (for a date and time, the day it falls
  on, or the day before at midnight).
  Rating supports one- and two-way factor tables, value-level matching, statistics,
  paste/copy and downloadable table data.
- Banding and Rating say whose rows their numbers describe; neither has a cache control of its
  own, since the node's Refresh caches its data. When the node's data point is cached both read the whole dataset: Banding its
  distribution, values and per-rule counts, Rating the levels of the raw factor columns its
  tables rate on, so a level absent from the preview can still be given a rate. Without a
  current point — or with one the node has moved on from — Rating says so and falls back to the
  preview sample rather than presenting a sample's answer as the data's, and a failure is shown
  in place of its label, carrying the server's own message. Banding never shows a number from
  the preview: its per-rule counts, its "x of N rows" and its histogram come only from the whole
  dataset. Until that answer is there, its label says why — "Counting…", "Caching the data…",
  "Not cached · Refresh this node to count all rows", "Cached data is out of date · Refresh this
  node to count all rows", or the failure in the server's words — the counts show as pending
  while they are being counted and are absent otherwise, and there is no histogram or "x of N
  rows". A server answer that the data needs caching, which can come for a point that looked
  current, reads "Not cached" too rather than counting on. Its categorical value list then offers the preview's values without counts, and
  Generate may start from the preview's range. Editing Banding's rules does not drop the whole
  dataset's answer while the new counts are asked: its total, values and distribution stay,
  categorical counts follow the edit at once from the data's value counts, and a count not yet
  known shows as pending.
- The Rating Step editor says none of this when nothing in it reads the data: a table whose
  factors are all banded outputs takes its levels from the banding configuration, so it shows
  no basis.
- Levels the data adds are appended to the ones the Rating Step editor already shows, never put
  in front of them: they arrive while the user is typing, and a row that moved would take the
  value meant for its neighbour. The slice of a three-factor table is held as the level itself
  rather than a position for the same reason. A table whose factors would make more cells than
  the editor can edit is not drawn or rebuilt; it says how many cells it would take instead, and
  a factor that would take a table past that size is not added at all, because entries the editor
  cannot build would leave the table without a value for its own factor. Dropping a factor stays
  possible whatever the size, so a table the data has made oversized can still be shrunk.
- IO editors obtain supported formats and their arguments from the server. API/data input,
  output, external-file, transform, explore, live-switch, scenario, submodel,
  model-score and optimiser-apply editors render only their own configuration contract.
- The Explore Charts pane owns ordered version-1 PivotChart cards. `Add Chart` appends a complete
  enabled draft with a unique id/name; each card's checkbox changes only visibility, `Configure`
  never toggles it, and a separately confirmed Delete removes only that card. Back changes only
  navigation view state — it clears the node's stored configured-chart id and never touches card
  config or the preview pane.
- Chart Configure selects any pivot on the same Explore node, including a hidden one, by stable id.
  It is a chart-formatting surface only: pivot structure (fields, zones, filters) is edited
  exclusively in the Pivots editor, and the chart view renders no field well, field summary, or
  disclosure box between the source picker and the chart controls. It
  exposes the chart name, a chart-type
  gallery over four options — Combo leftmost as the general category and the default for a
  newly sourced multi-Value chart (as in Excel; a single-Value chart's plain-column seed reads
  as Clustered columns), then clustered, stacked, and 100% stacked columns —
  where exactly one option is always highlighted: any
  arrangement beyond the three column layouts reads as Combo and is refined per Value through
  its chart-type and
  axis selects, plus a vertical/horizontal orientation toggle preserved
  across preset
  application, per-Value defaults with exact-series overrides nested beneath each Value box (a
  collapsed expandable list, present only when a Columns field splits that Value into several
  series or overrides already exist — a single-series Value's box is its series config), two
  numeric axes presented as separate Primary and Secondary boxes ordered before the per-Value
  boxes, with the Secondary box gated by a "Use secondary axis" checkbox whose untick moves
  secondary-assigned series back to primary in one edit, a Legend box after the Secondary box
  gated by a "Show legend" checkbox, and
  category-label controls. Navigation alignment is preview-driven — selecting Pivots or Charts
  in the lower preview aligns the editor, while editor-side selections and Configure/Back
  never change the preview; the configured subview is per-node view state that survives pane
  switches and clears when its card is deleted. Per-series controls use user-facing vocabulary — chart type, series,
  stacking (None / Stacked / 100% stacked with valid-by-construction group transitions), and a
  swatch-based colour control with an Automatic reset — and unused formatting is described by
  the series or Value name it belonged to, never by an internal id. A pivot Value the chart
  does not yet encode is reconciled with seeded
  defaults (surfaced as such, persisted with the next committed edit) instead of blocking the
  editor. Changing an already mapped source requires confirmation and commits
  the reset in one graph edit. Draft, missing, unconfigured, loading, stale, errored, hidden, and
  ready source states remain explicit, but the source picker itself never carries a status
  suffix — options show the pivot name (plus a hidden marker where applicable), and source
  state is communicated by the status messaging in the Configure body.
- A pivot cannot be deleted while charts reference it; the Pivots pane identifies dependent chart
  names so the analyst can reassign them. Chart appearance edits are presentation-only and never
  change dataframe/pivot calculation identities or structural execution version.
- Overview, Pivot, and Chart cards share one Explore toggle-card presentation. The card body is an
  accessible checkbox target: enabled cards use the Explore border, accent-soft background, and
  accent label treatment; disabled cards use the neutral input treatment. Pivot and Chart cards
  keep Delete and Configure as separate controls that never toggle visibility. Their list headers,
  Add actions, empty states, optional detail text, and delete eligibility remain supplied by the
  owning panes.
- The Explore Pivots pane owns ordered version-1 cards. `Add Pivot` appends a fully populated,
  uniquely named, enabled card with the first-unused `pivot_N` identity. Each card exposes an
  accessible visibility checkbox and a separate `Configure` button; configuring never toggles
  visibility, and Back returns to the card list by clearing the node's stored configured-pivot
  id without touching card config or the preview pane.
- Pivot Configure provides a committed unique name, an Excel-style searchable dtype-labelled
  field palette in a fixed-height scrolling list, and ordered Filters, Columns, Rows, and Values
  zones. A current Explore cache report supplies the palette's authoritative post-analysis schema;
  before one is available the editor may use the upstream preview schema, but it never uses a
  retained report from a different graph/source identity. Every field row shows `Add to:` followed
  by Filters, Columns, Rows, and Values buttons;
  pressing one adds that field directly to the matching zone, with no separate selection/action
  area. Assigned fields appear beneath `Drag fields between areas below:` in a fixed two-column
  grid ordered Filters, Columns, Rows, Values like Excel. A placement can be dragged onto another
  placement to insert before it, or onto open area space to append; this reorders within an area or
  moves between valid areas in one committed graph edit. The visible Move-to and Move-up/down
  controls are omitted; focused cards expose equivalent arrow-key movement, and Remove remains a
  separate action. One field may appear across zones; Filters/Columns/Rows
  reject same-zone duplicates; Values permit repeated fields with stable ids. Numeric Values
  default to Sum and other dtypes to Count, with Sum/Count/Average/Min/Max/Median/Distinct count
  available subject to dtype compatibility. Placement cards contain only placement-specific
  controls (filter members, Value aggregation, and Remove); sorting and formatting never appear
  inside the draggable grid. The Configure subview starts directly with the Pivot name setting,
  without repeating `Configure <name>` above it. Sorting, Formatting, and Conditional Formatting
  use the standard uppercase node-editor micro-title, standalone above their settings and never
  inside a bordered settings box. Immediately after the grid, the Sorting settings box exposes
  `Sort by` (default Row-label order, any placed Row, or any placed Value) and its matching `Order`
  control side by side. A following Formatting section lists every
  displayed Column, Row, Value, and selected-formula placement, with Values and formulas numbered
  independently per kind. Numeric output can use General, Number, Percentage,
  GBP currency, USD currency, or EUR currency formatting, Automatic or a fixed 0–10 decimal
  places, and an explicit thousands-separator option. Filters are omitted because they do not
  render in the pivot table; non-numeric placements remain identified but have no numeric-format
  controls, without redundant introductory copy above the placement list. Formatting is
  presentation-only and updates a retained result immediately. A separate
  Conditional Formatting title is followed by a bordered rules box. Every active rule is visible
  at the same time and exposes its Value field, colour scale, an optional `Split scale by`
  selector, labelled gradient preview, and Remove action; the `Add rule` action follows the rule
  list at the bottom of the box. Split choices are restricted to fields currently placed in Rows
  or Columns. With no split, one scale covers the whole Value; selecting a placement gives each
  distinct typed member of that Row or Column field its own scale. The preview uses Excel's prominent
  red–yellow–green three-colour palette (`#F8696B`, `#FFEB84`, `#63BE7B`), reversing the same
  endpoints for green-to-red. One Value placement can have at most
  one rule. Adding chooses the first still-unformatted numeric Value and applies `Low red → High
  green`; the action is disabled when no eligible Value remains, without an additional
  all-fields-configured message. A rule can be reassigned to any
  other eligible unformatted Value. Colour choices are `Low red → High green` and `Low green →
  High red`; removing a rule persists its scale as None and clears its split. Reassigning a rule
  carries its split with it. Removing the selected Row/Column placement, or moving it out of those
  two zones, clears every rule that referenced it; moving it between Rows and Columns preserves the
  stable reference. An aggregation change that makes a Value non-numeric also removes its rule and
  split. Missing source fields remain visible as invalid chips.
- Configuration edits commit immediately as ordinary graph changes. When the lower Pivots or
  Charts result pane is mounted, a committed calculation-affecting Pivot edit automatically
  schedules one recalculation for the current dataframe-cache and calculation identities;
  opening either pane later does the same for stale or missing source results. Pivot and Chart
  name/appearance edits reuse retained data and rerender immediately without calculation. There
  is no separate `Update preview` or routine manual refresh step.
- The Explore node pane strip is ordered Polars Code, Overview, Pivots, Charts, Export.
  Relationships is not exposed as an Explore pane. Pivots hosts its card workflow in that
  position, and its selection is remembered independently per Explore node like the other panes.
- The Edge Join editor presents the canvas-bound dominant/base and joining roles as fixed
  connections with one atomic swap action. Each role displays the executable input name
  contributed by that exact edge, using the same identity rule as other node editors: an API
  Input edge displays its selected frame label, while an ordinary edge displays the upstream
  node's executable input name. Internal source-node identities are not exposed in the role text
  or its truncation tooltip. Swapping updates only the incoming role handles in one graph
  transaction. Join type choices are exactly `inner`, `left`,
  `right`, `full`, `semi`, `anti`, and `cross`. A cross join has no key controls or persisted
  keys; every other mode requires either one-or-more same-name `on` keys or equal-length,
  non-empty `leftOn`/`rightOn` pairs, and the two key forms cannot coexist.
- Renaming an ordinary source or an API-input frame atomically migrates downstream
  `input_scenario_map`, instance `inputMapping`, and exact-name Optimiser/Optimiser Apply input
  selectors. A duplicate post-rename input
  name rejects the entire edit and is shown inline; no graph or mapping change is partially
  applied.
- Data Input and Data Output obtain a fresh capability payload when an editor mounts; mounts
  sharing the same pending request coalesce it. Provider changes replace the discriminated
  config in one undoable update, and output overwrite confirmation is tied to semantic graph
  and execution settings rather than preview/trace metadata.
- Data Input groups providers as File, Database, Lakehouse, Databricks, and
  Inline and derives every supported field, format, mode, dependency,
  and snapshot build class from the backend capability
  contract. A single available read mode is not rendered. Cache mode is also
  not presented as a choice, and the editor has no cache action: file-backed
  Parquet scans directly, and every other input's snapshot is prepared before
  a run.
  Its optional Polars editor transforms the resolved frame.
  Data Output presents only writable groups/modes, never Databricks or a Polars
  editor, resolves the actual destination, and keeps per-node write,
  collision-confirmation, and terminal state across panel remounts. Inactive
  discriminated-branch keys are removed rather than preserved invisibly.
- Rating consumes healthy configured Banding outputs across categorical and
  breakpoint shapes. Recognised non-blank outputs with zero
  valid levels produce one accessible warning and cannot be silently refilled
  from stale preview/table levels; healthy factors remain usable. Rebuilding
  several factors constructs their full Cartesian table, and edited
  relativities survive save/reload.
- The maintained Banding-to-Rating configuration-shape matrix names one
  component owner, representative fixture, and smallest proving test tier for
  categorical, breakpoint, mixed-factor, zero-level, malformed,
  mixed-output, and persisted-table variants. Browser promotion is reserved
  for cross-editor persistence/keyboard journeys rather than duplicating every
  component shape.
- Rating, Output, and API Input expose only their current persisted shapes:
  Rating uses `tables[].entries` plus `combinedOutputs`; Output uses
  `outputMapping` rows with all four required fields including `enabled`; API
  Input uses `tables`. Editors do not detect, upgrade, or mirror historical
  working-copy formats.

**Transform step builder.** A Transform node's config tab is labelled "Polars" (its config is
its steps or code). A new Transform node starts in step mode: its default config
carries an empty `steps` list, and the editor renders the step builder instead of the code
box whenever `config.steps` is a list. The builder shows a fixed start-from input selector,
numbered step cards ("Start from", then Step 1 onwards, the same numbering every message
uses) that open one at a time through a keyboard-operable disclosure (a new step opens
itself and its first field takes focus; Escape collapses; deleting a card moves focus to
the next disclosure or to `Add step`). A card's header, laid out like the trace panel's
step card, carries the chevron, the step number in muted monospace, the kind's icon and
its label, with the collapsed summary under the label in the same column: column names as
chips, values in their code colours, operator words muted, an unset part as a dashed
placeholder naming what is missing ("column", "name"), a step whose main column is not
chosen yet as a prompt ("Choose a column…"), and, at the right, the change the step makes
to the columns (`+gross`, `−3 columns`, `→ 2 columns` after a reshape) where the columns
before and after it are known exactly. Every card keeps the same three action slots (move
up, move down, delete; a move that does not apply is disabled) that show fully on hover or
focus; Alt+Up and Alt+Down on a card's header move it and keep its focus; and each card is
draggable by its header (a hand cursor and a grip; dropping on another card puts the step
there and the open card follows its step). Keys the canvas acts on stop at the step editor
whatever control has focus: Delete and Backspace outside a text box, and the graph's
Ctrl+C, Ctrl+V, Ctrl+A and Ctrl+G; save, undo and redo, node search, fit view, help and
Escape (once no open card has handled it) stay global. An `Add step` button under the last
card (under the start card while there are no steps) opens, in place, a chooser of every
step kind by icon and name, sectioned as rows, columns, combine, values and code, with a
search box that takes focus and filters the kinds by label, description or Polars call
(never SAS or Excel names; Enter adds the first match), a line at its foot giving the
focused or hovered kind's description and Polars call, arrow keys that move between the
search box and the kinds, and Escape that closes and returns focus; choosing a kind moves
focus into the new card. The chooser covers filter, derived
column, conditional column, window aggregate, select, drop, rename, cast, sort, unique,
group by, join, concat, pivot, unpivot, fill null, limit, and variable. Short forms read as
sentences (`Keep the first [100] rows`, `Sort by [column] [ascending]`, `Rename [column]
to [name]`); other fields carry short sentence-case labels with any note beside them;
options read in plain words with the Polars name where it helps (fill strategies such as
"the previous row's value (forward)", the common cast types first with their meaning, such
as "Int64 (whole number)"); secondary actions are quiet text buttons ("+ Add condition");
and options the simple case never needs sit behind a "More options" disclosure that opens
by itself when the saved step uses one (the type-wide selection on select and drop, the
per-aggregation row filters, and a join's key check, row order and suffix, where "used"
means differing from a new step's value, so a suffix other than `_right`). Column pickers
offer the start input's columns (for an edge from a multi-output producer such as a
submodel, the columns of that output handle as recorded by the last preview; in `frame`
mode, the surface's upstream columns) plus columns derived by earlier steps, never another
input's columns before a join brings them in, while accepting free text: every column box
lists the names starting with what is typed beneath it (all of them while the box is empty,
none active), typing makes the first match active, Up/Down move through them, Tab, Enter or
a click takes the active name, text that already is a name is shown ticked as matched and
kept, Escape closes the list, and with nothing active Tab moves on while Enter or leaving
the box keeps what was typed; the box keeps keyboard focus through a completion or a
commit. The active entry is tinted with an accent bar and the typed part is bold, and each
name shows its type from the preview (or `new` for a column an earlier step made). A
column's type follows the start input's own columns: a rename keeps it, a cast sets it, a
group-by key keeps it, and a step that creates or replaces a column leaves that column
untyped. Where the columns at a step are complete (the start input's columns have loaded
and no Free code, Append inputs, type-wide aggregation or join whose input's columns are
unknown comes earlier), a name that is not among them is drawn with a dashed amber border
and a note ("`quot_id` isn't in the data at this step. Did you mean `quote_id`?") whose
button replaces it with the closest name; a collapsed card lists such names too, so a
reorder marks the affected card at once. A window expression offers the plain aggregates plus row number, running total,
previous value, rank, dense rank and forward/backward fill, an optional in-group order
(one direction, with a hint that ordering needs a group column), a rank direction, and a
quantile; a text-join expression lists two or more parts and a separator; a group-by
aggregation reads as `[name] = [function] of [column]`, wrapping after the `=` when narrow,
takes an optional quantile and an optional row filter, and an empty key list summarises the
whole frame. An aggregation names itself `<column>_<function>` (`row_count` for a row count)
while its name is empty or still the name suggested for its previous function and column,
never replacing a typed name; a new aggregation starts from the previous row's column; the
function list says what the two counts do ("count of values (skips missing values)", "row
count (every row, missing values included)"). A join reads as `[kind] join [input]`, each
kind giving its Polars name and the rows it keeps ("left: every row here, with matches
added"); its keys are pairs, `[column here] = [column there]`, the right key completing
from the joined input's own columns and filled in when a left key has the same name there;
after the join the joined input's columns are suggested as Polars names them (inner and
left joins drop the right keys, a right join drops this frame's keys, full and cross joins
keep both, semi and anti joins add nothing, and any other clashing name takes the suffix);
with one input connected a new join's input starts unset with the note "Connect the table
to join on the canvas"; and it offers an optional key-cardinality check on inner, left
and full joins (cleared when the kind changes to any other) and an output row order.
Unique can drop every duplicate. The forms accept the renderer's own shorthand for a
persisted step (a null literal without a value, a columnless window aggregate without
a column, an order key without a direction, a text join without a separator) and
canonicalise it before editing. A value in a
condition or expression is a typed literal, a column, or a variable
defined by an earlier variable step, edited as one control: a marker at its start shows
what the value is (number, text, date, true/false, missing, column, variable or
expression) over a native select of the kinds allowed there, left out when only one kind
is, and each field offers only the sources and literal
types the step schema accepts there (string operators take text values only; a variable
holds a number, text or true/false; a `null` literal is offered for expression operands
but never in a membership list or a variable; function arguments are labelled and typed
per function). A fresh condition's value follows the chosen column's type (text for a
text column, a date for a date column, true/false for a boolean column, and the number 0
otherwise or when the type is unknown); a value already edited, or a text operator's
value, is left alone. Conditions read under a lead that says what they do: a filter's as
"Keep rows where [all] of these are true", an if-then's and an aggregation row filter's as
"When [all] of these are true", the match select sitting in the sentence. "Computed as"
offers Value, Formula, Function, If-then, Window and Join text; a new column starts in
Formula, its name field taking focus first. A formula is edited as text
(`(premium + tax) * 1.05 / 12`, `round(premium / sum_insured * 1000, 3)`: columns by name or
in backticks, earlier variables by name, quoted text, `true`/`false`/`null`,
`date('YYYY-MM-DD')`, Python operator precedence with `**` right-associative (power
binds before a leading sign, while negative exponents are accepted: `-2 ** 2` is
`-(2 ** 2)`, `(-2) ** 2` is distinct, and `2 ** -2` is valid), brackets,
and the catalogue's functions with plain-value arguments, their names read in any case and
kept in the catalogue's spelling); the text is parsed into the
nested expression schema on commit and kept on the expression as typed, so brackets and
spacing survive collapsing and reopening the card and the card summary shows the same
text (text that no longer describes the expression is replaced by a fresh rendering);
a bare value or a function typed as a formula stays a formula in the editor. Column
completion preserves column identity when a name is also a literal keyword or an
earlier variable, by inserting a backticked name. Variables named after functions
remain variable references; names the formula grammar cannot represent remain in
the structured editor. Removing or reordering a variable definition must not
turn its remaining references into columns when a formula is edited. Non-finite
numeric literals are rejected before a formula
can replace the last valid expression. A new formula box starts empty
(a placeholder tree keeps the step renderable until something is typed) showing an example
formula as its placeholder, as a tooltip on the box and on an info icon beside its label, and
grows onto more lines as the formula lengthens (Enter still commits); as a name is typed the
columns (with their types) and earlier variables starting with it are listed under the box,
then the catalogue's functions, marked `ƒ` with what they do (Up/Down move, Tab, Enter or a
click takes the active entry, a column backticked when it is not an identifier, a function
arriving as `name()` with the caret between the brackets; a word that already is a name is
kept; Escape closes; while no upstream column names are known a note says to run the step
above); with the caret inside a catalogue call, a line under the box names its arguments
(`round(value, decimal places)`) with the current one bold; text that cannot be read
keeps the last good expression and explains why in an amber note, marking under the box
the character where reading stopped (the note clears as soon as the box
holds the committed formula again, and the box keeps focus after a commit); a committed
formula naming a column the step does not have (where its columns are complete) says so
with the closest name offered, the offer rewriting the formula; and an expression text cannot express (one
holding a window, conditional or text join) is edited in the structured
left/operator/right form instead. An operand field also offers an "Expression" source that
opens a nested editor (the same "Computed as" select and expression form, indented under
the field); the source is withheld at the renderer's depth cap of twelve so the editor
never builds a step it could not save. Formula commits obey the same cap, including
their enclosing expression depth: an over-depth draft stays editable with an inline
error and leaves the last valid expression unchanged. Summaries print a value or
formula in formula notation
(quoted text, `date('...')`, brackets only where re-parsing needs them, a placeholder naming
what is missing for a part not yet filled in) and describe windows, conditionals and text
joins in words; a group-by
aggregation's row filter offers no nested expressions. The Combine group also offers
"Pivot to columns" (index chips, the spread column, aggregate and values column, and
one row per output column pairing a typed value with a name the value suggests, all
rows sharing the first row's type) and "Unpivot to rows" (stacked columns, index
chips that exclude them, name and value column fields, and a note that row order is
not guaranteed). Select and drop take column types beside named columns, and an
aggregation row can target every column of a type through an `every <type> column` entry
after the names in its column box, which turns the row into `*<suffix> = <function> of
every <type> column` with the suffix `_<function>` to start (row count and row filters are
withheld there; its "one column…" choice turns it back). Column suggestions treat a dtype
selection as unresolved: a typed select keeps every upstream column suggested, a
suffix is never suggested as a column, a pivot suggests its index and output names,
and an unpivot its index plus the two new columns. Membership lists add every value
type through an explicit Add action, so a select's default (true, today's date) can
be added like any other. A locked generated-code panel shows the code the render endpoint returns for
the current steps, highlighted with the same Python parser and colours as code mode, and
carries the confirmed one-way `Switch to code` action. Its lines are linked to their steps:
pointing at or focusing a card tints that step's line range (every line of a step that
spans several), pointing at a line highlights its card, and clicking a line opens that
card (a line of the start step focuses the start selector). The code fades, with a
"rendering…" note, only when a render is still pending after 400 ms, so typing does not
flicker it. A step being built is not an error yet: while no run has failed on the node, a
render problem is shown as a neutral note, never in the error colour, and the switch to
code stays disabled. While the latest render fails, the panel keeps the last good code
dimmed and labelled out of date; a failure that names a step reads "Step 3 isn't finished:
<message>" and that card shows the same need as a muted "Needs: <message>" line, said in
plain words for the common half-built states (a new column's name or formula, a condition's
column, an aggregation's name or column, a sort, rename or change-type column, the columns
to keep or drop, a join's input or keys: "Needs a formula.", "Step 2 isn't finished: it
needs a formula."), while a
failure that names no step (a list-level validation message, or a render request that
could not reach the server) gives its message alone and marks no card. Once the node's last run has failed (the panel receives the
run's error message, or its error line), the render problem shows as an error instead: the
panel names the failing step without opening it or collapsing the card being edited (a
"Go to error" action opens it), badges that step, and tints the failing and the last
execution-error line. A run that failed on a step's lines names that step the same way,
its card's badge giving the first line of the run's own error message in the warning
tone. A Polars transform's lazy-plan failure (a missing column, a type mismatch) also
arrives with an error line: the run reports the first line of the step whose plan failed,
so that card is badged even though Polars raised the error after the code ran. A data-only
error (a strict cast meeting a bad value), a transform with a Free code step, and the steps
of a frame-mode surface still arrive without a line. Renders are tagged with
the steps revision they were requested for, a response for an older revision never
replaces a newer one, and a response after the editor unmounts never writes code
back to the graph. Column and variable suggestions are computed only for the
open card. The switch is enabled only while the step list is empty or the
latest render succeeded for the current revision; on confirmation it writes that rendered
code (or empty code for an empty list) into `code` and removes `steps`. After each
successful render the rendered code is also written into `code` so read-only views stay
current. This generated cache update creates no undo entry, preserves redo, does
not dirty the document, and does not invalidate execution previews. Authored step
changes still invalidate execution previews. One step edit is one undo action. Undo/redo re-renders the
restored steps, and an obsolete render must not replace their code. As soon as the
start input is known (chosen in the selector, or the node's only
connected input) the start step is written to the config, so the node renders
`df = <input>` and can be previewed before any step is added. A
node without inputs cannot add steps and is told to connect an input or switch to code,
with the switch offered there under the same rule as the code panel's (disabled, with the
reason as its tooltip, while persisted steps cannot render). Renaming an upstream node rewrites the input references
inside a stepped transform's steps instead of recording an `inputMapping` binding on it.
A node whose steps were discarded on load shows the discard reason above the code box.
A persisted step whose shape the forms cannot edit (an unknown kind or a missing
setting, including a kind named after an inherited JavaScript object property)
renders as an invalid card that can only be deleted, never crashes the editor,
and remains visible even in the first position where the start step belongs.
Choosing a start input repairs that position without dropping an existing
non-source step. An invalid existing start prompts for an input even when only
one is connected; a persisted input that is no longer connected remains visible
as unavailable until another input is chosen. Adding steps requires a connected
start input. A missing step id is an invalid persisted shape. Invalid steps
are skipped by column and variable suggestions; the backend already keeps such a list
behind an incomplete body. A membership list keeps its chosen value type while empty.
Numeric controls parse the complete value, including scientific notation (`1e3`
commits as 1000). Integer controls reject fractional values instead of truncating;
empty, non-finite and below-minimum values never replace the committed value and
report a visible validation error.

The Add step chooser also offers **Free code** in a Code section. Its card embeds
the shared Python code editor, starts empty, and has no explanatory text below
the editor. The editor starts at a compact 120px height, can be resized vertically,
and fills the available height as its box grows. It can
be edited, reordered and deleted like any other step, and can be followed by
low-code steps. Column completion uses the columns known before the snippet;
after arbitrary code the editor does not infer its output schema, so later
column fields accept names typed by the user. Collapsed cards show the first
nonblank line of code or a prompt to write code. The render response's inclusive
line ranges map runtime errors to cards, including steps after multiline
snippets, and the generated-code panel highlights the runtime error's exact
line. Validation failures badge the offending card and show an error message;
their generated code is shown dimmed as out of date, never as current. Pending or failed renders do not reuse
stale line ranges to blame a different current step.

Nodes whose config has no `steps` list render the code box exactly as before.

**Stepped code pane.** The Transform editor's mode switch is a shared pane
(`SteppedCodePane`): a `steps` list renders the step builder, anything else the code
box, with the discard notice above it when steps were discarded on load. The pane
takes a start mode. `input` is the Transform's, as described above. `frame` is for a
surface whose code runs with `df` already bound: there is no start card at all (the
frame is the node's own, so there is nothing to choose or explain), no start step is
written or accepted (a persisted `source` step renders as an invalid
card that can only be deleted), cards are numbered from Step 1, every card can be moved,
the first card can be opened by "Go to error", and steps can be added without choosing
an input. The pane's step editor renders against the surface's eligible input names,
which come from the same table the backend uses (`edges` for a Transform, `none` for a
Data Input) rather than from the input chips it displays, so it never offers a join the
executor would refuse: while that list is empty the `Add step` chooser withholds join
and concat and keeps group by, pivot and unpivot. Column suggestions use the same
upstream columns the code box used. An empty frame-mode list renders to empty code, so
the confirmed switch to code on an empty list writes empty code, and the node behaves
exactly as with an empty code box until a step is added. Every Polars tab (Data Input,
External File, Scenario Expander, Rating Step, Model Score) and Explore's own "Polars
Code" pane mount this pane in `frame` mode, with the surface's eligible input names from the shared table: every connected
input for an External File (whose free code still reaches `obj`, as the tab's code hint
says), none for the others. A new node of each of these types starts in step mode with
an empty list, and changing a Data Input's provider or format keeps its steps as it
keeps its code. Renaming an upstream node rewrites the input references inside a stepped
External File's steps, as it does for a Transform. A node loaded without a `steps` list
stays in code mode; the switch is one way.

## Design rationale

The UI uses specialised editors rather than one schema-driven form because graph node contracts
are structurally different. Shared helpers centralise the places where consistency matters:
commit timing, clipboard parsing, path handling, rendered input-source chips, and normalisation
of persisted banding/rating data. Rating normalisation preserves optional factor-dtype
descriptors and ordered entry rows so opening and saving a table cannot erase backend-owned
lookup identity. Lazy dispatch keeps editor code out of the initial canvas load.
Accessibility automation is deliberately risk-based: component tests enforce
roles, names, descriptions, invalid state, and focus behaviour, while the
stable cross-editor Playwright journey enforces keyboard completion and
reviewed desktop/narrow visuals. Haute does not currently run a blanket DOM
scanner or claim whole-application WCAG conformance.

Display identity and executable identity are one identity. `InputSource.name` is the input's
single name — the chip text, the code argument, and the key persisted contracts use (the
live-switch `input_scenario_map` and the instance `inputMapping`, both consumed by the
backend). It is read per edge by the shared `edgeInputName` helper from server-owned editor
identity metadata, so the panel can never advertise a name the code does not
recognise.

## Interactions

The panel consumes selected-node/edge state from
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md), API capabilities from
[server-api](../server-api/high-level.md), and modelling/optimiser configuration panels from
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md). Preview
columns and rows are supplied by the execution/result stores, not computed by these editors.
Frame display labels for input chips and output frame naming are resolved through the
api-input frame-identity helpers owned by
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)
(`frontend/src/utils/apiInputPorts.ts`).

## Failure model

Client-side parse and shape checks show inline invalid state where implemented. Server failures
such as format, file, Databricks, or MLflow lookup errors are rendered by the invoking editor.
Malformed config that cannot be interpreted is surfaced as a visible diagnostic or an explicit
editor error; the component does not silently replace it with invented configuration. A dangling
`sourceHandle` (an edge bound to a frame that no longer exists) is displayed **verbatim** as the
edge's frame identity with an explicit unresolved warning state wherever the connection is
presented (input chips, live-switch mapping rows, output frame blocks) — never silently renamed
to the parent node and never a normal-looking entry. WebSocket-synchronised graphs can retain a
null-handle API-input edge so the user can repair the source file without silently losing
topology. Such an edge is displayed with the explicit `<unresolved>` marker and warning state;
it never crashes the panel or aliases the API input's sole emitted table.
An Edge Join with missing/ambiguous role edges, an unknown join mode, or invalid key shape remains
visibly invalid and blocks save; the editor never infers a role or silently substitutes join
keys. Edge Join diagnostics use the danger/error treatment rather than warning colours. Empty or
otherwise invalid visible join-key controls expose `aria-invalid` and a red border; when a
non-cross join has no keys, this applies to the required control or controls in the active key
mode. The incoming edges' `base`/`join` target handles are the only persisted role authority;
the removed `baseInput`/`joinInput` config representation has no compatibility path.

## Recovery diagnostics in node presentation

Editor-load availability is separate from transient execution status. A known recovered node uses
its normal canvas card with an accessible `unavailable` or `blocked` load indicator; an unknown or
removed authored decorator uses a dedicated recovery-only card that is not present in the palette
and cannot serialize as a canonical node. Unavailable cards retain authored identity and decorator
spelling instead of coercing the node to a supported type.

Selecting an unavailable or blocked node opens a read-only diagnostic inspector rather than its
normal configuration editor. The inspector shows attributed messages, remediation, source/config
location, incident id, and blocking path where present. Ready siblings in a degraded document may
be inspected through a static read-only configuration view. Normal editors are not mounted in
that state, so editor effects cannot start schema, preview, training, cache, or publication work
behind disabled controls. Selection, panning, zooming, recovery preview, and diagnostic inspection
remain available.

## Minimal unavailable-node removal

An unavailable node inspector may offer `Remove node` only when the validated
document capability allows repair and the node has a server recovery identity.
The action first opens a confirmation surface; it never invokes normal node
deletion, graph Save, codegen, or a client-authored source rewrite. The surface
states what the action does and offers an explicit choice to delete the config
file the node references, if it has one (kept by default; the server deletes
exactly the authored reference and refuses a shared or managed file). There is
no preview of the patch before applying.

Confirmation applies the action against the displayed document revision. A revision conflict,
implicit downstream consumer, ambiguous identity/span, mixed connection
chain, shared config, or server verification failure stays visible and leaves
the recovery inspector open. Success adopts the returned editor document and
closes the removed node's panel. Blocked and ready nodes never expose this
action. Known ordinary unavailable nodes offer `Recover settings` (primary) and `Reset node`. These actions follow the
server-owned apply contract in
[node recovery actions](../server-api/node-recovery-actions.md). Blocked nodes expose no
reset or recover action. Reset confirmation explicitly describes replaced settings/code and
required reconfiguration; a successful recover records a dismissible session summary, with
what it could not fix listed as still to complete, of retained/defaulted/needs-input/removed
fields with on-demand previous-configuration and diff views; updating a submodel preserves
its contents and consumer code. In degraded documents, `scoped_editable` nodes keep their
normal editors and save through the node-scoped save, which adopts the authoritative
document while whole-graph fences stay in place.
