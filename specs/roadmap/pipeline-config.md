# Pipeline config roadmap

## Scope

Node settings, their recovery and the project's `haute.toml`, as [the
pipeline config specification](../pipeline-config/high-level.md) describes
them: which paths a project saved on one system can open on another. `PCF-05` comes from the codebase
review plan's phase 4, which carried projects made on Windows to Linux on
12 November 2026; the main session ran each case again on both systems.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCF-05 | Planned | P2 | A node's absolute path and a node's path in another letter case than its file are each refused or rewritten on Windows when saved and linted, instead of running there and failing on Linux. |

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
