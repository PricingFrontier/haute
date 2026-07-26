# WS-14 — CLI, deploy & distribution

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: unassigned · Status: not started.

**Branch:** `opus5/ws-14-cli-deploy-distribution`

## Mission

Everything outside the running GUI: the CLI commands, the deploy path (bundling, container,
MLflow registration, impact/smoke), and packaging/docs publication. These three share
`haute.toml`, `DeployConfig` and the build hook, and they are the surface where a silent
error reaches production — a rolled-back model version, a stale bundled snapshot, or an
internal doc published to the public site.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| cli | 0 | 2 | 3 | 7 |
| deploy | 0 | 3 | 6 | 9 |
| build-and-distribution | 0 | 0 | 3 | 8 |
| **Total** | **0** | **5** | **12** | **24** |

## Priorities

**P1 — data loss and wrong-deploy (review Waves 1–2):**

- `cli-2` (H): `haute init` unconditionally deletes the user's root `main.py` with no backup,
  prompt, or summary line — and root `main.py` is Tier-2 of pipeline resolution, so this
  destroys the canonical pipeline entry point. Delete only a byte-matching `uv init` stub, or
  refuse and say so; spec it and test that a non-stub file survives.
- `deploy-2` (H): Databricks deploy serves model version `"1"` when the registry returns no
  versions — silently rolling a production endpoint back to an old model and reporting
  success. Raise instead of synthesising a version.
- `deploy-1` (H): snapshot Data Inputs are bundled via `open_generation()` with no lease, so
  a concurrent refresh can rmtree the generation mid-deploy (bare `FileNotFoundError`, or a
  partially-logged MLflow model). Hold `SourceCacheStore.lease(identity)` across
  resolve→bundle→ship and add the concurrency test the 0.7.0 contract already names.
- `cli-1` (H): the documented `[server].host` key in `haute.toml` makes `deploy`, `smoke`,
  `impact` and `status` hard-fail with an uncaught `ValueError` — two mutually incompatible
  readers of one file. Add `"server": {"host"}` to `_VALID_TOML_SCHEMA` and test both readers
  against one file.

**P2 — silent failures on the deploy path:** `deploy-4` (NDJSON `/quote` failures returned as
HTTP 200 with a silently truncated body and no logging), `deploy-5` (MLflow signature
coerces every unmapped Polars dtype to string), `deploy-12` (impact report crashes with a
Polars `ShapeError` when both endpoints drop the same number of rows), `deploy-8` (validation
check 5 discards the underlying exception entirely — undiagnosable failures), `deploy-11`
(`load_context` silently drops manifest artefacts missing from `context.artifacts`),
`cli-3` (`_smoke_databricks` swallows every exception and retries for 30 minutes),
`cli-4` (`--endpoint-suffix` inert on container/HTTP targets while the output claims
otherwise), `cli-10` (post-training formatting failure reported as "Training failed" after
the model was saved).

**P2 — publication and build:** `build-and-distribution-3` (M): internal engineering docs —
`CI_MIRROR`, `COMMIT_STANDARDS`, `PERFORMANCE_CHECKS`, **and now this review plus the
workstream files** — are published to the public MkDocs site, which excludes only
`specs/ roadmap/ trip/`. Do this early. Also `build-and-distribution-4` (frontend build
reuses an existing `node_modules` instead of `npm ci`, defeating lockfile determinism),
`-1` (build gate accepts a partial bundle the server refuses to serve), `-7` (docs workflow
cancels in-progress Pages deployments), `-8` (no Node/npm pin; CI builds the wheel two ways),
`-11` (build-hook subprocess decodes strictly with no timeout).

**P3 — spec truth:** the removed Databricks stub gate vs the shipped snapshot-readiness gate
(`contracts-b-6`, `deploy-7`), fold shipped deploy contracts (`contracts-b-11`,
`contracts-b-8`, `deploy-6`), Interactions claims about `read_data_source`
(`contracts-b-14`, `seam-io-11`), manifest/provenance and `_intercept` drift (`deploy-13`,
`deploy-9`, `deploy-10`), stale cross-references and the missing `tests/test_impact.py`
(`deploy-14`), CLI precedence and hygiene (`cli-5`, `cli-8`, `cli-9`, `cli-11`, `cli-13`,
`cli-7`, `contracts-d-9`), and the distribution module-map/config drift
(`build-and-distribution-12`, `-13`, `-5`, `-6`, `-10`).

## Finding inventory

High (5): `cli-1`, `cli-2`, `contracts-b-6`, `deploy-1`, `deploy-2`.
Medium (12): `cli-3`, `cli-4`, `cli-5`, `contracts-b-8`, `contracts-b-11`, `deploy-4`,
`deploy-5`, `deploy-7`, `deploy-12`, `build-and-distribution-3`, `build-and-distribution-4`,
`build-and-distribution-12`.
Low (24): `cli-7`, `cli-8`, `cli-9`, `cli-10`, `cli-11`, `cli-13`, `contracts-d-9`,
`contracts-b-14`, `deploy-6`, `deploy-8`, `deploy-9`, `deploy-10`, `deploy-11`, `deploy-13`,
`deploy-14`, `seam-io-11`, `build-and-distribution-1`, `build-and-distribution-5`,
`build-and-distribution-6`, `build-and-distribution-7`, `build-and-distribution-8`,
`build-and-distribution-10`, `build-and-distribution-11`, `build-and-distribution-13`.

## File ownership (exclusive)

- `src/haute/cli/**` (`_init_cmd.py`, `_serve.py`, `_deploy.py`, `_smoke.py`, `_impact.py`,
  `_status.py`, `_train.py`, `_run.py`, `_lint.py`, `_helpers.py`)
- `src/haute/deploy/**` (`_config.py`, `_bundler.py`, `_container.py`, `_mlflow.py`,
  `_scorer.py`, `_validators.py`, `_impact.py`, `_schema.py`, `_model_code.py`, `_utils.py`)
- `hatch_build.py`, `mkdocs.yml`, `.github/workflows/docs.yml`, `frontend/vite.config.ts`
  version define, `docs/` publication config
- `docs/specs/cli/**`, `docs/specs/deploy/**`, `docs/specs/build-and-distribution/**`
- Their tests (`tests/test_cli_init.py`, `test_host_binding.py`, `test_impact.py`,
  deploy/container/mlflow suites)

## Cross-stream touchpoints

- `deploy/_config.py` is this stream's file but WS-06's `pipeline-config-1` wants
  `DeployConfig.from_toml` to delegate to `_project.resolve_pipeline_file`. Two distinct
  hunks (`[server]` schema here, resolver delegation there) — agree the order, and prefer
  landing the schema fix first since it unblocks four commands.
- `deploy-1` consumes WS-02's source-cache lease API — coordinate the "lease across the whole
  deploy lifecycle" pattern rather than inventing a second one.
- `.github/workflows/**` and `scripts/**` are shared with WS-01 (mutation/lint gates). This
  stream owns `docs.yml` and the packaging jobs; WS-01 owns `mutation.yml` and the dependency
  audit script.
- `_model_scorer.py` / `_feature_contract.py` are WS-07's — deploy reads them; don't change
  the contract format here.
- `build-and-distribution-3` protects the whole review corpus from publication — land it
  before anyone links these documents externally.

## Definition of done

- `haute init` cannot destroy a user's pipeline; `[server].host` works across all commands;
  no deploy can synthesise a model version or ship an unleased snapshot — each with a
  regression test (including the concurrency test named in the 0.7.0 contract).
- Internal engineering docs and the review corpus are excluded from the published site.
- Deploy failure paths carry their cause (logging/`from exc`) instead of discarding it.
- CLI/deploy/distribution contracts folded and deleted; module maps include the MkDocs inputs.
- Baseline entries for the three components deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_cli_init.py tests/test_host_binding.py tests/test_impact.py -q`
- Deploy bundler/container/MLflow suites; `uv run mkdocs build --strict` and confirm the
  excluded pages are absent from `site/`.
- `uv run pytest tests/test_docs_accuracy.py -q`; full preflight before hand-off (this stream
  touches packaging).
