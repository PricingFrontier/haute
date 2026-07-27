# Build and Distribution — High-Level Specification

## Purpose

Haute is delivered as a typed Python package with a command-line entry point and,
where required by the server, a compiled browser client embedded in the package.
This component defines the reproducible boundary between the source checkout and
the installable wheel/source distribution, so a user does not need a frontend
toolchain to run an installed release.

It also owns publication of the public documentation site. Internal engineering
specifications remain in the repository for maintainers but are deliberately not
part of that published site.

## Scope

In scope:

- Python package metadata, dependency ranges, extras, console entry point, typed
  package marker, and Hatch build targets.
- The opt-in frontend build hook, its freshness validation, and the policy for
  `src/haute/static/` assets embedded in distributions.
- The Vite production build inputs, output location, lockfile, public assets,
  and TypeScript project configuration needed to create that embedded client.
- MkDocs configuration and the GitHub Pages workflow that validates and publishes
  the public documentation site.

Out of scope:

- Runtime HTTP routing and serving the embedded assets, which is owned by
  [server-api](../server-api/high-level.md).
- Browser UI behaviour and its source implementation, owned by the relevant
  `frontend-*` component specifications.
- Test, lint, type-check, release-smoke, and CI policy, which is owned by
  [engineering-quality](../engineering-quality/high-level.md).

## Behaviour

- The project builds with Hatchling and distributes the `haute` package for
  Python 3.11 and later. Installed users receive the `haute` command and typing
  metadata.
- Non-editable package builds made from a checkout that contains `frontend/`
  require a complete static dependency graph and an exact content proof for the
  current production inputs. The proof covers package-version metadata, the
  frontend HTML/public/source trees, npm metadata, Vite configuration, and every
  referenced TypeScript project, so additions, edits, renames, and deletions all
  invalidate it. Contributor documentation, tests, test support, and lint,
  Vitest, and Playwright configuration are not production inputs. When
  `frontend/` itself is absent, the hook returns without validating the static
  bundle.
- Setting `HAUTE_BUILD_FRONTEND` to a recognised true value explicitly permits
  the build hook to run `npm ci --prefer-offline` on every explicit build and
  then run the frontend production build when assets are stale. Recognised false
  values select validation only. Invalid values fail before any editable-build
  or missing-frontend early return.
- The frontend build type-checks first, then Vite replaces
  `src/haute/static/` with a fresh bundle and machine-readable dependency
  manifest; it uses stable vendor chunks. The build hook records the exact
  production-input proof beside that output. Vite reads the package version
  from `pyproject.toml` and defines `__APP_VERSION__` for browser surfaces that
  render it. A missing package version fails the build instead of substituting
  a stale or synthetic value.
- The source distribution intentionally excludes frontend source, documentation,
  tests and local/project artefacts, while the wheel includes the package and
  Hatch build artifacts. The generated static files are an explicit Hatch
  artifact so the wheel carries the browser client.
- A push to `main` that changes any `docs/**` path or `mkdocs.yml` builds MkDocs
  in strict mode and deploys the resulting `site/` artifact to GitHub Pages.
  `CI_MIRROR.md`, `COMMIT_STANDARDS.md`, and `PERFORMANCE_CHECKS.md` are
  excluded from the public site, but changes to those paths still trigger this
  workflow. Component specs and engineering roadmaps live in
  root-level `specs/`, outside the site source tree, so changes there neither
  publish nor trigger a docs deployment. A newer docs run queues behind an
  active Pages deployment instead of cancelling it.

## Design rationale

- Embedding already-built static assets makes normal installation independent of
  Node.js, while the explicit `HAUTE_BUILD_FRONTEND=1` escape hatch keeps release
  builds able to refresh them in a controlled, reproducible way. When frontend
  source is available, automatically rebuilding on every package build was
  rejected in favour of a loud stale-asset failure: an accidental Node/npm
  difference should not silently change a wheel.
- A canonical content inventory is used instead of modification times because
  the package contract must detect deleted and renamed inputs as well as edited
  files. Validation and the explicit-build skip path consume the same proof so
  they cannot disagree about freshness.
- `npm ci` and the checked-in `frontend/package-lock.json` make frontend
  dependency installation deterministic. The frontend is private because it is
  a package-build input, not an independently published npm library.
- Package workflows and frontend metadata pin the supported Node/npm toolchain.
  CI has one authoritative package-build path so it cannot compare two wheels
  produced by different implicit toolchains.
- The browser bundle is emitted into the Python package instead of being fetched
  from a CDN, preserving offline/self-hosted deployment and keeping server and
  client versions together.
- MkDocs strict mode prevents documentation delivery from masking broken links,
  navigation, or configuration. Internal engineering records are useful in the
  checkout but are intentionally excluded from a user-facing documentation site.

## Interactions

- [server-api](../server-api/high-level.md) serves the packaged static client at
  runtime and therefore consumes the artifact this component produces.
- [frontend-shared](../frontend-shared/high-level.md) and the other frontend
  components supply the TypeScript/CSS source consumed by Vite.
- [engineering-quality](../engineering-quality/high-level.md) verifies package
  builds and clean-install smoke paths in CI; it does not define package content.
- Public guides under `docs/` are the source input for this component's MkDocs
  publishing path; internal component specs are intentionally not published.

## Failure model

- An unrecognised `HAUTE_BUILD_FRONTEND` value raises `RuntimeError`; callers
  must choose an explicit true or false value.
- A non-editable build with `frontend/` present raises `RuntimeError` when the
  input proof is absent, malformed, or mismatched, or when the output graph is
  incomplete. Readiness requires a regular `index.html`, a non-empty Vite
  manifest with an entry chunk, every manifest file/CSS/asset and
  imported/dynamic chunk, and every local HTML script or link reference to
  resolve to a regular file inside the static root. An unrelated file in
  `assets/` is never sufficient evidence. The hook never substitutes a stale
  bundle.
- If `frontend/` is absent, the hook skips both building and validation. This
  source-distribution/CI accommodation also means that such a build context can
  bypass the missing-static-asset failure; callers must ensure the static bundle
  is already included.
- If Node/npm cannot be found, dependency installation fails, Vite fails, the
  dependency manifest is malformed/dangling/escaping, an input cannot be read,
  or the input proof cannot be written, the build hook raises `RuntimeError`.
  Subprocess output is forwarded to stderr with replacement decoding for
  malformed bytes, and every build subprocess has a finite timeout whose expiry
  is reported as a `RuntimeError`.
- Editable builds skip the hook's static-asset validation. This does not promise
  that an editable development environment can serve a production client.
- A strict MkDocs build failure prevents the documentation artifact from being
  uploaded or deployed. GitHub Pages deployment only runs after that build job
  succeeds.
