# Security and supply-chain roadmap

## Scope

Owns deserialisation allowlists, project/path containment, local-session and
request trust boundaries, dependency advisories, and accepted-risk evidence.
Shipped runtime policy is specified in
[sandbox security](../sandbox-security/high-level.md), while audit
orchestration is specified in
[engineering quality](../engineering-quality/high-level.md).

Execution/request containment (`AUD-C18`), dependency advisory closure
(`AUD-SEC-01`), and local-session bootstrap protection (`AUD-SEC-02`) are now
ordinary specified regression-tested behaviour and have been removed from
active roadmap work.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| `SEC-ENV-01` | Queued | P2 | Derive lazy environment-accessor coverage from production call sites. |

## Planned improvements

### SEC-ENV-01 — Complete lazy-accessor migration guard

**Why:** The environment-accessor regression uses a manually maintained
parallel table. A new environment knob can bypass `haute._env` without entering
that table, leaving the intended import-safety boundary untested.

**Plan:** Discover eligible production access sites from a checked source
inventory or enforce them statically, with an explicit allowlist only for
reviewed eager reads.

**Acceptance:** Adding a direct eligible `os.getenv`/`os.environ` access outside
`haute._env` fails the focused guard without a test-table edit; existing lazy
knobs and deliberate exceptions remain green.

**Dependencies:** Engineering quality owns repository-wide static-check
orchestration.

**Evidence:** `src/haute/_env.py`, `src/haute/`,
`tests/test_env_lazy_accessors.py`, and `tests/test_repository_hygiene.py`.
