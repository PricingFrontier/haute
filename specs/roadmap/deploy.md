# Deploy roadmap

## Scope

Resolving, validating, bundling and serving a pricing pipeline as a scoring
API. Current behaviour is specified in
[the deploy specification](../deploy/high-level.md). These packages come
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DEP-R01 | Planned | P1 | A deployed pipeline carries the project modules its preamble imports, or deploy refuses it. |
| DEP-R02 | Planned | P2 | The scoring image installs only what scoring needs. |
| DEP-R03 | Decision | P3 | Scaffolding and CI templates offer only deploy targets that work end to end. |
| DEP-R04 | Planned | P3 | Deploy reads git through the git component's one subprocess chokepoint. |

## Planned improvements

`DEP-R01` goes first: it is the only package here where validation passes and
the deployed service then fails.

### DEP-R01 — A deployed pipeline carries its project modules
**Why:** `haute init` writes `utility/__init__.py` and `utility/features.py`
("These are imported into main.py"), and the editor manages utility modules.
The container build copies only `deploy_manifest.json`, `app.py` and
`artifacts/`, and the Databricks pyfunc path passes no code paths. The scorer
compiles the preamble when it builds the scoring plan, so a pipeline whose
preamble imports `utility` fails with `ModuleNotFoundError` in the served
image. Deploy validation and golden test-quote scoring run in the project
directory, where `utility` is importable, so they pass. The deploy
specification records the limitation, but nothing enforces it.

**Plan:** First, make validation honest: run the resolve and golden-quote
dry-run with only the bundle on the import path, or detect project-local
preamble imports and refuse them with a message naming the module. Then
bundle the imported project packages into both targets (a `COPY` into the
image and MLflow `code_paths`) and specify which project modules deploy
ships.

**Acceptance:** A pipeline whose preamble imports `utility` either deploys
and scores in the built container and in the pyfunc model, or is refused at
`validate_deploy` with a message naming the module. A regression test
builds a bundle in a clean directory and scores through it. The deploy
specification states what is bundled.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_dockerfile`;
`src/haute/deploy/_container.py::prepare_build_directory`;
`src/haute/deploy/_mlflow.py`; `src/haute/deploy/_scorer.py`;
`src/haute/deploy/_validators.py::validate_deploy`;
`src/haute/_scaffold.py::starter_utility_features`;
`src/haute/cli/_init_cmd.py`.

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

### DEP-R04 — Git through the chokepoint
**Why:** `_git_sha_short` runs `git rev-parse` through `subprocess` directly,
although the git component's command core is meant to own every git
subprocess.

**Plan:** Call the git command core instead.

**Acceptance:** No module outside the git command core starts a git
subprocess; a repository-hygiene test enforces it.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_git_sha_short`;
`src/haute/_git_core.py::_run_git`.
