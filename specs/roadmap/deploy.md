# Deploy roadmap

## Scope

Resolving, validating, bundling and serving a pricing pipeline as a scoring
API. Current behaviour is specified in
[the deploy specification](../deploy/high-level.md). This package comes from
the follow-ups of the 24 September 2026 round that labelled three container
platforms "build and push only".

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DEP-R05 | Planned | P3 | A deploy to a build-and-push-only platform succeeds once the image is pushed, and says the service update is manual. |

## Planned improvements

### DEP-R05 — Build-and-push-only targets finish their deploy
**Why:** Azure Container Apps, AWS ECS and GCP Cloud Run are labelled "build
and push only" in the generated `haute.toml`, `.env.example`, CI files and the
`--target` help. `haute deploy` for them builds and pushes the image, then
`_update_service` raises `NotImplementedError`, so the command exits with an
error on every run and the generated CI deploy job for those targets always
fails, although it did everything the label promises.

**Plan:** Make a build-and-push-only target complete once the image is
pushed: `deploy_to_platform_container` returns the deploy result with the
image tag and no endpoint, and the command reports that the service was not
updated and names the image to point it at. Delete `_update_service` and its
placeholder error until a platform's service update is built. Update the
deploy and CLI specifications and the generated notice text first.

**Acceptance:** `haute deploy` for each of the three targets exits 0 after a
successful push and prints the manual-update instruction with the image tag;
a failed build or push still fails the command; the generated CI job for each
target passes a smoke run against a local registry, or the existing container
smoke test covers the same path.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::deploy_to_platform_container`;
`src/haute/deploy/_container.py::_update_service`;
`src/haute/_scaffold.py::_build_only_notice`;
`src/haute/_scaffold.py::BUILD_AND_PUSH_ONLY_TARGETS`;
`specs/deploy/high-level.md`.
