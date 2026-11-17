# Frontend preview and Explore roadmap

## Scope

The preview pane and the Explore node's panes, as [the frontend preview and
Explore specification](../frontend-preview-explore/high-level.md) describes
them: what a Refresh re-reads once the data under a node has changed. The
packages below come from the review plan's browser sweep of 10 November 2026
("state that outlives its moment"). Each was reproduced in a real browser
against a local server, first by the sweep and then by the main session on
the scaffolded test project, with a data file replaced on disk while the
editor was open. The main session added the Banding editor's case.

`FEP-04` comes from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker drove its journey in a browser and the main session ran it again on
15 November 2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEP-01 | Planned | P2 | After a data file's columns change, one Refresh previews the file as it is, instead of failing on the columns the editor remembered. |
| FEP-02 | Planned | P2 | A node's Refresh brings the Explore overview, and the Banding editor's counts and values, up to date with the data, as it does the preview. |
| FEP-04 | Planned | P2 | An edit to one utility file made while another is being deleted is kept and saved. |

## Planned improvements

### FEP-01 — A Refresh reads the file as it now is
**Why:** Reproduced on 10 November 2026 in the editor. A Data Input over a
Parquet file of six columns is previewed. The file is then replaced by a new
extract without one of them, `mileage`. Every Refresh answers "Requested
preview column(s) not found on target: mileage": the table empties and that
line is all the pane shows. The analyst asked for no column. The editor
remembers the columns of the last preview and sends them as the columns to
fetch, and the server refuses a name the data no longer has. Selecting
another node and coming back shows the old table again, `mileage` included,
with no request and no message. Only reloading the page reads the file as it
is.

A file that gains a column previews, but the new column appears only at the
second Refresh: the first still asks for the remembered six. With a wide
file cut from 60 columns to 10, a Transform and an Explore node downstream
fail their first Refresh with the engine's "Eager projection references
columns missing" and work at the second (seen by the sweep and in the main
session's re-run of it). A renamed column takes the same path and was not
run.

The specification's rule for the remembered layout ("Preview column
freshness") lets it seed a request only while the graph's structure and the
active source are the ones that produced it. Neither changes when the data
under the node does, and that is the case Refresh exists for.

**Plan:** A Refresh sends no remembered columns, so the server reports the
schema it finds, as after an edit. As a second line, when the server answers
that a requested column is not there, drop the remembered layout and ask
once more without it. Drop the node's cached preview with it, so that coming
back to the node does not show the table from before.

**Acceptance:** After a previewed Data Input's file loses a column, gains
one, or has one renamed, one Refresh shows the file's columns and rows on
that node and on a node downstream of it; no message names columns the
analyst did not ask for; and coming back to the node shows the same table. A
browser test replaces the file under a previewed node.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/usePipelineAPI.ts::previewColumnNamesForNode`;
`src/haute/executor.py::_preview_projection_columns`.

### FEP-02 — A Refresh reaches the data the Explore panes and the editors read
**Why:** Reproduced on 10 November 2026 in the editor. An Explore node's
Overview over a Data Input of 30 rows reads "ROWS 30", with the `value`
column running from 11 to 129. The file is replaced by a new extract with
the same columns, 12 rows and other values, and the analyst presses the
frame's Refresh, the only Refresh the page offers for that node, titled
"Refresh Explore outputs". The Preview tab then shows the 12 new rows. The
Overview still reads "ROWS 30" and "CACHED just now", with the old
statistics, however often Refresh is pressed and after a visit to the
Preview tab: no request for the data point or its profile is made. Selecting
another node and coming back, or reloading the page, asks again and shows 12
rows. It is the same over a Parquet file read directly and over a CSV file
that Haute snapshots, and with changed columns the schema card keeps listing
the old ones (60 where the file has 10).

The Banding editor reads the same shared data and is left behind in the same
way. With its data cached, its basis line reads "All rows · 30" and its
categorical factor knows the column's two values. After the file is replaced
by 12 rows that hold a third value, two Refreshes leave the line at "All
rows · 30" and the editor without the new value, while the preview table
beside it shows the 12 rows and the new value falling to the factor's
default. Selecting another node and coming back turns the line into "Cached
data is out of date · Refresh this node to count all rows". An analyst who
refreshes to map a new level is shown the old levels as the whole data. The
Rating Step's level list and Explore's Relationships pane read the data
through the same hook (read, not run).

The specification says Explore "renders statistics only from the shared
profile of the data version that point currently holds", and that the node's
Refresh is what deals with data that is "missing, stale, partial or
unreadable". The Refresh decides from what the page last heard about the
data: it leaves alone data it believes current, and nothing asks the server
whether it still is. So the statistics an analyst reads after refreshing can
describe a file that no longer exists, beside a Preview tab that shows the
new one.

**Plan:** On Refresh, resolve the node's data point again before deciding.
When its data version has moved, fetch the profile for the new version and
let the cards, pivots and charts follow it as they do after a reload, and
let the editors' whole-data answers do the same. One thing then needs care:
the schema card keeps its search text in state and filters by it even when
the schema is too short to show the search box, so a long schema searched
and then refreshed to a short one would hide columns with no control left to
clear the search (read from the source; today the card is remounted before
its schema can change under it). Clear the search when the box goes.

**Acceptance:** After the file under an Explore node is replaced, one
Refresh makes the Overview's dataset, numeric and schema cards show the new
data, for a Parquet and for a CSV input; the Banding editor's basis line and
values follow the new file after the same one Refresh; a Refresh on
unchanged data still starts no rebuild. Pivots, Charts, Relationships and
the Rating Step's levels, which this run did not exercise, are held by the
same test. A schema refreshed from long to short shows every column.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/ExplorePreview.tsx`;
`frontend/src/hooks/useNodeDataCache.ts::refreshNodeDataCache`;
`frontend/src/hooks/useNodeDataProfile.ts`;
`frontend/src/panels/editors/shared/useWholeDataAnswer.ts`;
`frontend/src/panels/editors/banding/useBandingStats.ts`;
`frontend/src/panels/explore/SchemaTableCard.tsx`.

### FEP-04 — A utility file edited while another is being deleted keeps its edit
**Why:** Reproduced on 15 November 2026 in the browser with the delete's
answer held back. `old_rates` was deleted in the Utility panel; while the
answer was on its way `new_rates` was chosen and `x * 2` changed to
`x * 3`. When the answer arrived the panel jumped to the first file, the
edit left the editor, no save of `new_rates` was sent, and the file still
holds `x * 2`. No message. The handler clears the draft and the chosen
file after the delete answers, whichever file is open by then.

**Plan:** After a delete answers, clear the draft only when the open
file is the deleted one; or keep the file selector disabled until the
answer.

**Acceptance:** The journey above ends with `x * 3` in the editor and
saved on close, or the selector cannot be used while the delete runs.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/UtilityPanel.tsx`;
`specs/frontend-preview-explore/low-level.md`.
