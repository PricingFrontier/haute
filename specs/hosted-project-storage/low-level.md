# Hosted Project Storage — Low-Level Specification

## Module map

| Path | Responsibility |
| --- | --- |
| `src/haute/_project_storage.py` | The component. Binding record (Files API), remote-URL validation (git and `uc://` forms), the `GIT_ASKPASS` credential helper, restore-at-boot, bind, the background `PushQueue`, and the `uc://` bundle transport (publish/restore over the Files API). Orchestrates git but never shells out itself. |
| `src/haute/_git.py` | Git chokepoint. Supplies `ensure_remote`, `clone_project`, `remote_has_content`, `bundle_create`/`bundle_verify` for the `uc://` transport, and the pre-existing `push_working_pair` that publishes the working/ledger pair atomically. |
| `src/haute/routes/git.py` | HTTP surface: `_with_storage_state` decorates readiness, `POST /api/git/storage/bind`, `POST /api/git/storage/retry`; the milestone route enqueues a push after a successful commit. |
| `src/haute/routes/_save_pipeline.py` | Enqueues a push after a save commit produces a SHA. |
| `src/haute/schemas.py` | `GitStorageSync` plus the `storage` / `storage_remote` / `sync` fields on `GitWorkingBranchResponse`. |
| `databricks_app/bootstrap.py` | Boot path: configure credentials → restore-if-bound → else seed volatile → `chdir` into the project directory before the server is imported. |
| `frontend/src/components/BranchIndicator.tsx` | The storage/sync chip beside the branch indicator. |
| `frontend/src/components/StorageBindModal.tsx` | The bind dialog, including the distinct restart-required state. |
| `frontend/src/types/guards.ts`, `frontend/src/api/{types,client}.ts`, `frontend/src/stores/useGitStore.ts` | Parsing, API calls, and store actions for the above. |
| `tests/test_project_storage.py` | Module tests, including the container-death survival scenario against a `file://` bare repo. |
| `tests/test_storage_routes.py` | HTTP-surface tests, including the no-raw-stderr regression. |

## Key types and data structures

- `StorageBinding` — frozen dataclass `{remote_url, branch, bound_by, bound_at}`, serialised as JSON. `from_payload` ignores unknown keys (a record written by a newer haute must not brick an older container) and raises `StorageUnavailableError` on a malformed or remote-URL-less record.
- `SyncStatus` — `{state: synced|pending|failed, pending: int, failure: transport|rejected|config|None, message: str|None}`. Counts and a failure CLASS plus hand-authored prose; never raw git stderr.
- `PushQueue` — one worker thread per process, guarded by a `threading.Condition`. State: `_pending` (unpublished commits), `_blocked` (do not attempt), `_terminal` (only a manual retry clears), `_failure`/`_message`.
- Module singletons `_queue` and `_active_binding`: one hosted container serves one project, and caching the binding keeps the frequently-polled readiness endpoint off the Files API.
- `UCHead` — frozen dataclass `{generation, tip_sha, writer_id, written_at}`, the `HEAD.json` pointer under a `uc://` location. `from_payload` tolerates unknown keys (same forward-compatibility rule as `StorageBinding`) and raises `StorageUnavailableError` on a malformed record.
- `uc://` layout under `/Volumes/catalog/schema/volume/path`: `bundles/NNNNNN.bundle` (generation-numbered, each a complete `git bundle create --all`) plus `HEAD.json`, written last. `_UC_BUNDLE_RETAIN = 5` generations are kept.
- Per-process `_uc_writer_id` (scope name + random suffix, generated lazily) and `_uc_last_seen_generation` (set at restore and after each publish) — the fencing state behind supersession detection.
- Errors: `StorageConfigError` (actionable misconfiguration → HTTP 400) and `StorageUnavailableError` (binding store unreadable → HTTP 503), both under `StorageError(HauteError)`; `StorageSupersededError(StorageError)` — another writer advanced the `uc://` pointer, classified as a terminal `rejected` sync failure.

## Control flow

**Boot** (`databricks_app/app.py` → `bootstrap.ensure_project`): refresh the vendored wheel → probe → `configure_git_credentials(~/.haute-runtime)` → `restore_if_bound(project_dir)`. `restored` clones the remote and rewrites `.haute/state.json` from the binding's branch; `present` reuses an existing clone; `unbound` seeds the standard scaffold plus a local repo. The process then `chdir`s into the project directory — before `haute.server` is imported, so pipeline discovery and the file watcher see the project, not the app bundle.

**Bind** (`POST /api/git/storage/bind`): validate the URL → `ensure_remote("origin", url)` → `remote_has_content`. Empty remote: `push_working_pair` first, then write the binding (a binding pointing at a remote we could not write to would promise durability the next boot cannot deliver), then activate the queue → `adopted`. Populated remote: write the binding and return `restart-required` WITHOUT activating the queue — the on-disk project is not that remote's project, so publishing from this process would push the wrong history. A `uc://` URL follows the same fork, but "is the remote empty?" cannot be asked with `git ls-remote` — it becomes "does `HEAD.json` exist under the location?", and the adopt path publishes a first bundle generation instead of pushing.

**Save/commit → publish**: `commit_save` (via `_save_pipeline`) and `commit_milestone` (via the route) call `enqueue_push()` after success. That bumps a counter and returns; the worker calls `publish_bound_project`, which selects the transport from the active binding's URL scheme — `uc://` publishes a bundle generation, anything else (including no active binding) is the pre-existing `push_working_pair`. N queued commits collapse into one publish either way.

**Publish (`uc://`)**: read `HEAD.json` → supersession check (see below) → `bundle_create` locally (the only step under the repository mutation lock) → `bundle_verify` — the bundle is the only durable copy, so it is proven readable before it is trusted → upload as generation `head.generation + 1` → write `HEAD.json` LAST → prune generations beyond the newest `_UC_BUNDLE_RETAIN` (best-effort: a failed prune logs and never fails the publish). The bundle byte size is logged so growth stays visible.

**Restore (`uc://`)**: read `HEAD.json` (absent → gate: the binding promises history) → download that generation's bundle → `git clone <bundle>` into the project directory → repoint `origin` to the `uc://` URL (a bundle clone leaves `origin` at a temporary file path, which would break the "is this clone the bound project?" check on the next boot) → the shared tail (`adopt_cloned_lineage`, working-branch record, queue activation) proceeds exactly as for a git-remote restore.

**Failure gating**: a transport failure sets `_blocked` (cleared by the next save or a manual retry); a rejection or `GitDomainError` also sets `_terminal` (only a manual retry clears it), so a diverged remote is not hammered by every subsequent save.

## Edge cases and invariants

- An unreadable, corrupt, or non-object binding record raises rather than reading as "unbound" — the invariant that stops a fresh project being seeded over durable work.
- `remote_has_content` propagates errors: an unreachable remote must never read as "empty" and trigger an adopt that publishes over someone else's project.
- Remote URLs are restricted to `https://`, `file://`, and `uc://`; embedded credentials are refused (they would land in `.git/config` and every remote-tracking log line). Plain `http://` and `ssh://` are rejected.
- A `uc://` URL must name a three-part volume (`catalog.schema.volume`) plus a non-empty project path with no empty, `.` or `..` segments — the path is joined under `/Volumes/` for the Files API, so a traversal segment would escape the volume.
- Supersession rule: publishing is refused (terminally, as a `rejected` failure) when the pointer was written by a different `writer_id` at a generation other than the one this process last saw. The generation this process restored from is exempt — that pointer was legitimately written by the predecessor container whose lineage this one adopted.
- The `present` restore path (an existing clone survived a process restart) arms the same fence: the new process blesses the current generation only when the clone actually contains the published `tip_sha`; an unknown tip means the volume moved on without this directory, so the first publish stops as superseded instead of overwriting the newer generation.
- Pointer-written-last is also the read-side contract: a bundle uploaded without its pointer (a torn publish) is invisible to restore, which only ever follows `HEAD.json`.
- The askpass helper contains no secret: it reads `HAUTE_GIT_TOKEN` from the environment at call time, is written `0700`, and answers the username prompt separately from the password prompt.
- `enqueue` on a queue that was never started (every local session) is inert, so the save path is unchanged off the hosted platform.
- `storage_state()` returns `unsupported` without `HAUTE_STATE_VOLUME`, which hides the whole surface in the UI rather than offering an action that cannot work.
- The worker never holds the queue lock across a push, so it cannot deadlock against the repository mutation lock `push_working_pair` acquires.

## Error handling

`StorageConfigError` → 400 with the setting named; `StorageUnavailableError` → 503; `GitPushRejectedError` → 409 with the structured rejection; other `GitError` → the existing `_handle_git_error` sanitiser. `_with_storage_state` never raises: storage is additive to git readiness, and a storage fault must not blank the branch indicator — it surfaces through the sync state instead. Clone and push failures log stderr server-side and return hand-authored prose, per the MAGINOT low-context-error class.

## Testing

`tests/test_project_storage.py` covers URL validation (git and `uc://` forms, including malformed volume names and traversal segments), binding-record semantics (round-trip, unknown-field tolerance, malformed and unreadable records), credential handling (token absent from the helper file, `0700`, correct answers to both git prompts), the push-queue state machine (coalescing, transport retry-on-next-save, terminal rejection, message sanitisation), transport dispatch (`publish_bound_project` routes a `uc://` binding to the bundle path and everything else to `push_working_pair`), and two container-death scenarios: `TestContainerDeathSurvival` against a real `file://` bare repository, and `TestUcContainerDeathSurvival` against the in-memory Files API stand-in — bind → save → publish → restore into a fresh directory, asserting the save's SHA, the resumed working branch, the repointed `origin`, pointer-written-last safety, retention pruning, and supersession. `tests/test_storage_routes.py` covers the HTTP surface and pins that no absolute path or raw stderr reaches the response body. Frontend behaviour is covered in `BranchIndicator.test.tsx` and `guards.contract.test.ts`.

Known gaps: no test exercises a real HTTPS remote or a real UC volume (the live smokes in `databricks_app/LEARNINGS.md` cover those paths manually).

## Approved change contract

Delivered, including the `uc://` bundle transport (initially deferred; now implemented as described above). One item from the design remains deliberately out of scope: per-commit authorship from the forwarded user identity — `bound_by` records who bound the project, and request logs carry the per-request user, but commits are authored by the app identity. A follow-up, not a regression.
