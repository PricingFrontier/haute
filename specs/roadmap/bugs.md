# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. `BUG-24` was found on 5 October 2026 while checking the deployment guides
against the CI/CD files `haute init` generates: as generated, those files fail before a
first container release succeeds. The guides give the manual step that works today, and
drop it when the package lands. `BUG-42` comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md): the storage binding's
unknown keys.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-24 | Decision | P1 | Container targets log in to their registry before `haute deploy` pushes, and GitLab container deploys run where Docker is available. |
| BUG-42 | Decision | P3 | The storage binding record either rejects unknown keys or is named as the exception to the canonical-only policy. |

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
