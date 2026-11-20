# Frontend shared roadmap

## Scope

The browser's shared code, as [the frontend shared
specification](../frontend-shared/high-level.md) describes it: what a failed
call says on the screen, and the panel values held outside the shared draft. `FES-03`
comes from the plan's browser sweep of failed calls on 11 November and the
main session's runs of that day in the editor and the underwriter app.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-03 | Planned | P3 | A call that got no answer, or an answer that is not Haute's, says that the server could not be reached, not the browser's or the JSON parser's own words, and is neither offered to the assistant as a node's error nor reported as a new version of Haute. |
| FES-07 | Planned | P3 | The panel values still held outside the shared draft follow it: a code editor's typing (a decision), the colour picker, the Columns tab's empty selection, and the lists whose fields are keyed by position. |

## Planned improvements

### FES-07 — The last panel values outside the shared draft
**Why:** Read on 18 November 2026, when the shared draft
(`frontend/src/components/form/useCommittedDraft.ts`) became the path for
every typed field of the panels and nodes and the guard
(`frontend/src/components/form/__tests__/draftsByHand.test.ts`) was added.
Four places still hold a value being edited another way. A code editor
(`CodeMirrorEditor.tsx`) keeps its own rule, deliberately and under test:
a change from outside that arrives while it holds typing not yet flushed
is held back, and dropped if the typing is then committed, so a live-sync
frame or the assistant's change can be lost under it, the opposite of
every other field; its unflushed text is also unknown to `isDraftOpen`.
The colour picker (`components/form/ColourSwatches.tsx`) keeps its own
draft so a pick previews tick by tick, and with a preview its draft
survives a change from outside. The Columns tab (`ColumnsTab.tsx`) keeps
an empty selection as a draft tagged with the selection it was made over,
which is correct but its own. And some lists still key their fields by
position: the API Input's tables and columns, the Edge Join's key boxes,
and the field of the selected Banding factor or Rating Step table keyed by
the selected index. A shared field's draft drops when its own value moves,
so only a change from outside while the analyst types that leaves the same
text at the same place could carry one; nobody has seen it.

**Plan:** Decide the code editor's rule: the stored value wins, as for every
field, once its consumers are shown to store exactly what it sends (a
transformed echo would otherwise replace the text being typed); register
its unflushed text with the shared draft either way. Give the colour picker
the shared draft, its previews moving the draft's basis the way a commit
that stays does. Give the Columns tab's empty selection the shared draft.
Key the remaining lists by `useRowKeys`, the selected item's field by its
row's key, and teach the guard to find a shared field keyed by an index.

**Acceptance:** The entries are drivers of
`frontend/src/components/form/__tests__/panelDraftsUndo.test.tsx`; the
guard fails for a shared field given an index as its key or owner.

**Dependencies:** A decision on the code editor's rule.

**Evidence:** `frontend/src/panels/editors/CodeMirrorEditor.tsx`;
`frontend/src/panels/editors/__tests__/_shared.test.tsx`;
`frontend/src/components/form/ColourSwatches.tsx`;
`frontend/src/panels/editors/ColumnsTab.tsx`;
`frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/EdgeJoinEditor.tsx`.

### FES-03 — A call that got no answer of Haute's says so in words
**Why:** Reproduced in a browser on 11 November 2026, in the editor and in
the underwriter app. When a call gets no answer, or an answer that is not
Haute's, the screen shows whatever text the failure happened to carry:

- No answer, the connection lost: the preview pane reads "Failed to fetch"
  and nothing else, a save "Failed to save pipeline: Failed to fetch", and
  the underwriter app's save "Failed to fetch". The words are the
  browser's.
- A sign-in page answered with 200, as a proxy may send once a session has
  run out: the preview pane reads
  `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`, and a save
  the same after "Failed to save pipeline:". The words are the JSON
  parser's.
- A gateway's 502 or 504 page: "Bad Gateway" and "Gateway Timeout", the
  status line alone.
- A preview that got no answer is offered to the assistant as the node's
  fault: under "Failed to fetch" the pane shows "Ask the assistant to fix",
  and pressing it opens the assistant with "Fix the error this node raises
  when it runs." and the chip "Run error: enriched", for a node that raised
  nothing.
- A part of the page that is fetched on first use, with the connection away
  for two seconds as a node of a kind not yet opened is clicked: the node's
  panel reads "Haute has been updated" and "This page was opened before the
  update and cannot load this part of it. Reload to use the new version.",
  and still does ten seconds after the connection is back. Nothing was
  updated. The specification gives that fallback to a part the rebuilt
  server no longer has. Its test is the browser's message for a part that
  could not be fetched, which is the same when the server did not answer
  (seen on the development server).

None of them tells an analyst, or an underwriter, what happened: that the
server could not be reached, or that something between them and Haute
answered in its place, and that what they typed is still on the screen.
One helper makes every such message: it returns the server's detail when
there is one, and otherwise the thrown error's own text. A page load is
the exception and shows the pattern to follow: after its retries it says
"Haute could not open this pipeline" and explains (run: 12 seconds of
"Loading pipeline...", then the message).

**Plan:** In the one helper, name the three cases before falling back to
the error's text: a request that got no answer (the fetch's own failure
or a timeout), an answer that is not JSON where JSON was due, and a status
of 502, 503 or 504 without a detail of Haute's. Say that the server could
not be reached, or that the answer did not come from Haute, with the
status when there is one, and keep the error's own text for the console.
Offer "Ask the assistant to fix" only under an error the node raised. For a
part of the page that could not be fetched, tell a file the server no longer
has from a server that did not answer, and for the second say so and offer
to try again.

**Acceptance:** With the connection cut, a preview and a save in the editor
and a save in the underwriter app each say that the server could not be
reached, and none shows "Failed to fetch". With a sign-in page answered
with 200, and with a gateway's 502 page, each says that the answer did
not come from Haute. A detail the server sends is shown as it is today.
A preview that got no answer shows no "Ask the assistant to fix". With the
connection cut as a panel is first opened, the panel says that the server
could not be reached and loads once it is back, without a page reload; with
the part's file gone it says "Haute has been updated" as today.

**Dependencies:** None.

**Evidence:** `frontend/src/api/errors.ts::apiErrorMessage`;
`frontend/src/hooks/usePipelineAPI.ts::usePipelineAPI`;
`frontend/src/app/useOpenQuoteStore.ts`;
`frontend/src/panels/DataPreview.tsx`;
`frontend/src/components/AskAssistantButton.tsx`;
`frontend/src/utils/chunkLoadError.ts::isChunkLoadError`;
`frontend/src/components/ErrorBoundary.tsx`.
