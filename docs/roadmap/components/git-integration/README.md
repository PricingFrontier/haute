# Git integration improvement backlog

## Scope

Owns repository mutation safety, ledger/working-branch lifecycle, history
integrity, Git subprocess performance, route error semantics, and the Git
panel's novice/expert workflows. Current contracts live in the
[git-integration specification](../../../specs/git-integration/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| GIT-G01 | Reverify | P0 | Serialize cross-request repository mutations so successful saves cannot be orphaned. | [Repository mutation lock](../../../fable-Review/git-implementation/G01-repo-mutation-lock.md) |
| GIT-G02 | Reverify | P0 | Seed unborn repositories without sweeping state, credentials, or datasets into the first commit. | [Unborn seed safety](../../../fable-Review/git-implementation/G02-unborn-seed-safety.md) |
| GIT-G03 | Reverify | P0 | Make active-pair deletion/switch fallback and rollback atomic across adopted repositories. | [Pair lifecycle edges](../../../fable-Review/git-implementation/G03-pair-lifecycle-edges.md) |
| GIT-G07 | Reverify | P0 | Surface actionable backend Git errors instead of generic HTTP status text. | [Frontend error surfacing](../../../fable-Review/git-implementation/G07-frontend-error-surfacing.md) |
| GIT-G08 | Reverify | P0 | Protect unsaved editor work during branch switches. | [Dirty-switch guard](../../../fable-Review/git-implementation/G08-dirty-switch-guard.md) |
| GIT-G05 | Reverify | P1 | Batch version labels and commit context instead of one subprocess per milestone. | [Version-label N+1](../../../fable-Review/git-implementation/G05-version-label-nplus1.md) |
| GIT-G06 | Reverify | P1 | Batch working-branch state and remove redundant panel refetches. | [Working-branches N+1](../../../fable-Review/git-implementation/G06-working-branches-nplus1.md) |
| GIT-G04 | Reverify | P1 | Preserve tabbed messages and merged-ledger topology in history/moves. | [History integrity](../../../fable-Review/git-implementation/G04-history-integrity.md) |
| GIT-G09 | Reverify | P1 | Pin locale and classify Git failures without parsing unstable translated prose. | [Locale and error classification](../../../fable-Review/git-implementation/G09-locale-and-error-classification.md) |
| GIT-G10 | Reverify | P1 | Remove or repair the dead status surface and its login-name crash path. | [Dead status surface](../../../fable-Review/git-implementation/G10-dead-status-surface.md) |
| GIT-G11 | Reverify | P1 | Make non-Git, invalid, and divergent states understandable and recoverable. | [Repository-state UX](../../../fable-Review/git-implementation/G11-nongit-and-invalid-state-ux.md) |
| GIT-G12 | Reverify | P1 | Move remote fetch off latency-sensitive request paths. | [Fetch off request path](../../../fable-Review/git-implementation/G12-fetch-off-request-path.md) |
| GIT-G13 | Reverify | P1 | Harden show/compare archive, temp-directory, and parse-failure behaviour. | [Show and compare robustness](../../../fable-Review/git-implementation/G13-show-compare-robustness.md) |
| GIT-G14 | Reverify | P1 | Make the Git state file update atomic. | [State-file atomicity](../../../fable-Review/git-implementation/G14-state-atomicity.md) |
| GIT-G15 | Reverify | P2 | Reconcile README promises, fixtures, and current Git behaviour. | [Docs and fixture truth](../../../fable-Review/git-implementation/G15-docs-and-fixture-truth.md) |
| GIT-G16 | Reverify | P2 | Batch low-risk Git UX, hygiene, test-gap, and CI improvements. | [Polish batch](../../../fable-Review/git-implementation/G16-polish-batch.md) |

## Dependencies

- GIT-G01 precedes lifecycle and fetch changes that reason about serialized
  repository state.
- [Frontend and canvas](../frontend-canvas/README.md) owns shared toast,
  loading, and panel-state conventions; this component owns Git semantics.
- [Security and supply chain](../security-supply-chain/README.md) owns general
  credential/path policy; GIT-G02 owns the Git-specific first-commit boundary.

## Evidence and retirement

The [Git Fable review](../../../fable-Review/git-implementation/README.md)
contains reproductions, measured subprocess costs, ordering, TDD plans, and a
cleared-behaviour list. Reverify every package and preserve those cleared
behaviours. Retire the queue only after accepted work is proved by real-repo
tests and current Git specs.
