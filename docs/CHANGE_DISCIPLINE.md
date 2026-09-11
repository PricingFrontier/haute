# Change Discipline — what a change owes, from plan to landing

Haute's automated gates say whether a change *passes*. This page says what a change *owes* — decided before the work starts, re-checked before it lands. It exists because the expensive failures in this repo have not been failing tests; they have been changes that never predicted which gate would judge them, and landings where nobody re-derived the answer from the diff that actually shipped.

Companion pages: [CI Mirror](CI_MIRROR.md) (how to run the gates locally), [Commit Standards](COMMIT_STANDARDS.md) (how the code itself should read), [Verification](VERIFICATION.md) (what "verified" means before you claim done).

## The two checkpoints

**1. Plan time — state the CI delta.** Every non-trivial change states, before implementation: which existing lanes already cover it, which lanes need extending, and any new invariant it introduces that no lane defends yet.

"No CI impact" is a valid answer, but it must be *written*, not implied. The whole value is in having made the prediction — an unwritten prediction cannot be wrong, and cannot be checked.

**2. Landing — reconcile against the diff.** Before merge, re-derive the delta from the **actual diff**, not from the plan, and compare the two. A lane the plan didn't predict, or a promised extension that never happened, blocks the landing until it is resolved or explicitly waived.

The order matters: derive from the diff first, *then* look at the plan. Reading the plan first anchors you to it, and the failure this checkpoint exists to catch is exactly the scope that grew without anyone noticing.

## Change-class checklist

Consult at plan time; re-verify at landing. Keyed by what the change touches — a change usually triggers several rows.

- **A user-facing action** → an end-to-end path per [Verification](VERIFICATION.md). DOM-dependent gestures need browser-E2E coverage; gestures that mutate persisted state need assertions at the persistent boundary.
- **A new runtime dependency** → floor-plus-cap specifier per the dependency rules in [Commit Standards](COMMIT_STANDARDS.md); lockfile updated; the package passes at its floor; extras placement decided (which optional-dependency lane owns it).
- **A dependency floor/cap or `requires-python` bump** → a deliberate, owner-level decision; the CI matrix and the floor move in the same commit.
- **Templates, `haute init`, CLI entry, packaging, or the static-asset build** → fresh-install smoke run before landing; scaffold output is diffed against a committed golden copy, so any drift is reviewed deliberately rather than absorbed.
- **A perf-sensitive surface** (executor, caches, parsers, hot routes) → tests covering the changed hot path carry the `perf` marker (see [Performance Checks](PERFORMANCE_CHECKS.md)); budgets sanity-checked against the change.
- **Path/file handling, subprocess, or an external tool** → [Platform Divergence](PLATFORM_DIVERGENCE.md) consulted; the tool is invoked only through its **chokepoint module** — the one module that owns every call to that tool, so nothing else shells out to it directly (the rule and the current chokepoints are catalogued there); Windows/macOS lane relevance assessed.
- **A node type, new or changed** → schema-mapping persistence round-trips tested; every persisted entry surfaces in the UI (the 1:1 JSON↔UI invariant — see [Verification](VERIFICATION.md)).
- **A new external interface** → its own chokepoint module, its own section in [Platform Divergence](PLATFORM_DIVERGENCE.md), and its own non-Linux lane tests.
- **Tests themselves** → all writes derive from the `haute_scratch` fixture (or `tmp_path`, the same substrate); the write-sandbox layers enforce it — see the test write sandbox section of the [engineering-quality specification](../specs/engineering-quality/low-level.md).

## Plan checklist

Run against a plan before implementation starts.

1. **A plan exists**, written down, for anything beyond a trivial fix.
2. **CI delta present and explicit** — which lanes cover it as-is, which need extending, what new invariant no lane defends; or the words "no CI impact". Implied is not stated.
3. **Delta derived, not asserted** — each change class above is explicitly considered against the plan's scope: touched or not, and if touched, the obligation named.
4. **Out-of-scope named** — the plan states what it will *not* do and what it defers. Silent scope decisions are the failure mode this item exists for.
5. **Verification pre-commitment** — for each user-facing change, the plan names how it will be verified end-to-end, through the real server or browser route, not unit tests alone.
6. **Cross-feature touchpoints** — interactions with other in-flight work are named with citations (file, commit, spec section). Any claim about another area carries a source or is marked unverified.

## Landing checklist

Run against the tree about to merge. **Stance: execute as a reviewer, not a confirmer** — for each item, try to fail it, and record the evidence that stopped you.

1. **Tree identity.** The tree being checked is the tree being landed: record the commit; the working tree must be clean.
2. **Forward-merged.** The branch contains the current target tip. A stale combination fails — green-in-isolation is not green.
3. **Local gates green.** The full preflight exits 0 on this tree; record the exit code and output tail. (See [CI Mirror](CI_MIRROR.md) for how far local verification should reasonably go.)
4. **CI delta reconciled.** Derived from the actual diff, compared against the plan's stated delta. An unpredicted lane impact, or a promised extension that never happened, fails unless waived.
5. **Change-class obligations met.** Walk every change-class row the diff triggers and record the evidence for each.
6. **Verification quantified.** The verification statement exists and covers breadth (which user actions and code paths were exercised end-to-end), side effects checked, and limitations named. Unit tests only fails this item.
7. **Assertions at the persistent boundary.** New or changed tests for state-mutating gestures assert on persisted results, not on call arguments. Spot-check every new test file in the diff.
8. **Delegated-work provenance.** Any branch produced by a delegated worker and merged in carries its verification record. An unrecorded merge fails.
9. **Post-land obligations listed** — restated as reminders for the landing session, listed rather than checked.

**Output contract.** A landing verdict records: the tree checked, a per-item verdict (pass / fail / not-applicable) each with one line of evidence (the command run, the file inspected, the diff line), a findings list, and an overall verdict of pass or block. **Any failed item without a recorded waiver is a block.**

## Who runs the checklists

A checklist is worth more when the party under review does not author its reviewer's brief.

Where these run as an agentic step, the reviewing agent is spawned from a **pinned, canonical brief** — a pointer to this page plus the output contract — copied verbatim rather than composed by the session under review. The reviewer is adversarial to its spawner by design; letting the reviewed party write the reviewer's instructions quietly removes the property that makes the check worth running. The same logic is why code review routes to external tooling rather than to another instance of the model that wrote the code.

## When an honour-tier rule should become a gate

Most rules here are honoured, not enforced. That is a deliberate resting state, not an aspiration gap — mechanising a rule costs a hook, a lane, or a checker item, and most rules never earn it.

The promotion rule: **an honour-tier rule that has been violated twice gets mechanised.** Two violations is evidence the rule does not survive contact with real work on memory alone. The same idiom applies to this page and to [Commit Standards](COMMIT_STANDARDS.md): if you are corrected twice on something the written rules do not cover, propose a clause.
