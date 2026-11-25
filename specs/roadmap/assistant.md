# Assistant roadmap

## Scope

The assistant inside Haute, as [the assistant
specification](../assistant/high-level.md) describes it. `AST-02` is about what an
applied or undone change tells the editor of its version capture. It comes
from the second run of the review plan's sweep of failed writes on
10 November 2026 and was reproduced through the assistant's service with a real git lock.
`AST-03`, `AST-04` and `AST-06` come from the plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit.
A worker ran each through the editor's assistant routes with a stand-in
for the provider, and the main session ran each again on 13 November.

## Priorities

**Start with:** `AST-03`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| AST-02 | Planned | P3 | An applied or undone assistant change whose version capture fails says so and names its files, as a save does. |
| AST-03 | Planned | P2 | With executable source withheld, a step's comment is withheld with it. |
| AST-04 | Planned | P2 | A provider is sent only what its own policy allows: not the earlier answers of a chat begun under a wider policy, and not what a policy saved during the turn allows. |
| AST-06 | Planned | P3 | An apply that is saved after the turn's time limit is shown as applied, with its Undo. |

## Planned improvements

### AST-02 — An assistant change reports its version capture as a save does
**Why:** Reproduced on 10 November 2026 with a real cause, a
`.git/index.lock` left behind, through the assistant's own
service as its tests drive it. An assistant change is applied and captured.
Then, with the lock in place, it is undone. The undo's answer holds a null
commit and nothing else about the capture: no warning, and no list of the
files left out of version history. History stays at the applied change
while the files hold the undone state, and nothing in the editor says so. A
pipeline save under the same lock answers "Changes saved; version capture
failed (git error — see server log)." and names its three files, which the
editor keeps so that the next save captures them. The undo's answer has no
field for either.

An applied change keeps the warning in its result but not the list of files
(read from the service; what the route and the message stream pass on was
not run). Neither carries the flag that opens the identity prompt, which the
capture code describes as the only way a restored hosted container learns
that it has no git identity. Every other write in the editor reports its
capture through one function: the pipeline's save, the form's save, a
Pipeline file, a utility module, a test quote. The assistant's apply and
undo do not call it.

**Plan:** Give the assistant's apply and undo answers the save's capture
fields (the warnings, the files left out, whether an identity is needed),
and report them in the editor through the function every other write uses.

**Acceptance:** With version capture failing, once for a lock left behind
and once for a missing identity, an applied change and an undone one each
show the save's warning, add their files to the editor's list for the next
capture, and open the identity prompt when that is the cause.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_application.py`;
`src/haute/schemas.py::AssistantUndoResponse`;
`src/haute/routes/assistant.py`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`frontend/src/stores/saveCapture.ts::reportSaveCapture`;
`frontend/src/stores/useAssistantStore.ts`.

### AST-03 — A step's comment is withheld with the code it belongs to
**Why:** Reproduced on 13 November 2026 through the editor's assistant
routes, with a stand-in for the provider. The project's assistant policy
withholds executable source: `allow_executable_source = false`. A Transform
has a comment above a line that becomes a step, "# SECRET_STEP: broker ACME
is loaded by 1.37 after the 2026 referral review", and another above a
line that stays code. The assistant asks for the node's settings. The
request that carries the answer to the provider holds the first comment
word for word, as the step's `comment`. The second comment is withheld
with its code.

The list of settings treated as executable source holds `code` and not a
step's `comment`. The assistant's own rendering of a node treats that
comment as source, and the syntax specification says of a step's comment
that "the assistant neither writes nor reads it".

**Plan:** Withhold a step's comment wherever code is withheld, from one
list of what counts as executable source.

**Acceptance:** With executable source withheld, no request to a provider
holds the text of a step's comment, for every tool that returns a node's
settings. A test puts a marker in a step's comment and searches each
request for it.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_tools.py`;
`src/haute/assistant/_render.py`;
`specs/codegen/structured-syntax-boundary.md`.

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

### AST-06 — An apply that lands after the turn's time limit is shown as applied
**Why:** Reproduced on 13 November 2026 with the turn's time limit set to
4 seconds in place of the default 600. The assistant's last call of the
turn applies a plan that deletes a node. The call starts before the limit
and its save ends after it. The stream carries the start of the apply and
then the failure "Assistant time limit exceeded.", with no end of the
apply and no change card. On disk the node is gone and the change is
committed. The chat shows a failed turn, and no card with its Undo, until
the chat is opened again, when the change is listed. The assistant
specification has "a change-applied event after each successful apply".

**Plan:** Send the end of the apply and its change card whenever the apply
completed, and the failure after them.

**Acceptance:** In the run above the stream carries the apply's end and
its change card before the failure, and the chat shows the change with its
Undo without being reopened.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_loop.py`;
`specs/assistant/high-level.md`.
