# Build and distribution roadmap

## Scope

Package metadata, the embedded frontend build, and the published MkDocs
documentation site. Current behaviour is specified in
[the build-and-distribution specification](../build-and-distribution/high-level.md).
This package comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUILD-R01 | Planned | P2 | The published node reference documents the node types that exist, and a check keeps it that way. |

## Planned improvements

### BUILD-R01 — The node reference documents the canonical nodes
**Why:** The published documentation site still has "Data Source" and "Data
Sink" pages in its navigation, describing `sourceType: "flat_file"` configs
for node types that the canonical-only code has removed and now rejects.
There is no Data Input or Data Output page; only the index and the optimiser
page mention Data Input. About 5,000 lines of tests keep the internal
specification corpus consistent, but nothing checks the user-facing node
reference against the node vocabulary.

**Plan:** Replace the two pages with Data Input and Data Output pages that
describe the current provider and destination unions. Add a documentation
test that every authorable `NodeType` has exactly one node-reference page in
the navigation and that no page documents a removed type or config key. Once
`PCFG-R07` gives each node type a config model, generate the configuration
tables from the models.

**Acceptance:** The navigation lists one page per authorable node type and
none for a removed type; the documentation test fails if a node type is
added without a page or a removed type is documented; `mkdocs build --strict`
passes.

**Dependencies:** `PCFG-R07` (pipeline config) for generated tables; the page
rewrite does not wait for it.

**Evidence:** `docs/building-models/nodes/data-source.md`;
`docs/building-models/nodes/data-sink.md`; `docs/building-models/nodes/index.md`;
`mkdocs.yml`; `src/haute/_types.py::NodeType`; `tests/test_docs_accuracy.py`.
