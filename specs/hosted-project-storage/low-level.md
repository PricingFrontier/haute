# Hosted Project Storage — Low-Level Specification

## Module map

The component is **not yet implemented**. Current shipped behaviour, for
orientation:

| Path | Responsibility today |
| --- | --- |
| `databricks_app/bootstrap.py` | Seeds a volatile `haute init` project + git repo into the container cwd on first boot; state dies with the container. |
| `src/haute/hosted.py` | Environment-contract detection and the platform-proxy boundary; no storage awareness. |
| `src/haute/_git.py` | Save/milestone commit machinery, `git_binary_available()`, push/fetch primitives used by existing flows. |

Everything below is the approved-change design, not current behaviour.

## Approved change contract

**Current limitation.** A hosted session's project directory — including
its git history — is destroyed on every redeploy, restart, or app stop.
Saves and milestone commits are therefore not durable; there is no
development cycle on the hosted platform.

**Approved target behaviour.** The lifecycle in
[high-level.md](high-level.md) §Behaviour: bind → restore → async
push-on-save → nothing-on-close, gated on the hosted environment
contract, with the invariants and failure model stated there.

**Non-goals.** Multi-writer coordination or merge handling beyond loud
non-fast-forward stop; volume (`uc::`) transport — designed below,
explicitly deferred; MLflow persistence; any local-mode change; secret
*creation* UX (operator attaches the secret resource out of band).

### Planned module map

| Path | Responsibility |
| --- | --- |
| `src/haute/_project_storage.py` | Binding record model + Files-API read/write; restore-at-boot (clone, branch adoption); the serialised async push queue with sync-state; askpass credential helper materialisation. |
| `src/haute/hosted.py` | Gains `restore_project()` called from the entry point before serving; exposes storage state to the server. |
| `src/haute/routes/git.py` (or new `routes/storage.py`) | Bind endpoint (validate remote, clone-or-init, record binding); sync-state in the working-branch readiness response; manual retry endpoint. |
| `src/haute/_git.py` | Post-commit hook point: save + milestone commit call sites enqueue a push (no behavioural change when no binding exists). |
| `frontend` (guards, BranchIndicator, startup modal, App gates) | New readiness field: `storage: bound / volatile / restoring`; sync chip (synced / pending *n* / failed) beside the branch indicator; bind dialog in the startup flow. |
| `databricks_app/bootstrap.py` | Becomes: binding recorded → restore; else seed volatile (current behaviour) and mark volatile. |

### Key decisions (fixed by this contract)

- **Binding record**: JSON at
  `/Volumes/<catalog>/<schema>/<state volume>/haute-apps/<app-name>/binding.json`
  via the Files API (SDK, SP auth): `{"remote_url", "branch", "bound_by",
  "bound_at"}`. No credential material in the record. The state volume
  is configured via one env var (`HAUTE_STATE_VOLUME`, e.g.
  `workspace.default.haute_data`); SP needs `READ VOLUME` +
  `WRITE VOLUME` on it.
- **Credential**: HTTPS remotes only. Token supplied as env
  `HAUTE_GIT_TOKEN` from an app **secret resource**; injected into git
  exclusively via a generated `GIT_ASKPASS` helper (0700, in the
  container tmp dir) — never in the URL, never in `.git/config`, never
  logged. `HAUTE_GIT_TOKEN` joins the secret-surface AST allowlist with
  a justification, and a contract test asserts no config/log leakage.
- **Push queue**: single background worker, FIFO, coalescing (a push
  synchronises all local refs that matter: working branch + ledger
  refs — reuse the ref enumeration the existing fetch/push machinery
  uses). States: `synced`, `pending(n)`, `failed(reason class)`.
  Enqueue on save-commit and milestone-commit; retry on next enqueue or
  manual trigger. Failure classes: unreachable / auth / non-fast-forward
  (terminal until user action).
- **Boot order** (bundle entry point): refresh wheel → probe → read
  binding → if bound: clone + adopt branch (failure ⇒ gate per failure
  model, do NOT seed over it) → else seed volatile → serve.
- **Environment gating**: every new surface no-ops (and hides in the UI)
  when `databricks_app_environment()` is `None`. Local mode byte-identical.
- **`uc::` transport (deferred design sketch)**: a git remote helper
  shuttling `git bundle` files over the Files API to a volume path,
  registered as transport #2 behind the same binding record
  (`remote_url: "uc://catalog.schema.volume/path"`). Not built in v1;
  the binding validator rejects the scheme with "not yet supported".

### Failure and compatibility semantics

As high-level.md §Failure model, plus: a binding written by a NEWER
haute (unknown fields) is tolerated (ignore unknown keys); an unreadable
or malformed binding record gates exactly like an unreachable remote
(never silently volatile). Message shapes follow the MAGINOT
low-context-error class and are pinned by tests.

### Acceptance evidence (executable)

- Unit/contract (`tests/test_project_storage.py`): binding record
  round-trip with a stubbed Files API; clone-or-init against a local
  **`file://` bare repo** standing in as the remote (full lifecycle:
  bind → commit → push → simulate container death by re-restoring into a
  fresh tmp dir → history intact); push-queue coalescing, retry, and
  non-fast-forward terminality; askpass helper leaves no token in
  `.git/config`, process env of child git carries no token in argv.
- Secret backstops extended: `HAUTE_GIT_TOKEN` reviewed; response-model
  walk stays clean (sync state exposes counts and classes, never
  reasons' raw stderr).
- Hosted-sim (`tests/test_hosted.py` pattern): boot with binding +
  unreachable remote gates with the specified message; volatile session
  requires the explicit flag.
- Real-deploy smoke (manual, scripted steps in `databricks_app/LEARNINGS.md`):
  bind the live app to a scratch private repo, save, `apps stop`/`start`
  (the cheapest container death), confirm the project and history
  restore; confirm the pending-counter path by revoking the token
  mid-session.

### Open decisions (Nick, before or during implementation)

1. State volume: reuse `workspace.default.haute_data` or a dedicated
   `haute_state` volume (cleaner grants: data volume stays read-only for
   the SP).
2. Are volatile sessions permitted outside testing, or does production
   posture require a binding to serve at all?
3. Commit identity: keep the seeded app identity, or stamp
   `X-Forwarded-Email` (already captured in the request scope) as
   author on save commits — natural rider on this work.
4. Push cadence: every save (contract above) vs milestone-only with a
   periodic background sync — contract assumes every save; flag if the
   save frequency makes remote churn a concern.

**Implementation plan**: single branch off
`claude/haute-databricks-app-deployment-b99fca` (or main after it
lands); order: `_project_storage.py` + tests → git hook points →
routes/readiness → frontend → bundle boot order → real-deploy smoke.
Verification ladder: unit → contract → hosted-sim → live smoke, gates
green (`scripts/preflight.sh`), Codex (or fallback cross-model) review
before PR.

## Key types and data structures

Not yet implemented — see §Approved change contract.

## Control flow

Not yet implemented — see §Approved change contract (boot order, push
queue).

## Edge cases and invariants

Not yet implemented — see §Approved change contract (failure and
compatibility semantics).

## Error handling

Not yet implemented — see high-level.md §Failure model.

## Testing

Not yet implemented — see §Acceptance evidence.
