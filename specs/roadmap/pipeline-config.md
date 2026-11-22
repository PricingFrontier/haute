# Pipeline config roadmap

## Scope

Node settings, their recovery and the project's `haute.toml`, as [the
pipeline config specification](../pipeline-config/high-level.md) describes
them: which paths a project saved on one system can open on another, and
what recovery makes of a node's settings. `PCF-05` comes from the codebase
review plan's phase 4, which carried projects made on Windows to Linux on
12 November 2026; the main session ran each case again on both systems.

`PCF-06` to `PCF-08` come from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built each run and the main session ran each again on 14 November
2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCF-05 | Planned | P2 | A node's absolute path and a node's path in another letter case than its file are each refused or rewritten on Windows when saved and linted, instead of running there and failing on Linux. |
| PCF-06 | Planned | P2 | A node whose placeholder body was replaced by hand is no longer called unfinished: the editor, `haute lint`, `haute run` and plain Python agree. |
| PCF-07 | Planned | P3 | Recover settings rebuilds a Model Score that follows an alias, instead of refusing a version it added itself. |

## Planned improvements

### PCF-05 — A path only the analyst's machine can open is said so when it is saved
**Why:** Reproduced on 13 November 2026 by saving projects on Windows
through the editor's save route, committing them, and cloning and running
them on Linux as a CI job does. Two ways of writing a node's path run on
Windows with nothing said and fail on Linux.

An absolute path. A Data Input's path typed as the file's full Windows
path inside the project, as Explorer's "Copy as path" gives it, is saved as
typed, the user's name included. The preview and `haute run` pass on
Windows. On Linux `haute lint` passes and `haute run` ends "Path
'C:\\Users\\analyst\\pricing\\data\\synthetic.csv' resolves outside the
project root". The plan's sweep also saw every node's preview there, the
unrelated ones too, answer 403 with that sentence and no node's name.

Another letter case. A path saved as `data/Synthetic.csv` for the file
`data/synthetic.csv` runs on Windows. `haute run` prints one log line,
`input_path_case_ambiguous`; the editor, lint and the dry run say nothing.
On Linux the run ends with the fixed text of `IOL-01`, which names no file.
The module that audits letter case calls the mismatch "worth a loud
warning at access time"; the warning is a line in the server's log.

**Plan:** Decide each case where the analyst is, on Windows, when the path
is saved and when the project is linted. Store an absolute path that lies
inside the project as the path from the project's root, and refuse one that
lies outside it. Report a path whose spelling differs in case from the
entry on disk as a problem of its node, naming both spellings. On Linux,
say of a path with a drive letter that it was written on Windows.

**Acceptance:** Each of the two projects above either runs on Linux as it
does on Windows, or is refused on Windows by the save and by `haute lint`
in words that name the node, the path and what to write instead. No file a save writes holds an absolute path. A test carries a
project from Windows to Linux and back.

**Dependencies:** `IOL-01` for what an input that cannot be read says.

**Evidence:** `src/haute/_path_resolution.py::resolve_node_input_path`;
`src/haute/_path_case_audit.py`; `src/haute/_project.py`;
`src/haute/routes/_save_pipeline.py`; `src/haute/cli/_lint.py`.

### PCF-06 — A record of unfinished steps ends when the node's code is rewritten by hand
**Why:** Reproduced on 14 November 2026. A Data Input saved in the editor
with a half-built step gets a placeholder body that raises, and a record
of its unfinished steps in `.haute.json`. The analyst then replaces the
body by hand with `return df`. The four ways of using the file now
disagree. Run as plain Python, the pipeline returns its three rows. The
editor loads the node as ready, shows the old steps, and its preview says
"This node's steps are incomplete". `haute lint` warns the same and exits
0. `haute run` on the node exits 1 with a traceback ending in
`NotImplementedError`. The specification has the record ignored once the
body is no longer the placeholder. The record is restored whenever the
body yields no step code, and `return df` yields none.

**Plan:** Restore the record only while the body is the placeholder
itself; drop it from `.haute.json` on the next save otherwise.

**Acceptance:** After the hand edit above, the editor's preview,
`haute lint`, `haute run` and plain Python all treat the node as
`return df`.

**Dependencies:** None. A save of a node nobody edited writes it from its
record only while the node carries the code its record loads as ("Which
steps a write renders" in the code generation specification), so the
loader's rule here is the save's too.

**Evidence:** `src/haute/_sidecar.py`; `src/haute/_builders.py`;
`specs/pipeline-config/high-level.md`.

### PCF-07 — Recover settings keeps a Model Score that follows an alias
**Why:** Reproduced on 14 November 2026 through the editor's routes. A
Model Score's settings name a registered model and the alias `champion`,
no version, and hold one key the node type no longer declares. The node
loads unavailable, and Recover settings answers 409: "modelScore config
names both a version and an alias; choose one ... (alias=champion,
version=latest)". The file names no version. Recovery fills the missing
key with a new node's default, `latest`, and then refuses its own result.
With a pinned version and no alias, the same recovery removes the old key
and succeeds.

**Plan:** When recovery fills missing keys from a new node's defaults,
leave out a default the kept settings exclude, here a version beside an
alias.

**Acceptance:** Recover settings on the file above removes the obsolete
key and keeps the model and its alias, with no version.

**Dependencies:** None.

**Evidence:** `src/haute/_node_config_recovery.py`;
`src/haute/_config_validation.py`.
