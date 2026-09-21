# Pipeline config roadmap

## Scope

A node's declarative settings are built from decorator arguments and a JSON
sidecar, repaired when they no longer parse, validated before they are
written back, and read by the executor when the node runs. Current behaviour
is specified in [the pipeline-config specification](../pipeline-config/low-level.md).

These packages close one gap that runs through all four of those stages: a
node config that **cannot execute** can be produced by repair, persisted by
save, and then reported to the user as an internal server error. Each stage
knows enough to prevent it and none of them acts.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCFG-R01 | Planned | P2 | A repair never reports success while leaving a node unrunnable. |
| PCFG-R02 | Planned | P2 | A config defect reads as a node error, not an internal server error. |
| PCFG-R03 | Planned | P3 | Save refuses a config the executor cannot build. |

Delivery order is `PCFG-R01` → `PCFG-R02` → `PCFG-R03`. `R01` stops new
unrunnable configs being written; `R02` makes the ones already on disk
legible; `R03` closes the remaining write path. `R02` is independently
useful and may be taken first if the error contract is the more pressing
need.

## Worked example

The three packages were found from one failure, which is worth keeping
because it exercises all of them in sequence. A Scenario Expander sidecar
written before `e9b37e6e` carried the pre-rename grid-size key `steps: 11`.
After the rename, `steps` means the step list, so the parser rejected the
config outright (`scenarioExpander 'steps' must be a list.`) and the
pipeline would not load.

Repair then ran. It replaced the invalid `steps` with the audited default
`[]`, reported that field as `defaulted`, and — correctly — raised
`incomplete_range: "Scenario range and stepCount are required."` The repair
applied anyway, that issue never reached the user, and the sidecar was
written without any grid size. The pipeline now loaded and every preview of
that node failed in `_build_scenario_expander` with a bare `ValueError`,
which the preview route cannot classify, so the browser received
`500 Operation failed. Check the server logs for details.` and the server
log recorded only `interactive_worker_remote_failure … remote_type=ValueError`.

Three defects, one user-visible symptom: an opaque 500 on a node the system
had already diagnosed precisely.

## Planned improvements

### PCFG-R01 — A repair states what it could not fix
**Why:** `reconcile_config` returns the issues it found alongside the repaired
config, and `_recover_node` discards them. Its own comment says engine issues
are surfaced as completeness, but `_recover_completeness` returns `[]` for
every node type except `DATA_INPUT` and `DATA_OUTPUT`, so for every other
node an error-level issue is dropped. The repair plan then reports success.
Field changes are still reported, so a dropped required key is shown to the
user as `defaulted` — which reads as *fixed*.

**Plan:** Carry `ConfigRecoveryResult.issues` through `_recover_node` into the
plan. Report an error-level issue as node completeness for every node type,
not only the two Data provider families; `_recover_completeness` keeps its
provider-specific gap detail and gains the engine issues as its general case.
Decide and record one product rule for an error-level issue that survives a
repair: either the plan reports the node incomplete and applies (the node is
visibly unfinished, matching a declared-incomplete Data Input), or the repair
is refused and the user is offered reset. Do not resolve this by consulting
`node_defaults.json` on the recover path — `reconcile_config(reset=False)`
deliberately does not adopt defaults, because a recover must not invent
settings the user never chose.

**Acceptance:** A recover of a Scenario Expander whose config lacks `stepCount`
reports that node as incomplete with the engine's own message, and a test
asserts the issue reaches the plan rather than the engine's return value. A
recover that resolves every issue still reports no completeness gaps. The
chosen rule for applying-versus-refusing is specified in the pipeline-config
low-level spec before the behaviour changes.

**Dependencies:** None.

**Evidence:** `src/haute/_pipeline_repair_actions.py` (`_recover_node`,
`_recover_completeness`); `src/haute/_node_config_recovery.py`
(`reconcile_config`, `_validator_issues`).

### PCFG-R02 — A config defect is a node error, not an internal error
**Why:** A node builder that rejects its config raises a bare `ValueError`.
The preview route classifies worker exceptions by exact `(module, type)`
identity against `PUBLIC_CONTRACT_ERROR_TYPES`; a builtin `ValueError` is not
on that list, so it logs `interactive_worker_remote_failure` and returns
`500` with `_INTERNAL_ERROR_DETAIL`. The user is told to check a server log
that records only the exception's type. This is a user-authored, user-fixable
condition presented as a server fault, and it applies to every bare
`ValueError` a builder raises, not only the scenario grid.

**Plan:** Give config rejection a typed, public failure. Add a `ConfigError`
subclass with a stable `error_code` and register it in
`PUBLIC_CONTRACT_ERROR_TYPES`, so the existing curated-payload path returns
`422` with the message — the same route `LiveSwitchScenarioError` already
takes for a node-config problem. Raise it from `scenario_step_count`, then
audit the other bare `ValueError`s raised at build time in `_builders.py` and
`_node_apply.py` and convert those that describe a config defect rather than
an internal invariant. Leave genuine invariant violations as they are: they
are not user-fixable and a 500 is the honest answer.

**Acceptance:** Previewing a node whose config is rejected returns `422` with
the message naming the missing or malformed setting, and a test asserts the
status and payload rather than just the absence of a 500. The frontend shows
that message on the node instead of the internal-error text. An exception
that is *not* a config defect still returns `500` with no detail, proving the
allowlist was not widened into a general message leak.

**Dependencies:** The curated public-payload contract in
`routes/_contract_errors.py` (owner: server-api).

**Evidence:** `src/haute/_node_apply.py` (`scenario_step_count`);
`src/haute/_builders.py` (`_build_scenario_expander`);
`src/haute/routes/pipeline.py` (`_raise_interactive_remote_http_error`).

### PCFG-R03 — Save refuses a config the executor cannot build
**Why:** `_validate_strict_node_configs` runs `validate_node_config` for
`DATA_INPUT`, `DATA_OUTPUT` and `BANDING` only. Every other node type is
written to its sidecar unchecked, so a config missing a key its builder
requires is persisted without complaint and fails at the next preview. The
file that produced the worked example above was written by a save.

**Plan:** Extend strict save-time validation past the three discriminated
families to any node type whose builder has a required setting, reusing the
`require_complete=False` distinction already established: a *declared
incomplete* node (a Data Input with no locator yet) stays saveable, while a
config that names a setting invalidly, or omits one with no incomplete form,
is rejected with a `400` naming the node and the reason. Decide per node type
which required settings have a legitimate incomplete form — a node the user
has not finished configuring must remain saveable, because refusing to save
work in progress is worse than the deferred error.

**Acceptance:** Saving a pipeline whose Scenario Expander has no `stepCount`
is rejected with a message naming the node and the setting; saving a
deliberately unfinished node of each type that has an incomplete form still
succeeds. Tests cover both directions per node type touched.

**Dependencies:** PCFG-R01 (a repair should stop producing these configs
before save starts refusing them, or a user with an already-damaged project
can neither repair nor save). The save route is owned by server-api; this
package changes the validation it calls, not the route's contract.

**Evidence:** `src/haute/routes/_save_pipeline.py`
(`_validate_strict_node_configs`); `src/haute/_config_validation.py`
(`validate_node_config`).
