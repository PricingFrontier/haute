# Haute engineering roadmap

This folder is the single source of truth for planned engineering
improvements. Each active component roadmap contains the problem,
implementation direction, acceptance criteria, dependencies, and current
code/test evidence needed to take one package through delivery.

Shipped behaviour remains defined by code, tests, and the component
specifications. `Reverify` packages came from older evidence and must be
reproduced against `HEAD` before implementation. `Decision` packages require
an explicit product or architecture choice. Remove a package when its outcome
is covered by current specifications and ordinary regression tests.
`Start with` names a non-deferred package; it is `—` when a component has no
currently startable package. Priority `P1` marks a package that threatens
persisted work, computed results, or security; `P2` a material correctness, UX,
or maintenance issue; `P3` opportunistic work.

Keep measured defects, source-supported risks, unconfirmed reachability, product
decisions and maintenance work distinct.

| Component | Improvement surface | Start with |
|---|---|---|
| [Assistant](assistant.md) | The shipped examples refused on a copy of Haute that pip installed; an applied or undone change that says nothing when its version capture fails; a step's comment, earlier answers and restricted settings sent to a provider whose policy withholds them; an apply saved after the time limit and shown as failed | `AST-01` |
| [Caching](caching.md) | A snapshot build that fails when another program has one of its new files open for a moment; a walk of the whole store after every node output a preview stores | `CCH-02` |
| [CLI](cli.md) | CI commands that name the wrong setting or none; a new project that fails its own lint; `haute run --node` that no longer finds a node grouped into a submodel; `haute ci update` deleting a workflow the team wrote; a pre-commit hook recorded without its executable bit on Windows; a `.gitattributes` a second `haute init` writes and does not record; a restart of `haute serve` refused while an old connection lingers on Linux; a command piped into `head` reported as a parse error | `CLI-06` |
| [Engineering quality](engineering-quality.md) | Promise rules in the frontend lint gate; a test that never runs in CI; dead branches and unused suppressions; the commands run whole on the shipped examples by a test; a coverage gate that misses a change on a statement's later lines; two Windows-only tests no job runs | `EQ-01` |
| [Execution engine](execution-engine.md) | Pipelines with more than one non-live source under the optimiser and a write; preamble failure attribution; a join option the planner does not read; a Transform that cross-joins a Constant; previews that hang after a worker dies; a memory estimate that takes a replaced column's old width | `EXE-01` |
| [Explore and EDA](explore-eda.md) | A pivot's Sum of a 32-bit or 64-bit integer column that wraps round past the column's range and shows a wrong total; timestamps that differ below a microsecond merged out of a pivot's rows | `XPL-01` |
| [Frontend graph canvas](frontend-graph-canvas.md) | What one edit costs and what the undo history holds when the graph carries large tables; a Backspace delete that takes two undo steps; a connection that closes a loop accepted by the canvas, after which no save lands; a tab with unsaved work left read-only after the file on disk is back as it was; pasted nodes that still read the originals; an Edge Join that leaves an optimiser's input behind; an error screen in place of a submodel's comparison | `FEG-01` |
| [Frontend modelling and optimiser UI](frontend-modelling-optimiser-ui.md) | A missing value in the optimiser's data preview drawn as zero, and crashing its Statistics pane; a result pane that crashes when a t-boost model is trained again under a three-way table | `FEM-01` |
| [Frontend node editors](frontend-node-editors.md) | An "is one of" list typed as text on a number column; a Rename to an existing column; a Constant dropped with its cleared name; a Banding factor's missing column left unmarked; a saved Group by with a row count that opens as an invalid step | `FEN-02` |
| [Frontend preview and Explore](frontend-preview-explore.md) | A Refresh after a data file changed: the preview failing on the columns it remembered, and the Explore overview and the Banding editor left on the old data | `FEP-01` |
| [Frontend shared](frontend-shared.md) | Failed calls told in the browser's or the JSON parser's own words, offered to the assistant as a node's error, or reported as a new version of Haute; the last panel values held outside the shared draft (a code editor's typing, a decision) | `FES-03` |
| [Git integration](git-integration.md) | Commit and Restore failing once a deletion or a rename is staged; git's own refusals reported as "not a repository"; a branch delete confirmed against a branch that has since moved; saves left out of version history once a folder is renamed to another letter case; branch names that clash answered "check the server logs"; a file edited by hand that blocks a switch with "check the server logs" and a catch-up with advice that does not apply; an open editor that does not learn the project changed branch, whose save then lands on the other branch, moves the checkout back or is left uncommitted | `VCS-02` |
| [IO layer](io-layer.md) | Inputs whose file is wrong and the fixed text the analyst is given; byte-order marks, empty files and repeated keys in JSON inputs (a decision); a Quote Input on a CSV that cannot be run; a UTF-16 CSV read as garbled columns; Load File replacing bytes it cannot read; XML names that collide across namespaces | `IOL-01` |
| [MLflow model registry](mlflow-model-registry.md) | The model-load retry that treats every failure as a corrupt artifact; eviction of local model copies; the run picker's silent omissions; a missing run reported as a traceback; a load that waits minutes on a server that cannot be reached; a registered version that scores another model file of its run; a re-logged model file that is not picked up; a logged model that refuses a part-year exposure; a canvas GLM logged with no parameters; Retry after a changed experiment showing the old run as logged to the new one | `MLR-01` |
| [Modelling](modelling.md) | Temporal splits on a date column with gaps; tuning and rerun messages; evaluation planning cost; the constructor-only split seam; how `haute train` loads a script; a GLM asked to classify; nulls and infinities a model family cannot take; an imported LightGBM model's Boolean categories scored as missing | `MDL-01` |
| [Optimiser](optimiser.md) | Scenario grids the solver cannot use answered "check the server logs"; a solve with one constraint that does not converge where its own frontier's first point does; a ratebook solve marked converged short of a bound it could meet (a decision); a solver process kept for every finished solve of a node | `OPM-01` |
| [Pipeline config](pipeline-config.md) | One pointer form for a recovery issue on a nested setting; a node's paths that run on Windows and fail on Linux with nothing said (absolute, another letter case); a record of unfinished steps that outlives a hand edit; Recover settings refusing a model alias; Reset node replacing a settings file a submodel shares | `PCF-01` |
| [Pipeline parsing and code generation](codegen.md) | Polars imported under another name; names bound through `exec` or `globals()`; a step list or a node's settings that lose a number's spelling a browser held, and a rebound input name read as a step | `CGEN-06` |
| [Polars 2](polars-2.md) | Remaining RAM-estimate calibration, large-scale operator measurements and worker baselines; Map columns and quantile/rank binning (deferred) | `PL2-17` |
| [Polars node clarity](polars-node-clarity.md) | Step card visual baseline, formula comparisons (deferred) | `PNC-13` |
| [Sandbox and local security](sandbox-security.md) | The hosted editor's cross-site checks; NumPy 1 pickles and the refusal's wording; temporary folders shared between users | `SBS-01` |
| [Server API](server-api.md) | Live sync on the event loop; node and submodel names too long for their files refused only by the save; the staged copies a cut-short Data Output Write and a model save leave; a missing pipeline file shown as "HTTP 500" on the page; typed file names that Windows cannot hold refused without naming the rule, and an output with a device name that stops every Commit | `SRV-02` |
| [Tracing](tracing.md) | A trace that computes the branch of an `if` that did not run; a grouped row explained by the rows of other groups once a grouping column is dropped | `TRC-01` |
| [Workbench](workbench.md) | The underwriter app on hosts past Azure Container Apps (a Databricks App, AWS, internal platforms); a workbench project's CI/CD: a merge to `main` takes form, pipeline and app, tested together, to staging and production; managing quotes in the app past the Quotes page: outcomes, many quotes at once; exports from the app (awaiting a decision on formats); test quotes analysts keep and price again, built again after the first version was taken out (awaiting decisions); actions that fill the sheets from databases and APIs (awaiting decisions on auth and sources); the app's sign-in (a decision); typed-in allowed values (a decision); submodel `WORKBENCH_OUTPUT` resolution in `priceSample`; from the 9 November 2026 review: the quote store's missing timeouts, a release built with outputs nothing fills (a decision), the hosted check's single probes, a save priced before it is refused, a form request answered 500, a column name the save accepts and the pipeline refuses, what a failed pricing tells the underwriter (a decision), a store the app cannot write to reported open, and a SQLite file held exclusively, damaged or deleted not named as such; on its server databases, a quote reached through a padded reference on SQL Server, and a quote list searched and ordered differently on each database; a release id that changes with the checkout's line endings; in the app, hidden values exchanged between rows on a save; an app opened before a migration blanking the new column; SQL Server migration SQL that drops NOT NULL; test quotes misplaced when a params list is reordered; a repeated creation refused as taken; a corrected `.env` password ignored until restart; the sample priced without its stand-in identity by the preview and `haute run` | `WB-08` |

## price-contour on Polars 2 — 18 October 2026

The [price-contour on Polars 2](price-contour-polars-2.md) plan is a handover
specification for the price-contour project: what is left of it is stage 3,
moving price-contour to Polars' own 2.0 Rust crates once Polars publishes them,
and what Haute checks when it takes that release. It is a supporting report and
owns no Haute work.

## Codebase review plan — 10 November 2026

The [codebase review plan](codebase-review-plan.md) is the method and the
order for finding defects in the part of the backend and frontend that the
9 November 2026 review did not reach: wrong inputs made by machine, injected
failures, one answer computed more than one way, the editor in a real
browser, and environments not yet run. It is a supporting report and owns
no work; a reproduced finding becomes a package in its component's roadmap
above, and a phase leaves the plan when it has run. All five phases ran
from 10 to 16 November 2026 and have left it: it now holds what they did
not reach.

## Working protocol

1. Pick one package from its owning component, starting with the package the
   table above names; a component's scope says when packages go together.
2. For `Reverify`, reproduce the stated failure against `HEAD`; retire the
   package if current code and tests already prove the outcome.
3. Update the owning component specification before changing behaviour.
4. Add the smallest failing regression, implement the change, and run the
   affected tests once.
5. Commit each package on its own, with its ID in the message, removing the
   package from its component roadmap in the same commit. Delete a component
   roadmap left empty, with its row above and its
   `_EXPECTED_ACTIVE_COMPONENT_ROADMAPS` entry in `tests/test_docs_accuracy.py`.

Packages that go together share one branch cut from a fresh `origin/main` and
one pull request, with "Before a pull request" in `AGENTS.md` followed first;
never build on an unmerged branch. A measurement-gated package records its
numbers in the pull request and decides by the rule its entry states. A package
that turns out to need a product decision stops there and goes to the user with
options and a recommendation. A package that changes what a user sees or relies
on says so in its entry, with the date it was decided, before work starts.
Deferred packages start only when the evidence or demand their entry names
arrives.

Package IDs are stable and component-owned. Cross-component consumers name a
dependency instead of copying the package.
