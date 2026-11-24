# Sandbox and local security roadmap

## Scope

The trust boundaries [the sandbox security
specification](../sandbox-security/high-level.md) and [the hosted Databricks
App specification](../hosted-databricks-app/high-level.md) describe: the
requests the server accepts from a browser, the files it creates outside the
project, and the pickles it agrees to load. The packages below come from the
9 November 2026 codebase review. `SBS-03` is a risk read from the source
whose reach depends on the host and was not confirmed; its entry says what
would have to be true. `SBS-02` was reproduced against `HEAD` on that day.

## Priorities

**Start with:** `SBS-02`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SBS-02 | Planned | P2 | A pickle written under NumPy 1 loads, and a refused pickle tells the analyst what they can do. |
| SBS-03 | Planned | P3 | Haute's files under the shared temporary folder belong to one user and are created private. |

## Planned improvements

### SBS-02 — A NumPy 1 pickle loads, and a refusal is written for the analyst
**Why:** Reproduced. The restricted unpickler allows NumPy's array
reconstruction under the module path NumPy 2 writes, `numpy._core.multiarray`.
A pickle written under NumPy 1 names `numpy.core.multiarray` and is refused.
An array pickled with protocol 2 loaded as written; with the module path a
NumPy 1 install writes it raised "Blocked unpickling of
numpy.core.multiarray._reconstruct — not in the allowlist. If this is a
legitimate model/data class, add its exact (module, qualname) to
_ALLOWED_PICKLE_CLASSES in src/haute/_sandbox.py; add only vetted scaffolding
functions to _ALLOWED_PICKLE_GLOBALS". Any Load File or model pickled before
the move to NumPy 2 hits this, and the message tells an analyst to edit
Haute's source, which they do not have and should not change.

**Plan:** Allow the NumPy 1 module paths for exactly the functions already
allowed under the NumPy 2 paths. Rewrite the refusal for the analyst: it
names the file, the class it refused, and what they can do (write the file
again from a current environment, or use Parquet for data).

**Acceptance:** An array, a scalar and a dtype pickled with the NumPy 1
module paths load through `safe_unpickle`; a pickle naming a function
outside the allowlist is still refused. The refusal's text names the file and
does not mention Haute's source files.

**Dependencies:** None.

**Evidence:** `src/haute/_sandbox.py::_blocked_pickle_error`;
`src/haute/_sandbox.py::_ALLOWED_PICKLE_GLOBALS`;
`src/haute/_sandbox.py::safe_unpickle`.

### SBS-03 — Haute's temporary folders are per user and private
**Why:** A risk read from the source; it needs a host where several users
share one temporary folder. Training artifacts, optimiser artifacts (apply
results, ratebook factors, quote analyses, frontier histories, scratch), the
project mutation locks and the form locks all live in fixed-name folders
under the system temporary directory, created with the default mode and no
check of who owns them. On Windows the temporary folder is per user, and a
container has one user, so the product's usual hosts are unaffected. On a
shared Linux or macOS host, the second user's server cannot create job
folders or lock files under the first user's folders, so training, the
optimiser and every workbench form read fail with a permission error; staged
models and optimiser artifacts are readable by every local user; and another
local user can create the fixed folder first and then receive, or replace,
artifacts a later Apply reads.

**Plan:** One root per user, created with mode `0o700` and verified for
owner and mode on every start, holding all of the folders above; refuse to
start with a clear message when the root is someone else's.

**Acceptance:** On POSIX the root and everything under it are owned by the
server's user and not readable by others, asserted by a test that creates
the folders; a root owned by another user stops the server with a message
naming the folder. Windows behaviour is unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_artifacts.py`;
`src/haute/routes/_training_artifacts.py`;
`src/haute/_project_mutation_lock.py`; `src/haute/_workbench_folder.py`;
`src/haute/_artifact_housekeeping.py`; `src/haute/_file_lock.py`.
