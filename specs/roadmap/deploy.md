# Deploy roadmap

## Scope

Resolving, validating, bundling and serving a pricing pipeline as a scoring
API. Current behaviour is specified in
[the deploy specification](../deploy/high-level.md). These packages come
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DEP-R02 | Planned | P2 | The scoring image installs only what scoring needs. |
| DEP-R03 | Decision | P3 | Scaffolding and CI templates offer only deploy targets that work end to end. |

## Planned improvements

### DEP-R02 — A slim scoring runtime
**Why:** The generated image installs the haute wheel, and haute's core
dependencies include the assistant's `anthropic` and `openai` SDKs, `optuna`,
`mlflow`, `scipy`, `pandas`, `libcst`, `watchfiles` and the editor's server
stack. Every pricing API image carries them, which enlarges the image, its
build time and its vulnerability surface.

**Plan:** Split the distribution into a scoring runtime (execution, the model
flavours actually bundled, the scoring app) and extras for the editor, the
assistant, training and optimisation. The Dockerfile installs the runtime
plus the extras the bundle's artifacts require.

**Acceptance:** The container image installs no assistant, tuning or editor
dependency; the container smoke test passes; the package metadata declares
the extras and the build-and-distribution specification describes them.

**Dependencies:** The package metadata owned by build-and-distribution.

**Evidence:** `pyproject.toml`;
`src/haute/deploy/_container.py::_pinned_core_dockerfile_deps`;
`src/haute/deploy/_container.py::_detect_extra_deps`;
`scripts/container_smoke.py`.

### DEP-R03 — Offer only targets that deploy
**Why:** `haute init` generates complete CI workflows for three providers
crossed with seven deploy targets. Azure Container Apps, AWS ECS and GCP
Cloud Run build and push an image and then raise `NotImplementedError` at the
service update; SageMaker and Azure ML are rejected outright. Their templates
and secrets are still generated and parsed under test.

**Plan:** Decide whether unimplemented targets stay visible. If not, remove
them from `haute init` and the CI templates until their adapters exist, and
keep them only as named future targets in the deploy specification.

**Acceptance:** Every target `haute init` offers deploys end to end, or is
labelled in the generated files and CLI help as build-only; the scaffold
tests cover only offered targets.

**Dependencies:** The scaffolding owned by pipeline-config.

**Evidence:** `src/haute/deploy/__init__.py::_validate_target`;
`src/haute/deploy/_container.py::_update_service`; `src/haute/_scaffold.py`;
`src/haute/cli/_init_cmd.py`.
