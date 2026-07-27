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
| — | — | — | No active security or supply-chain package remains. |

## Planned improvements

There are no active security/supply-chain roadmap packages.

## Delivered outcomes

- `SEC-ENV-01` migrates the remaining positive numeric concurrency and cache
  knobs to `haute._env` and adds an AST-derived production inventory. Literal
  `os.getenv`, `os.environ.get`, and environment-subscript reads—including
  common aliases and string constants—must now either use the shared parser or
  match the exact reviewed set for credential, string, Boolean, mapping, or
  custom non-negative semantics. Synthetic visitor tests and 177 combined
  accessor/cache/route regressions enforce the boundary.
