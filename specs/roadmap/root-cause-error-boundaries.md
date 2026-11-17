# Root cause: failures answered without words

## Scope

Failures answered with a bare 500, a traceback or "check the server logs",
at the editor's server, the underwriter app, background jobs and the
command line.

This file is one pull request. It holds one package, `SRV-09`, which names
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
| `CLI-01` | [cli](cli.md) | An expected failure is one message, not a traceback | Resolves |
| `CLI-08` | [cli](cli.md) | `haute run` refused for memory says so in one line | Resolves |
| `WB-71` | [workbench](workbench.md) | A store connection lost in the middle of a write is answered in words | Resolves |
| `MDL-10` | [modelling](modelling.md) | Training that runs out of memory inside a container says so | Resolves |
| `SRV-04` | [server-api](server-api.md) | A write that cannot replace its file says which file and why | In part |
| `SRV-06` | [server-api](server-api.md) | A missing pipeline file is reported in one line | In part |
| `WB-54` | [workbench](workbench.md) | The underwriter app says why its store refused a write | In part |
| `WB-55` | [workbench](workbench.md) | A busy or damaged SQLite store is called what it is | In part |
| `SRV-01` | [server-api](server-api.md) | A node's own failure is named by Write, Train and the optimiser | In part |
| `VCS-03` | [git-integration](git-integration.md) | A git command that git refuses is reported with git's reason | In part |
| `IOL-01` | [io-layer](io-layer.md) | An input that cannot be prepared says why | In part |

What the packages marked "In part" keep: `SRV-04`, the check of a
submodel's name and of a read-only file; `SRV-06`, what the editor's page
shows and what `haute serve` does at start; `WB-54`, the release route's
report of a store it cannot write to; `WB-55`, reads that carry on after a
refused commit.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-09 | Planned | P2 | A failure is put into words once, by one module, for the editor's server, the underwriter app, a job and the command line. |

## Planned improvements

### SRV-09 — Every boundary answers a failure in the same words
**Why:** Read on 16 November 2026. The editor's server translates errors
once, at its edge, for a list of named error types, and its request
middleware answers anything else with one fixed sentence and a request
id, logging the traceback. Both were an earlier roadmap's work and hold
for that application. Three things keep producing the bare 500s,
tracebacks and "check the server logs" this review found. The underwriter
app is a second application: it registers only the handler for a malformed
request and has no such middleware (`WB-54`, `WB-55`, `WB-71`). Background
jobs, workers and the command line are further edges with their own lists:
the command line expects three types of failure (`CLI-01`, `CLI-08`), and
"check the server logs" is written out in a dozen places (`MDL-10`,
`SRV-01`). And the failures every edge meets, the operating system
refusing a file, memory running out, a database connection lost, have no
one place that names them, so they fall through to the fixed sentence or
are worded again at each call site (`SRV-04`): routes build their own HTTP
errors more than 200 times, and `src/haute` catches every exception in 293
places in 92 files.

**Plan:** One module that turns an exception into a status, a code, a
message and what to do next, used by all four edges: the editor's server,
the underwriter app, a job's or worker's outcome, and the command line. It
takes over the editor's list and its middleware; it is not a second
handler beside them. Classify there, once, the failures every edge meets:
the operating system refusing a file, memory running out, a database
connection lost. Each edge's last handler answers a failure nothing names
in words with an incident number and logs the traceback. Add a test that
finds every place outside the module that builds an HTTP error or catches
every exception without handing it to the module, and fails for any not
on a list that names the function and gives its reason. The pull request
shows it failing on a new such place while an old one is removed.

**Acceptance:** A test raises an exception no list names inside a route of
each application, a job and a command; each answers in words with an
incident number, and none answers "Internal Server Error" or a traceback.
A second raises each classified failure at each edge and requires the same
code and message from all four. The test for places outside the module
passes. The acceptance of `CLI-01`, `CLI-08`, `WB-71` and `MDL-10` is met
through the one module.

**Dependencies:** The decision `SRV-01` records.

**Evidence:** `src/haute/routes/_error_handlers.py`; `src/haute/server.py`;
`src/haute/_workbench_app.py`; `src/haute/_workbench_app_quotes.py`;
`src/haute/cli/_helpers.py`; `src/haute/routes/_helpers.py`.
