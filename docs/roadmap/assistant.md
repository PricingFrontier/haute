# Assistant roadmap

## Scope

Owns the in-app authoring assistant's session and transcript fidelity,
provider/model workflow, graph-update feedback, and user-facing recovery
behaviour. Current behaviour is specified in
[assistant](../specs/assistant/high-level.md) and
[assistant UI](../specs/frontend-assistant-ui/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `ASSIST-01` | Reverify | P1 | Make resumed sessions reconstruct the complete authoring conversation without consuming live capacity for rejected resumes. |
| `ASSIST-02` | Decision | P2 | Turn the early-preview prompt, provider, model, and recovery controls into a deliberate analyst workflow. |

## Planned improvements

### ASSIST-01 — Session and transcript fidelity

**Why:** Persisted neutral message history does not currently reconstruct
transient “Canvas updated” rows, and a resume offer for the wrong pipeline is
looked up before it is rejected, briefly admitting it to the live-session LRU.
Those details can make a resumed conversation look incomplete or displace a
useful live session.

**Plan:**

- Define which graph-mutation events are durable transcript facts and persist
  the smallest safe representation needed to reconstruct them.
- Validate persisted session metadata against the requested pipeline before
  promoting the session into the live LRU.
- Keep unknown or mismatched resume offers non-fatal while making the reason
  observable to the client.
- Retain the existing turn reservation, provider-stream teardown, bounded
  persistence, and single-process storage contracts.

**Acceptance:**

- Save/restart/resume tests reconstruct user, assistant, tool, and selected
  graph-update rows in their original order.
- A wrong-pipeline or corrupt resume offer creates a fresh session without
  evicting an unrelated live session.
- Resume diagnostics contain no prompt, credential, or model-response content.
- Provider teardown and concurrent-turn regressions remain green.

**Dependencies:** [Frontend and canvas](frontend-canvas.md) owns graph-update
identity; engineering quality owns cross-platform/session fixtures.

**Evidence:** `src/haute/assistant/_session.py`,
`src/haute/routes/assistant.py`, `frontend/src/stores/useAssistantStore.ts`,
`frontend/src/panels/assistant/TranscriptEntryView.tsx`,
`tests/test_assistant_session_persistence.py`,
`tests/test_assistant_routes.py`, and
`frontend/src/stores/__tests__/useAssistantStore.test.ts`.

### ASSIST-02 — Deliberate authoring workflow

**Why:** Prompt guidance, transcript polish, model choice, and provider recovery
were intentionally shipped as an early preview. Their long-term product
contract has not yet been chosen.

**Plan:**

- Observe the highest-friction authoring journeys and choose which guidance
  belongs in the composer, transcript, system prompt, or node-aware actions.
- Decide whether model/provider selection is project configuration,
  per-session choice, or an operator-only setting.
- Design actionable recovery for readiness, authentication, rate-limit,
  provider, and interrupted-stream failures without exposing secrets.
- Keep graph mutation approval and the working-branch precondition explicit.

**Acceptance:**

- A short decision record names the supported workflow and rejected
  alternatives before implementation.
- Browser/component tests cover the chosen prompt, model/provider, recovery,
  cancellation, and graph-update feedback paths.
- Provider-specific details do not leak into the neutral session/tool contract.

**Dependencies:** Security owns credential handling; Git integration owns the
working-branch mutation precondition.

**Evidence:** `src/haute/assistant/_config.py`,
`src/haute/assistant/_providers.py`, `src/haute/assistant/_loop.py`,
`frontend/src/panels/assistant/AssistantPanel.tsx`,
`frontend/src/panels/assistant/Composer.tsx`,
`tests/test_assistant_config.py`, `tests/test_assistant_providers.py`, and
`tests/test_assistant_loop.py`.
