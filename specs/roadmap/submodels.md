# Submodels roadmap

## Scope

Submodel definitions and occurrences, and the other mechanisms for reusing
logic in a pipeline. Current behaviour is specified in
[the submodels specification](../submodels/high-level.md). `SUB-R01`
comes from the [23 September 2026 codebase review](codebase-review-2026-09-23.md);
`SUB-R02` from the user's direction on 4 October 2026 that a submodel should
work like a Python module.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-R02 | Planned | P2 | A pipeline imports each submodel module and registers the imported definition, so Python resolves submodels as it resolves any module. |
| SUB-R01 | Planned | P3 | One mechanism reuses a node or group of nodes. |

## Planned improvements

`SUB-R02` changes how an occurrence is registered and `SUB-R01` how a node
instance becomes an occurrence, so whichever lands second follows the other's
registration form. `SUB-R01` is best taken before
`PCFG-R08` and `PCFG-R07` (pipeline config), so the editor-state move and the
typed config models never have to carry node-level instances.

### SUB-R01 — One reuse mechanism
**Why:** Four mechanisms overlap. Node-level instances (`@pipeline.instance`,
`instanceOf` with `inputMapping`) reuse one node's logic. Submodel
definitions with instance occurrences reuse a group of nodes, with read-only
copies and port minting that suffixes `_2` and `_3` on collision. Plain
Polars transforms carry `inputMapping` to keep logical input names across
rewires. Project `utility` modules reuse functions. Together they account
for 258 `instanceOf`/`instance_of` references across the backend and
frontend. Files named after submodels hold 6,300 source lines and 11,700 test
lines, and 25 of the 48 submodel commits since May 2026 were fixes or
hardening of identity and aliasing.

**Decided (24 September 2026):** the review's recommendation. A node
instance is a one-node submodel occurrence, so node-level instances go as a
separate mechanism. Submodel definitions with their occurrences and project
`utility` modules are the reuse mechanisms. `inputMapping` on a plain Polars
transform stays: it names a node's inputs across rewires and reuses no logic.
The capability users have today stays: **Create Instance** on a Polars or
Model Score node wraps the node in a one-node submodel definition (its own
`modules/<name>.py`) and places a second occurrence, so editing the
definition still updates every copy.

**Plan:** Specify the kept mechanisms, the one-node submodel form of an
instance and the targeted rejection of `@pipeline.instance` and `instanceOf`
(the canonical-input rule) in the submodels and pipeline-config
specifications. First prove that a one-node submodel carries every current
instance use (Polars and Model Score, with remapped inputs) through preview,
trace, training, codegen round-trip and deploy; if one cannot, stop and bring
the gap back as a decision. Then make **Create Instance** produce the
submodel form, remove the node-instance parser, codegen, executor, flattening
and editor paths, and rewrite `docs/building-models/nodes/instances.md` as
"reusing a node" through submodels.

**Acceptance:** The submodels specification names the supported reuse
mechanisms; `@pipeline.instance` and `instanceOf` are rejected with a message
naming the submodel form; no parser, codegen, executor or editor path handles
node-level instances; **Create Instance** yields a working one-node submodel
occurrence for Polars and Model Score nodes; the documentation describes only
supported mechanisms.

**Dependencies:** None. The canonical-input rule that applies to removed forms
is in the [specification README](../README.md#canonical-only-format-policy).

**Evidence:** `src/haute/pipeline.py::instance`;
`src/haute/_submodel_instances.py`; `src/haute/_flatten.py`;
`docs/building-models/nodes/instances.md`;
`docs/building-models/nodes/submodel.md`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`.

### SUB-R02 — Submodels registered by import
**Why:** A submodel's nodes live in their own file, but the pipeline file only
names that file as a string, so Python never imports it:

```python
# main.py today
pipeline.submodel("modules/vehicle_factors.py", "vehicle_factors")
pipeline.connect("policies", "vehicle_factors", target_port="vehicles")
pipeline.connect("vehicle_factors", "premium", source_port="factored")

# modules/vehicle_factors.py
submodel = haute.Submodel(
    "vehicle_factors",
    definition_id="definition_vehicle_factors",
    input_ports=[{"name": "vehicles", "targets": [{"nodeId": "vehicle_age"}]}],
    output_ports=[{"name": "factored", "source": {"nodeId": "vehicle_factor"}}],
    pipeline_dir="..",
)
```

The editor, `haute run` and the parser read both files as text and splice
the submodel's nodes into the pipeline (`flatten_graph`). Because nothing
imports the module, a standalone run had no way to reach it, so commit
`732cfcc2` (branch `bug-fixes`) made `Pipeline.run()` and `score()`
import each registered file by path with `importlib` at run time
(`Pipeline._with_submodels_expanded`, `_load_submodel_definition`), a
workaround layered on path registration. Editor go-to-definition, ruff and
type checkers also cannot follow a path string. The intended design, decided
on 4 October 2026, is that a submodel works like a Python module: its
functions live in a file the pipeline imports.

**Decided (4 October 2026):** the registration changes; the definition files
and their declared ports stay. A submodel definition is still a
`haute.Submodel` with `input_ports` and `output_ports` and decorated node
functions, and occurrences still connect by port. The pipeline file imports
the module and registers the imported object:

```python
# main.py
from modules import vehicle_factors

pipeline.submodel(vehicle_factors.submodel, "vehicle_factors")
pipeline.submodel(vehicle_factors.submodel, "trailer_factors", instance_of="vehicle_factors")
pipeline.connect("policies", "vehicle_factors", target_port="vehicles")
```

Redesigning a submodel as a Python function of its inputs (ports as
parameters and return value) was considered and not chosen. Under the
repository's canonical-only policy the path-string form is refused with a
message naming the import form, not kept beside it; projects have no external
users to migrate, but the editor's Recover flow should rewrite an old file.

**Plan:** Specify the form first in the submodels and pipeline-config
specifications, then:

1. Settle the open questions in the specification: the import spelling codegen
   writes (`from modules import vehicle_factors` is the proposal) and whether
   `modules/` needs an `__init__.py`; whether the occurrence name stays a
   required argument; whether `pipeline_dir=".."` stays on the constructor or
   is derived from the importing pipeline (it anchors the definition's
   `config=` paths); how a module that defines several `Submodel` objects is
   addressed (the attribute names the definition, so it may be allowed).
2. Live API: `Pipeline.submodel(definition: Submodel, name, *, instance_of=None)`
   takes the object. `RegisteredSubmodel` keeps the definition object (and
   its file, from the module's `__file__`, for the editor). A standalone run
   expands occurrences from the registered objects, so `_load_submodel_definition`
   and its `importlib` call go; `_with_submodels_expanded`, the boundary
   wiring and `_in_parameter_order` stay.
3. Parser: resolve `from modules import x` plus `pipeline.submodel(x.submodel, ...)`
   statically to the file `modules/x.py` without executing it, through
   `resolve_submodel_reference` so containment is unchanged; refuse the
   path-string form and an import that does not resolve to a project file.
   Registration extraction lives in
   `src/haute/_parser_submodels.py::extract_submodel_registrations`; the
   recovery loader reads registrations through `_source_references` in
   `src/haute/_pipeline_recovery.py`.
4. Codegen: `graph_to_code_multi` writes the import lines (they are generated,
   so the preamble boundary in `_ast_helpers._module_boundaries` and
   `unkept_module_statements` must recognise them, or they become preamble or
   an `unkept_module_statement` diagnostic) and the object registrations
   (`src/haute/codegen.py`, the `registrations` built near
   `"pipeline.submodel"`).
5. Executors: the editor, `haute run` and the deploy scorer keep flattening
   the parsed graph; the import only changes how the parser finds the file.
   Make sure the import resolves when the executor compiles the preamble and
   node code (`src/haute/executor.py::_compile_preamble` already puts the
   pipeline directory on `sys.path` for `utility` imports).
6. Deploy: bundle `modules/` as an importable package beside the pipeline, as
   `utility` is bundled (`src/haute/deploy/_project_modules.py`,
   `src/haute/deploy/_bundler.py`). Coordinate with `BUG-18`, which makes
   deploy flatten submodels at all.
7. Editor: Create Submodel, Dissolve, rename and delete keep the import line
   and the registration in step (the save path in
   `src/haute/routes/_save_pipeline.py`, `_derive_definition_file_lifecycle`);
   Recover rewrites a path-string registration to the import form.
8. Update `docs/building-models/nodes/submodel.md` and the generated-file
   examples in the specifications.

**Acceptance:** A generated pipeline with a submodel imports its module and
registers the imported definition; `python main.py`-style imports, ruff and
go-to-definition resolve it; `pipeline.run(source=...)` and `score()` run it
without importing by path; parse, preview, trace, `haute run` and a deployed
scorer give the same frame as today's tests in
`tests/test_pipeline.py::TestStandaloneSubmodels` and
`tests/test_cli.py::TestRun::test_run_executes_a_submodel_in_place_of_its_occurrence`;
a path-string registration is refused naming the import form and Recover
rewrites it; codegen round-trips the import form byte-for-byte.

**Dependencies:** The standalone expansion from commit `732cfcc2` (branch
`bug-fixes`) is what this keeps. `BUG-18` makes deploy flatten submodels; step 6 builds
on it.

**Evidence:** `src/haute/pipeline.py::Pipeline.submodel`,
`src/haute/pipeline.py::RegisteredSubmodel`,
`src/haute/pipeline.py::Pipeline._with_submodels_expanded`,
`src/haute/pipeline.py::_load_submodel_definition`;
`src/haute/_parser_submodels.py::extract_submodel_registrations`;
`src/haute/_submodel_paths.py::resolve_submodel_reference`;
`src/haute/codegen.py` (`graph_to_code_multi`, `_render_module`);
`src/haute/_flatten.py::flatten_graph`;
`src/haute/routes/_save_pipeline.py`; `specs/submodels/high-level.md`;
`docs/building-models/nodes/submodel.md`.

