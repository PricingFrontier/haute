# Verification — what "done" means before you claim it

Unit tests and a clean TypeScript compile are not sufficient evidence that a change works. They have repeatedly missed integration bugs in this repository that only surface when the real running server, the real on-disk config, and the actual route the frontend calls are exercised together.

This page has three parts: how to verify a change end-to-end, what the tests you write should actually assert, and the rule for test sequences you hand to someone else.

## 1. End-to-end verification before claiming done

Before marking a change complete or reporting it back:

1. **Identify the user-facing actions the change affects** — "cache as parquet", "preview a Quote Input node", "save the graph", "reorder a column". Anything a user can do whose behaviour depends on this change.

2. **Drive each action through the real running server.** Use the same endpoint the frontend calls, with the same payload shape, against the actual on-disk config and data files. For changes spanning backend and frontend, drive the whole chain through the route the frontend will use — never through a unit-test-only path that bypasses the route.

3. **Inspect the response *and* the side effects.** Side effects include cache files written, schema files updated, log lines emitted, in-memory graph state changed, files removed. A 200 with the wrong payload, a 422 with the wrong message, a cache hit that was supposed to fire and didn't — all are bugs a bare status code misses.

4. **Quantify the coverage in the report.** State three things explicitly:
    - **Breadth** — which user actions and code paths were actually exercised end-to-end.
    - **Side effects checked** — what was inspected to confirm the action did what it claimed (response payload, on-disk artefacts, server logs, derived state).
    - **Limitations** — what was *not* verified, and why. Naming a gap up front is cheap; having it found for you later is not.

5. **Only then commit and report.**

Where live verification is genuinely impossible — most often browser-state-dependent interactions that cannot be driven from outside the browser — say so explicitly and ask for those specific actions to be verified by hand. Never claim done on the basis of unit tests alone.

**Exempt:** pure type renames, documentation edits, and other changes with no runtime effect. If you are unsure whether a change has a runtime effect, treat it as if it does.

### Restart before believing it

Pre-restart success does not count. Backend-touching changes get a walkthrough against a **freshly started** server, with on-disk inspection to confirm the side effects landed. If a fix works only before a restart, that *is* the bug — not flakiness.

## 2. What UI tests must assert

Section 1 is about running the system. This section is about what the tests inside should check.

The recurring failure mode here is an assertion that stops at the editor's outgoing call, leaving every subsequent link unverified. Editor bugs in this repository have passed green test suites for exactly this reason.

**1. Assert at the persistent boundary, not at the call argument.** A test ending in `expect(onUpdate).toHaveBeenCalledWith(...)` proves only that the editor *intended* to write the right thing. The chain from editor to disk has several links — the editor calls its update handler, the panel merges into the live node config, the store updates, the save route persists the merged config, the next reload re-parses and re-classifies it. Any link after the first can break the chain, and a first-link assertion cannot see it.

  **Rule:** for any test of a user gesture that mutates persisted state, drive at least one path through the real (non-mocked) config-update reducer and assert on the merged object. Where the contract spans the backend, round-trip through the save route and inspect the saved JSON on disk.

**2. Assert the persisted object's exact shape, and separately name legacy fields that must be absent.**
   - *Exact shape* is the general invariant — compare the full expected object, not a partial match. It catches "something I didn't notice leaked through".
   - *Named absence* is logically implied by exact shape but worth its own test, because it documents the specific contract being defended, tells you immediately what regressed when it fails, and survives if the exact-shape test relaxes for a legitimate reason such as a new optional field.

**3. Render gates must surface every persisted entry.** Any editor whose UI iterates over a subset of persisted state — registry iteration, exclusion lists, prefix grouping — needs a test asserting that every persisted entry surfaces somewhere visible. Greying out is fine; silent suppression is not.

  The 1:1 JSON↔UI invariant is load-bearing: **an entry that exists on disk but renders nowhere is still active at execution time, and the user has no surface to repair it.** Real bugs of this shape in this repository include output mappings silently dropped when their input path didn't match a known column, and inherited parent-key columns missing from child frames.

**4. Bind it back to a real server.** A passing component suite is not evidence that the gesture works. Point 1 of this section and section 1 of this page overlap deliberately — this entry exists so that "the vitest suite is green" is never mistaken for "the gesture works".

## 3. Recommended test sequences are pre-run

When you hand someone a test sequence to walk through — "open the editor, click X, expect Y" — you must have executed that sequence yourself first, against the running application, in the same order.

A sequence derived from reading the code is a hypothesis, not a script. Recommending an unexecuted one spends someone else's time finding your bugs.

1. **Write the sequence, then drive it.** Every step the recommendation will contain gets performed once, including the edge-state setup steps.
2. **If a step fails**, either fix the code and re-drive until the sequence is green, or — where you suspect the sequence itself misstates the intent — ask about the step rather than shipping a broken script.
3. **States you cannot drive** are named explicitly in the recommendation as unverified, with why.
4. **Component coverage does not substitute.** The point is catching integration-level failures — stacking contexts, ports, stale servers, config drift — that component tests structurally miss.
