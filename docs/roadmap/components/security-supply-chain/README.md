# Security and supply-chain improvement backlog

## Scope

Owns deserialisation allowlists, project/path containment, local-session and
request trust boundaries, security-sensitive dependency decisions, and the
policy for converting advisories into required upgrades. Current runtime
policy lives in the
[sandbox-security specification](../../../specs/sandbox-security/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C18 | Reverify | P0 | Harden executor path containment, reflected request metadata, and unpickler allowlists as one trust-boundary package. | [Audit cluster C18](../../../review/REMEDIATION-PLAN.md#c18-security-boundary-hardening-executor-path-containment--reflected-request-id--unpickler-allowlist) |
| AUD-SEC-01 | Reverify | P0 | Close any reachable critical/high backend and frontend dependency advisories with locked-version and compatibility evidence. | [Supply-chain must-fix package](../../../review/REMEDIATION-PROGRAM.md#p0-9--supply-chain-dependency-advisories-criticalhigh-security) |
| AUD-SEC-02 | Reverify | P0 | Require origin/token protection before serving local-session bootstrap material from the SPA boundary. | [Security finding index](../../../review/MASTER/INDEX.md#high-110) |

## Dependencies

- [Deploy and platform](../deploy-platform/README.md) consumes path and
  artifact policy for validation/container execution.
- [Engineering quality](../engineering-quality/README.md) owns scheduled
  advisory monitoring and CI mechanics; this component decides remediation
  and accepted risk.
- Security packages are not batched with unrelated cleanup and take precedence
  over performance or capability work.

## Evidence and retirement

All audit security claims require current exploitability/reachability
verification and a failing negative test before implementation. AUD-C18 owns
the allowlist narrowing and negative gadget coverage rather than splitting
them into a second package. Retire a
package only when the trust boundary is explicit in specs, known gadget/path/
origin bypasses are regression-tested, and any accepted dependency risk is
recorded with version and reachability rationale.
