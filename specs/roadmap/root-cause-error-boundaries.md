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
| `SRV-04` | [server-api](server-api.md) | A write that cannot replace its file says which file and why | Resolves |
| `SRV-06` | [server-api](server-api.md) | A missing pipeline file is reported in one line | Resolves |
| `WB-54` | [workbench](workbench.md) | The underwriter app says why its store refused a write | Resolves |
| `WB-55` | [workbench](workbench.md) | A busy or damaged SQLite store is called what it is | Resolves |
| `WB-71` | [workbench](workbench.md) | A store connection lost in the middle of a write is answered in words | Resolves |
| `MDL-10` | [modelling](modelling.md) | Training that runs out of memory inside a container says so | Resolves |
| `SRV-01` | [server-api](server-api.md) | A node's own failure is named by Write, Train and the optimiser | In part |
| `VCS-03` | [git-integration](git-integration.md) | A git command that git refuses is reported with git's reason | In part |
| `IOL-01` | [io-layer](io-layer.md) | An input that cannot be prepared says why | In part |
| `FES-03` | [frontend-shared](frontend-shared.md) | A call that got no answer of Haute's says so in words | In part |
| `WB-51` | [workbench](workbench.md) | A form whose column name is not text is refused, not answered 500 | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-09 | Planned | P2 | A failure no list names is answered in words with an incident number by the editor's server, the underwriter app, a job and the command line. |

## Planned improvements

### SRV-09 — Every boundary answers a failure it does not know in words
**Why:** Read on 16 November 2026. The editor's server translates errors
once, at its edge, for a list of named error types; that was an earlier
roadmap's work and it holds for those types. Four gaps keep producing the
bare 500s, tracebacks and "check the server logs" this review found.
Nothing answers an exception that is not on the list, so the reply is the
framework's plain "Internal Server Error". The underwriter app is a second
application and registers only the handler for a malformed request
(`WB-54`, `WB-55`, `WB-71`). Routes still build their own HTTP errors, more
than 200 times, and `src/haute` catches every exception in 293 places in
92 files, so each call site decides again what the user is told. Background
jobs, workers and the command line are further edges with their own lists:
the command line expects three types of failure (`CLI-01`, `CLI-08`), and
"check the server logs" is written out in a dozen places (`MDL-10`,
`SRV-01`).

**Plan:** One module that turns an exception into a status, a code, a
message and what to do next, used by all four edges: the editor's server,
the underwriter app, a job's or worker's outcome, and the command line.
Classify there, once, the failures every edge meets: the operating system
refusing a file, memory running out, a database connection lost. Give each
edge a last handler that answers in words with an incident number and logs
the traceback. Add a test that counts HTTP errors built outside the edge
and catch-alls that do not classify, and only lets the counts fall.

**Acceptance:** A test raises an exception no list names inside a route of
each application, a job and a command; each answers in words with an
incident number, and none answers "Internal Server Error" or a traceback.
The counting test passes. The acceptance of `CLI-01`, `CLI-08`, `SRV-04`,
`SRV-06`, `WB-54`, `WB-71` and `MDL-10` is met through the one module.

**Dependencies:** The decision `SRV-01` records.

**Evidence:** `src/haute/routes/_error_handlers.py`;
`src/haute/_workbench_app.py`; `src/haute/cli/_helpers.py`;
`src/haute/routes/_helpers.py`.
