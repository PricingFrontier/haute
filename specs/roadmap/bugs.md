# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. `BUG-18` was found on 4 October 2026 while making standalone runs
execute submodels: `haute run` then ran a pipeline without its submodels, and
deploy still parses the same way.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-18 | Decision | P1 | A deployed pipeline runs each submodel, and an unflattened submodel placeholder never reaches an executor silently. |

## Planned improvements

### BUG-18 — A deployed pipeline runs its submodels
**Why:** `parse_pipeline_file` keeps submodel occurrences as placeholder nodes
unless it is asked to flatten them, and the executor's builder for a
placeholder (`_build_submodel`) passes its input through. The editor's routes
flatten every graph they execute, and `haute run` now does too, but deploy
parses the pipeline without flattening (`deploy/_config.py`), prunes and
validates that graph, and scores it with the placeholder still in place. A
deployed pipeline with a submodel therefore appears to skip it: the node after
the occurrence receives the frame that went into it, and the price is wrong
with no error when the columns happen to line up. `haute run` showed the same
failure on 4 October 2026 (the example project
`tests/assistant_eval/projects/submodel_pricing` failed with "unable to find
column vehicle_factor") before it was made to flatten; deploy's behaviour is
from reading the code and has no reproduction yet.

**Plan:** Decide whether deploy flattens right after parsing or later (the
pruner reads `graph.submodels`, and bundled artifact keys are built from node
ids, which flattening turns into `submodel_runtime/<occurrence>/<node>`). Then
flatten for deploy, and make the placeholder builders refuse to build, naming
the occurrence, so an unflattened graph fails instead of passing data through;
check first that no other caller of `parse_pipeline_file` (`_scaffold.py`,
`_pipeline_repair.py`, `routes/_helpers.py`) reaches a builder with one.

**Acceptance:** A deploy scorer test serves a pipeline whose output sits behind
a submodel and returns the submodel's columns; executing a graph that still
holds a placeholder raises an error naming the occurrence.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_config.py` (`parse_pipeline_file`);
`src/haute/deploy/_pruner.py::prune_for_deploy`;
`src/haute/_builders.py::_build_submodel`; `src/haute/parser.py::parse_pipeline_file`
(`flatten=False` by default); `src/haute/cli/_run.py`.
