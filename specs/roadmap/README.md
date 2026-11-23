# Haute engineering roadmap

This folder is the single source of truth for planned engineering
improvements. Each active component roadmap contains the problem,
implementation direction, acceptance criteria, dependencies, and current
code/test evidence needed to take one package through delivery.

Shipped behaviour remains defined by code, tests, and the component
specifications. `Reverify` packages came from older evidence and must be
reproduced against `HEAD` before implementation. `Decision` packages require
an explicit product or architecture choice. Remove a package when its outcome
is covered by current specifications and ordinary regression tests. Priority
`P1` marks a package that threatens persisted work, computed results, or
security; `P2` a material correctness, UX, or maintenance issue; `P3`
opportunistic work.

Keep measured defects, source-supported risks, unconfirmed reachability, product
decisions and maintenance work distinct.

Each `<component>.md` file here is one component's roadmap: its scope, its
priorities, which package to start with (`**Start with:**`, a non-deferred
package, or `—` when every package is deferred) and the packages themselves. A
`root-cause-<name>.md` file is one shared cause, taken before the packages it
lists. This README names no component roadmap, so a branch that works on one
roadmap edits no file another roadmap's branch does.

## price-contour on Polars 2 — 18 October 2026

The [price-contour on Polars 2](price-contour-polars-2.md) plan is a handover
specification for the price-contour project: what is left of it is stage 3,
moving price-contour to Polars' own 2.0 Rust crates once Polars publishes them,
and what Haute checks when it takes that release. It is a supporting report and
owns no Haute work.

## Codebase review plan — 10 November 2026

The [codebase review plan](codebase-review-plan.md) is the method and the
order for finding defects in the part of the backend and frontend that the
9 November 2026 review did not reach: wrong inputs made by machine, injected
failures, one answer computed more than one way, the editor in a real
browser, and environments not yet run. It is a supporting report and owns
no work; a reproduced finding becomes a package in its component's roadmap
above, and a phase leaves the plan when it has run. All five phases ran
from 10 to 16 November 2026 and have left it: it now holds what they did
not reach.

## Working protocol

1. Pick one package from its owning component, starting with the package its
   `Start with` line names; a component's scope says when packages go together.
2. For `Reverify`, reproduce the stated failure against `HEAD`; retire the
   package if current code and tests already prove the outcome.
3. Update the owning component specification before changing behaviour.
4. Add the smallest failing regression, implement the change, and run the
   affected tests once.
5. Commit each package on its own, with its ID in the message, removing the
   package from its component roadmap in the same commit, and point its
   `Start with` line at the next package. Delete a component roadmap left empty.

Packages that go together share one branch cut from a fresh `origin/main` and
one pull request, with "Before a pull request" in `AGENTS.md` followed first;
never build on an unmerged branch. A measurement-gated package records its
numbers in the pull request and decides by the rule its entry states. A package
that turns out to need a product decision stops there and goes to the user with
options and a recommendation. A package that changes what a user sees or relies
on says so in its entry, with the date it was decided, before work starts.
Deferred packages start only when the evidence or demand their entry names
arrives.

Package IDs are stable and component-owned. Cross-component consumers name a
dependency instead of copying the package.
