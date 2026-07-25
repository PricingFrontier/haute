# Security and supply-chain roadmap

## Scope

Owns deserialisation allowlists, project/path containment, local-session and
request trust boundaries, dependency advisories, and accepted-risk evidence.
Shipped runtime policy is specified in
[sandbox security](../specs/sandbox-security/high-level.md), while audit
orchestration is specified in
[engineering quality](../specs/engineering-quality/high-level.md).

Execution/request containment (`AUD-C18`) and local-session bootstrap
protection (`AUD-SEC-02`) are now ordinary specified regression-tested
behaviour and have been removed from active roadmap work.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-SEC-01` | Verify | P0 | Run the new locked audit against current advisory services and remediate or explicitly accept every reported blocking finding. |

## Planned improvements

### AUD-SEC-01 — Current dependency advisory closure

**Why:** The fail-closed locked audit, scheduled/lock-change CI gate, retained
reports, and exact expiring accepted-risk registry are implemented. Advisory
status still changes independently of source, and this checkout has not yet
sent its dependency inventory to the external advisory services, so current
lockfile closure cannot be claimed from parser/unit evidence alone.

**Plan:**

- With explicit authorization to send locked package names and versions, run
  `python scripts/check_dependency_audit.py --reports-dir
  dependency-audit-reports` against current `pip-audit` and npm advisory data.
- Upgrade every reported Python advisory and every npm high/critical advisory,
  or add a temporary exact acceptance only with owner, exposure, compensating
  control, approval date, and review date.
- Re-run the audit to a clean result and retain the nearest compatibility
  evidence for any dependency upgrade.

**Acceptance:**

- Current Python and frontend lockfiles pass the committed advisory policy
  against live advisory services.
- No malformed, expired, duplicate, mismatched, or unused acceptance remains.
- Scheduled and lock-change CI continue to fail closed and retain reports.

**Dependencies:** [Engineering quality](engineering-quality.md) owns scheduled
CI mechanics; security owns remediation and risk acceptance.

**Evidence:** `scripts/check_dependency_audit.py`,
`security/accepted-risks.toml`, `tests/test_dependency_audit.py`,
`.github/workflows/dependencies.yml`, `pyproject.toml`, `uv.lock`,
`frontend/package.json`, and `frontend/package-lock.json`.
