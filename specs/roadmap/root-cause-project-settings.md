# Root cause: finding and reading the project

## Scope

Where the project is, how its `haute.toml` is parsed and which pipelines
it holds, each answered in more than one place, so that commands and the
editor disagree about one folder.

This file is one pull request. It holds one package, `PCF-16`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `PCF-02` | [pipeline-config](pipeline-config.md) | Every command, and the editor, reads `haute.toml` by one rule | Resolves |
| `CLI-02` | [cli](cli.md) | Every command finds the project from a folder inside it | Resolves |
| `PCF-03` | [pipeline-config](pipeline-config.md) | A configured pipeline file is run or refused for its real cause | In part: the recogniser's own treatment of a `.PY` extension, a trailing space and a locked file |
| `PCF-04` | [pipeline-config](pipeline-config.md) | `haute run` in a project without git runs or names the missing repository | In part: which of the two it does is still to choose |
| `PCF-05` | [pipeline-config](pipeline-config.md) | Paths that run on Windows and fail on Linux are refused or rewritten | In part: only the paths of `haute.toml`; a node's path is its own |
| `CLI-03` | [cli](cli.md) | A mistyped deploy target or table is named where it is | In part: only the mistyped table; the target's wrong attribution and the empty registry are its own |
| `SRV-06` | [server-api](server-api.md) | A missing pipeline file stops `haute serve` in one line | In part: what the editor's page shows is its own |
| `SRV-08` | [server-api](server-api.md) | A save deletes a submodel's file only when no pipeline beside it uses it | In part: whether the three other checks take the wider list is still to decide |

## Planned improvements

### PCF-16 — One finder, one parse of `haute.toml` and one list of pipelines
**Why:** Read on 19 November 2026. Three questions about a project are
each answered in more than one place.

How `haute.toml` is parsed. One group of readers goes through
`DeployConfig.from_toml`: `status`, `impact`, `smoke`, `ci`, `deploy` and
the workbench's build. It reads the text through `read_user_text`, which
takes off a byte-order mark, and names an unknown key. Seven other readers
open the file with `tomllib` themselves and look up their own table: the
configured pipeline in `_project.py`, the editor's pipeline route,
`haute serve` for its host, the model name in the commands' helpers, the
workbench's settings, the assistant's settings and the MLflow settings.
Three of them refuse an unknown key in their own table (the workbench's,
the assistant's and the MLflow settings) and four check none, and each
meets a byte-order mark its own way. No reader owns the file as a whole. So the reading of one file depends on which reader a command happens
to reach: `PCF-02` recorded the commands disagreeing on a mark and on a
misspelt key, and the editor loading an empty canvas with no issue.
`PCF-02`'s own table of which command does what was taken on 11 November
and is to be taken again against `HEAD` first; the two groups above are
what the source holds now. The paths the file names are read without the
rule a node's path is read by (`PCF-05`), and a mistyped table is reported
by `app build` as another setting (`CLI-03`).

Where the project is. `resolve_project` is called by `haute serve` alone.
Four commands look above the shell's folder for the project and the
others do not (`CLI-02`), and `haute run` carries on outside any project
where `haute serve` names the missing repository (`PCF-04`). `haute serve`
does not look at the pipeline file before it starts (`SRV-06`).

Which pipelines it holds. `discover_pipelines` sees the configured
pipeline and the project folder's `.py` files, and not the pipelines
beside the configured one, so a save deletes a definition a neighbour
uses (`SRV-08`); three more checks read the same list.

Each package plans a repair at its own reader. A command or a table added
next would bring another.

**Plan:** Share the three answers and leave each command its own needs.

- One parse of `haute.toml`: one stated rule for its encoding, every table
  and key Haute knows checked and an unknown one named where it is, and
  the file's paths read by the rule a node's path is read by. Each table's
  reader, the deploy settings' included, takes its table from that parse
  and opens the file nowhere else. The parse is made afresh for each
  operation, as the pipeline config specification has it: an edit to the
  file reaches the next call, and nothing holds the settings for the life
  of a process.
- One finder, walking up from a starting folder, and each command states
  what it needs of it. No command's need changes. The pull request begins
  with a table of every command and what it needs today, and keeps it:
  `init` and `gpu-setup` need no project; `haute run` and `haute lint`
  given a file, a pipeline run as Python, and `status` and `deploy` given
  a model explicitly, run in a folder without a `haute.toml`; `smoke` and
  `impact` need one. Commands with the same need refuse in the same words
  when it is not met.
- One list of a project's pipelines, those beside the configured one
  included, for every check that asks what else uses a file.
- The editor's load reports what the parse refuses as an issue of the
  document.

Update the pipeline config and CLI specifications before the change.

**Acceptance:** A test runs every command, and the editor's load, on a
table of projects: started from a folder inside the project; with a
byte-order mark; with a misspelt key; with a misspelt table; with a
backslash in a path; with no git repository; with no `haute.toml` at all;
and with a second pipeline beside the configured one. Commands that need a
project give the same reading or the same refusal, in the same words. Each
command that runs without a project or without a `haute.toml` today still
does: `haute run` and `haute lint` on a file in a bare folder among them. A setting edited between two
calls to a running server is read by the second. A command without a row
in the test fails it. A second test fails when a module other than the
parse's own opens `haute.toml`. The acceptance of `PCF-02` and `CLI-02` is
met.

**Dependencies:** None. Deliver before the packages its table names.
`PCF-04` and `SRV-08` each keep a choice to make; `IOL-02` records the
byte-order-mark rule for JSON inputs and the two should agree.

**Evidence:** `src/haute/_project_context.py::resolve_project`;
`src/haute/_project.py::_toml_configured_pipeline`;
`src/haute/discovery.py::discover_pipelines`;
`src/haute/deploy/_config.py`; `src/haute/_io.py::read_user_text`;
`src/haute/cli/_helpers.py`; `src/haute/cli/_serve.py`;
`src/haute/cli/_ci.py`; `src/haute/routes/pipeline.py`;
`src/haute/_workbench_config.py`; `src/haute/assistant/_config.py`;
`src/haute/modelling/_mlflow_settings.py`;
`specs/cli/high-level.md`; `specs/pipeline-config/high-level.md`.
