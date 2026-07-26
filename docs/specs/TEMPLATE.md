# Spec Document Conventions

Every component in this codebase has a directory under `docs/specs/` containing two
documents, written as if the component had been produced by spec-driven development:

```
docs/specs/<component>/
  high-level.md   # WHAT the component does and WHY — audience: any engineer or reviewer
  low-level.md    # HOW it is implemented and verified — audience: an engineer changing it
```

## high-level.md structure

```markdown
# <Component Name> — High-Level Specification

## Purpose
One or two paragraphs: what problem this component solves for the user/system and why it exists.

## Scope
What is in scope and explicitly out of scope (pointing to the neighbouring component that owns it).

## Behaviour
The observable behaviour, described from the outside: inputs, outputs, user-visible effects,
invariants that must always hold. No implementation detail.

## Design rationale
Why it works the way it does — the constraints and trade-offs that shaped the design
(performance, safety, correctness, UX). Include rejected alternatives where evident from the code.

## Interactions
Which other components it depends on and which depend on it (link to their spec dirs).

## Failure model
How the component is expected to behave on invalid input, missing resources, or internal errors.
This codebase prefers loud failure over silent fallbacks — specs must state where errors surface.
```

## low-level.md structure

```markdown
# <Component Name> — Low-Level Specification

## Module map
Table of the source files that make up the component, one line each on their responsibility.
Use an inline-code, repository-root-relative path for every maintained source or operational
artifact (for example, `src/haute/executor.py` or `frontend/src/App.tsx`). Enumerate files rather
than relying on a directory wildcard; generated outputs and large non-normative corpora may be
grouped only when their lifecycle and exclusion are stated explicitly.

## Key types and data structures
The central classes/dataclasses/TypedDicts/interfaces, their fields and invariants.

## Control flow
The main entry points and the call paths through the module map, step by step.
Cover concurrency, caching, and ordering guarantees where present.

## Edge cases and invariants
The tricky cases the implementation explicitly handles (empty inputs, unicode paths,
case-insensitive filesystems, concurrent mutation, etc.) and the invariants enforced.

## Error handling
The exception types raised, where they propagate to, and any deliberate re-raising/wrapping.

## Testing
Where the tests live, what strategy they use (unit/property/integration/regression),
the key scenarios covered, and known coverage gaps.
```

## Writing rules

- Describe the code **as it is**, not as it should be. If behaviour looks like a bug, describe
  the behaviour and add a `> NOTE:` callout rather than speccing the intended-but-absent behaviour.
- For an approved spec-first change, add a final `## Approved change contract` section before
  implementation begins. State the current limitation, the approved target behaviour, explicit
  non-goals, failure and compatibility semantics, and executable acceptance evidence; link to the
  implementation plan. Never write the future behaviour into the ordinary present-tense sections
  before it ships. The implementation/release change must reconcile the approved contract into
  the normal sections and remove the temporary section. The historical
  `## Polars backend contracts (<version>)` class is legacy temporary-contract debt: do not add
  new sections with that heading, and fold and remove every existing section under the same rule.
- Reference source with an exact repository-root-relative path such as `src/haute/parser.py`, and
  functions as `haute.parser.parse_pipeline_file()`. Do not use an ambiguous basename when a path
  is available. Do not paste long code excerpts; the spec must survive refactors that keep
  behaviour. A `path::symbol` reference must resolve to that symbol in the named file.
- Every file named in `## Testing` resolves unambiguously in the repository. Frontend unit and
  browser references are additionally repository-root-relative and start with `frontend/`; a
  frontend basename or path relative only to `frontend/src/` is invalid even when it happens to
  be unique today.
- Every maintained backend and frontend runtime file must appear in at least one low-level module
  map. Build, CI, developer-tooling, and reference-project artifacts are subject to the same rule;
  generated and historical material must instead be explicitly classified by the owning spec and
  the coverage contract in [README.md](README.md).
- A file normally has one **primary owner**: its component documents the file's behaviour. A
  second component may name it only to describe a real direct interaction; then it is a consumer
  and the exact primary/consumer set must be recorded in [ownership.toml](ownership.toml).
  Explicit ownership claims in prose follow the same ledger discipline even when only the primary
  component names the file in its Module map.
- Cross-link related components with relative links. From a component document the target is
  `../caching/high-level.md`; see [caching](caching/high-level.md) for a live link from this file.
- British or American spelling both fine; match the terminology used in the code
  (e.g. "optimiser", "modelling").
- In high-level.md, prefer component-level language over file paths, but citing a module
  filename to ground a behavioural claim is acceptable; detailed per-file responsibilities
  belong in low-level.md's Module map.

## Version semantics

`pyproject.toml` is the single product/package release version. The installed
`haute.__version__` and FastAPI/OpenAPI `app.version` derive from that package metadata; they are
not independent API generations. A version embedded in a temporary contract heading records the
planned change cohort only. It neither changes nor predicts the shipped product version, and the
heading disappears when the contract is folded into the present-tense specification.

## Accuracy ratchet

`tests/test_docs_accuracy.py` validates repository paths and globs across complete spec documents,
`path::symbol` targets, Module-map responsibility symbols, exact required headings, Testing
references and backend-test coverage, relative-link anchors, roadmap `Evidence:` paths, shared
ownership claims, and temporary-contract retirement. Known debt lives in
`tests/docs_accuracy_baseline.txt` as one sorted TSV line per document/rule/detail. Delete the one
line when a violation is fixed. Adding or changing a line is a reviewed expansion of known debt,
not an automatic fallback.
