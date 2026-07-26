# Reference Pipeline — High-Level Specification

## Purpose

`rating/` is a checked-in, non-runnable layout/example snapshot of a Haute
project. It preserves a small pipeline graph, its generated pipeline/submodel
Python, selected JSON sidecars, a quote-input schema, response mappings, model
artifacts, and user utility helpers. It is useful for manual inspection of how
a project directory can be laid out around Haute; it is not an executable
compatibility fixture.

The repository-root `haute.toml` selects `rating/main.py` through
`[project].pipeline`, making this snapshot the repository's authoritative
pipeline for commands that resolve the project default.

It is not the Haute package's runtime implementation or a guaranteed runnable
demonstration from a fresh clone. In particular, it is missing
`rating/data/quotes/nest_example.json`, the quote JSON input referenced by the
generated main pipeline, and `rating/config/expander/premium.json`, the
scenario-expander sidecar named by the generated submodel; neither is part of
the tracked reference tree.

## Scope

In scope:

- The tracked files under `rating/`: pipeline metadata/generated code, quote
  sidecars, submodel/user helpers, and the checked-in model artifacts.
- The relationship between these generated declarations and Haute's pipeline,
  JSON input/output, and model-file interfaces.
- The reference project's observable failure modes when a referenced local
  resource or model/runtime dependency is absent.

Out of scope:

- The reusable rating/banding transform engine and its sidecar normalisation,
  owned by [rating](../rating/high-level.md).
- The generic graph/config parser, persistence, and code-generation behaviour,
  owned by [pipeline-config](../pipeline-config/high-level.md) and
  [codegen](../codegen/high-level.md).
- Production distribution and the quality policy for excluded non-product
  directories, owned by [build-and-distribution](../build-and-distribution/high-level.md)
  and [engineering-quality](../engineering-quality/high-level.md).
- Untracked/ignored files that may exist under `rating/` locally, including
  generated output, data, caches, or Python bytecode.

## Behaviour

- The main reference graph creates a `haute.Pipeline` named `my_pipeline` with
  one API-input node (`quotes`) and one output node (`Quote_Response_9`), then
  connects the four emitted input ports to the corresponding output function
  parameters.
- The API-input decorator retains a relative `config=` reference that resolves to
  `rating/config/quote_input/quotes.json`, plus `contract="opaque"`, so the parser can load and
  cross-check the sidecar. Its body delegates to `resolve_api_input_from_config()` with the
  generated script directory as its guarded base. The helper validates the v2 `tables` shape,
  resolves the sidecar's data path, and returns the emitted lazy-frame table mapping.
  The sidecar declares four emitted tables: quotes, drivers, vehicles, and
  licenses.
- The output node returns the `quotes` frame. Its response sidecar maps selected
  columns from those four input ports back into nested JSON locations.
- The submodel reference declares Polars transforms and a scenario-expander
  node. Its utility helpers provide Polars date/interval, postcode, column-name,
  and column-selection expressions for project-authored pipeline code.
- The checked-in `.rsglm` and model artifact files are project data, not Python
  modules. They can be referenced by project/model workflows but do not cause a
  model to train, score, or deploy merely by being present in the repository.

## Design rationale

- Keeping generated graph Python next to JSON sidecars makes the relationship
  between code, graph layout, API schema, and output mapping inspectable without
  making the example part of the installed `haute` package.
- API-input sidecars preserve the schema/multi-table mapping separately from
  the executable pipeline code, allowing the UI/project tooling to retain user
  configuration while generated code concentrates on loading and wiring.
- User utility functions remain ordinary project Python so they are explicit,
  reviewable, and reusable by project-authored transforms rather than hidden in
  a serialized pipeline setting.
- This reference is deliberately narrow: it demonstrates file layout and
  generated constructs, not a maintained end-to-end rating product fixture or
  executable compatibility contract. Its missing quote input and expander
  sidecar mean it must not be advertised as a self-contained fresh-clone smoke
  test.

## Interactions

- [pipeline-config](../pipeline-config/high-level.md) supplies the decorators,
  project-relative configuration resolution, graph connection semantics, and
  parser/codegen contracts consumed by `rating/main.py` and its submodel.
- [json-shredding](../json-shredding/high-level.md) supplies the v2 schema
  validation and multi-table JSON loader used by the reference API-input node.
- [io-layer](../io-layer/high-level.md) and [modelling](../modelling/high-level.md)
  are consumers/contexts for the reference's JSON and model artifacts; the
  reference itself does not reimplement either subsystem.
- [engineering-quality](../engineering-quality/high-level.md) explicitly treats
  `rating/` as outside the normal Ruff target, so this example is not a claim of
  lint-clean packaged source.

## Failure model

- The reference API-input function raises `FileNotFoundError` when
  `rating/data/quotes/nest_example.json` is absent, before it can return a
  frame. The generated submodel's `rating/config/expander/premium.json` is
  likewise not a tracked reference artifact; no behaviour for a missing or
  locally supplied version is promised here.
- A sidecar without a valid `tables` list fails through the shared API-input configuration
  error contract; malformed v2 table configuration propagates rather than being guessed or
  repaired.
- The generated output function assumes the named four input parameters and
  returns only `quotes`; a graph/port/signature mismatch is left to the Haute
  pipeline/runtime validation rather than hidden by a local fallback.
- Utility helpers propagate Polars expression/schema errors. Model artifacts
  are opaque files here; an absent, incompatible, or unsupported artifact fails
  in whichever model loader consumes it, not inside this reference metadata.
