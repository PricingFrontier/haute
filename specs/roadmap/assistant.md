# Assistant roadmap

## Scope

The assistant inside Haute, as [the assistant
specification](../assistant/high-level.md) describes it. `AST-04` comes from the plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit.
A worker ran it through the editor's assistant routes with a stand-in
for the provider, and the main session ran it again on 13 November.

## Priorities

**Start with:** `AST-04`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| AST-04 | Planned | P2 | A provider is sent only what its own policy allows: not the earlier answers of a chat begun under a wider policy, and not what a policy saved during the turn allows. |

## Planned improvements

### AST-04 — What a provider is sent follows that provider's policy, for the whole chat and the whole turn
**Why:** Reproduced on 13 November 2026 in two runs through the editor's
assistant routes, with a stand-in for the provider.

A chat begins under a provider of the organisation's own, which may be
sent row samples. The assistant reads a column's profile and answers "The
brokers are BROKER_ALPHA_PRIVATE and BROKER_BETA_PRIVATE." The analyst
then changes `haute.toml` to an external provider and sends another
message in the same chat. The request to the external provider holds the
earlier answer word for word. The tool's result is dropped from the
history that is sent; the assistant's sentence repeating it is not.

A turn starts under the external provider. While it runs, `haute.toml` is
saved with a local provider whose policy allows restricted settings. The
turn keeps the provider it began with, but each tool call reads the policy
afresh, so a request for a node's settings is now answered, and the next
request sends those settings to the external provider. Without the edit
the same call is refused, `egress_policy_denied`.

The assistant specification says "external policy is public-only and
cannot enable executable source, row samples or aggregate statistics".

**Plan:** Bind a turn to the provider and the policy it began with. When a
chat's provider changes to one with a narrower policy, do not send it what
earlier turns produced under the wider one: begin the history again, or
ask the analyst first.

**Acceptance:** After the change of provider in the first run, no request
to the external provider holds the marker text. In the second run the
settings request is refused for the whole turn. A test covers each.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_session.py`;
`src/haute/assistant/_tools.py`; `src/haute/routes/assistant.py`;
`specs/assistant/high-level.md`.
