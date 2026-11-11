# Pipeline config roadmap

## Scope

Node settings, their recovery and the project's `haute.toml`, as [the
pipeline config specification](../pipeline-config/high-level.md) describes
them: how an issue found in a settings file says where it is, how the
commands read `haute.toml`, and what a project is. `PCF-01` comes from the
9 November 2026 codebase review. It is read from the source; what the editor
does with the two pointer forms was not traced. `PCF-02` to `PCF-04` come
from the review plan's sweep of the commands on 11 November and were run
again by the main session.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCF-01 | Planned | P3 | A recovery issue points at a nested setting the same way whichever check found it. |
| PCF-02 | Planned | P3 | Every command reads `haute.toml` by one rule: a byte-order mark and a misspelt key are treated, and named, the same by all of them. |
| PCF-03 | Planned | P3 | A configured pipeline file with a trailing space, an upper-case extension or a moment's lock is run or refused for that cause, not called "not a pipeline". |
| PCF-04 | Planned | P2 | `haute run` in a project that is not yet a git repository runs, or names the missing repository as `haute serve` does, instead of calling an existing data file unavailable. |

## Planned improvements

### PCF-01 — One pointer form for a nested setting
**Why:** Read from the source. Recovery reports each issue with a path into
the node's settings. An issue from the completeness checks goes through
`gap_pointer`, which splits a dotted setting into segments:
`/input_scenario_map/quotes`. An issue the engine's own check raised is built
with the whole dotted setting as one segment: `/input_scenario_map.quotes`.
The same function decides whether the issue is a missing value or an invalid
one by looking the dotted setting up as a single key, which is never present
for a nested setting, so a nested issue is always reported as missing. The
only nested setting an engine check names today is a Source Switch input
mapped to an empty source, so the reach is one message in one editor.

**Plan:** Build the engine issue's path with `gap_pointer` and walk the
segments to decide its code.

**Acceptance:** A Source Switch input mapped to an empty source is reported
at `/input_scenario_map/<input>` with the invalid-value code, and a test
holds the two routes to the same path for the same setting.

**Dependencies:** None.

**Evidence:** `src/haute/_node_config_recovery.py::_engine_issues`;
`src/haute/_node_config_recovery.py::gap_pointer`;
`src/haute/_node_config_models.py::_check_live_switch` raises with the
dotted setting.

### PCF-02 — Every command reads `haute.toml` by one rule
**Why:** Reproduced on 11 November 2026 by running the commands. The same
`haute.toml` is read differently by different commands.

A file saved with a byte-order mark, as Windows PowerShell 5 writes "utf8",
is refused by `haute run`, `haute lint`, the dry run, `haute status` and
`haute ci update` with "Invalid statement (at line 1, column 1)", which does
not name the mark; `haute status` prints only that.
`haute ci registry-host` accepts the file, and the sweep saw `haute init`,
run again on it, do the same. `IOL-02` records the same mark for JSON
inputs.

A key misspelt by one letter, `pipline = "rating/main.py"`, makes
`haute run` and `haute lint` answer "No pipeline file found in" the folder,
"Expected one of: a haute.toml with [project].pipeline set, ...", to an
analyst who believes they set it. The dry run names it: "[project] unknown
key 'pipline'". An unknown key that changes nothing for a run, `version`
under `[project]`, passes `haute run` and `haute lint` with exit 0; the
sweep saw the dry run refuse it.

**Plan:** One reader for `haute.toml`, used by every command: it accepts or
refuses a byte-order mark by one stated rule and names it when it refuses,
and it checks the keys it knows, so that a misspelt key is named by every
command that reads the file.

**Acceptance:** A `haute.toml` with a byte-order mark is treated the same
by every command, and a refusal names the mark. `pipline` under `[project]`
is named as an unknown key by `haute run` and `haute lint` as it is by the
dry run.

**Dependencies:** None. `CLI-01` covers the traceback that precedes some of
these messages.

**Evidence:** `src/haute/_project.py::_toml_configured_pipeline`;
`src/haute/deploy/_config.py::_validate_toml_keys`;
`src/haute/cli/_serve.py::_load_toml_server_host`.

### PCF-03 — A configured pipeline file is not misread as "not a pipeline"
**Why:** Reproduced on 11 November 2026. `[project].pipeline` names a file
that holds a Haute pipeline, and `haute run` answers that it "does not look
like a Haute pipeline", advising "Point [project].pipeline at a .py file
containing 'haute.Pipeline'." Three ordinary causes give that answer: a
space typed after the path inside the quotes; the extension written `.PY`,
which Windows opens and which runs when the same path is given as the
command's argument; and the file held open by another program at that
moment. The check compares the extension letter for letter and answers "not
a pipeline" for any file it cannot read.

**Plan:** Have the check say what it found: a path that ends in a space, a
file that could not be read and why, or a file that was read and holds no
pipeline. Compare the extension without regard to letter case, as the
argument's path is treated.

**Acceptance:** With a trailing space in the configured path, with `.PY`
and with the file held open, `haute run` either runs the pipeline or names
that cause. None of the three is told that the file is not a pipeline.

**Dependencies:** None.

**Evidence:** `src/haute/_project.py::_looks_like_pipeline_file`;
`src/haute/discovery.py`.

### PCF-04 — `haute run` in a project without git names what is missing
**Why:** Reproduced on 11 November 2026. An analyst runs `haute init` in a
folder that is not a git repository, adds a Data Input that reads
`data/x.csv`, and runs `haute run`. It exits 1: "Error executing pipeline:
This Data Input's source is unavailable and no published snapshot exists."
The file is there. After `git init` the same command runs the pipeline.
`haute serve` names the cause for the same folder: "haute.toml found but not
inside a git repository; run 'git init'". A project is defined as a
`haute.toml` inside a git repository; without the repository `haute run`
carries on outside any project, and the input's path no longer means the
file beside the `haute.toml`.

**Plan:** When `haute run`, `haute lint` or the dry run meet a `haute.toml`
that is not inside a git repository, refuse in the words `haute serve` uses,
or run with that folder as the project. Either way a path in a Data Input
means the same file as it will after `git init`.

**Acceptance:** In a folder made by `haute init` without git, `haute run`
on a pipeline with a Data Input either runs it or answers naming the missing
repository and `git init`. It never answers that the source is unavailable
for a file that exists.

**Dependencies:** None. `IOL-01` words an input whose file is wrong.

**Evidence:** `src/haute/_project.py::get_project_root`;
`src/haute/_project_context.py::resolve_project`;
`src/haute/cli/_run.py`.
