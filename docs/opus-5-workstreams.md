# Opus 5 review — parallel workstream split

Source: `docs/opus-5-review.md` (26-Jul-2026, 461 verified findings: 3 critical, 67 high,
200 medium, 191 low). This index partitions all 461 findings into 14 workstreams that can be
worked in **parallel git worktrees**. Each workstream has its own file
(`opus-5-ws-NN-*.md`) defining scope, file ownership, priorities and done-criteria.

The review remains the single source of truth for finding evidence and fix guidance — the
workstream files scope and sequence the work, they do not restate it.

> **Publication caveat:** `mkdocs.yml` currently excludes only `specs/`, `roadmap/`, `trip/`,
> so root-level `docs/*.md` — including the review and these files — are published to the
> public site (finding `build-and-distribution-3`). WS-14 should extend the exclusion list
> early; until then treat these documents as public.

## Partition principles

1. **File-disjoint ownership.** Each stream exclusively owns a set of source, test and
   `docs/specs/<component>/` paths. A stream never edits another stream's files; shared-file
   needs are declared as *touchpoints* and handled by the owning stream (or an explicitly
   coordinated hunk).
2. **Whole components.** Every finding travels with its component; the handful of
   cross-cutting findings are individually assigned (table below).
3. **Full-stack verticals** where the frontend and backend halves share a payload contract
   (tracing, git, assistant); **layer clusters** where files are hubs (data I/O, execution
   spine, frontend platform).
4. **Wave 0 as a ratchet, not a gate.** WS-01 extends `tests/test_docs_accuracy.py` with a
   committed per-file baseline of known violations so CI stays green; every other stream
   deletes its baseline entries as it reconciles its specs. No stream is blocked on another.

## Workstreams

| WS | File | Components | C | H | M | L | Total | Headline items |
|---|---|---|---:|---:|---:|---:|---:|---|
| 01 | `opus-5-ws-01-corpus-governance.md` | cross-cutting (docs infra), engineering-quality | 0 | 2 | 15 | 7 | 24 | docs-accuracy guard extension + ratchet, contract-retirement rule, roadmap cleanup, version stamps, mutation-gate fix |
| 02 | `opus-5-ws-02-data-io-caching.md` | io-layer, databricks-io, caching | 2 | 18 | 27 | 16 | 63 | databricks-io spec rewrite (both criticals), query-string credentials, cross-process cache destruction, sink-API drift |
| 03 | `opus-5-ws-03-execution-jobs-sandbox.md` | execution-engine, background-jobs, sandbox-security | 0 | 7 | 23 | 17 | 47 | `str.format` sandbox bypass, checkpoint traversal, worker-isolation queue hang, env-knob policy |
| 04 | `opus-5-ws-04-server-api-output.md` | server-api, json-shredding | 0 | 4 | 20 | 6 | 30 | blank-canvas parse swallow (Wave 1), supersession admission race, OUTPUT null-key regression, watcher fixes |
| 05 | `opus-5-ws-05-codegen-submodels.md` | codegen, submodels | 0 | 5 | 9 | 13 | 27 | preserve-block growth/relocation/loss (Wave 1), dissolve edge-drop + stale-mirror deletion (Wave 1) |
| 06 | `opus-5-ws-06-config-parsing-reference.md` | pipeline-config, expression-parsing, reference-pipeline | 0 | 6 | 12 | 18 | 36 | evaluator identity-fallback fabrication, aliased-import meta loss, three-resolver unification, zero-param instance hole |
| 07 | `opus-5-ws-07-modelling-registry.md` | modelling, mlflow-model-registry | 0 | 3 | 4 | 14 | 21 | cancelled training overwrites durable model (Wave 1), register_model wrong URI, `clear_model_cache(".")` deletes cache |
| 08 | `opus-5-ws-08-optimiser-explore.md` | optimiser, explore-eda | 0 | 1 | 12 | 12 | 25 | timed-out frontier mutates parent job, frontier single-flight race, explore cancel, god-module carve plan |
| 09 | `opus-5-ws-09-frontend-platform-canvas.md` | frontend-shared, frontend-graph-canvas | 1 | 6 | 15 | 14 | 36 | WebSocket `submodels` drop → save overwrites file (critical, Wave 1), unguarded client endpoints, polling consolidation |
| 10 | `opus-5-ws-10-frontend-editors-panels.md` | frontend-node-editors, frontend-modelling-optimiser-ui, frontend-preview-explore | 0 | 3 | 18 | 14 | 35 | null-handle panel crash, node-rename mapping loss, lost `banding_source` write, invisible Explore job |
| 11 | `opus-5-ws-11-rating-tracing.md` | rating, tracing, frontend-trace-ui | 0 | 3 | 18 | 18 | 39 | duplicate outputColumn silent overwrite, multi-frame trace crash, silent enrichment-failure rendering |
| 12 | `opus-5-ws-12-git.md` | git-integration, frontend-git-ui | 0 | 3 | 7 | 7 | 17 | stale-ref catch-up/spin-off (Wave 1-adjacent), unbounded push under mutation lock, dirty-canvas reload loss (Wave 1) |
| 13 | `opus-5-ws-13-assistant.md` | assistant, frontend-assistant-ui | 0 | 1 | 8 | 11 | 20 | orphaned tool call bricks session, turn-lock race, response-body leak, markdown CSS |
| 14 | `opus-5-ws-14-cli-deploy-distribution.md` | cli, deploy, build-and-distribution | 0 | 5 | 12 | 24 | 41 | `haute init` deletes `main.py` (Wave 1), deploy lease + version-"1" rollback (Wave 2), `[server]` TOML schema split, mkdocs exclusions |

Totals: 3 C, 67 H, 200 M, 191 L = **461**. Machine-checked: every finding ID in the review
appears in exactly one workstream's inventory, and each row's severity counts are recomputed
from the review rather than transcribed.

## The review's waves, mapped to streams

The review recommends an order that cuts across this partition. Streams work their own
priorities independently, but if you want the data-loss bugs cleared first, these are they.

**Wave 1 — bugs that lose user data**, in the review's order:

| Finding | Stream | What is lost |
|---|---|---|
| `frontend-graph-canvas-1` | WS-09 | externally-edited submodels reverted on next save |
| `cli-2` | WS-14 | the user's root `main.py` — the pipeline entry point |
| `codegen-1` / `-2` / `-3` | WS-05 | preserve blocks grow unbounded; saves permanently blocked; submodel code discarded |
| `failure-model-3` | WS-05 | dissolve silently drops edges and persists the result |
| `failure-model-2` | WS-04 | blank canvas on parse error, then overwrite on save |
| `frontend-git-ui-1` | WS-12 | unsaved canvas edits on branch archive/delete |
| `modelling-1` | WS-07 | the deployed model and feature contract, on cancel |
| `submodels-2` | WS-05 | the authoritative submodel file, to a stale client mirror |

**Wave 2 — security and concurrency:** `sandbox-security-1` and the checkpoint traversal
(`execution-engine-3`) in WS-03; the two credential-in-query-string findings (`io-layer-1`,
`seam-io-7`) and `io-layer-9` in WS-02; `server-api-1` and the unlocked `_ensure_module_deps`
twin (`over-complication-2`) in WS-04; `optimiser-2` in WS-08; `deploy-1` in WS-14.

Waves 3 (remaining silent fallbacks) and 4 (over-complication) are distributed across every
stream's P2/P3 sections rather than tracked centrally.

## Cross-cutting finding assignments

The review's `cross-cutting` component (15 findings) is distributed:

| Finding | Stream | Reason |
|---|---|---|
| `readme-coherence-1, -2, -4, -6, -9`, `testing-credibility-8, -9`, `readme-coherence-3` | WS-01 | README/TEMPLATE/ownership/roadmap files and CI rules |
| `contracts-a-11`, `seam-exec-1`, `seam-io-5` | WS-02 | io-layer/caching spec folds + `_source_cache.py` ownership decision |
| `failure-model-6` | WS-03 | env-knob policy lives in `_env.py` (optimiser call-site noted in WS-08) |
| `failure-model-2` | WS-04 | fix is in `routes/pipeline.py` (frontend-shared spec correction noted in WS-09) |
| `testing-credibility-7` | WS-11 | tracing Testing-section claims |
| `testing-credibility-11` | WS-06 | pipeline-config counts (modelling counts noted in WS-07) |

## Shared-file ownership map

When a fix in stream A wants to touch a file below, the listed owner makes (or approves) the
edit. Hunk-level coordination notes live in each stream file.

| File | Owner | Streams that touch it |
|---|---|---|
| `src/haute/_input_providers.py` | WS-02 | WS-14 (deploy lease usage) |
| `src/haute/execution.py`, `executor.py`, `projection.py` | WS-03 | WS-02 (stat-gate unification), WS-04 (json-shredding-4 in `projection.py`) |
| `src/haute/routes/pipeline.py`, `_save_pipeline.py`, `_supersession.py` | WS-04 | WS-05 (save/dissolve), WS-11 (trace route), WS-03 (admission) |
| `src/haute/parser.py`, `_submodel_paths.py` | WS-05 | WS-06 (ParseError wrapping at call sites) |
| `src/haute/_builders.py` | WS-06 (spec/ownership decision) | WS-04, WS-08 (reference only) |
| `src/haute/deploy/_config.py` | WS-14 | WS-06 (resolver delegation — distinct hunks, coordinate) |
| `src/haute/routes/_job_lifecycle.py`, `_job_store.py`, `_background_jobs.py` | WS-03 | WS-07 (publication CAS), WS-08 (frontier CAS) |
| `src/haute/_trace_enrichment.py` | WS-11 | — (rating-3 assigned to WS-11) |
| `frontend/src/api/client.ts`, `types/guards.ts` | WS-09 | WS-10 (new guards — request or coordinate), WS-12/13 (avoid) |
| `frontend/src/App.tsx`, `panels/NodePanel.tsx` | WS-10 | WS-09 (avoid; fixes live in hooks/stores) |
| `docs/specs/README.md`, `TEMPLATE.md`, `ownership.toml`, `docs/roadmap/**`, `tests/test_docs_accuracy.py` | WS-01 | All streams may **append** `[[shared_file]]` entries to `ownership.toml`; baseline deletions are per-stream |
| `src/haute/server.py` | WS-04 | WS-01 (the `version="0.1.0"` line only) |

## Sequencing and merge policy

- **Start first:** WS-01 (the ratchet baseline makes everyone else's spec state explicit).
  All other streams can start immediately — nothing hard-blocks on WS-01.
- **Within every stream:** Wave-1 data-loss items first, then Wave-2 security/races, then
  bug-category items, then the spec-truth pass (drift + contract folding + Testing sections),
  then consolidation. Each stream file lists its own P0/P1 explicitly.
- **Branch naming:** `opus5/ws-NN-<slug>`, one worktree per stream:
  `git worktree add ..\haute-wsNN -b opus5/ws-NN-<slug>`.
- **Merging:** no auto-merge. Each stream lands as its own PR for independent review.
  Suggested merge order: WS-01 first; code-fix-heavy streams (04, 05, 07, 09, 12, 14) as
  they complete; spec-sweep-heavy streams (02, 03) any time after their P0/P1 land. Merge
  conflicts should be rare by construction; `ownership.toml` and `docs/specs/README.md` are
  the only expected append-collision points.
- **Every stream follows `AGENTS.md`**: spec-first for functionality changes, smallest
  failing regression test before each bug fix, verification ladder, no coverage gaming.
