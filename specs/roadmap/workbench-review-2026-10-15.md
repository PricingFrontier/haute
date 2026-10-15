# Workbench review — 15 October 2026

Reviewed at `main` @ `db7d03a5`: what pull requests #303 to #309 built (`git diff d88ad14d db7d03a5`, 456 files, about 10K lines of production Python, 15K of TypeScript, and their tests, specifications and documentation). The review read the code and changed none of it. This is a dated supporting report: its findings are tracked as packages in the [Workbench](workbench.md), [bugs](bugs.md) and [frontend shared](frontend-shared.md) roadmaps, mapped in [Where each finding is tracked](#where-each-finding-is-tracked), and those packages, not this report, carry the plans.

## How the review was done

- **Codex** (`gpt-6-astra` at `ultra`, read-only), five passes:
  - A, architecture, duplication and drift across the whole system
  - B, correctness at the seams between the pull requests
  - C, the frontend line by line
  - D, the backend line by line
  - E, the specifications and the tests

  Each pass was told what the earlier ones had reported.
- **Fable 5.1**, six reviews, one per mechanism, two at a time:
  - pricing, visibility and preset rows
  - the quote store and the app's quotes
  - the form, its revision and the one Save
  - the sheets UI
  - test quotes, deploy, the CLI and the release
  - project files, git and the shared UI kit

  Some confirmed a defect with a script or a test against a temporary project or SQLite file, kept outside the repository.
- **Verification.** Every finding was read at its cited lines before it was recorded here; overlapping findings are merged under one ID. The severities follow `AGENTS.md` (Critical, Major, Minor, Suggestion) after verification. Where a reviewer rated a finding differently, [Downgraded, upgraded or unconfirmed](#downgraded-upgraded-or-unconfirmed) says why.
- **Checks.**
  - CI was green on `db7d03a5` and on the head of PR #309.
  - No `skip`, `xfail`, `.skip(` or `.todo(` was added anywhere in the change.
  - No coverage gate was lowered, and one was added (`src/app/use*Store.ts`).
  - Contract drift is guarded by `check:contracts` and the OpenAPI fingerprint test.

**Limits.** Browser behaviour was not run in a browser; FB-30 in particular rests on Chromium's documented select behaviour and is marked unconfirmed. Concurrency findings on a server database are traced, not executed: no server engine exists yet ([WB-10](workbench.md#wb-10--the-quote-store)). The Workbench is unreleased, so backward compatibility, rollback and old-file handling were out of scope and are rated no higher than Minor.

**IDs.** `CX-n` came from a Codex pass, `FB-n` from a Fable review, `TQ-n` and `SD-n` from the tests-and-specifications pass, and `MS-n` from the main session's own reading. They name findings, not roadmap packages.

## Summary

**Request changes.** The architecture needs no rework; the implementation has one Critical finding, 17 Major findings and 4 Major test gaps to fix, then a consolidation pass over mechanisms written several times.

What is sound and should stay:
- **One request builder and one reader.** `sample_quote`/`quote_request` build every request: Preview's (through `POST /api/workbench/tables`), the app's price route's, and test-quote save, run and accept. `read_quote` reads every request, in previews and in the deployed scorer. `test_a_sample_prices_as_its_quote_would_when_deployed` pins it.
- **One engine and one comparer.** `score_graph` with `QuoteRequest` prices a quote in the dry run, the container, MLflow and the app; `response_changes` serves the panel's compare, the report, the app's `_moved`, deploy's check and the smoke test.
- **Visibility and preset rows**, mirrored in TypeScript and Python, are each held to one JSON fixture both suites read; the quote_details columns are held to a parity test.
- **The quote store.** Each write is one transaction; revisions move by compare-and-set; references come from a counter row locked in the creating transaction; operation keys are written last and replayed as their write left the quote. It is correct on SQLite and under PostgreSQL's READ COMMITTED.
- **The form file.** One canonical, strict shape; the revision is the content hash of its bytes; writes are atomic.
- **The node types.** They join Haute through the request-input and response classifications, enforced by an AST test; codegen round trips pass.
- **The test-quote file in git** is never merged as text; the merge driver's case table is right.
- **The project-file writer** refuses before reading a body, stages outside the versioned folder, and publishes with one no-clobber or revision-checked rename under both locks.

What did not hold: the places where mechanisms from different pull requests meet (Preview and the pipeline, CI and the panel, the app's retries and the store's keys, the container and Preview), and mechanisms written several times, slightly differently each time (stale answers, the typed rule, the pricing basis, the project writers, the response encoding, the confirmations).

| Severity | Count |
|---|---:|
| Critical | 1 |
| Major (production; FB-30 unconfirmed) | 17 |
| Major (tests that pass against a defect) | 4 |
| Minor | 59 |
| Suggestion | 15 |

| ID | Severity | Finding | Tracked by |
|---|---|---|---|
| CX-22 | Critical | `haute app build` deletes its output folder, `.git` included, whenever a file named `release.json` is in it | `WB-14` |
| CX-1 | Major | Preview shows an old pipeline's price as current after the Assistant, a branch switch, a data file replaced or an edit on disk | `WB-18` |
| FB-13 | Major | Preview prices a pipeline that never reads the sheets (a Quote Input beside a Workbench Output): every value gives the same premium | `WB-18` |
| CX-2 | Major | CI replays each test quote's stored request; the panel rebuilds it, so form changes escape the check the specification promises | `WB-16` |
| FB-50 | Major | The container prunes nulls and empty tables from a Workbench response, so `haute smoke` fails what the dry run passed | `WB-16` |
| FB-51 | Major | An answer whose values are all null or whose tables are all empty renders to no document: the app's price route answers 500 | `WB-16` |
| CX-23 | Major | Deploy and `haute app build` skip the saved test quotes when `haute.toml` has no `[test_quotes]` table | `WB-16` |
| CX-3 | Major | After a lost answer (any 5xx, an edit, a reload), the next save of a new quote creates a second quote | `WB-15` |
| CX-14 | Major | Mark final, Reopen and Assign act on whichever quote is open when the dialog is answered | `WB-14` |
| FB-1 | Major | Duplicate, Renew and Create alternative open "Unsaved", ask before leaving, and later offer the carried values back | `WB-20` |
| CX-4 | Major | A held value no longer allowed draws as a blank select while it is priced | `WB-18` |
| FB-19 | Major | A table named `quote_details` blocks Save with a 5 KB JSON toast | `WB-19` |
| FB-28 | Major | Delete, the arrows and Ctrl/Cmd+D act on the selected component from inside the properties panel | `WB-14` |
| FB-29 | Major | A sideways drag moves a component 8 px down whenever a section above has a height off the grid | `WB-21` |
| FB-30 | Major, unconfirmed | Picking from a select in Preview may leave the sections held until the next click | `WB-18` |
| FB-43 | Major | Pipeline files answers 500 without git, and without a repository promises deletes stay in version history | `BUG-32` |
| FB-44 | Major | Commit sweeps and publishes dot-files the Files panels hide | `BUG-33` |
| FB-45 | Major | The identity prompt's retry never captures the uploaded file it was raised for | `BUG-34` |
| TQ-1 to TQ-4 | Major (tests) | The digest test, the reader-role test, the app's mocked request bodies and the storeless pricing test would each pass against a defect | `WB-15`, `WB-16`, `WB-20` |

## 1. Actions on the right target — `WB-14`

- **CX-22 [Critical] `haute app build` deletes its output folder whenever `release.json` is in it.** `src/haute/_workbench_app.py:277-282` takes any non-empty folder holding a file of that name as an earlier release and runs `shutil.rmtree` on it; `src/haute/cli/_app.py:76` resolves whatever OUTPUT is given. `haute app build .` in a project with a stray `release.json` at its root deletes the project, `.git` included. The default output, `.haute/app/release`, is safe. Verified by reading.
- **CX-14 [Major] Mark final, Reopen and Assign can act on another quote.**
  - The handlers in `frontend/src/app/QuoteView.tsx:275-294, 411-425` capture the quote, await the app's one dialog (`useAppStore.ask`, drawn by `UnderwriterApp.tsx:62`, which outlives a page change), then call `change`, which reads whichever quote is open by then (`useOpenQuoteStore.ts:461`).
  - Back during "Mark Q-000001 final?" opens another quote. Confirming sends that quote's version with the first quote's revision number; when the numbers agree, the server's check (`_book.py:873`) passes and the other quote goes final under a toast naming the first.
  - Verified by reading.
- **FB-28 [Major] The sheet's selection keys act from inside the properties panel.** `useWorkbenchShortcuts.ts:54` exempts only targets `isFormControl` (`keyboardTargets.ts:33-35`) recognises: inputs, textareas, selects and the code editor. From a focused Order row (a focusable div taking Alt+arrows, `PropertiesPanel.tsx:721-735`), a pane tab or a panel button, Delete or Backspace removes the whole component, the arrows nudge it, and Ctrl/Cmd+D duplicates it. Verified by reading.
- **CX-27 [Minor] The test-quote file and `.gitattributes` are written in place through a hard link.** `_test_quotes.py:57, 68, 130` resolve through `contained_path`, which cannot see a hard link; SQLite and the `.gitattributes` append write in place. The Files panels refuse hard links through `ordinary_path_problem`. Git never checks a hard link out, so only one made locally reaches this. Verified by reading.

## 2. A quote is written once — `WB-15`

- **CX-3 [Major] A lost answer turns a create into two quotes.** The browser drops the operation key in three cases where the server may already have written:
  - On any answer with an HTTP status (`failed`, `useOpenQuoteStore.ts:216-218`), although a 502 or 504 from the ingress during a rollout, and a 500 raised after the book's commit, follow a write that landed.
  - When the values or the price shown change (`saveBody`, `:274-287`).
  - On a reload, since the draft does not keep it (`keepDraft`, `:251-267`).

  The next Save of a new quote mints a fresh key, and `create_quote` creates a second quote under a new reference. The server side is right (`_or_replayed`, `_book.py:924-939`, tested). The browser's test covers a 422 and a `TypeError`, never a 5xx. Found by two reviewers; verified by reading.
- **FB-3 [Minor] A change made while a save is in flight is refused as a conflict with the user's own save.** `save` guards only `saving` and `change` only `changing` (`useOpenQuoteStore.ts:391-394, 460-464`); Reopen, Archive, Restore, the assignee and Save note are disabled by `changing` alone (`QuoteView.tsx:322-324, 370`).
- **CX-11 [Minor] A note can land on a quote archived meanwhile.** `set_note` (`_book.py:908-920`) checks `archived` with a plain read and inserts its event without the quote's row lock. Reproduced on SQLite with a script: events `created, archived, noted`.
- **FB-6 [Minor] The quote's name goes into `haute_quotes.name`, a `String(200)`, unchecked.** `_book.py:728` copies the free-text `quote_name` cell, while the other details are `Text`. A server engine refuses a longer value with a data error that is not a `QuoteStoreError`, which the routes answer as 500.
- **FB-7 [Minor] `render_as_batch=True` does not give runtime batch mode.** `migrations/env.py:18-19` and the low-level specification say every migration runs in batch mode on SQLite. The flag only changes how `--autogenerate` writes scripts, so a future `op.alter_column` on SQLite fails unless the script uses `op.batch_alter_table`.
- **TQ-2 [Major, test] The reader-role test covers create and the change routes, not save or note.** `tests/test_workbench_app_quotes.py:455-496`; `save_quote` and `note` (`_workbench_app_quotes.py:374-400, 456-461`) each call `_editor` themselves, so removing either call passes the suite.
- **TQ-3 [Major, test] The app's API client is mocked out of every app test.** The request bodies are built untyped (`frontend/src/api/app.ts:100-130`), backend tests build their own, and the browser journey covers the editor alone. Renaming `version` would pass tsc and every test.

## 3. The regression gate — `WB-16`

- **CX-2 [Major] CI and `haute app build` replay each test quote's stored request.** `_saved_test_quote_cases` (`deploy/_validators.py:127-153`) prices `revision.request`. The panel's Run instead carries the values onto the current form, rebuilds the request, and compares request and response (`routes/workbench.py:446-486`). A Show-when change, a params refresh (`haute app build` reads allowed values again, `_workbench_app.py:274`) or a preset-rows change alters what the app sends while the check passes. This contradicts the specification's "a change the panel shows is a change the check fails on" (`specs/workbench/high-level.md:765-767`).
- **CX-23 [Major] Deploy reads `[test_quotes] dir` with no default.** The editor saves test quotes in `tests/quotes` when the table is absent (`_test_quotes.py:45-57`), but `deploy/_config.py:334` reads none, so `validate_deploy` checks no saved test quote. `haute init --workbench` writes the table, so hand-enabled projects are the ones affected.
- **FB-50 [Major] A Workbench response has three encodings.**
  - Preview keeps a Workbench Output's rows as `to_dicts()`, because only a Quote Response is rendered (`executor.py:1603-1609`). The accepted response and the dry run's comparison (`_validators.py:459-466`) use that shape, and MLflow answers it too.
  - The container renders through `render_output_document` (`deploy/_container.py:516-529`), whose `_prune` drops null fields, empty lists and empty objects (`_output_assembler.py:174-206`).
  - `haute smoke` against a container target therefore reports every accepted null and every empty many-row table as removed. The smoke tests' mocked answers hold no null.

  Confirmed with a script.
- **FB-51 [Major] An answer whose values are all null, or whose tables are all empty, renders to no document.** `_prune` drops a top-level row that pruned to `{}`, so `[document] = render_output_document(result)` (`_workbench_app.py:538`) raises into FastAPI's 500, and the container answers `rows: []` for a one-row request. Reached by a Workbench Output of many-row tables alone with a quote that has no rows, or by a connected table not yet mapped, both of which Preview prices. Confirmed with a script.
- **FB-14 [Minor] A test-quote run has no "nothing typed" gate.** `run` (`useWorkbenchTestQuotesStore.ts:263-307`) prices a quote whose columns were all removed as the null quote, one null row per table (`_workbench_input.py:194-201`), which no deployed request is. Accepting it stores a request the check then fails. `save` refuses the same quote through `quoteRefusal`.
- **TQ-1 [Major, test] The digest test never makes two different revisions with one number.** `tests/test_quote_store.py:364-387` refuses an accept by comparing revision 1's digest with revision 2's, so a digest of the quote and number alone would pass. The case it names, two branches each saving a revision 2, is never built.

## 4. Test quotes in the panel and in git — `WB-17`

- **CX-6 [Minor] Test-quote lists and opens land in completion order.** `useWorkbenchTestQuotesStore.ts:199-210, 244-260` have no ticket and no document check, where `run` has one. A late open replaces a newer sample; an open across a branch switch writes values carried onto the old form into the new one.
- **CX-9 [Minor] Opening a test quote, and answering any write, reads every quote's summary.** `routes/workbench.py:340, 425` filter `store.quotes()` in Python, so a run of N quotes reads N² summaries.
- **FB-54 [Minor] Every test-quote read runs three `git config` calls, and a failed registration is silent.** `_test_quotes.py:119-122` discards the result of the set; about 7 s of a 30-quote run went on it.
- **FB-55 [Minor] The git driver warns on every diff and merge.** `python -m haute.cli._test_quotes` imports the module through its package and again as `__main__` (`cli/__init__.py:16`), printing a RuntimeWarning to git's terminal.
- **MS-1 [Minor] The GitLab report compares with the target's tip.** The generated job runs `haute test-quotes report --base origin/<target>` on a merge-request pipeline, which checks out the source branch. `cli/_test_quotes.py:65-78` reads the file at that tip, not at the merge base, so quotes accepted on the target since the branch point read as this branch's. GitHub and Azure DevOps check out the merge commit and are right.
- **FB-57 [Suggestion] The test-quote git workflow:**
  - a conflict, a clone without the drivers and a stale interpreter path are all safe, keeping ours, but none says how to resolve;
  - `haute init --workbench` writes the file without its `.gitattributes` line;
  - the GitHub report's comment step fails on a fork's pull request;
  - the underwriter app's bundle (1.35 MB raw) has no size budget.

## 5. What the sheets show is what is priced — `WB-18`

- **CX-1 [Major] Preview shows a price for an old pipeline as current.**
  - The pricer is built once on a stable `resolveWorkbenchGraph` (`WorkbenchView.tsx:81-101`, `App.tsx:1622`), and a pricing is scheduled only when the form's pricing basis or the section changes. A late answer is dropped only when the pricer changes (`useWorkbenchPricingStore.ts:142`), and staleness compares the same basis.
  - The Assistant, a branch switched to one with the same form, a data file replaced through Files, and an edit on disk all change the pipeline while Preview stays shown, and each leaves the old price undimmed.
  - Test-quote runs judge staleness by the pipeline and source too (`currentBasis`), but not by data files.
- **FB-13 [Major] Preview prices a pipeline that does not read the sheets.**
  - `priceSample.ts:24-35` patches the top-level workbench nodes and checks only that a Workbench Output exists. The singleton rules allow a Quote Input beside a Workbench Output (`_graph_shape.py:14-17`), and a Workbench Input inside a submodel is left alone.
  - In either case every value typed gives the same premium, undimmed, and every test-quote run reports "unchanged".
- **CX-4 [Major] A held value no longer allowed draws as a blank select while it is priced.**
  - `WidgetBody.tsx:186-195` renders a controlled `<select>` with no matching option, and React selects the first, the placeholder.
  - The mark, "Not one of the allowed values" (`sheetValues.ts:216`), does not name the value.
  - Renewing or carrying a quote onto sheets whose params file dropped a value reaches it. In a preset-rows grid the row of such a value disappears while its typed cells stay in the saved values.
- **FB-16 [Minor] A tick or text left in a column since retyped is invisible and unmarked.** `WidgetBody.tsx` draws it as empty or unticked; `cellProblem` marks neither; the server refuses the pricing naming a cell the sheet shows as empty.
- **CX-7 [Minor] "Is anything typed" is decided by different rules.** The app's pricing leaves hidden and app-set columns out (`useWorkbenchPreviewStore.ts:88-89`); its Save button and save body leave out app-set columns only, on raw values (`QuoteView.tsx:238`, `useOpenQuoteStore.ts:280`). A value left in a hidden column enables Save, and the server answers 422.
- **SD-1 [Minor] The server takes a quote holding only defaults.** The specification says defaults are not typing (`specs/workbench/high-level.md:566, 839`), and the browser agrees; the server's check (`_workbench_app_quotes.py:233-236`) counts them, so a direct API client can save a default-only quote.
- **FB-12 [Minor] Two pricing loops.** `useWorkbenchPreviewStore.ts:67-173` re-implements `useWorkbenchPricingStore.ts:93-162`, differing in the typed rule, the basis, the late-answer check and the error text.
- **FB-31 [Minor] Date cells and date conditions commit on every keystroke.** `WidgetBody.tsx:198-200`, `ShowWhenEditor.tsx:255-257`: a year typed digit by digit is four edits, and after a pause a pricing dated year 2. `DefaultField` already commits on blur.
- **FB-30 [Major, unconfirmed] Picking from a select in Preview may leave the sections held until the next click.** `useHeldWhilePressed` (`steadyPreview.ts:12-40`) holds what is drawn from a press until a release, cancel, context menu or window blur. Chromium's select menu takes the release of the press that opened it, so a Show-when section driven by a select may not open after the pick. Not run in a browser.
- **CX-25 [Minor] Integer text is typed through `float`.** `_workbench_tables.py:391-394` and `parseTypedNumber` turn `9007199254740993` into `9007199254740992` silently.
- **CX-8 [Minor] The schema editor reads numeric allowed values with `Number()`.** `workbenchForm.ts:1193-1196, 1250`, while the cells and the server use the typed-number rule: `1,000` is flagged and then priced as 1000, and `0x10` passes and then fails as a cell.
- **FB-15 [Minor] The params reader can save a mangled column name.** `_workbench_params.py:65-91` falls back to Windows-1252 only on a Polars error naming UTF-8, but Polars replaces bad bytes in a header silently. `Région` or `Limit (£)` is offered as `R�gion` and saved so, and `haute app build` fails once a value gains an accent. Confirmed with a script.
- **FB-18 [Suggestion] The typed-number and filled rules have no shared fixture**, unlike visibility and preset rows. They agree on every case tried.
- **CX-28 [Minor] Tracing a Workbench Output fed straight from a Workbench Input fails.** `trace.py:584-622` redirects to the source node without its port, and a multi-table input is refused as one that "emits multiple frames".
- **CX-13 [Suggestion] Editor pricing is one preview job per output table** (`priceSample.ts:38`), where the app prices a quote in one scoring call.

## 6. The form and its Save — `WB-19`

- **FB-19 [Major] A table named `quote_details` blocks Save with a 5 KB JSON toast.** The server refuses any table so named that is not Haute's (`_workbench_form.py:418-423, 482-485`), while the editor locks Haute's table by id and only warns of a duplicate label. No `RequestValidationError` handler is installed, so FastAPI's 422 repeats the whole form in pydantic's `input` (4,522 bytes), and `apiErrorMessage` shows the list as text. Preview's pricing fails the same way. Confirmed with a script.
- **FB-22 [Minor] Ids are never checked for uniqueness.** `newId` gives 32 unchecked random bits, and `removeWidget`/`removePage` filter every match while the finders take the first. A merged or hand-edited file holding a copied block draws two components with one id, and Delete removes both.
- **FB-21 [Minor] Save can end with nothing saved and nothing said.** `flush` checks the status at once and queues `save`, `read()` sets `loading` synchronously, and `save` then returns false with no message (`useWorkbenchFormStore.ts:145-148, 262-263, 299-302`; `App.tsx:1315`).
- **FB-20 [Minor] A save whose answer was lost leaves the next one refused as stale.** Reload then loses the typing since, though the file holds exactly what was sent (`routes/workbench.py:116-123`).
- **FB-23 [Minor] Edit, Save, Undo, Save resets the chosen zoom.** `useWorkbenchViewStore.ts:171-177` infers a form read anew from the shape of the history. Confirmed with a test.
- **CX-24 [Minor] The form save checks and writes outside the repository lock a branch switch holds.** `routes/workbench.py:112-138, 172-197`; the test-quote writes hold it throughout.
- **FB-17 [Suggestion] and CX-10 [Minor] The node copies and the undo history.**
  - A copy update from a fetch is an undo step (`useWorkbenchTables.ts:85-94`), so an Undo leaves the copy behind until the next save.
  - An Undo inside a save's wait for pending commits (`App.tsx:1331`) can save a stale Workbench Output copy, which deploy then refuses.
- **FB-24 [Minor] Mirrored constants and limits are pinned on one side or neither.** The blank sheet's width and section height (`_workbench_form.py:100,103`, `sheetGeometry.ts`), Rows 1-50, Columns 1-12 (`PropertiesPanel.tsx:548,558`), and the decimal places, where the Python literal does not reuse `PIVOT_DECIMAL_PLACES_MAX`.
- **CX-12 [Suggestion] `WorkbenchTestQuoteChange`'s values are generated as objects.** `schemas.py:340-346`, `api-contracts.generated.ts`; the client shadows the type with a hand-written interface and a double cast (`api/workbench.ts:114-119, 206`).
- **SD-2 [Minor] Two low-level invariants describe the retired Build and Preview model.** `specs/workbench/low-level.md` says building plots the sample and that the quote is the preview store's alone. The code gives Build a new quote's defaults, editor Preview the form's sample, and the app its quote.
- **FB-26 [Suggestion] The form is re-read only when the pipeline file changes**, so a change to the form alone shows only as a refused save.
- **FB-27 [Suggestion] A merged file whose geometry is off cannot be opened in the editor that would repair it.**

## 7. The app's quote life — `WB-20`

- **FB-1 [Major] A derived quote opens "Unsaved".** `useOpenQuoteStore.ts:351-367` leaves the baseline at the empty quote after the carry puts values on the sheets. The leave guard asks, a draft is kept, and the next Duplicate, Renew or Create alternative from the same revision offers the carried values back as unsaved changes.
- **FB-2 [Minor] A derived quote carries its source's reference, stage, assignee and archived flag until its first save.** The carry route returns them (`_workbench_app_quotes.py:362-372`), and the route test asserts it.
- **CX-19 [Minor] A value typed back to the saved one leaves the old draft**, offered after a reload (`QuoteView.tsx:141-161`).
- **FB-4 [Minor] Archive then Restore offers back the values already on the sheet.** `offerDraft` compares with the baseline, not the sheet.
- **FB-5 [Minor] A restored draft drops cells the sheets no longer have without saying so**, and its `formRevision` has no reader.
- **CX-31 [Minor] Two history loads for one quote land in completion order.** `loadHistory` (`useOpenQuoteStore.ts:531-541`) checks only the session and the quote id, so an older answer removes the newest revision.
- **CX-16 [Minor] Undo on an archive reloads the list it was made from**, replacing the list now shown (`QuotesPage.tsx:161-176`).
- **FB-8 [Minor] Mark final's dialog says revision N is pinned**, where the server pins N+1 as the change's own revision.
- **FB-11 [Suggestion] A pricing refused for another release shows as a pricing failure**, and the Reload notice comes only after a save.
- **FB-52 [Minor] A node failing on the typed quote answers "Internal Server Error"** (`_workbench_app.py:556-559`), where the container answers the reason.
- **FB-53 [Minor] `haute app build`, `serve` and `migrate` print tracebacks** for `HauteError` and SQLAlchemy faults their handlers do not catch (`cli/_app.py`). Confirmed with `CliRunner`.
- **CX-29 [Minor] A malformed release raises a raw error.** `read_release` lets a `TypeError`, `ValidationError` or `KeyError` escape (`_workbench_app.py:356-361`).
- **FB-56 [Suggestion] The release id moves with a node's canvas position, and not with the pinned runtime.** `_release_id` hashes the graph whole. Confirmed with a script.
- **CX-26 [Minor] Pricing releases its admission before the response is rendered**, then converts it on the event loop (`_workbench_app.py:527-564`), where the MLflow model keeps admission through conversion.
- **TQ-4 [Major, test] "A release without a quote store prices without its packages" never prices.** `tests/test_workbench_app.py:868-895` constructs the app and stops.
- **FB-9 [Suggestion] The Easy Auth headers are trusted because `release.json` names the host.** A container reached without Easy Auth signs in whoever names themselves, with edit rights when no edit role is set. A startup check, or an explicit trust setting, would refuse the misconfiguration.
- **FB-10 [Suggestion] The browser alone ticks `is_renewal`**; the server records the relation but not the column.

## 8. Building the sheets — `WB-21`

- **FB-29 [Major] A sideways drag moves a component 8 px down when a section above has a height off the grid.**
  - A move is snapped on the sheet (`moveRect`, `sheetGeometry.ts:48-50`) and again against its section's top (`landing`, `sheetInteractions.ts:96-100`).
  - With a 300-pixel section above, the second section's top is 348: a drag with no vertical movement gives `snap(348) = 352`, then `snap(4) = 8`.
  - Typed heights, Fit after a pixel nudge and growth all give off-grid tops.
- **CX-17 [Minor] Gestures ignore the sheet scrolling under them, and never stop on blur or a release outside the window.** `sheetInteractions.ts:145-175`; `startCreate` drops its stop callback.
- **CX-18 [Minor] A side panel unmounted mid-resize leaves the body's cursor and text selection set** (`haute-ui/SidePanel.tsx:69-101`).
- **CX-20 [Minor] The component's properties panel is not keyed by the component**, so a rejected value shows on the next one (`PropertiesPanel.tsx:163`).
- **CX-21 [Minor] A failure listing the params folder is swallowed** (`SchemaEditor.tsx:649-660`).
- **FB-32 [Minor] Pressing the viewport's scrollbar deselects the component** (`SheetCanvas.tsx:98-101`).
- **FB-33 [Minor] Moving a tab before any sheet was chosen clears the selection** (`PageTabs.tsx:13-21`).
- **FB-34 [Minor] Alt+Down on a schema column loses the focus**, where the tabs, bars and Order rows each refocus by a mechanism of their own.
- **FB-35 [Minor] Undo, redo and fit are ignored while a checkbox has the focus.** `isTextField` counts every input.
- **FB-36 [Minor] Accessibility:**
  - while building, the section bars follow every component in reading order;
  - required cells have no `aria-required`, and grid headers no `scope`;
  - the tabs are tabs without arrow keys, and renaming one loses the focus;
  - a clicked component is not focused.
- **FB-37 [Minor] Tooltips carry instructions** (`SectionHeaders.tsx`, `PageTabs.tsx`); the owner's rule puts instructions in `docs/`.
- **FB-38 [Minor] Each drag step clones and serialises the whole form and redraws every component.** Nothing on the sheet is memoised.
- **FB-40 [Suggestion]** No keyboard way to add or resize a component.
- **FB-41 [Suggestion]** A Table of output columns alone shows dashes with no reason, where a Chart of the same columns plots them.
- **FB-42 [Suggestion]** Pane state kept for deleted components, a floating-point zoom, `aria-pressed` on the bars, and the Display box without `aria-modal`.

## 9. One mechanism per concern — `WB-22`

- **Stale answers and turn-taking are hand-rolled about ten times:**
  - the form store's `turns`
  - the pricing store's run and pricer identity
  - the preview store's `asked`
  - the test quotes' `generation`
  - the open quote's `opening`
  - the list store's `asked`
  - `refreshTables`' fetch numbers
  - `useWorkbenchTables`' generations
  - the Files panel's request string
  - App's `projectSaveTurns`
  - `AllowedField`'s ticket

  CX-6, CX-14, CX-16, CX-31, FB-3, FB-12 and BUG-35's finding sit where one was forgotten or scoped to the wrong thing.
- **Three writers into the project** (the form save, the test-quote writes, project files), with three revision ideas, two locking shapes, two path rules and one capture retry (CX-24, CX-27, FB-45). A junction at `forms/` is followed where the Files panels refuse it.
- **FB-25 [Minor] The form's save mechanics restate the pipeline's** stale detection, the uncaptured rule, two turn queues, and the panel's floors beside the operations'.
- **Smaller copies:**
  - the app-set details applied twice on the server;
  - the form installed into its store by two `setState` calls with different fields;
  - `version` repeating `latest`;
  - `_read_quotes` and `_histories` each copying and migrating a file.
- **The three largest view files each do several jobs:** `WidgetBody.tsx` (747 lines), `PropertiesPanel.tsx` (888) and `SchemaEditor.tsx` (932).

## 10. The Files panels and the git ledger — `BUG-32` to `BUG-35`

- **FB-43 [Major] Pipeline files assumes git and a repository.**
  - Without git on PATH, the listing and writes answer 500 (`_git_read_models.py:44-46, 85-87`).
  - Without a repository, every file lists as committed, and the delete and replace dialogs promise "It stays in version history" (`FilesPanel.tsx:56-62`) for a file history never held.

  Confirmed by probes. Tracked by `BUG-32`.
- **FB-44 [Major] Commit sweeps and publishes dot-files the panels hide.** The sweep (`_git_transactions.py:132-171`) leaves out only `__pycache__`, `.haute_build` and `*.pyc`, while the panels refuse every dot-name. `rating/.env.local`, `.ipynb_checkpoints/`, `.DS_Store` and an editor's lock file were all swept in a probe. Tracked by `BUG-33`.
- **FB-45 [Major] The identity prompt's retry saves the pipeline and the form, never an uncaptured upload.** `App.tsx:472`; `reportSaveCapture` remembers nothing of what went uncaptured, and only test quotes have a capture retry. Tracked by `BUG-34`.
- **FB-46 [Minor] An upload cancelled after its last byte is published and captured, but its answer is dropped.** Tracked by `BUG-34`.
- **FB-49 [Suggestion] Nothing sweeps `.haute/uploads/`** after a server is stopped mid-upload. Tracked by `BUG-34`.
- **CX-15 [Minor] The Files panel keeps the previous folder's rows, actions included, while the next folder loads.** Uploads check for clashes against that listing (`FilesPanel.tsx:112-143, 187`). Tracked by `BUG-35`.

## 11. Generated CI triggers — `BUG-36`

- **FB-48 [Minor] The generated triggers name `data/`, which is ignored, and a root `models/`, which is no longer used** (`_scaffold.py:186-194`). The pipeline-config specification documents them as live.

## 12. One kit for repeated controls — `FSH-R04`

- **MS-2 [Minor] Five confirmation mechanisms:** `window.confirm` (deleting a section or a test quote), the schema editor's `ConfirmBar`, `DeleteSheetDialog`, the Files panel's dialog, and the app's `ask`.
- **FB-39 [Minor] Repeated panel controls.**
  - `FIELD_CLASS` is defined four times.
  - The labelled row is built by hand and as `Labelled`.
  - There are three whole-number validators, two collapsed-set toggles, and four Alt+arrow handlers with three refocus mechanisms.
  - The quiet add action appears in six places.
  - `Section` sits beside `ConfigSection`.
  - `INPUT_STYLE` is imported from the pipeline editors' private module.
- **FB-47 [Minor] The kit leaves copies behind.**
  - Half the toolbar's labelled buttons spell the kit's style in Tailwind beside `ToolbarButton`.
  - `PanelHeader.tsx` is a re-export nothing imports.
  - A keyframe is duplicated.

## 13. Already tracked — `WB-10`

- **CX-5 [Minor] Two first saves onto a new form race on a server database.** `_keep_form` counts and then inserts, and the loser's `IntegrityError` reaches a 500. `WB-10`'s plan already keeps form copies through the engine's insert-if-absent.
- **CX-30 [Minor] Two migrators can race on a fresh database.** There is no lock between reading the version and upgrading; `WB-10`'s migrate step is the one migrator.

## Downgraded, upgraded or unconfirmed

- **Downgraded, Major to Minor:**
  - CX-5, which `WB-10` already plans.
  - CX-6, a short window on local requests.
  - CX-10, which deploy refuses and the next save repairs.
  - CX-15 and CX-16: each row still deletes its own file, and the list is only shown wrongly.
  - CX-24, CX-25 and CX-26: rare interleavings or inputs.
- **Downgraded, Critical to Minor:** CX-27. Git never checks out a hard link, and the sandbox specification's threat model excludes the author's own machine.
- **Upgraded:** CX-1 is Major, though one review rated it a Suggestion against the stated rule of re-pricing when Preview shows again, because the Assistant, Git and Files change the pipeline while Preview stays shown.
- **Unconfirmed:** FB-30 needs a check in Chrome; its fix is harmless either way.

## Where each finding is tracked

| Finding | Package |
|---|---|
| CX-14, CX-22, CX-27, FB-28 | `WB-14` ([Workbench](workbench.md)) |
| CX-3, CX-11, FB-3, FB-6, FB-7, TQ-2, TQ-3 | `WB-15` ([Workbench](workbench.md)) |
| CX-2, CX-23, FB-14, FB-50, FB-51, TQ-1 | `WB-16` ([Workbench](workbench.md)) |
| CX-6, CX-9, FB-54, FB-55, FB-57, MS-1 | `WB-17` ([Workbench](workbench.md)) |
| CX-1, CX-4, CX-7, CX-8, CX-25, CX-28, FB-12, FB-13, FB-15, FB-16, FB-18, FB-30, FB-31, SD-1 | `WB-18` ([Workbench](workbench.md)) |
| CX-13 | `WB-23` ([Workbench](workbench.md)), after its measurement |
| CX-10, CX-12, CX-24, FB-17, FB-19, FB-20, FB-21, FB-22, FB-23, FB-24, SD-2 | `WB-19` ([Workbench](workbench.md)) |
| FB-26, FB-27 | `WB-24` ([Workbench](workbench.md)) |
| CX-16, CX-19, CX-26, CX-29, CX-31, FB-1, FB-2, FB-4, FB-5, FB-8, FB-11, FB-52, FB-53, FB-56, TQ-4 | `WB-20` ([Workbench](workbench.md)) |
| FB-9, FB-10 | `WB-25` ([Workbench](workbench.md)) |
| CX-17, CX-18, CX-20, CX-21, FB-29, FB-32 to FB-38, FB-40, FB-41, FB-42 | `WB-21` ([Workbench](workbench.md)) |
| FB-25, and the mechanisms in section 9 | `WB-22` ([Workbench](workbench.md)) |
| FB-43 | `BUG-32` ([bugs](bugs.md)) |
| FB-44 | `BUG-33` ([bugs](bugs.md)) |
| FB-45, FB-46, FB-49 | `BUG-34` ([bugs](bugs.md)) |
| CX-15 | `BUG-35` ([bugs](bugs.md)) |
| FB-48 | `BUG-36` ([bugs](bugs.md)) |
| FB-39, FB-47, MS-2 | `FSH-R04` ([frontend shared](frontend-shared.md)) |
| CX-5, CX-30 | `WB-10` ([Workbench](workbench.md)), already planned |
