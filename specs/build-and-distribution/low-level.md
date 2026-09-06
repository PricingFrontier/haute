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
| `hatch_build.py` | Defines `FrontendBuildHook`: inventories and fingerprints production inputs, validates the generated input/output manifests and every local output dependency, explicitly rebuilds embedded frontend assets, resolves npm, and turns failed prerequisites/commands into runtime errors. |
| `src/haute/__init__.py` | Defines the installed package's public import surface, which is the package root selected for wheel distribution. |
| `src/haute/py.typed` | PEP 561 marker declaring that the installed `haute` package ships type information. |
| `frontend/package.json` | Declares the private frontend's pinned Node/npm engines, locked-toolchain commands, production build (`tsc -b && vite build`), explicit contract generation/check commands, and build-time dependencies. Ajv, json-schema-to-typescript, and esbuild are exact-pinned development generators, not production runtime dependencies. |
| `frontend/package-lock.json` | Pins the sole supported frontend dependency graph consumed by `npm ci` for reproducible frontend builds. Secondary package-manager lockfiles are unsupported and must not be checked in because CI cannot verify their parity. |
| `frontend/.npmrc` | Supplies npm configuration used when installing the frontend dependency graph. |
| `frontend/README.md` | Documents the frontend project for repository contributors. |
| `frontend/index.html` | Vite HTML entry document for the browser bundle. |
| `frontend/public/favicon.svg` | Public favicon copied through the Vite build. |
| `frontend/public/vite.svg` | Checked-in Vite public asset copied through the Vite build. |
| `frontend/vite.config.ts` | Reads the package version from `pyproject.toml`, defines `__APP_VERSION__`, and configures React/Tailwind plugins, a strict `127.0.0.1:5173` development listener, API/WebSocket proxies, chunking, a Rollup manifest, and output to `src/haute/static/`. |
| `docs/overrides/home.html` | Supplies the public documentation landing-page override and is a documentation-build input. |
| `docs/stylesheets/extra.css` | Supplies public documentation styling and is a documentation-build input. |
| `docs/CI_MIRROR.md`, `docs/COMMIT_STANDARDS.md`, `docs/PERFORMANCE_CHECKS.md`, `docs/ENGINEERING_QUALITY_AUDIT_2026_08.md`, `docs/ENGINEERING_QUALITY_AUDIT_2026_08_COVERAGE.toml` | Internal engineering procedures and dated audit records: retained in the repository but excluded from public-site output. |
| `frontend/tsconfig.json` | References the application and Vite-node TypeScript projects. |
| `frontend/tsconfig.app.json` | Sets strict browser-source TypeScript compilation and build-info placement. |
| `frontend/tsconfig.node.json` | Sets strict TypeScript compilation for `frontend/vite.config.ts`. |
| `mkdocs.yml` | Configures the Material/MkDocs public site, navigation, strict-build plugins, and exclusions for the internal engineering reference documents and dated audit records that remain under `docs/`. |
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
- **Production-input manifest** is `src/haute/static/haute-build-inputs.json`.
  It records schema version, SHA-256 algorithm, a digest, and a sorted row for
  every input containing its project-relative path, byte size, and content
  digest. The closed inventory contains root `pyproject.toml`;
  `frontend/.npmrc`, `index.html`, package/lock metadata and Vite config; every
  recursively referenced TypeScript config; all regular files below
  `frontend/public/`; and production files below `frontend/src/`. Test files,
  test-only directories, test support, and the vitest setup files
  (`setupTests.ts`, `setupStorageCanary.ts`) are excluded.
- **Static output graph** is the Vite output rooted at
  `src/haute/static/`: regular `index.html`, non-empty
  `src/haute/static/manifest.json` with at
  least one entry, every declared file/CSS/asset and import/dynamic-import key,
  and every local script `src` or link `href` parsed from the entry document.
  All resolved paths must remain inside the static root and exist as regular files;
  JavaScript and CSS referenced by HTML must be declared by the Vite manifest,
  and the HTML must reference a manifest entry chunk.
- **Frontend TypeScript projects** are strict, no-emit build projects: the app
  project targets browser ES2022/React JSX and the node project targets ES2023
  for Vite configuration. The root TypeScript config references both.
- **Browser package version** is the required `project.version` value read from
  root `pyproject.toml` by Vite and exposed as the compile-time
  `__APP_VERSION__` string.
- **Python dependency boundary:** MLflow is a core dependency because model
  scoring, registry browsing, and modelling export are first-class palette
  features. MLflow brings `databricks-sdk` transitively; the `databricks`
  extra pins the supported SDK range and adds the SQL connector needed for
  workspace data access and deployment.
- **MkDocs site inputs** are public Markdown, `docs/overrides/home.html`,
  `docs/stylesheets/extra.css`, and `mkdocs.yml`; `exclude_docs` prevents the
  internal engineering documents and dated audit records from entering the
  site output. Component specs and roadmaps live outside the MkDocs source
  tree.

## Control flow

1. `uv build` invokes Hatchling according to `pyproject.toml`; non-editable
   target initialisation invokes `FrontendBuildHook.initialize()` in
   `hatch_build.py`.
2. The hook parses `HAUTE_BUILD_FRONTEND` first, then returns immediately for
   editable builds. Otherwise it finds
   `frontend/` and `src/haute/static/index.html`; if `frontend/` is absent it
   also returns, which accommodates source-distribution/CI contexts without
   frontend source.
3. Validation mode validates the complete output graph, rebuilds the canonical
   production-input manifest in memory, and requires exact equality with the
   recorded proof. Build mode always runs `npm ci --prefer-offline`, then skips
   the production build only if both those checks pass; otherwise it invokes
   `npm run build`.
4. Contract generation is an explicit contributor/preflight path, not an
   implicit Hatch, Vite, or browser-runtime side effect. Preflight requires the
   committed Pydantic-derived JSON Schema, TypeScript declarations/constants,
   and standalone validators to be current. The production-input manifest
   naturally fingerprints those committed frontend source files, so packaging
   consumes exactly the reviewed artifacts and detects later source drift.
   `frontend/package.json` runs `tsc -b` before Vite. `frontend/vite.config.ts`
   reads the package version, supplies React/Tailwind and development proxies,
   clears `src/haute/static/`, then emits the HTML/public assets,
   JavaScript/CSS chunks, and `src/haute/static/manifest.json` there. The hook captures the input
   manifest immediately before Vite and requires the same bytes afterwards, so
   a concurrent source edit invalidates rather than blessing the generated
   output. It then atomically writes `haute-build-inputs.json` and revalidates
   both proofs before allowing packaging to continue.
5. Hatch packages `src/haute` into the wheel and explicitly includes both
   generated static artifacts and `src/haute/assistant/assets/**`; the latter
   declaration is required because the teaching bundles and authoring guide
   are non-Python package resources. The sdist exclusions in `pyproject.toml`
   remove source-only trees. The
   console command entry point resolves `haute.cli:cli` after installation;
   Python's `-m haute` package entry point imports and invokes that same group.
6. Separately, `.github/workflows/docs.yml` runs `uv sync --group dev --locked`
   and `uv run mkdocs build --strict`, uploads `site/`, then lets the dependent
   deploy job publish it to GitHub Pages.

## Edge cases and invariants

- The input inventory is path- and content-sensitive, so additions, removals,
  renames, byte changes, and referenced-TypeScript-project changes invalidate
  it. Source tests and contributor/test-tool configuration remain outside that
  inventory by design.
- `frontend/vite.config.ts` uses `emptyOutDir: true`, so old emitted chunks do
  not survive a successful frontend build.
- Vite-manifest imports and dynamic imports use manifest keys, not output paths.
  The validator requires every named key to exist and validates every manifest
  entry's own output file, so the complete reachable and declared graph is
  checked without accepting an orphaned reference.
- ECharts/zrender, React/ReactFlow, ELK, CodeMirror, and Lucide dependencies are
  assigned explicit vendor chunks; other dependencies retain Vite/Rollup's default
  chunking behaviour.
- The generated `src/haute/static/` tree is untracked in this checkout
  (`.gitignore`). A validated wheel build must create or receive it; source code
  is never used as a runtime fallback for a missing browser bundle.
- The MkDocs exclusion is publishing policy, not repository access control:
  excluded files continue to exist in the checkout and can be read by
  maintainers.

## Error handling

- `FrontendBuildHook._should_build_frontend()` raises `RuntimeError` for an
  invalid environment value.
- `_validate_static_assets()` raises `RuntimeError` for a missing, unreadable,
  malformed, escaping, dangling, or incomplete output graph and for an absent,
  malformed, unreadable, or mismatched input manifest. It names the explicit
  rebuild opt-in where rebuilding can repair the state.
- `_npm()` raises `RuntimeError` if npm is not on PATH and the known Windows
  location is unavailable. `_run()` supplies an environment from
  `FrontendBuildHook._node_env()` (prepending the known Windows Node.js directory
  when `node` is not on PATH), uses replacement decoding, applies the
  package-build timeout, prints subprocess stdout/stderr, and raises
  `RuntimeError` on a non-zero return code or timeout; the missing-output sanity
  check does the same.
- TypeScript/Vite failures return non-zero through the npm command and therefore
  surface as the hook's `RuntimeError`; they are not converted into a partial
  static bundle.
- Input-manifest publication uses a sibling temporary file followed by replace;
  an operating-system failure removes the temporary file and raises rather than
  leaving a proof that could be mistaken for a successful build.
- Production inputs changing while Vite runs invalidate that build before an
  input proof is published; the caller must rerun against a stable checkout.
- A missing root `pyproject.toml` package-version declaration raises from the
  Vite configuration before bundling; no fallback version is embedded.
- MkDocs strict-mode errors make the docs workflow's build job fail. The deploy
  job is gated by `needs: build`, so it is not attempted after such an error.

## Testing

- `tests/test_dependency_contracts.py` — dependency version floors for Polars ordered joins, ratebook factor contexts, and required build/runtime assumptions.
- `tests/test_optional_dependency_extras.py` — core MLflow plus Databricks-extra
  import smoke checks (skipped when the Databricks extra is absent).
- `tests/test_optional_dependency_matrix.py` — core-install import/route smoke
  checks with core MLflow present and the optional Databricks SQL connector
  absent. MLflow itself brings `databricks-sdk` transitively.

- `tests/test_hatch_build.py` covers editable-build behavior, environment-value
  validation ordering, the complete included/excluded input matrix,
  additions/deletions/content changes, recursive TypeScript references,
  absent/corrupt/mismatched input manifests, direct and transitive/dynamic
  missing output assets, manifest-key/path/schema failures, path escapes,
  validation mode, explicit-build skip/rebuild behavior, unconditional locked
  installation, safe subprocess decoding and timeout translation, the atomic
  input-manifest publication failure branch
  (`tests/test_hatch_build.py::test_atomic_proof_publication_failure`: a failed
  temporary-file replace raises `RuntimeError` naming the destination, leaves no
  temporary file and leaves an existing manifest unchanged), navigation-link
  exclusion, and coherent post-build readiness.
- Package smoke jobs exercise the real sdist/wheel and clean-install paths;
  focused hook tests keep failure branches deterministic without invoking npm.
- `tests/test_docs_accuracy.py` is a repository documentation consistency gate;
  it is not a substitute for MkDocs's strict render/build validation.
- Package/install smoke coverage is defined and run by
  [engineering-quality](../engineering-quality/low-level.md#testing), notably
  `scripts/package_smoke_check.py`, `scripts/init_smoke.py`, and CI's
  package/init-smoke jobs. The frontend's build/typecheck commands are likewise
  quality gates, not independent package-format tests.
- `tests/test_cli.py` — CLI regression coverage invokes the package through a
  real `python -m haute` subprocess and verifies success, nested-command argument
  routing, Click usage failures, and exit-code parity with the console group.
- `scripts/package_smoke_check.py` also calls the installed
  `haute.assistant._assets.validate_example_bundles(execute_fast=True)` entry
  point. It uses only installed resources, validates closed manifests and
  content hashes, parses every valid bundle, and executes the `fast` tier's
  production graph, trace, and no-write dry-run checks when declared.
- `scripts/update_assistant_example_manifests.py` is the source-authoring
  boundary for bundle inventories. Its default check mode fails on stale
  digests or inventory drift; `--write` is the only supported digest refresh
  and still rejects unsafe, duplicate, missing, or undeclared resources.
