# Build and Distribution — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Declares Hatchling, PEP 621 package metadata, Python/dependency compatibility, the `haute` console script, type classifier, build artifacts, custom hook, sdist exclusions, and wheel package selection. |
| `README.md` | Supplies the PEP 621 long description named by `pyproject.toml` and is the repository's public introduction. |
| `LICENSE` | Supplies the checked-in GNU AGPL-3.0 license text corresponding to the `AGPL-3.0-only` package metadata. |
| `.python-version` | Pins the checkout's selected Python interpreter version for version-manager tooling. |
| `.gitattributes` | Enforces LF checkout normalisation, including byte-sensitive JSON test fixtures, so generated and golden-file inputs are stable across platforms. |
| `uv.lock` | Locks the repository's Python dependency resolution used by the build, docs, and quality workflows. |
| `hatch_build.py` | Defines `FrontendBuildHook`: validates or explicitly rebuilds embedded frontend assets, detects stale inputs, resolves npm, and turns failed build prerequisites/commands into `RuntimeError`. |
| `src/haute/__init__.py` | Defines the installed package's public import surface, which is the package root selected for wheel distribution. |
| `src/haute/py.typed` | PEP 561 marker declaring that the installed `haute` package ships type information. |
| `frontend/package.json` | Declares the private frontend's pinned Node/npm engines, locked-toolchain commands, production build (`tsc -b && vite build`), and build-time dependencies. |
| `frontend/package-lock.json` | Pins the npm dependency graph consumed by `npm ci` for reproducible frontend builds. |
| `frontend/bun.lock` | Checked-in Bun lockfile for the frontend; current package-build and CI commands use npm and `frontend/package-lock.json`, not this lockfile. |
| `frontend/.npmrc` | Supplies npm configuration used when installing the frontend dependency graph. |
| `frontend/README.md` | Documents the frontend project for repository contributors. |
| `frontend/index.html` | Vite HTML entry document for the browser bundle. |
| `frontend/public/favicon.svg` | Public favicon copied through the Vite build. |
| `frontend/public/vite.svg` | Checked-in Vite public asset copied through the Vite build. |
| `frontend/vite.config.ts` | Reads the package version from `pyproject.toml`, defines `__APP_VERSION__`, and configures React/Tailwind plugins, a strict `127.0.0.1:5173` development listener, API/WebSocket proxies, chunking, and output to `src/haute/static/`. |
| `docs/overrides/home.html` | Supplies the public documentation landing-page override and is a documentation-build input. |
| `docs/stylesheets/extra.css` | Supplies public documentation styling and is a documentation-build input. |
| `docs/CI_MIRROR.md`, `docs/COMMIT_STANDARDS.md`, `docs/PERFORMANCE_CHECKS.md`, `docs/opus-5-*.md` | Internal engineering procedures and review corpus: retained as workflow inputs in the repository but excluded from public-site output. |
| `frontend/tsconfig.json` | References the application and Vite-node TypeScript projects. |
| `frontend/tsconfig.app.json` | Sets strict browser-source TypeScript compilation and build-info placement. |
| `frontend/tsconfig.node.json` | Sets strict TypeScript compilation for `frontend/vite.config.ts`. |
| `mkdocs.yml` | Configures the Material/MkDocs public site, navigation, strict-build plugins, and exclusions for internal specs/roadmaps/reviews/TRIP material. |
| `.github/workflows/docs.yml` | Builds public docs strictly and deploys the resulting `site/` artifact to GitHub Pages after `main` pushes affecting `docs/**` or `mkdocs.yml`, or on manual dispatch. |

`src/haute/static/` is a generated build output, not a tracked source module.
`pyproject.toml` includes it as a Hatch artifact; `frontend/vite.config.ts` clears
and recreates it for every production build. It must therefore be treated as a
package input validated by `hatch_build.py`, not hand-edited source.

## Key types and data structures

- **`FrontendBuildHook(BuildHookInterface)`** has `PLUGIN_NAME = "frontend-build"`
  and receives Hatch's root/version/build data. Its only build-state input is
  `HAUTE_BUILD_FRONTEND`.
- **Frontend-build mode** is a Boolean encoded by strings: `1`, `true`, `yes`,
  and `on` opt in; empty, `0`, `false`, `no`, and `off` select validation. Any
  other value is invalid.
- **Static artifact set** is the Vite output rooted at
  `src/haute/static/`, with both `index.html` and a non-empty `assets/` directory
  required for readiness and `index.html` as the freshness sentinel.
  The hook checks frontend `src/` TypeScript/TSX/CSS/HTML files and
  `vite.config.ts`, `tsconfig.json`, `tsconfig.app.json`, `package.json`, and
  `package-lock.json`
  against that sentinel's modification time.
- **Frontend TypeScript projects** are strict, no-emit build projects: the app
  project targets browser ES2022/React JSX and the node project targets ES2023
  for Vite configuration. The root TypeScript config references both.
- **Browser package version** is the required `project.version` value read from
  root `pyproject.toml` by Vite and exposed as the compile-time
  `__APP_VERSION__` string.
- **MkDocs site inputs** are public Markdown, `docs/overrides/home.html`,
  `docs/stylesheets/extra.css`, and `mkdocs.yml`; `exclude_docs` prevents internal
  specs, roadmaps, the three engineering procedure documents, the complete Opus
  review/workstream corpus, and TRIP material from becoming pages.

## Control flow

1. `uv build` invokes Hatchling according to `pyproject.toml`; non-editable
   target initialisation invokes `FrontendBuildHook.initialize()` in
   `hatch_build.py`.
2. The hook parses `HAUTE_BUILD_FRONTEND` first, then returns immediately for
   editable builds. Otherwise it finds
   `frontend/` and `src/haute/static/index.html`; if `frontend/` is absent it
   also returns, which accommodates source-distribution/CI contexts without
   frontend source.
3. Validation mode checks that the complete static artifact set exists and is
   not older than the selected frontend source/configuration. Build mode always
   runs `npm ci --prefer-offline`, then skips the production build if the
   artifact set is current or invokes `npm run build` when it is stale.
4. `frontend/package.json` runs `tsc -b` before Vite. `frontend/vite.config.ts`
   reads the package version, supplies React/Tailwind and development proxies, clears
   `src/haute/static/`, then emits the HTML/public assets and JavaScript/CSS
   chunks there.
5. Hatch packages `src/haute` into the wheel and includes static artifacts;
   the sdist exclusions in `pyproject.toml` remove source-only trees. The
   command entry point resolves `haute.cli:cli` after installation.
6. Separately, `.github/workflows/docs.yml` runs `uv sync --group dev --locked`
   and `uv run mkdocs build --strict`, uploads `site/`, then lets the dependent
   deploy job publish it to GitHub Pages.

## Edge cases and invariants

- The stale check intentionally covers only `frontend/src/` files with the
  TypeScript/TSX/CSS/HTML extensions plus the named configs; a change outside
  that set is not by itself a stale signal in `hatch_build.py`.
- `frontend/vite.config.ts` uses `emptyOutDir: true`, so old emitted chunks do
  not survive a successful frontend build.
- React/ReactFlow, ELK, CodeMirror, and Lucide dependencies are assigned
  explicit vendor chunks; other dependencies retain Vite/Rollup's default
  chunking behaviour.
- The static marker is intentionally absent from source control in this
  checkout. A validated wheel build must create or receive it; source code is
  never used as a runtime fallback for a missing browser bundle.
- The MkDocs exclusion is publishing policy, not repository access control:
  excluded files continue to exist in the checkout and can be read by
  maintainers.

## Error handling

- `FrontendBuildHook._should_build_frontend()` raises `RuntimeError` for an
  invalid environment value.
- `_validate_static_assets()` raises `RuntimeError` for a missing or stale
  sentinel, naming either the frontend rebuild command or the environment opt-in.
- `_npm()` raises `RuntimeError` if npm is not on PATH and the known Windows
  location is unavailable. `_run()` uses replacement decoding, applies the
  package-build timeout, prints subprocess stdout/stderr, and raises
  `RuntimeError` on a non-zero return code or timeout; the missing-output sanity
  check does the same.
- TypeScript/Vite failures return non-zero through the npm command and therefore
  surface as the hook's `RuntimeError`; they are not converted into a partial
  static bundle.
- A missing root `pyproject.toml` package-version declaration raises from the
  Vite configuration before bundling; no fallback version is embedded.
- MkDocs strict-mode errors make the docs workflow's build job fail. The deploy
  job is gated by `needs: build`, so it is not attempted after such an error.

## Testing

- `tests/test_hatch_build.py` covers editable-build behavior, environment-value
  validation ordering, missing/incomplete static artifacts, unconditional
  locked installation for explicit builds, lockfile freshness, safe subprocess
  decoding and timeout translation, and post-build readiness.
- Package smoke jobs exercise the real sdist/wheel and clean-install paths;
  focused hook tests keep failure branches deterministic without invoking npm.
- `tests/test_docs_accuracy.py` is a repository documentation consistency gate;
  it is not a substitute for MkDocs's strict render/build validation.
- Package/install smoke coverage is defined and run by
  [engineering-quality](../engineering-quality/low-level.md#testing), notably
  `scripts/package_smoke_check.py`, `scripts/init_smoke.py`, and CI's
  package/init-smoke jobs. The frontend's build/typecheck commands are likewise
  quality gates, not independent package-format tests.
