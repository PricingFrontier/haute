# Assistant roadmap

## Scope

The assistant's capability catalogue, teaching examples and packaging.
Current behaviour is specified in
[the assistant specification](../assistant/high-level.md). This package comes
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ASSIST-R01 | Planned | P3 | One capability catalogue and one example format; evaluation harnesses leave the runtime package. |

## Planned improvements

### ASSIST-R01 — One catalogue, one example format
**Why:** The assistant keeps a legacy node catalogue beside the capability
manifest that its own comment calls authoritative, and serves a "legacy
node-catalogue compatibility view" from `list_node_types`. It loads legacy
single-file examples beside the manifest-backed example bundles. The
self-test harness (795 lines) and the provider-qualification evaluation (796
lines) ship inside the runtime package.

**Plan:** Serve node descriptions from the capability manifest only, convert
the single-file examples into bundles, and move the self-test and evaluation
harnesses to `scripts/` or a development-only package.

**Acceptance:** One catalogue and one example format remain; the installed
package contains no evaluation or self-test harness; the assistant tool and
asset tests pass.

**Dependencies:** `PCFG-R06` (pipeline config) states the legacy-handling
rule this applies.

**Evidence:** `src/haute/assistant/_catalog.py::NODE_CATALOG`;
`src/haute/assistant/_catalog.py::capability_manifest`;
`src/haute/assistant/_tools.py::list_node_types`;
`src/haute/assistant/_assets.py::_example_resources`;
`src/haute/assistant/_self_test.py`; `src/haute/assistant/_evaluation.py`.
