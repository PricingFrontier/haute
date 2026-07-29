# Assistant roadmap

## Product direction

The Haute assistant is an agent for building Haute pipelines and answering
questions about how the installed Haute library works.

The model is responsible for understanding natural-language intent, asking for
missing decisions, and explaining results. Haute itself is responsible for the
facts, supported actions, validation, persistence, and proof that a change is
correct.

The installed library must therefore be the assistant's source of truth. The
assistant must discover Haute's capabilities at runtime instead of relying on
a separately maintained prompt that can drift behind the library.

This direction extends the existing [assistant](../assistant/high-level.md) and
[assistant UI](../frontend-assistant-ui/high-level.md) components. It does not
replace the parser, execution engine, transactional save service, or graph
update channel.

## Core principle

Haute should be self-describing:

```text
Canonical Haute capability registry
    |
    +-- runtime validation and execution
    +-- agent-facing CLI
    +-- in-app assistant tools
    +-- human CLI help
    +-- generated documentation
    +-- optional external agent protocol adapters
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
- Inspect the current project, pipeline graph, node configuration, schema, and
  supported optional capabilities.
- Find relevant packaged recipes, executable examples, and troubleshooting
  guidance.
- Explain why a proposed action is invalid or unavailable in this installation.
- Distinguish canonical library facts from project policy, retrieved guidance,
  live project state, and model inference.

### Building pipelines

- Translate user intent into typed Haute operations or higher-level recipes.
- Inspect the saved graph and relevant schemas before proposing changes.
- Ask for decisions that Haute cannot infer safely, such as band boundaries,
  join cardinality, missing-value policy, model version, or optimiser
  constraints.
- Dry-run and validate a proposed change through the real library services.
- Apply accepted changes through the existing transactional save path.
- Verify the resulting graph and return a semantic diff, warnings, and the new
  project revision.

The assistant should not use a raw shell or unstructured source-file editing as
an undisclosed fallback for operations the library does not support.

## Library-owned capability registry

The library should expose one versioned capability registry through a Python
API. The agent-facing CLI and in-app assistant tools should be adapters over
this API.

### Registry identity

The registry should report:

- installed Haute version;
- agent protocol version;
- manifest schema version;
- deterministic capability hash;
- project configuration version where relevant;
- installed optional engines and data formats;
- enabled feature flags and target capabilities.

An agent can cache the manifest by Haute version and capability hash, while
still refreshing dynamic project state separately.

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

Every agent-callable operation should declare:

- stable operation name and version;
- closed input and output JSON Schemas;
- whether it reads or mutates state;
- required project and branch state;
- expected project revision semantics;
- privacy classification;
- side-effect and cost classification;
- whether it is cancellable;
- whether it can run in parallel;
- timeout and payload limits;
- stable errors and recovery guidance.

The primitive graph operations should remain available, but the registry may
also expose deterministic higher-level actions where they make common pipeline
work safer and easier.

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

## Agent-facing CLI

The CLI should provide a stable, non-interactive, machine-readable interface to
the capability registry and pipeline application services.

Illustrative discovery and query commands:

```text
haute agent manifest --json
haute agent status --json
haute project inspect --json
haute pipeline inspect --json
haute pipeline validate --json
haute node describe banding --json
haute node inspect vehicle_bands --json
haute node schema enriched_quotes --json
haute data schema data/quotes.parquet --json
haute data profile data/quotes.parquet --columns vehicle_age --json
haute docs search "rating step missing values" --json
haute examples search banding --json
haute examples show motor-rating --json
haute recipes list --json
haute recipe describe add-continuous-banding --json
```

Illustrative planning and mutation commands:

```text
haute recipe plan add-continuous-banding --args args.json --json
haute pipeline patch --ops edits.json --dry-run --json
haute pipeline patch --ops edits.json --expect-revision <revision> --json
haute pipeline validate --affected vehicle_age_bands --json
```

The final names may follow the wider CLI's eventual command taxonomy. The
important contract is that the underlying operations are shared and
machine-readable rather than being implemented specifically for one chat UI.

### CLI contract

Agent-facing commands should:

- accept structured arguments from files or stdin;
- never prompt interactively in machine mode;
- emit versioned structured data on stdout;
- emit logs and progress on stderr;
- use stable result and error codes;
- expose closed schemas for inputs and outputs;
- report the installed Haute and protocol versions;
- report the project revision used by a read or mutation;
- fail loudly on unknown fields or unsupported capabilities;
- avoid exposing secret values in output.

A typical result envelope could contain:

```json
{
  "ok": true,
  "schema_version": "1",
  "haute_version": "0.5.0",
  "capability_hash": "abc123",
  "project_revision": "def456",
  "result": {},
  "warnings": []
}
```

Errors should be equally structured:

```json
{
  "ok": false,
  "schema_version": "1",
  "error": {
    "code": "stale_revision",
    "message": "The pipeline changed after it was inspected.",
    "current_revision": "def456"
  }
}
```

## Canonical agent workflow

For a pipeline-building request, the supported workflow is:

1. Read the installed capability manifest.
2. Inspect the current project and saved graph revision.
3. Retrieve only the node descriptors, recipes, examples, and schemas relevant
   to the request.
4. Ask the user for any material decision that remains ambiguous.
5. Produce typed operations or a recipe plan against the observed revision.
6. Dry-run the operations through the real validators and return a semantic
   diff.
7. Apply through the transactional save service with an expected project
   revision.
8. Reparse and validate the affected graph state.
9. Return the exact changes, warnings, resulting revision, and recovery or Undo
   reference.

For a question about Haute, the workflow stops after retrieving and explaining
the relevant canonical facts. Read-only assistance should remain available
when mutation prerequisites are not satisfied.

## Revision and validation model

Transactional saving protects files from partial writes but does not by itself
prove that the model acted on the state it inspected.

Agent mutations should carry an expected project revision. The revision must
cover every file whose change could invalidate the plan, including pipeline
source and relevant sidecars. A simple graph fingerprint may be included for
display but is not necessarily sufficient as the sole concurrency token.

A dry-run result should include:

- normalized operations;
- semantic node, edge, config, and preamble diff;
- affected nodes and contracts;
- validation errors and warnings;
- resulting graph shape;
- proposed postconditions;
- base project revision;
- optional short-lived validation token.

Application must revalidate the revision and operation payload rather than
trusting a prior model or CLI response.

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
evidence rules, clarification policy, privacy rules, and the required
inspect/validate/apply/verify discipline. Detailed product knowledge should be
retrieved from the installed library only when relevant.

Retrieved knowledge should carry an ID, version or hash, source, and status such
as canonical, approved, provisional, or user-supplied. Tool results, project
files, documentation, and dataset values are untrusted content, not
instructions.

## Project-specific knowledge

Projects may optionally provide tracked, schema-validated context such as:

- business glossary and field meanings;
- data grain, keys, units, null conventions, and sensitivity labels;
- naming conventions;
- approved defaults and missing-value policies;
- approved model and ratebook versions;
- prohibited features or operations;
- currency, rounding, tax, commission, and effective-date conventions;
- shared submodel ownership;
- deployment and review requirements.

Project context must be visibly separate from the library's canonical facts.
It should be versioned with the project, attributable, validated, and included
in the project revision where it can affect a proposed change. This is not a
free-form hidden system prompt or silently learned memory.

Live UI context, user choices, and temporary consent belong in an ephemeral
turn envelope rather than durable project knowledge.

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
- targeted parse, execute, trace, and dry-run tests where applicable.

The example portfolio should cover the library's distinctive pipeline shapes,
including:

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

Examples should be discoverable through the capability registry and CLI.
Packaging tests must load and execute the installed artifacts rather than only
testing examples from a source checkout.

## Safety and privacy

An agent-facing read operation is also a potential provider-egress operation.
The capability layer must make that boundary explicit.

Required design properties include:

- schema inspection is schema-only by default;
- raw row samples require a separate operation and explicit consent;
- aggregate profiling is bounded and sensitivity-aware;
- connection details, credentials, inline records, and sensitive config fields
  are typed and redacted;
- provider endpoint trust and data-egress policy are visible;
- operation descriptors state privacy and side-effect classes;
- executable code, preamble changes, deletion, shared-submodel changes, and
  other high-risk actions can require explicit capabilities or approval;
- raw tool payloads are not automatically retained in durable chat history;
- transcript, provider working context, audit record, and durable task state
  are separate representations;
- payload, operation-count, time, and context-size limits are enforced by the
  library;
- prompt instructions are not treated as a security boundary.

An optional second model may critique a plan, but only deterministic library
validation and explicit capability policy can authorize a mutation.

## Compatibility and evolution

The agent protocol must evolve independently from the package version while
remaining attributable to it.

- Additive manifest fields require a compatible schema-version policy.
- Breaking operation or manifest changes require a protocol/schema version
  change.
- Operations and recipes have stable IDs and explicit versions.
- Unknown fields fail where the contract is closed.
- Unsupported operations return a named capability error rather than a silent
  fallback.
- Agent responses should record the manifest and project revisions used.
- Older external agents can inspect compatibility before attempting an
  operation.

As Haute develops, a newly installed version immediately exposes its current
capabilities without waiting for an external assistant prompt, documentation
release, or model update.

## Quality contract

Library and packaging checks should enforce:

- every node type has a complete descriptor;
- derived descriptors agree with canonical runtime validators;
- every operation has closed input/output schemas and risk metadata;
- recipes produce valid primitive operations;
- packaged examples are present in wheel and source distributions;
- executable examples parse, validate, and run against their assertions;
- CLI machine-mode output conforms to its declared schemas;
- human CLI, assistant tools, and optional protocol adapters agree with the
  same capability registry;
- secret and sensitive fields are redacted in agent-facing outputs;
- stale revisions are rejected;
- mutations produce no unrelated graph changes;
- evaluation scenarios cover clarification, recovery, interruption, privacy,
  prompt injection, and cross-version compatibility.

Model-quality evaluation should assert graph semantics and postconditions
rather than exact prose or an exact sequence of tool calls.

## Unsequenced design workstreams

These are architectural workstreams, not a priority or delivery order.

| Workstream | Outcome |
|---|---|
| Capability registry | The installed library exposes complete, versioned, self-describing capabilities. |
| Agent-facing operations | Query, plan, validate, apply, and verify use shared typed application services. |
| Machine-readable CLI | External agents can use those operations through stable structured commands. |
| Recipes and examples | Common Haute intents have deterministic planners and executable teaching fixtures. |
| Project context | Teams can provide tracked, validated, attributable pricing knowledge. |
| Revision and diff protocol | Agent changes are stale-safe, inspectable, and recoverable. |
| Privacy and capability policy | Reads, egress, execution, and mutation have enforceable boundaries. |
| Evaluation harness | Real models are measured against semantic pipeline tasks and adversarial cases. |

## Non-goals

- A general-purpose shell agent.
- A separately maintained assistant vocabulary that can drift from Haute.
- Silent source editing when an operation is unsupported.
- Hidden, automatically learned project instructions.
- A raw system-prompt editor.
- Treating an LLM reviewer as an authorization or security boundary.
- Automatically granting training, optimisation, deployment, or Git authority
  merely because those capabilities exist elsewhere in the library.
- General actuarial, regulatory, or legal advice unrelated to building or
  explaining Haute pipelines.

## Design decisions

### CLI evolution is additive

Existing public CLI commands and scripting behaviour remain compatible. New
grouped and machine-readable commands are added alongside them. Existing
commands may delegate to the same application services or become documented
aliases, but they are not removed or silently reinterpreted.

Any future deprecation follows an explicit compatibility policy. The exact
names and grouping of the new commands remain a discoverability question, not
an excuse to break current automation.

### Primitive operations are canonical; recipes are optional

Typed primitive operations are the complete, stable mutation vocabulary.
Recipes are optional, preferred planners for common or complex intents. A
recipe expands deterministically into ordinary primitive operations and cannot
bypass their validation, revision checks, capability policy, or audit trail.

An agent may use primitives directly for valid work that has no matching
recipe. The absence of a recipe never authorizes raw source editing as a
fallback.

### Mutation authority is deterministic and risk-based

The following actions may run without a separate approval step:

- capability and documentation queries;
- saved-state inspection;
- schema-only inspection;
- planning and dry-run validation;
- additive, non-executable graph edits that are explicitly requested by the
  user, stay within the stated node scope, and pass deterministic validation.

Before executing a high-risk action, the assistant must show a concrete
mutation diff or, for a sensitive read, a disclosure summary, and obtain
explicit confirmation. High-risk actions include:

- node or edge deletion;
- executable code or preamble changes;
- shared-submodel changes;
- sensitive-data reads;
- external writes;
- model, ratebook, or optimiser artifact selection and version changes;
- training, optimisation, deployment, or Git operations;
- a change exceeding the library-defined mutation scope limit;
- any operation outside the user's explicit scope.

Project policy may require stricter approval but cannot weaken the library's
high-risk classifications. A model, including a second reviewer model, never
grants capabilities or authorizes a mutation.

### Project revisions use a canonical snapshot manifest

The authoritative project revision is a deterministic digest of a canonical
snapshot manifest. The manifest includes:

- pipeline source;
- relevant sidecars;
- tracked project knowledge that affected planning;
- the installed capability manifest hash;
- immutable identifiers or content digests for referenced artifacts;
- any other declared input whose change could invalidate the proposed
  operation.

Large artifacts need not be copied or hashed repeatedly when an authoritative
immutable digest already exists. A local monotonic workspace sequence may
accompany the manifest digest for efficient change detection, but it does not
replace content identity.

Dry-run returns the exact base revision. Apply recomputes and compares it before
writing. A graph fingerprint remains useful for display and canvas
synchronisation but is not the sole concurrency token.

### Provider trust and data egress are explicit

A syntactically valid provider URL is not automatically trusted. Provider
configuration must identify an explicit trust and egress policy.

- Non-loopback provider endpoints require HTTPS.
- The UI and status surfaces show the effective endpoint and trust class
  without displaying credentials.
- Schema-only inspection is the default.
- Raw rows, sensitive configuration, executable source, and project knowledge
  with restricted classifications require separately permitted egress and,
  where applicable, user consent.
- Credentials are scoped to the configured provider and are never included in
  model-visible context or agent-facing command output.
- Endpoint or policy changes invalidate cached consent and capability state.

An OpenAI-compatible protocol describes wire behaviour; it does not establish
that the endpoint is an approved recipient of project data.

### Read-only assistance is independent of mutation readiness

Library capability queries and documentation lookup are always available when
the installation itself can serve them. Saved-state graph, config, and schema
inspection may operate on dirty, detached, divergent, or otherwise
mutation-disabled projects.

Every result identifies the saved project revision it describes. When the
browser has unsaved canvas state, the assistant must say that its result covers
saved state. It may inspect unsaved state only when the UI deliberately
provides a typed snapshot with separate provenance; it must never imply that a
backend read saw browser-local changes.

Submodel navigation does not disable read-only help. Reads declare their graph
scope and follow the submodel capability supported by the installed library.
Mutation remains subject to the normal graph-scope and revision rules.

### One application layer supports multiple adapters

The shared typed Python application-service layer is canonical. The in-app
assistant calls it directly rather than spawning CLI subprocesses. External
agents use the machine-readable CLI or another protocol adapter over the same
services.

Adapters own transport, serialization, authentication, and presentation only.
They do not reimplement node semantics, recipes, validation, mutation, or
revision handling.

### Pricing review is proportional to semantic content

Executable examples and evaluation tasks that encode actuarial or commercial
pricing choices require pricing-domain review. This includes exposure
treatment, rating defaults, premium composition, model validation, optimisation
objectives or constraints, fairness claims, and governance conclusions.

Purely mechanical fixtures, such as basic graph wiring or output-path syntax,
require normal engineering review. Every example, regardless of reviewer, must
still pass its executable assertions and packaging checks.

## Remaining design questions

- What exact grouped CLI command names and help hierarchy provide the clearest
  agent and human experience while retaining the compatibility decision above?
- What is the smallest useful first public schema for tracked project knowledge,
  and how should organization-specific extensions compose with it without
  weakening validation or provenance?
