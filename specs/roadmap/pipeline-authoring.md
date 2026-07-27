# Pipeline authoring roadmap

## Scope

Owns the decorator DSL, parser/submodel structure, graph round trips, generated
code, standalone `Pipeline.run()`/`score()` semantics, registry wiring, and
persisted authored configuration. Current behaviour is specified in
[pipeline config](../pipeline-config/high-level.md),
[code generation](../codegen/high-level.md), and
[submodels](../submodels/high-level.md).

The `AUD-C05`, `AUD-PIPE-01`, and `AUD-C01` packages are delivered. Parser
structure conservation, explicit public `run()`/`score()` semantics, and
standalone/executor equivalence are present-tense contracts in the
specifications above, enforced by ordinary regressions including
`tests/test_parser_conservation.py`, `tests/test_pipeline.py`, and
`tests/test_codegen_execution_equivalence.py`, so they no longer appear as
roadmap work.

## Priorities

There are no active pipeline authoring improvement packages.

## Planned improvements

There are no queued pipeline authoring improvements. New work must enter this
catalogue as a concrete package with evidence reproduced against `HEAD`.
