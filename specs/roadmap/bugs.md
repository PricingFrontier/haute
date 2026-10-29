# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. `BUG-24` was found on 5 October 2026 while checking the deployment guides
against the CI/CD files `haute init` generates: as generated, those files fail before a
first container release succeeds. The guides give the manual step that works today, and
drop it when the package lands. `BUG-40` and `BUG-42` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md): the hosted Databricks
app's boot and build script and the storage binding's unknown keys.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-24 | Decision | P1 | Container targets log in to their registry before `haute deploy` pushes, and GitLab container deploys run where Docker is available. |
| BUG-40 | Planned | P2 | The hosted Databricks app installs the wheel and its pinned dependencies from a lock at boot, and its build script takes the app name as a parameter. The build selects its current wheel rather than a stale lexicographic match. |
| BUG-42 | Decision | P3 | The storage binding record either rejects unknown keys or is named as the exception to the canonical-only policy. |
| BUG-46 | Planned | P2 | Rating a source column named value is either faithfully represented or refused before the editor overwrites its levels. |

## Planned improvements

### BUG-24 — Container deploys log in to their registry
**Why:** For the container targets, `haute deploy` pushes with a plain `docker push`
and the generated workflows pass `DOCKER_USERNAME`/`DOCKER_PASSWORD` (and the AWS or
Azure credentials) to the deploy steps, but nothing logs in with them, so a push to a
private registry fails. The generated GitLab jobs also run in `python:3.11`, which has
no Docker, so `haute deploy` stops with "Docker is not available".

**Plan:** Decide how each registry authenticates (a `docker login` with the DOCKER pair,
`aws ecr get-login-password` for ECR, a service principal for ACR) and whether Haute or
the generated workflow performs it; give GitLab container deploys a Docker-capable job.

**Acceptance:** A generated container workflow logs in before the deploy step for each
CI provider, and its GitLab deploy jobs can run Docker; the manual login steps in the
Docker, AWS ECS and Azure guides are removed.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_check_docker_available` and the push;
`src/haute/_scaffold.py::TARGETS` (secrets) and the workflow generators.

### BUG-40 — The hosted app boots from a lock
**Why:** Every boot of the hosted Databricks app force-reinstalls the vendored wheel and
then runs `pip install <wheel>[databricks]`, resolving Haute's dependency ranges from the
index at boot time, so two boots of one deployment can run different dependency versions
and a boot needs the index to be reachable. The committed `requirements.txt` names a wheel
version and digest that drift from the released version until the build script is run,
and `deploy.sh` hard-codes the app name `haute-spike` while taking the profile and the
workspace directory as parameters.

**Plan:** Use `DEP-R09`'s shared resolver to ship a lock for the hosted app's Linux
platform, Python version and `databricks` extra beside the wheel; preserve hashes and
credential-free index provenance. Verify the wheel digest and install it with
`--no-deps`, and install the locked runtime without resolving fresh dependencies at
boot. Generate `requirements.txt` in the build script only and ignore it in git; take
the app name as a parameter. This fixes version drift; offline boot additionally needs
the locked artifacts to be supplied locally and is not implied by a lock alone.

**Acceptance:** Two boots of one deployment install the lock's versions and nothing else;
the target installation passes `pip check`, including when the bundle was prepared
on Windows; `requirements.txt` is not committed; `deploy.sh` with another app name
deploys to it.

**Dependencies:** `DEP-R09` supplies target resolution and the Model Serving runtime
rules; share them without assuming the app and Model Serving need identical extras.

**Evidence:** `databricks_app/app.py::_refresh_vendored_wheel`;
`databricks_app/requirements.txt`; `databricks_app/scripts/deploy.sh`.

**28 October full-pass addition:** The unchanged bundle script also selects the
lexicographically last wheel in shared `dist/`, not necessarily the wheel just built. A
controlled real-shell run builds 0.10.0 beside 0.9.0 and bundles OLD RELEASE from 0.9.0.
**Plan/acceptance:** build into a fresh owned directory or consume the exact build output,
require one matching artifact, and derive the digest/requirements from it. Mixed stale versions
and rebuilding a lower checked-out version must both bundle the current build. Evidence:
`databricks_app/scripts/build_bundle.sh`, wheel selection statement `wheel="$(ls "$repo_root"/dist/haute-*.whl | sort | tail -n1)"` (builder output and missing local digest tool only
were substituted; no deployment ran).

### BUG-42 — Unknown keys in the storage binding record
**Why:** `StorageBinding.from_payload` deliberately ignores unknown keys so that an older
Haute can read a newer record, which the canonical-only format policy forbids in general
(an unknown key is rejected by name). Either the hosted-project-storage specification
names this record as the exception, or the loader rejects.

**Plan:** Decide; then record the exception in the specification or reject unknown keys
by name.

**Acceptance:** The specification and the loader agree, with a test for an unknown key.

**Dependencies:** None.

**Evidence:** `src/haute/_project_storage.py::StorageBinding`; `specs/README.md`;
`specs/hosted-project-storage/high-level.md`.

### BUG-46 — A rating factor cannot collide with its rate field

**Why:** The editor offers `value` as a factor. Its Cartesian-entry builder spreads the factor
values and then writes the rate into the same `value` key, losing the selected level. The
canonical backend codec also accepts this shape, but the real rating join selects `["value",
"value"]` and raises Polars `DuplicateError`. One flat key cannot carry both values.

**Plan:** Specify one consistent representation/validation rule across rating config, grids,
sidecars and runtime. Either separate factor-level storage from the rate field or explicitly
refuse this reserved factor name with actionable rename guidance before mutation. Do not
silently rename columns or coerce the lost level.

**Acceptance:** A source string column named `value` and two distinct levels cannot produce an
accepted ambiguous table. The chosen contract either rates both levels with distinct rates
through editor/save/run or refuses selection and imported config before overwriting data.
Ordinary factor names continue to round-trip.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/RatingStepEditor.tsx::RatingStepEditor`;
`frontend/src/panels/editors/rating/ratingTableUtils.ts::buildCartesianEntries`;
`src/haute/_rating_step_config.py::_validate_factors`; `src/haute/_rating.py::rating_table_lookup`.
