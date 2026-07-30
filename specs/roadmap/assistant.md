# Assistant roadmap

## Scope

The Haute assistant is an agent for building Haute pipelines and answering
questions about how the installed Haute library works.

The model is responsible for understanding natural-language intent, asking for
missing decisions, and explaining results. Haute itself is responsible for the
facts, supported actions, validation, persistence, and proof that a change is
correct.

The installed library must therefore be the assistant's source of truth. The
assistant must discover Haute's capabilities at runtime instead of relying on
a separately maintained prompt that can drift behind the library.

The in-app assistant, running inside `haute serve`, is the product's agent
surface. Haute is used through the running server: this direction adds no
headless project tooling, no machine-readable agent CLI, and no integration
surface for external coding agents.

This direction extends the existing [assistant](../assistant/high-level.md)
and [assistant UI](../frontend-assistant-ui/high-level.md) components. It does
not replace the parser, execution engine, transactional save service, or
graph update channel. Design sections below are direction, not shipped
behaviour: each package moves its slice into the owning component
specification when it is delivered, per the roadmap working protocol.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ASSIST-A04 | Planned | P1 | Make the installed library the versioned source of truth for assistant capabilities. |
| ASSIST-A05 | Planned | P1 | Put shared typed query, plan, validate, apply, and verify services, with stale-safe revisions and deterministic mutation authority, behind the in-app assistant. |
| ASSIST-A06 | Planned | P1 | Package deterministic recipes and executable examples for representative Haute pipeline work. |
| ASSIST-A07 | Planned | P1 | Deliver the decided provider-egress, automatically derived project-knowledge, sensitivity, and retention contracts. |
| ASSIST-A08 | Planned | P1 | Qualify supported provider/model configurations against semantic, safety, cost, and latency release gates. |

`ASSIST-01`–`ASSIST-03` predate this numbering scheme and are recorded under
[Delivered outcomes](#delivered-outcomes).

## Planned improvements

### ASSIST-A04 — Library-owned capability registry

**Why:** The current assistant catalog derives top-level vocabulary but still
duplicates model-facing knowledge and omits important nested semantics,
defaults, constraints, port behaviour, and installed capability facts. That
representation can drift from the library it describes.

**Plan:** Add one versioned Python capability registry derived from canonical
node, config, validation, format, and execution definitions. Supplement only
non-derivable semantic guidance as colocated, completeness-checked metadata.
Expose a manifest schema version, deterministic capability hash, resolved
node descriptors, operation descriptors, and dynamic installed
capabilities. Cache immutable registry material by installed Haute version
and capability hash while refreshing project state separately.

**Acceptance:** Every supported node and assistant-callable operation has a
closed, versioned descriptor; derived facts agree with runtime validators;
unknown or unsupported capabilities fail by stable code; installing a newer
Haute version immediately changes the reported manifest without an external
prompt update; capability hashing and cache invalidation are deterministic
and covered by compatibility tests.

**Dependencies:** Existing node/config registries, validation services, input
format registry, packaging metadata, and assistant provider-neutral tool
contracts.

**Evidence:** `src/haute/assistant/_catalog.py`;
`src/haute/assistant/_tools.py`; `src/haute/_types.py`;
`src/haute/_config_validation.py`; `tests/test_assistant_catalog.py`;
`tests/test_assistant_tools.py`.

### ASSIST-A05 — Shared application services, revision, and mutation authority

**Why:** The assistant's operations are implemented against route-private
helpers, so they cannot be tested or reused as a coherent service surface,
and nothing proves a plan was applied to the same saved state it inspected.
Structural save validation also cannot prove that the result satisfies the
requested postconditions, while a schema-valid deletion or executable change
can still be outside the authority the user intended to grant.

**Plan:** Define a shared typed Python application-service layer for
capability queries and pipeline inspect/plan/dry-run/apply/verify
operations, and let the in-app assistant's tools call it directly. Own the
canonical snapshot-revision contract here: dry-run reports the base
revision, a stable plan hash, a semantic diff, declared postconditions,
verification tier, and deterministic risk classification; apply recomputes
and compares the revision and exact plan before writing. Permit direct apply
only for library-defined low-risk, bounded, non-executable edits. Bind
high-risk confirmation to the exact dry-run plan hash, and verify the applied
diff and postconditions through the strongest bounded local verification
tier the affected capabilities support.

**Acceptance:** Services expose closed typed inputs and outputs with stable
errors, and produce the same semantic results however they are invoked. A
dry-run reports normalized operations, semantic changes to nodes, edges,
configuration, preamble, and sidecars, risk and egress disclosures, expected
postconditions, base revision, plan hash, and verification tier. Mutations
use the existing transactional save and ledger-capture paths, reject stale or
altered plans before writing, cannot apply the same plan twice, cannot cross
the library-defined authority boundary, leave no unrelated graph changes,
and report the actual diff and verification evidence without claiming a
stronger tier than ran.

**Dependencies:** ASSIST-A04, the parser, transactional save service, graph
operations, execution/trace services, and the server API boundaries.

**Evidence:** `src/haute/assistant/_ops.py`;
`src/haute/assistant/_tools.py`; `src/haute/routes/_save_pipeline.py`;
`tests/test_assistant_ops.py`; `tests/test_assistant_integration.py`.

### ASSIST-A06 — Deterministic recipes and executable examples

**Why:** The packaged examples currently teach only a few structural shapes
and are parse-oriented rather than complete runnable pricing scenarios. The
assistant needs progressive, trustworthy worked practice without making
recipes a second mutation vocabulary.

**Plan:** Add optional versioned recipe planners that expand into canonical
primitive operations. Replace parse-only examples with discoverable project
bundles containing source, sidecars, synthetic data, expected graphs/schemas,
golden inputs/outputs, boundary cases, paired user prompts, and semantic
assertions. Add troubleshooting guidance tied to stable error codes to the
packaged knowledge index. Deliver the portfolio in phases: first minimal
batch and live quote flows, continuous banding, a reference join, and a
rating step; then the remaining portfolio shapes.

**Acceptance:** Recipes cannot bypass primitive validation, revision,
authority, consent, or verification policy; every packaged bundle loads from
installed wheel and source distributions; every bundle parses and validates
in packaging checks, the fast non-training subset also executes end-to-end
there, and training/optimisation bundles execute in the ordinary test suite;
pricing semantics receive the review required by the decision below.

**Dependencies:** ASSIST-A04, ASSIST-A05, installed package-resource support,
execution/trace services, and pricing-domain review.

**Evidence:** `src/haute/assistant/_assets.py`;
`src/haute/assistant/assets/examples`;
`src/haute/assistant/assets/authoring_guide.md`;
`tests/test_assistant_assets.py`; `scripts/package_smoke_check.py`.

### ASSIST-A07 — Provider egress and automatically derived project knowledge

**Why:** Read operations can send rows, configuration, executable source, or
project knowledge to the configured provider and can retain tool payloads in
session history. Credential filename denylisting does not protect personal or
commercially sensitive values. Teams also hold pricing knowledge — field
meanings, conventions, and referenced artifact versions — across ordinary
project artifacts. The assistant should use that knowledge while keeping it
visibly separate from the library's canonical facts and hidden prompt state.
Requiring analysts to copy it into an assistant-specific file would duplicate
those sources, add setup work, and inevitably become stale.

**Plan:** Make schema inspection schema-only by default. Separate raw-row
sampling behind a typed, bounded operation with a concrete disclosure and
explicit consent. Define provider endpoint trust and egress policy,
sensitivity labels, typed redaction, and distinct provider-working,
transcript, audit, and durable-state representations. Build a bounded,
task-specific knowledge view automatically from sources Haute already owns or
can inspect through path-secure services: the saved graph, pipeline source and
sidecars, `haute.toml`, dataset schemas and declared metadata, immutable
artifact manifests, and allowlisted project documentation. Every retrieved
item carries its source identity and digest, extraction version, sensitivity,
and evidence class. Cache only content-derived indexes under the existing
private `.haute/` state; they are disposable, automatically invalidated, and
never a source of truth. Add the required `[assistant.egress]` policy table to
`haute.toml`, using the exact trust classes decided below. Live UI state, user
choices, and temporary consent stay in an ephemeral turn envelope. Unknown
sensitivity is never silently treated as public.

**Acceptance:** No raw row, sensitive configuration, executable source, or
restricted project-derived material crosses the provider boundary without a
permitted egress class and any required request-bound consent; schema queries
cannot silently include samples; credentials and restricted fields never
enter model-visible or durable assistant payloads. An ordinary Haute project
provides useful source-linked knowledge without an assistant-specific file or
onboarding form. Source changes invalidate affected index entries; deleting
the generated cache changes only warm-up cost and the cache rebuilds
automatically. Assistant answers distinguish canonical library facts,
deterministic project facts, retrieved untrusted text, live state, and model
inference. Every project source that affects planning and the effective egress
policy is covered by the revision digest. Material ambiguity produces one
focused question at the point of use rather than silent guessing, a hidden
system prompt, or silently learned durable memory. Invalid or unknown egress
fields fail loudly naming `haute.toml` and their full TOML path.

**Dependencies:** ASSIST-A04, ASSIST-A05, provider configuration, session
persistence, project inspection services, and project/path security.

**Evidence:** `src/haute/assistant/_config.py`;
`src/haute/assistant/_project_knowledge.py`;
`src/haute/assistant/_session.py`; `src/haute/assistant/_tools.py`;
`tests/test_assistant_config.py`; `tests/test_assistant_session_persistence.py`;
`tests/test_assistant_project_knowledge.py`; `tests/test_assistant_tools.py`;
`specs/assistant/high-level.md`.

### ASSIST-A08 — Semantic, adversarial, and performance evaluation gate

**Why:** Scripted-provider tests strongly cover mechanics but do not measure
whether supported models produce correct, minimal Haute changes across
representative pricing tasks, nor whether quality, privacy, cost, and latency
remain inside a supportable product envelope.

**Plan:** Run pinned provider/model configurations in isolated temporary
projects and score graph semantics, postconditions, unrelated diffs,
clarification, recovery, prompt injection, unauthorized actions, sensitive
data handling, time to first token, time to validated plan, end-to-end
latency, provider round trips, tool calls, input/output tokens, and estimated
cost, including cold/warm p50 and p95 timing. Reuse ASSIST-A06 project
mechanics where useful, but keep evaluation
requests, expected operations, and adversarial variants held out and
undiscoverable through assistant tools. Run repeated trials with attributable
provider parameters and keep live-provider evaluation outside deterministic
unit tests.

**Acceptance:** The harness produces attributable results by Haute version,
capability manifest, prompt, provider, model version, provider parameters,
fixture version, and run. A version-controlled support matrix defines
per-task success, zero-tolerance safety, token/cost, tool-call, and cold/warm
latency thresholds before a configuration is qualified; repeated trials and
regression reports gate supported releases. Assertions target semantic
outcomes rather than exact prose or tool order. Adversarial cases cover
embedded instructions in project data and documents, interruption, stale
state, secret or sensitive-data leakage, and out-of-scope or unsupported
operations. Teaching examples and held-out evaluation cases are checked to
remain separate. Representative tasks start from ordinary Haute project
artifacts and do not receive assistant-specific knowledge fixtures.

**Dependencies:** ASSIST-A04 through ASSIST-A07, approved provider
credentials, isolated project fixtures, a versioned threshold configuration,
and pricing review for domain-bearing golden tasks.

**Evidence:** `tests/test_assistant_loop.py`;
`tests/test_assistant_providers.py`; `tests/test_assistant_integration.py`;
`tests/test_assistant_assets.py`.

## Core principle

Haute should be self-describing:

```text
Canonical Haute capability registry
    |
    +-- runtime validation and execution
    +-- in-app assistant tools
    +-- packaged knowledge and example index
    +-- compatibility and completeness tests
```

Adding or changing a library feature should update its canonical definition
once. Every assistant-facing representation should then be generated from or
linked to that definition.

No assistant-only copy of node names, configuration keys, defaults, or
validation rules should become a second source of truth.

## Assistant responsibilities

The assistant should support two primary classes of work.

### Querying Haute

- Explain a node type, configuration field, wiring rule, error, or execution
  behaviour using the installed library's definitions.
- Inspect the current project, pipeline graph, node configuration, schema,
  and supported optional capabilities.
- Find relevant packaged recipes, executable examples, and troubleshooting
  guidance.
- Explain why a proposed action is invalid or unavailable in this
  installation.
- Distinguish canonical library facts, source-linked project facts, retrieved
  untrusted guidance, live project state, and model inference.

### Building pipelines

- Translate user intent into typed Haute operations or higher-level recipes.
- Inspect the saved graph and relevant schemas before proposing changes.
- Ask for decisions that Haute cannot infer safely, such as band boundaries,
  join cardinality, missing-value policy, model version, or optimiser
  constraints.
- Validate a proposed change with a dry-run through the real library
  services, including its semantic diff, risk class, and postconditions.
- Apply bounded low-risk changes, or an exactly confirmed high-risk plan,
  through the existing transactional save path.
- Verify the resulting graph and the strongest supported local executable
  contracts, then report the actual changes, evidence, warnings, and new
  project revision.

The assistant should not use a raw shell or unstructured source-file editing
as an undisclosed fallback for operations the library does not support.

## Library-owned capability registry

The library should expose one versioned capability registry through a Python
API. The in-app assistant's tools should be adapters over this API.

### Registry identity

The registry should report:

- installed Haute version;
- manifest schema version;
- deterministic capability hash;
- installed optional engines and data formats;
- enabled feature flags.

The capability hash changes exactly when any derived or hand-authored
capability fact changes; it feeds the project-revision digest and
evaluation attribution, while dynamic project state is refreshed
separately. Immutable manifest material is cached by installed Haute version
and capability hash rather than rebuilt or resent in full for every provider
round.

### Node descriptors

Every supported node type should expose a complete descriptor containing:

- canonical node type and decorator;
- resolved configuration JSON Schema;
- required and optional fields;
- defaults and enum values;
- nested structures and conditional configuration branches;
- cross-field constraints;
- input and output ports;
- input cardinality and wiring rules;
- singleton and sidecar behaviour;
- expected schema effects;
- execution and side-effect classification;
- concise "when to use" guidance;
- common anti-patterns;
- relevant examples and recipes;
- stable error codes and remediation guidance.

Mechanical facts should be derived from the same enums, config types,
registries, and validators used at runtime. Semantic guidance that cannot be
derived mechanically should be first-class, hand-authored metadata colocated
with the canonical feature definition and guarded by completeness tests.

### Operation descriptors

Every assistant-callable operation should declare:

- stable operation name and version;
- closed input and output JSON Schemas;
- whether it reads or mutates state;
- required project and branch state;
- expected project revision semantics;
- deterministic risk and provider-egress classes;
- side effects and local execution or financial cost class;
- idempotency and retry semantics;
- whether it is cancellable, cacheable, or safe to run in parallel;
- concurrency group and ordering constraints where applicable;
- timeout, operation-count, payload, and model-context contribution limits;
- stable errors and recovery guidance.

Operation results should expose bounded, redacted timing, cache, payload, and
usage metadata needed for evaluation and production diagnosis without
retaining user content.

The primitive graph operations should remain available, but the registry may
also expose deterministic higher-level actions where they make common
pipeline work safer and easier.

### Recipe descriptors

A recipe packages a common Haute intent, such as adding continuous banding or
joining reference data. It should define:

- stable recipe ID and version;
- user intent and appropriate use cases;
- required inputs and unresolved decisions;
- preconditions;
- typed arguments;
- deterministic planning implementation;
- operations it may produce;
- validation and postconditions;
- applicable examples;
- common failure modes.

Recipes are library functionality, not prompt snippets. Planning a recipe
should return ordinary validated Haute operations that can be inspected,
dry-run, and applied through the same operation layer as every other client.

## Canonical assistant workflow

For a pipeline-building request, the supported workflow is:

1. Read the installed manifest identity and compact capability index, reusing
   immutable manifest content when its capability hash is unchanged.
2. Inspect the current project and saved graph revision.
3. Build or incrementally refresh the source-linked project knowledge view,
   then retrieve only the project facts, node descriptors, recipes, examples,
   and schemas relevant to the request.
4. Use schema-only inspection by default; request a bounded sensitive-read
   disclosure and consent before retrieving rows or restricted context.
5. Ask the user for any material decision that remains ambiguous.
6. Produce typed operations or a recipe plan, explicit postconditions, and a
   verification tier against the observed revision.
7. Dry-run the exact plan through the real validators and return its semantic
   diff, risk class, egress disclosure, plan hash, and base revision.
8. Apply a library-defined low-risk plan directly. For a high-risk plan, show
   the concrete diff or disclosure and bind explicit confirmation to the
   exact plan hash before apply.
9. Apply through the transactional save service with the expected project
   revision, rechecking the plan, revision, authority, and consent.
10. Reparse the result and run the strongest bounded local verification tier
    supported by the affected capabilities.
11. Compare the actual diff and postconditions with the dry-run, then report
    exact changes, evidence, warnings, resulting revision, and the existing
    git ledger reference used for review and undo.

For a question about Haute, the workflow stops after retrieving and
explaining the relevant canonical facts. Read-only assistance should remain
available when mutation prerequisites are not satisfied.

## Revision and validation model

Transactional saving protects files from partial writes but does not by
itself prove that the model acted on the state it inspected. Mutations
therefore carry an expected project revision: dry-run reports the base
revision, and apply recomputes and compares it before writing, rejecting a
stale plan with a stable error.

The authoritative project revision is a deterministic digest of a canonical
snapshot manifest covering every input whose change could invalidate the
plan:

- pipeline source;
- relevant sidecars;
- `haute.toml` and every additional project source from which a fact that
  affected planning was derived;
- the installed capability manifest hash;
- immutable identifiers or content digests for referenced artifacts.

Large artifacts need not be rehashed when an authoritative immutable digest
already exists. A local monotonic workspace sequence may accompany the
manifest digest for efficient change detection, but it does not replace
content identity. The graph fingerprint remains useful for display and canvas
synchronisation but is not the concurrency token.

A dry-run result should include the normalized operations, affected nodes and
contracts, semantic node/edge/config/preamble/sidecar diff, validation errors
and warnings, the resulting graph shape, explicit postconditions,
deterministic risk and egress classifications, required consent, verification
tier, base project revision, and a stable plan hash. A short-lived
server-owned validation token may bind those facts for efficient apply, but
application still revalidates the revision, operation payload, authority,
consent, and plan hash rather than trusting a prior model response.
One plan applies at most once as one transactional primitive-operation batch;
a repeated apply returns the recorded result or a stable already-applied
error, never a duplicate mutation. Any correction is a new plan against the
resulting revision.

Verification is tiered and attributable:

- structural verification reparses and validates the saved graph;
- plan verification builds the same lazy execution plan and schema contracts
  the affected nodes use at runtime without collecting rows;
- bounded local execution verification runs only where the operation and data
  policy permit it, returns postcondition evidence rather than raw rows by
  default, and obeys execution budgets;
- training, optimisation, deployment, external writes, and Git operations
  remain unavailable unless a later owning component explicitly adds a
  separately classified operation.

The assistant must report which tier ran and must never describe structural or
plan verification as proof of row-level outputs, trained-model quality, or
commercial correctness.

## Packaged knowledge

The registry supplies authoritative runtime facts, but correct pipeline
authoring also needs reviewed semantic guidance.

Haute should package:

- concise node usage and anti-pattern guidance;
- task-oriented recipes;
- troubleshooting cards tied to stable error codes;
- executable example projects;
- a compact index suitable for progressive retrieval.

The permanent model prompt should remain small: role, authority boundaries,
evidence rules, clarification policy, and the required
inspect/validate/apply/verify discipline. Detailed product knowledge should
be retrieved from the installed library only when relevant.

Retrieved knowledge should carry an ID, version or hash, source, sensitivity,
and approval status, so the assistant can distinguish canonical facts from
source-linked project facts and model inference. Tool results, project files,
documentation, and dataset values are untrusted content, not instructions.

## Automatically derived project knowledge

Haute should build the knowledge needed for each task from project artifacts
users already maintain as part of normal work:

- the saved graph, pipeline source, and validated sidecars for topology,
  configuration, naming, and declared behaviour;
- `haute.toml` for canonical project and provider policy;
- schema services for column names, types, nullability, and other declared
  metadata, without reading rows by default;
- immutable artifact manifests for referenced model, ratebook, and other
  versions and digests;
- project documentation exposed through a bounded, path-allowlisted
  inspection service for business language and conventions.

Structured facts derived deterministically from validated project artifacts
may be treated as project facts. Natural-language documentation remains
untrusted evidence rather than policy or instructions. Model-derived
interpretations are labelled as inference and cannot authorize operations,
weaken library policy, or silently become durable facts. Conflicting sources
and material gaps cause a focused clarification at the point of use.

Each retrieved item is attributable to a source path or stable artifact ID,
source digest, extraction version, sensitivity, and evidence class. Sources
that affect a proposed change are included in its project revision. Live UI
state and user choices belong in an ephemeral turn envelope. A confirmed
decision becomes durable only when an existing owning project artifact has a
typed field for it and the user authorizes updating that artifact through the
ordinary plan/apply path.

Haute may keep a content-addressed retrieval index under the existing
Git-ignored `.haute/` state to make warm turns fast. The index contains no
independently editable facts, is refreshed incrementally from source digests,
and is safe to delete and rebuild.

## Executable examples

Examples should be complete, runnable project bundles rather than parse-only
topology demonstrations. Each bundle should contain:

- pipeline source and sidecars;
- tiny synthetic data;
- project configuration;
- expected graph and schemas;
- golden inputs and outputs;
- boundary and invalid cases;
- paired natural-language requests;
- semantic assertions for the expected assistant result;
- targeted parse, execute, trace, and dry-run tests in its declared
  assertion tier.

The example portfolio should cover the library's distinctive pipeline
shapes, including:

- minimal batch and live quote flows;
- continuous and discrete banding;
- rating tables, combined outputs, and missing-factor policy;
- reference joins and cardinality validation;
- multi-table API input and output mapping;
- modelling and model scoring;
- live and batch source parity;
- reusable submodels;
- online scenario optimisation;
- ratebook optimisation and versioned apply;
- tracing and audit evidence;
- deployment-safety fixtures;
- deliberately invalid and adversarial examples.

Examples should be discoverable through the capability registry.
Packaging tests must load the installed artifacts rather than only testing
examples from a source checkout: every bundle parses and validates there,
the fast non-training subset also executes end-to-end there, and training
and optimisation bundles execute in the ordinary test suite.

Discoverable examples are teaching assets, not the complete evaluation
corpus. ASSIST-A08 may reuse their project mechanics, but held-out requests,
expected operations, perturbations, and adversarial variants must not be
available through the capability registry, packaged knowledge tools, or
permanent prompt.

## Safety

Safety reuses the existing save, path, sandbox, and Git controls and adds the
authority and provider-egress boundaries required at the model/tool seam:

- Every assistant mutation is a batch of validated primitive operations
  committed through the existing transactional save service, under the same
  validation the GUI uses.
- Mutations require the ledger-ready working branch, so every applied change
  is captured and reviewable through the existing Git diff and undo surfaces.
  That recovery path does not itself authorize a change.
- The library, not the model or retrieved project content, assigns operation
  risk, egress, side-effect, and scope-limit classes. Those classes are
  minimums and project-derived material cannot weaken them.
- Capability queries, saved-state inspection, schema-only reads, planning,
  dry-run, and bounded low-risk non-executable edits may proceed without a
  second confirmation. High-risk mutations and sensitive reads require a
  concrete diff or disclosure and confirmation bound to the exact mutation
  plan or sensitive-read disclosure hash.
- High-risk classes include deletion, executable code or preamble changes,
  shared-submodel changes, sensitive-data reads, artifact or model-version
  changes, external writes, training, optimisation, deployment, Git
  operations, and any mutation exceeding the library-defined scope limit.
- Schema inspection never includes rows implicitly. Raw samples are a
  separate bounded operation; provider trust, data sensitivity, current
  egress policy, and temporary consent are checked before the read.
- Credentials remain environment-only, denylisted paths stay unreadable, and
  secret or restricted values never appear in model-visible output or
  durable assistant payloads. Transcript, provider working context, audit
  evidence, and durable task state have separate representations and
  retention rules.
- The library enforces each descriptor's payload, operation-count, timeout,
  cancellation, concurrency, and model-context contribution limits.
- Tool results, project files, documentation, and dataset values are
  untrusted content, not instructions, and prompt instructions are not a
  security boundary.
- A second model may critique a plan, but only deterministic library
  validation, authority policy, revision checks, and, where required,
  plan-bound user consent can authorize a mutation.

## Compatibility and evolution

- The capability manifest has an explicit schema version.
- Operations and recipes have stable IDs and explicit versions.
- Unknown fields fail where the contract is closed.
- Unsupported operations return a named capability error rather than a
  silent fallback.
- Assistant results record the capability hash, operation versions, project
  revision, and plan hash used.
- A resumed session records the capability hash that produced each retained
  tool payload; incompatible payloads are migrated explicitly or excluded
  from new provider context rather than silently reinterpreted.

As Haute develops, a newly installed version immediately exposes its current
capabilities without waiting for an external prompt, documentation release,
or model update.

## Quality contract

Library and packaging checks should enforce:

- every node type has a complete descriptor;
- derived descriptors agree with canonical runtime validators;
- every operation has closed input/output schemas and complete risk, egress,
  idempotency, concurrency, timeout, payload, and context-budget metadata;
- recipes produce valid primitive operations;
- packaged examples are present in wheel and source distributions;
- executable examples parse, validate, and run their declared assertion
  tier;
- schema-only reads never include row values;
- consent, provider trust, sensitivity, redaction, and retention policy are
  enforced before sensitive context becomes provider-visible;
- project knowledge is useful without assistant-specific setup, every
  retrieved item is source-linked, and source changes invalidate affected
  cache entries;
- deleting generated project-knowledge indexes and rebuilding them preserves
  the source-derived result;
- secret and restricted values never appear in assistant-facing or durable
  output;
- stale revisions are rejected before writing;
- changed plan payloads cannot reuse a prior confirmation or validation token;
- high-risk operations cannot apply without confirmation of the exact plan;
- mutations produce no unrelated graph changes;
- apply and verify report the actual semantic diff, postcondition evidence,
  and truthful verification tier;
- evaluation scenarios cover clarification, recovery, interruption, prompt
  injection, sensitive-data handling, and out-of-scope requests;
- held-out evaluation fixtures remain undiscoverable to the assistant;
- supported provider/model configurations meet version-controlled semantic,
  safety, tool-call, token/cost, and cold/warm latency thresholds across
  repeated trials.

Model-quality evaluation should assert graph semantics and postconditions
rather than exact prose or an exact sequence of tool calls. Safety outcomes
such as unauthorized mutation or restricted-data leakage are zero-tolerance,
not averaged into an overall quality score.

## Non-goals

- A general-purpose shell agent.
- A machine-readable agent CLI, headless mutation surface, or other
  serverless project tooling; Haute is used through the running server.
- An integration protocol or compatibility surface for external coding
  agents.
- A separately maintained assistant vocabulary that can drift from Haute.
- A second canvas staging editor or general approval workflow. High-risk
  confirmation is a chat interaction bound to the application service's
  concrete dry-run plan; Git remains the review and recovery surface after
  apply.
- Silent source editing when an operation is unsupported.
- Hidden, automatically learned project instructions.
- A raw system-prompt editor.
- Treating an LLM reviewer as an authorization boundary.
- Automatically granting training, optimisation, deployment, or Git
  authority merely because those capabilities exist elsewhere in the
  library.
- General actuarial, regulatory, or legal advice unrelated to building or
  explaining Haute pipelines.

## Design decisions

### Low-risk edits apply directly; high-risk confirmation is plan-bound

The initial user request authorizes library-defined low-risk, bounded,
non-executable graph edits. Those changes apply through the normal
transactional save path without a second approval step. Git capture, diff,
and undo remain the ordinary post-apply review and recovery surface.

A model cannot widen that authority. Before a high-risk mutation or sensitive
read, the assistant presents the application service's concrete semantic diff
or disclosure. Confirmation is stored server-side against the exact mutation
plan or sensitive-read disclosure hash, project revision, risk class, and
consent scope; any changed operation, revision, endpoint, policy, or sensitive
field invalidates it. This is an in-chat control, not a second canvas staging
editor. Stop, new-chat, clean-canvas, top-level-view, and working-branch gates
remain complementary interaction controls (`ASSIST-02`).

### The running server owns all project use

Haute is a locally run, single-analyst tool operated through `haute serve`
and the app. Every mutation flows through the server process: GUI saves and
assistant edits share its save lock and broadcast channel. No serverless or
headless project surface is built, and no cross-process write coordination
is designed — a second writer process is out of scope, not defended
against. The expected-revision check exists because the save lock covers
each save, not the span between the assistant's inspect and its apply; its
job is rejecting stale plans, nothing more.

### Primitive operations are canonical; recipes are optional

Typed primitive operations are the complete, stable mutation vocabulary.
Recipes are optional, preferred planners for common or complex intents. A
recipe expands deterministically into ordinary primitive operations and
cannot bypass their validation, authority, consent, or revision checks. An
agent may use primitives directly for valid work that has no matching recipe.
The absence of a recipe never authorizes raw source editing as a fallback.

### Project knowledge is derived, never separately authored

Haute does not define, read, generate, or recommend a dedicated project
context file or replacement catch-all knowledge file. There is no setup form
that asks users to restate their project for the assistant. The assistant
constructs a task-scoped view from the canonical project artifacts and
bounded documentation sources listed above, retrieving only what the current
request needs.

Content-addressed extraction and indexing make this incremental: unchanged
sources reuse cached entries, changed or removed sources invalidate their
entries, and a cold cache rebuilds automatically. Generated index data is
private `.haute/` state, not tracked project state and not an input to the
project revision; the source digests represented by the index are the revision
inputs. Cache content never gains authority independent of its source.

The assistant may infer a candidate interpretation when the evidence supports
it, but exposes that status and asks one focused question before a material
ambiguity can affect a plan. The answer is bound to the current turn or plan.
It is persisted only through a separately authorized typed update to an
existing owning artifact; there is no silent cross-session memory.

### Provider trust and data egress are explicit

A syntactically valid provider URL establishes wire compatibility, not trust.
ASSIST-A07 adds one required, closed table to `haute.toml`:

```toml
[assistant.egress]
trust = "organization"
max_sensitivity = "internal"
allow_project_knowledge = true
allow_executable_source = false
allow_row_samples = false
```

`trust` is exactly `local`, `organization`, or `external`;
`max_sensitivity` is exactly `public`, `internal`, or `restricted`; and all
three `allow_*` booleans are required. No credentials or credential
references are accepted. On delivery, the closed `[assistant]` table's
accepted keys become `provider`, `model`, `base_url`, and the nested `egress`
table. A configured assistant without a valid egress table is reported not
ready for provider use with a field-specific migration message; it never
guesses a trust class.

Sensitivity is ordered `public` < `internal` < `restricted`.

The trust classes set library maximums that project configuration may narrow
but never widen:

| Trust | Endpoint requirement | Maximum provider-visible content |
|---|---|---|
| `local` | The effective endpoint host is `localhost` or a loopback IP literal; HTTP or HTTPS is allowed. | Content up to the configured sensitivity, including `restricted`; source and rows still require their allow flag and request-bound consent. |
| `organization` | HTTPS and an explicit operator assertion that the endpoint is an approved organizational recipient. | Content up to the configured sensitivity, including `restricted`; restricted content, executable source, and rows require their allow flag where applicable and request-bound consent. |
| `external` | HTTPS; no organizational trust assertion. | Canonical library knowledge and `public` project metadata/schema only. `max_sensitivity` must be `public`; executable source and row samples are forbidden even if a user would consent. |

Invalid combinations fail during readiness: `local` with a non-loopback host;
`organization` or `external` without HTTPS; a sensitivity above the class
maximum; or `external` with executable-source or row-sample access enabled.

Schema-only inspection is the default and never includes rows. Source-linked
project knowledge is eligible only when `allow_project_knowledge` is true and
its sensitivity does not exceed `max_sensitivity`. Executable source and raw
rows are eligible only for `local` or `organization` trust when their
corresponding flag is true; each read still requires a disclosure and consent
bound to the exact disclosure hash, project revision, endpoint identity,
egress-policy hash, fields, sensitivity, and row limit. Consent expires after
that operation and is neither persisted nor reusable. Endpoint, trust,
policy, revision, field set, sensitivity, or row-limit changes invalidate it.
Unknown sensitivity is handled as `restricted`, never `public`.

Status and confirmation surfaces show the effective endpoint host, trust
class, maximum sensitivity, content category, fields, and row limit without
credentials or values. An OpenAI-compatible protocol establishes only wire
behaviour; official or custom endpoints are `organization` only when the
operator makes that explicit assertion.

Provider working context contains only the bounded material needed for the
current turn. Raw tool payloads are not copied automatically into durable chat
history; durable transcript and audit representations retain redacted
summaries, stable identifiers, hashes, revisions, decisions, and evidence
required for recovery and attribution.

### Read-only assistance is independent of mutation readiness

Library capability queries and documentation lookup are always available
when the installation itself can serve them. Saved-state graph, config, and
schema inspection may operate on dirty, detached, divergent, or otherwise
mutation-disabled projects. Every result identifies the saved project
revision it describes; the existing clean-canvas send gate remains the
in-app answer for browser-local edits. Submodel navigation does not disable
read-only help: reads declare their graph scope, and mutation remains
subject to the normal graph-scope and revision rules.

### One application layer behind every assistant surface

The shared typed Python application-service layer is canonical. The in-app
assistant's tools call it directly, and any future surface is a thin
adapter over the same services, owning transport, serialization, and
presentation only. Nothing reimplements node semantics, recipes,
validation, mutation, or revision handling.

### Pricing review is proportional to semantic content

Executable examples and evaluation tasks that encode actuarial or commercial
pricing choices require pricing-domain review. This includes exposure
treatment, rating defaults, premium composition, model validation,
optimisation objectives or constraints, fairness claims, and governance
conclusions. Purely mechanical fixtures, such as basic graph wiring or
output-path syntax, require normal engineering review. Every example,
regardless of reviewer, must still pass its executable assertions and
packaging checks.

## Component-spec deltas on delivery

Per the working protocol, each package updates the owning component
specification before changing behaviour. Known deltas:

- ASSIST-A04 replaces the assistant catalog with registry-backed descriptors
  and moves prompt assembly from re-sending the full catalog every turn to a
  compact always-present index plus on-demand retrieval.
- ASSIST-A05 adds the application-service layer, semantic plan/diff/verify
  contract, verification tiers, revision handling, and plan-bound mutation
  authority to the assistant, server-api, execution, and assistant-UI
  specifications.
- ASSIST-A06 changes the packaged-example format from parse-only exemplars
  to executable bundles and separates discoverable teaching assets from
  held-out evaluation cases.
- ASSIST-A07 adds provider trust, egress, consent, redaction, retention,
  source-linked project-knowledge retrieval, and disposable index contracts
  to the assistant, provider configuration, session, and assistant-UI
  specifications. It expands the current closed `[assistant]` configuration
  with the required nested `egress` table and a field-specific migration
  error for older configurations.
- ASSIST-A08 adds the supported provider/model qualification matrix and
  real-model performance lane to the assistant and engineering-quality
  specifications.

Ledger-captured saves, environment-only credentials, and the clean-canvas
gate remain as specified today. ASSIST-A05 narrows unconditional direct apply:
low-risk bounded edits remain direct, while high-risk plans and sensitive
reads gain exact-plan confirmation.

## Remaining design questions

None for ASSIST-A04 through ASSIST-A08. The automatic project-knowledge source,
provenance, cache, revision, ambiguity, provider trust, egress, and consent
contracts are decided above; ASSIST-A07 is planned rather than
decision-blocked.

## Delivered outcomes

Delivered before the current numbering scheme:

- `ASSIST-01` validates a resume offer's source binding before touching a
  live session or promoting a disk record. Successful persisted graph
  mutations rehydrate their settled "Canvas updated" activity in order,
  while current tool records require an explicit error flag. Backend
  restart/LRU tests and the frontend hydration test enforce the complete
  contract.
- `ASSIST-02` is resolved by the following 2026-07-27 product decision:
  provider and model selection remain operator-owned project configuration
  in `haute.toml`, not per-session UI state; credentials remain
  environment-only. Domain guidance belongs in the versioned system-prompt
  catalog, authoring guide, and on-demand examples rather than a free-form
  prompt editor or speculative node-action palette. Recovery remains typed
  and explicit: readiness blocks sending with its reason, status-fetch
  failure offers retry, an expired session offers a new chat, a 409 explains
  that the prior turn is still finishing, transport/provider failures stay
  inline, and no mutating send is automatically retried or failed over to
  another provider. Stop, new-chat, clean-canvas, top-level-view, and
  working-branch controls are the supported approval/recovery surface.
  Per-session provider selectors, raw system-prompt editing, automatic
  provider failover, and automatic replay of a failed mutating turn are
  rejected because they undermine reproducibility or risk duplicate graph
  edits. Existing API/store/component suites cover readiness, recovery,
  cancellation, neutral provider events, and graph-update feedback.
- `ASSIST-03` closes `[assistant]` to `provider`, `model`, and `base_url`;
  unknown keys name their TOML paths, and OpenAI endpoints must be
  credential-free absolute HTTP(S) URLs. Structural errors are redacted and
  fail before SDK probing or provider construction.
