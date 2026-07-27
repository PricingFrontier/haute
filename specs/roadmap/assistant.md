# Assistant roadmap

## Scope

Owns the in-app authoring assistant's session and transcript fidelity,
provider/model workflow, graph-update feedback, and user-facing recovery
behaviour. Current behaviour is specified in
[assistant](../assistant/high-level.md) and
[assistant UI](../frontend-assistant-ui/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| — | — | — | No active assistant roadmap package remains. |

## Planned improvements

There are no active assistant roadmap packages.

## Delivered outcomes

- `ASSIST-01` validates a resume offer's source binding before touching a live
  session or promoting a disk record. Successful persisted graph mutations
  rehydrate their settled “Canvas updated” activity in order, while current
  tool records require an explicit error flag. Backend restart/LRU tests and
  the frontend hydration test enforce the complete contract.
- `ASSIST-02` is resolved by the following 2026-07-27 product decision:
  provider and model selection remain operator-owned project configuration in
  `haute.toml`, not per-session UI state; credentials remain environment-only.
  Domain guidance belongs in the versioned system-prompt catalog, authoring
  guide, and on-demand examples rather than a free-form prompt editor or
  speculative node-action palette. Recovery remains typed and explicit:
  readiness blocks sending with its reason, status-fetch failure offers retry,
  an expired session offers a new chat, a 409 explains that the prior turn is
  still finishing, transport/provider failures stay inline, and no mutating
  send is automatically retried or failed over to another provider. Stop,
  new-chat, clean-canvas, top-level-view, and working-branch controls are the
  supported approval/recovery surface. Per-session provider selectors, raw
  system-prompt editing, automatic provider failover, and automatic replay of
  a failed mutating turn are rejected because they undermine reproducibility
  or risk duplicate graph edits. Existing API/store/component suites cover
  readiness, recovery, cancellation, neutral provider events, and graph-update
  feedback.
- `ASSIST-03` closes `[assistant]` to `provider`, `model`, and `base_url`;
  unknown keys name their TOML paths, and OpenAI endpoints must be
  credential-free absolute HTTP(S) URLs. Structural errors are redacted and
  fail before SDK probing or provider construction.
