# IO07 — OUTPUT's generated code is a passthrough: the saved file doesn't do what the canvas does

**Severity: MEDIUM-HIGH (consistency of the core product story) · Effort: S-M · Review mode: pair**

## Evidence

`_gen_output` (`src/haute/_codegen_builders.py:1038-1055`) emits:

```python
@pipeline.output(config="config/quote_response/<name>.json")
def result(df: pl.LazyFrame) -> pl.LazyFrame:
    """..."""
    return df          # ← passthrough; the comment says "the runtime assembles the
                       #    response document from the mapping, not from the body"
```

But "the runtime" here means **only the graph executor** (`_build_output`,
`src/haute/_builders.py:774-826`, → `assemble_output_from_mapping`). The standalone execution
path — `Pipeline.run()` / `Pipeline.score()` on the saved file (`src/haute/pipeline.py:486-561`)
— calls the generated function body, gets the passthrough, and returns the **raw upstream
frame**. Deploy is unaffected (it scores through `execute_lazy_graph`,
`deploy/_scorer.py:849`), so this bites exactly the audience the README courts: someone
running the Python file directly to verify what their pipeline produces.

Every other config-folder node keeps the two paths identical by calling a shared
`*_from_config` helper in the generated body — banding
(`apply_banding_from_config`), rating step, scenario expander, optimiser apply, model score —
with comments like *"shared … so the canvas and the saved file cannot drift"*
(`_builders.py:850-853`). OUTPUT is the one node type that got the comment's opposite.

## Why it matters

- "The visual editor and the code are always the same thing" (README) is false for the node
  that defines the pipeline's *result*.
- `Pipeline.run()`'s own semantics (`_resolve_output_node`, `pipeline.py:422-455`) make the
  OUTPUT node's return value THE pipeline result — and that value differs between canvas and
  file.
- Anyone writing tests against the saved file (the workflow the README encourages) asserts on
  the wrong shape.

## Fix design

Add the missing shared helper and emit it, following the banding pattern exactly:

```python
# haute/_node_apply.py (or graph_utils re-export, matching siblings)
def assemble_output_from_config(config_path, base_dir, /, **frames):  # or (df, config_path, base_dir)
    config = _resolve_config(config_path, base_dir)     # load_node_config
    mapping = config["outputMapping"]
    return pl.LazyFrame(assemble_output_from_mapping(frames_by_port, mapping),
                        infer_schema_length=None)        # IO03's fix applies here too
```

Generated body becomes `return assemble_output_from_config({params...}, config=..., base_dir=base)`.
Port naming: single-input OUTPUT can pass the lone frame under the mapping's referenced port
(mirroring `_build_output`'s single-frame resolution, `_builders.py:812-816`); multi-input
OUTPUT passes `{param_name: frame}` — the generated parameter names ARE the sanitized source
ports, which is the same alignment `_build_output` relies on via `ctx.source_ports`.

Then `_build_output` and the generated body share one assembly entry point — the drift becomes
structurally impossible, which is this codebase's own stated standard.

## TDD plan (failing tests first)

- Round-trip e2e (extend `tests/test_e2e.py::test_full_lifecycle` family): build a graph with
  an OUTPUT mapping, save, import the generated module, `pipeline.run()` → assert the result
  equals the canvas executor's OUTPUT frame (document shape, not raw upstream columns). This
  test fails today.
- Multi-frame variant: two inputs into OUTPUT, mapping referencing both ports → standalone run
  assembles the same document the dry-run route returns.
- Guard: `Pipeline.score()` seeding still works (OUTPUT is not a source; no arity change).

## Cross-refs

- Build on IO03 first (the helper must construct the frame with `infer_schema_length=None` /
  explicit schema, or the standalone path inherits the silent field drop).
- IO02's note applies: this is the same "shared helper from config" consolidation, output-side.
