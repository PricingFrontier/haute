# Root cause: finding and reading the project

## Scope

Where the project is, what its `haute.toml` says and which pipelines it
holds, answered separately by each command and by the editor's server, so
that they disagree about one folder.

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
| `PCF-03` | [pipeline-config](pipeline-config.md) | A configured pipeline file is run or refused for its real cause | In part: the check's own wording for a trailing space and a locked file |
| `PCF-04` | [pipeline-config](pipeline-config.md) | `haute run` in a project without git runs or names the missing repository | In part: which of the two it does is still to choose |
| `PCF-05` | [pipeline-config](pipeline-config.md) | Paths that run on Windows and fail on Linux are refused or rewritten | In part: only the paths of `haute.toml`; a node's path is its own |
| `CLI-03` | [cli](cli.md) | A mistyped deploy target or table is named where it is | In part: the empty registry is its own |
| `DPL-03` | [deploy](deploy.md) | A dry run refuses a target no deploy can use | In part: the target's own check still has to run in the dry run |
| `SRV-06` | [server-api](server-api.md) | A missing pipeline file stops `haute serve` in one line | In part: what the editor's page shows is its own |
| `SRV-08` | [server-api](server-api.md) | A save deletes a submodel's file only when no pipeline beside it uses it | In part: whether the three other checks take the wider list is still to decide |

## Planned improvements

### PCF-16 — One answer to what the project is
**Why:** Read on 19 November 2026. There is a function that resolves a
project, `resolve_project`, and only `haute serve` and the server call it.
The other commands take the folder from `current_project()` and then open
`haute.toml` themselves: `status`, `impact`, `smoke`, `ci`, `deploy`,
`serve` for its host, the editor's pipeline route, the workbench's
settings, the assistant's settings, the MLflow settings and
`_project.py`'s own reader of the configured pipeline. Each parses the
file with `tomllib` and looks up its own table.

So one file is read by many rules. A byte-order mark is refused by five
commands and accepted by two; a misspelt key is named by the dry run alone
and gives the editor an empty canvas with no issue (`PCF-02`). Four
commands look for the project above the shell's folder and the rest do
not (`CLI-02`). `haute run` carries on outside any project where
`haute serve` names the missing repository (`PCF-04`). A mistyped table is
blamed on another table (`CLI-03`), and a target no deploy can use passes
the dry run (`DPL-03`). The paths in `haute.toml` are not read by the rule
a node's path is (`PCF-05`), and the configured pipeline's path is
compared letter for letter where the command's argument is not
(`PCF-03`). Which pipelines the project holds has its own answer too:
`discover_pipelines` sees the configured pipeline and the project folder's
`.py` files, and not the pipelines beside the configured one, so a save
deletes a definition a neighbour uses (`SRV-08`). Each package plans its
own repair at its own reader. A command added next will write another.

**Plan:** Make the resolved project the only way in. One function finds
the project from a starting folder, walking up; reads `haute.toml` once by
one stated rule for its encoding; checks every table and key it knows and
names the unknown ones where they are; reads the file's paths by the rule
a node's path is read by; and lists the project's pipelines, those beside
the configured one included. It returns one object, or one refusal that
every caller words the same way. Each command, the server and the routes
take what they need from that object and open `haute.toml` nowhere else.
The editor's load reports the refusal as an issue of the document. Update
the pipeline config and CLI specifications before the change.

**Acceptance:** A test runs every command, and the editor's load, on a
table of projects: started from a folder inside the project, with a
byte-order mark, with a misspelt key, with a misspelt table, with a
backslash in a path, with no git repository, and with a second pipeline
beside the configured one. Each gives the same reading or the same
refusal, in the same words. A command without a row in the test fails it.
A second test fails when a module other than the one that owns the
project opens `haute.toml`. The acceptance of `PCF-02` and `CLI-02` is
met.

**Dependencies:** None. Deliver before the packages its table names.
`PCF-04` and `SRV-08` each keep a choice to make; `IOL-02` records the
byte-order-mark rule for JSON inputs and the two should agree.

**Evidence:** `src/haute/_project_context.py::resolve_project`;
`src/haute/_project.py::_toml_configured_pipeline`;
`src/haute/discovery.py::discover_pipelines`;
`src/haute/cli/_helpers.py`; `src/haute/cli/_serve.py`;
`src/haute/cli/_status.py`; `src/haute/cli/_impact.py`;
`src/haute/cli/_smoke.py`; `src/haute/cli/_ci.py`;
`src/haute/cli/_deploy.py`; `src/haute/deploy/_config.py`;
`src/haute/routes/pipeline.py`; `src/haute/_workbench_config.py`;
`src/haute/assistant/_config.py`;
`src/haute/modelling/_mlflow_settings.py`.
