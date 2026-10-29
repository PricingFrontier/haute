# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. `BUG-24` was found on 5 October 2026 while checking the deployment guides
against the CI/CD files `haute init` generates: as generated, those files fail before a
first container release succeeds. The guides give the manual step that works today, and
drop it when the package lands.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-24 | Decision | P1 | Container targets log in to their registry before `haute deploy` pushes, and GitLab container deploys run where Docker is available. |

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
