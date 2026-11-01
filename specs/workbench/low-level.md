# Workbench — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_workbench_rollout.py` | The hosted app rolled out and checked, for `haute app rollout` (Control flow 35). `RolloutError` (a `WorkbenchError` naming what failed and what was done) and `QuotesClosedError` (the release stays: its `differences`); `CREDENTIAL_VARIABLES` (the subscription, tenant, client ID and secret of the CI service principal, each an AZURE_ variable) and `credentials` (their values read from the environment, each one unset named in one refusal); `DIGEST_REFERENCE` (an image named by digest); `ROLLOUT_TIMEOUT_SECONDS` (600: Azure's update and the provisioning together), `HEALTH_TIMEOUT_SECONDS` (300), `RECOVERY_TIMEOUT_SECONDS` (600), `REQUEST_TIMEOUT_SECONDS` (30, each request) and `POLL_SECONDS` (10). The host as the rollout sees it: `Recorded` (the release a rollout recorded on the app: `image`, `release`, `commit`), `AppState` (whether it keeps one revision active, its containers' images, its public address, its latest revision, its active revisions, its Entra ID registration's client ID, None without one, the names of its Azure tags and its `Recorded`, None without a whole one), `TAG_LIMIT` (50) and `TAG_VALUE_LIMIT` (256), Azure's limits on a resource's tags, `RevisionState` (a revision's image, whether it is active, its provisioning and running states) and the `ContainerAppHost` protocol (`state`, `revision`, `deactivate`, `limit`, which bounds every request from then on, retries and their waits included, to a deadline, `set_image`, which sets the image with a revision suffix of its own within a deadline and answers the revision it made, `record`, which sets the record's Azure tags, and `token`), which `AzureContainerApps` implements through `azure-mgmt-appcontainers`' `ContainerAppsAPIClient` and an `azure-identity` `ClientSecretCredential`, the client ID read from the app's `current` auth config and a token asked for `<client id>/.default`, Azure's long-running update waited on no longer than the deadline. `HttpAnswer`, `fetch` (one GET through `urllib` within `REQUEST_TIMEOUT_SECONDS`, redirects not followed, answering status, body and where a redirect points; a stall, no server or a body that ends early answering status 0, an error whose body ends early its status) and `post` (one POST of a JSON body the same way), the hosted check's and the impact's transport; `forged_principal` (the headers a host's sign-in would set for a principal, which the app accepts when they reach it unchanged). `rollout` (Control flow 35: the preflight, the maintenance window, the wait, `hosted_check`, `_roll_back`, the record; answers the release id), `hosted_check` and `promoted` (the release `--from` takes, its record validated against the source app and the commit checked out). |
| `src/haute/_workbench_impact.py` | Impact on saved quotes, for `haute app impact` (Control flow 36). `ImpactError` (a `WorkbenchError` naming the environment and what failed); `IMPACT_TIMEOUT_SECONDS` (600, the whole run), `PRICE_REQUEST_SECONDS` (75, a pricing request's bound, beyond the app's 60), `DEFAULT_SAMPLE` (100) and `MAX_SAMPLE` (1,000), `BUSY_TRIES` (3), `REPORT_FILE` (`impact_report.md`) and `RELEASE_TAG` (`haute-release`, whose absence on production is no baseline). `AppReader` (what the impact reads of a host: `state` and `token`, which `src/haute/_workbench_rollout.py::AzureContainerApps` implements) and `Side` (an app as the impact reaches it: its environment, address, token, release id, form revision, form and recorded commit, the routes reached through the rollout module's `fetch` and `post`); `SharedTable` and `SharedFields`, which `shared_fields` answers (the output tables both forms name with the same rows, by name, each with the columns both name and the columns both mark as keys, in staging's order; the tables and columns only staging has, `added`, only production has, `removed`, and the tables whose rows differ, `reshaped`); `Move` (a field that moved: its table, its row's key values or position, none in a one-row table, its column, production's value and staging's; a row only one side holds is a `Move` with no column, its values saying which side holds it), `moved` (the rule a value moved by) and `compare_quote`, which answers a `Comparison` (two answers' tables on the shared fields: the moves, or why the quote no longer compares); `QuoteImpact` (a quote by reference and option, its outcome, "moved", "unchanged", "no_longer_compares", "refused", "failed", "baseline_failed" or "not_compared", its moves and its reason, never the app's own words), `Released` (an app's environment, release and commit) and `ImpactReport` (the two `Released`, the sample asked for, how many were listed, the `SharedFields`, each `QuoteImpact`, whether time ran out, its verdict, "compared", "same_release", "no_baseline" or "stopped", with why, and `failed` and `passed`); `impact` (Control flow 36: answers the `ImpactReport`, an app that cannot be reached or used stopping the run rather than raising) and `render_report` (the report as Markdown). |
| `src/haute/_workbench_config.py` | The `[workbench]` table of `haute.toml`: `WORKBENCH_TOML_KEYS` (`enabled`, `app`, `store`), `APP_HOSTS` (where the underwriter app can run: `azure-container-apps`), the form folder's name in the pipeline folder (`workbench`), `WorkbenchError` (the base of every workbench problem the analyst fixes in the project), `WorkbenchConfigError`, the frozen `WorkbenchConfig` record (`enabled`, `project_root`, `app_host`, `app_edit_role` and `app_environments` (each hosted environment's `AppEnvironment`, its `name`, `resource_group` and `container_app`, empty while unset, from `[workbench.app.<name>]`, a name `[workbench.store]` declares, refused otherwise as is a key other than `APP_ENVIRONMENT_KEYS` or a value that is not text) from the nested `[workbench.app]` table through `_app_settings`, `store`, the quote store's environments, the form folder, the pipeline folder's `workbench/`, the pipeline folder being the folder of the project's pipeline file, and `form`, that folder's path from the project root as the status and every message name it) and `read_workbench_config` (the table read and checked from the project root's `haute.toml`: disabled without the file or the table; `form` refused with what replaced it); the store's environments: `StoreEnvironment` (a name, a provider, its `auth` and the names of its sign-in's `variables`, and `declared`, as a release records it), read by `store_environments` from `[workbench.store.<name>]` or a release through `_environment` (a name matching `ENVIRONMENT_NAME`, `dev` among them; a provider of `STORE_PROVIDERS`; for a database not in `FILE_PROVIDERS`, an `auth` of `STORE_SIGN_INS` with each of its keys naming a variable, `VARIABLE_NAME`, a managed identity's client_id_env optional (`_OPTIONAL_SIGN_IN_KEYS`) and, on a provider of `ROLE_NAMED`, an Entra ID sign-in's username_env, the role it takes; a location key refused naming the variable it belongs in, `_LOCATION_KEYS`, a secret's value naming the key that names its variable, `SECRET_KEYS`, and any other key by name), and the variables a host names its environment and its database with, `ENVIRONMENT_ENV`, `STORE_SERVER_ENV`, `STORE_DATABASE_ENV`, `STORE_SCHEMA_ENV` and `STORE_URL_ENV`. |
| `src/haute/_connections.py` | `connections.toml` at the project root (`CONNECTIONS_FILE`), the databases the editor and `haute app serve` reach on the analyst's machine: `read_connections` (each table of the file a `Connection` by name, in the file's order, none without the file; a name matching `ENVIRONMENT_NAME`; a provider of `STORE_PROVIDERS`; a file provider's `database` alone, the path from the project root, taking no sign-in; a server's `server`, `database` and optional `schema` (`_LOCATION_KEYS`), each text that may be empty, and its `auth` of `CONNECTION_SIGN_INS` (`password`, `service-principal`, `entra`) with each key naming a variable, `VARIABLE_NAME`, an Entra ID sign-in (`_ENTRA`) on a provider of `ROLE_NAMED` naming username_env too; `managed-identity` refused as a hosted app's, a key holding a secret's value or a connection string (`_REFUSED_KEYS`, `SECRET_KEYS` with connection_string and `url`) refused naming what to name instead, any other key by name, and a file that cannot be read, parsed or decoded, each a `ConnectionsError`, a `WorkbenchConfigError`, never saying the value refused, which may be a secret put in the wrong key); `Connection` (frozen: `name`, `provider`, `database`, `server`, `schema`, `auth` and the sign-in's `variables` by key, never a value; `where`, the file and its table as a message names them); and `project_environ` (the clone's `.env`, read afresh through python-dotenv's `dotenv_values`, under the process's own environment, which wins). |
| `src/haute/_workbench_app.py` | The underwriter app. `write_release` writes, beside a bundle already in a release directory (`MANIFEST_FILE` and its artifacts), the form rendered without its sample (`FORM_FILE`, written as rendered so its revision is the one recorded), the `Release` record (`RELEASE_FILE`: an id hashed from the form and the manifest without when and by whom it was built, the form's revision, the pipeline's name, the Haute version, when, the host, the edit role and the quote store's `environments` the project declares, each as its `declared` record holds it, which `store_environments` reads back) and the entry point `APP_ENTRY` (`app.py`); the id, from `_release_id`, hashes through `canonical_json` the form as shipped, the manifest's `_PRICING_KEYS` with the pipeline graph as it prices (`_as_priced`, each graph through `_graphs`: its nodes by id, label, type and config, sorted by id, and its edges by their ends, `_EDGE_ENDS`, so a node's place on the canvas, its description, an edge's canvas id and the pipeline's file name nothing), each artifact's bytes, the bundled utility code's (`_utility_hashes`) through `content_hash`, and the runtime the pipeline prices on (`write_release`'s `runtime`, the installed versions `build_release` reads through the deploy's `runtime_versions`, an image or not, a runtime that cannot be read refused as a `WorkbenchAppError`); `read_release` reads the three back, each checked where it is read, refusing as `WorkbenchAppError` an unreadable file, a `RELEASE_FILE` that is not an object of the `Release` fields, a `FORM_FILE` that is not a form (its first error's place and message) and a `MANIFEST_FILE` without the keys the app prices with (`_MANIFEST_KEYS`). `build_release` builds a project's release: the workbench enabled and its sheets saved, each input column's allowed values from a params file read again through `with_param_options` (a `ParamFileError` refused as a `WorkbenchAppError` naming the form, before the directory is touched), the directory checked by `_check_output` (refused when it is or holds the project or the working directory, when it is a file, and when it holds anything but an earlier release: `haute.toml`, `.git`, or files `_is_release` does not take, a release being a `RELEASE_FILE` that reads as a `Release` with `MANIFEST_FILE` and `app.py` beside it), the pipeline resolved from `haute.toml` through `resolve_config`, checked through `validate_deploy` with its test quotes, bundled through `write_bundle`, or, with `[deploy.container] base_image` set and pinned (`_validate_base_image_pinning`), through `prepare_build_directory` with `FORM_FILE` and `RELEASE_FILE` copied too and `STORE_DEPENDENCIES` installed with each declared environment's driver (`store_dependencies`, through the provider's packages and azure-identity for an Entra ID sign-in) and Microsoft's ODBC driver where an environment is `sqlserver`, the image's build context, into a folder beside the directory, then the release written there, its entry point in the scoring app's place, which passes `create_app` `hosted_store` for its release, for the store's gate to resolve as it checks, and the folder put in the directory's place by `_swap_in` (an earlier release moved aside first, put back when the new one cannot take its place, and removed after, each move through `src/haute/_file_ops.py::rename_directory` and the removal through `src/haute/_file_ops.py::remove_tree`, a removal that fails refused naming where it was moved); a build that fails removes its folder and leaves the earlier release as it was. `build_image` builds that context with Docker (`_check_docker_available`, `_docker_build`), refusing a release without a Dockerfile and naming the setting; `image_reference` names the image `haute app build --push` publishes, `<registry>/<name>:<short commit>` from `[deploy.container] registry` (refused, naming it, when empty), the project's name in lower case and the commit `git rev-parse --short HEAD` names in the project (refused when git names none), and `push_image` builds it, pushes it (`_docker_push`) and answers the reference by digest the registry holds for it (`_docker_repo_digest`, the container module's, read from what `docker image inspect` reports, refused when no digest names that repository). `create_app` serves a release, each graph's source file placed in the release so the pipeline imports the bundle's utility code, the project root set to it through `set_project_root` and the page from the `static_dir` given or Haute's own; its quotes are kept behind a `StoreGate` (`src/haute/_workbench_app_store.py`) over the `store` it is given, a `StoreLocation` (dev's, on the analyst's machine), a resolver of one (`hosted_store`: the environment `ENVIRONMENT_ENV` names, which the release must declare, at the database the host's variables locate, through `resolve_store`, and none while no environment is named, nothing of the store's loaded) or none, the gate opening the book through `open_book` for the release's form and pipeline in the store's environment, SQL schema and sign-in, tried once as the app is made, a closed gate logged and never raised, and its book closed in the app's lifespan; given a `local_principal`, `LocalTrustedHostMiddleware` with `DEFAULT_TRUSTED_HOSTS` is installed outside every other middleware, refusing (400) a request, `GET /health` included, for any other host or carrying forwarded metadata before it is signed in (a hosted app takes its front's forwarded headers); a middleware signs in every request but `GET /health` (the `local_principal` given, else, for `azure-container-apps`, the `EASY_AUTH_NAME` or `EASY_AUTH_ID` header) and answers 401 without one, worded for a release with no host, 403 to a write `_cross_site` judges another site's (its `sec-fetch-site` not in `_SAME_SITE`, or, without one, its `origin` not the host), and 401 naming `EASY_AUTH_PRINCIPAL` when `may_edit` cannot tell whether the principal may change quotes (a release's `edit_role` held, through `easy_auth_roles`, or the local principal unless `local_reader`), keeping the answer as the request's `can_edit`; `GET /api/app/release` (the release without its environments, the principal, `quotes`, the gate's state as `src/haute/_workbench_app_store.py::StoreGate.state` checks it on a worker thread, and whether the principal may change quotes), `GET /api/app/form` (the form and its revision) and `POST /api/app/price` (the body read within the deployed `/quote` route's limit, `deploy_quote_request_body_limit_bytes`, through `read_limited_json_body` before it is parsed: 413 past it, 400 for a malformed length and 422 for a body that is not JSON or not a `WorkbenchAppPriceRequest`; 409 for another form revision or release, 503 with the gate's refusal while it is closed (the gate's `book` on a worker thread, the answer its `QuoteStoreClosedError`'s `detail`), else `price_values`: 422 for an empty quote, else the request through `quote_request`, admitted through `admit_deploy_execution` and priced through `score_graph` as a `QuoteRequest` of one, its admission kept past the scoring, through rendering it with `render_output_document`, one document for the one quote however little it holds, each value in it that is not finite made null through `finite_document`, all in the app's own pricing pool, and released there, bounded by `PRICE_TIMEOUT_SECONDS`: the pool's threads and a bounded semaphore of its places are both sized by `int_env(PRICING_WORKERS_ENV, PRICING_WORKERS)` when the app is made, a place taken without waiting before the pricing is handed to the pool and given back by the pricing's own completion, never the request's, so one past its timeout keeps its place until it ends, 503 with `Retry-After: BUSY_RETRY_SECONDS` at once when no place is free, the pool shut down without waiting in the app's lifespan; 504 past the timeout, 503 refused admission, 507 over the memory limit, 422 a public contract error, and 500 "The pipeline failed on this quote: <reason>" for any other failure of the pipeline, logged, as the deployed pricing API answers one; `price_values` answers a `Pricing`, its tables through `priced_tables`, the request priced through `to_json_safe` and the document, which a save keeps), `GET /` and `APP_PAGES` (`APP_PAGE` from the app's own build, `_STATIC`, `src/haute/static/app/`, 503 while it is not built) and its assets; and the quote routes of `quote_router` over the gate, mounted whatever it holds. |
| `src/haute/_workbench_app_quotes.py` | The app's quote routes: `QuoteApp` (the release's id and sheets' revision, its form, the `StoreGate` and its `Pricer`, which answers a `Pricing`), `quote_model` (an `AppQuote` as a `WorkbenchAppQuote`, its details by column name and its source's label), `quote_router` (under `/api/app`, a quote named by its reference and option, the reference URL-encoded: the list; create (a creation repeated with its key answered first, through `replayed`; then 409 for another sheets' revision or release through `_drawn_for`; then `create`, unpriced, with the reference and the source the body names); open (`read`); save (a save repeated with its key answered first, through `replayed`, before the release is checked or anything priced; then `_drawn_for`; the values as the write keeps them, `as_written` with the book's `managed` at the one instant `now` gives, 422 when nothing is typed (`quote_request` with `_APP_SET_CELLS` untyped, so the app's details decide what the sheets show but are no typing); unpriced as it is, else its values' types checked through `input_rows` before they are priced again through the `Pricer`, the `Pricing`'s document kept as `PricedOutputs`); the changes through `change` (`assignee`, `archive`, `restore`, `final`, `amend`, each answering with the quote as its own transaction left it); the note (no version); the activity, through `_related` for the quotes it names, and the people), each quote answered through `answer` (its values, its outputs, the request its values price as, through `quote_request`, the rows it dropped, and, for a save, `_moved`: the prices it keeps against those the page showed, through `response_changes` at `PRICE_TOLERANCE`, one part in a billion, the quote's identity left out of both by `_without_identity`), `_body` (a body read within `deploy_quote_request_body_limit_bytes` through `read_limited_json_body`), `_editor` (the principal changing a quote, 403 to one who may only read them, before any create, save or change) `_book` (the gate's book on a worker thread as each route starts, a closed gate answered 503 with its `QuoteStoreClosedError`'s `detail`) and `_call` (the work on a worker thread through `run_in_threadpool`: `QuoteNotFoundError` 404, `StaleQuoteError` 409 with the quote as it is now, `QuoteNotReadyError` 409 with each field missing, `WriteSupersededError` 409 with when the write landed and who changed the quote when, `QuoteLockedError` 409, `ReferenceTakenError` 409 with the quote holding the reference, `QuoteValuesError` 422 with each cell, any other `QuoteStoreError` 422, each detail its message and error code). A store `_call` meets unreached (`src/haute/_quote_store/_store.py::unreached`) closes the gate as unreachable (`src/haute/_workbench_app_store.py::StoreGate.unreachable`, on a worker thread, as the gate may be held by a check) and is answered 503, quote_store_unreachable, logged by `logger` without the driver's text; any other statement the database refuses (a `DBAPIError` holding its statement) makes the gate check at once (`src/haute/_workbench_app_store.py::StoreGate.recheck`), answered 503 with the refusal's `detail` when the tables no longer fit and raised as it was when they do; and a gate closed meanwhile (`QuoteStoreClosedError`) is answered so too. |
| `src/haute/_workbench_app_store.py` | The app's quote store as it stands: `StoreGate` (over an opener, which `src/haute/_workbench_app.py::create_app` makes from the store it is given and the release: `book` answers the open `QuoteBook`, or raises `QuoteStoreClosedError`; while closed it calls the opener again at most once every `RECHECK_SECONDS`, 10 on its `clock`, a request in between raising the last check's refusal, one check at a time under its lock; a check that opens the book opens the gate, one raising a `QuoteStoreError` (the store's own refusals, a location that cannot be resolved and a server not reached among them, each keeping its error code and differences) or a driver's error before any statement (`unreached`, closed as quote_store_unreachable) closes it with that refusal, through `_closed_by`, and any other error is raised as it was; `recheck` closes the book and checks at once, answering the refusal or None; `unreachable` closes it as quote_store_unreachable (`UNREACHABLE`), its next check due after the bound; `state` what the release route answers, checking as `book` does; `close` closes the book); `QuoteStoreClosedError` (a `WorkbenchError` carrying the refusal's `code`, message and `differences`, its `detail` what a route answers it with); and `no_environment`, the opener of an app started with no environment named, its refusal `NO_ENVIRONMENT`, naming `ENVIRONMENT_ENV`. It answers no request itself, and imports the store's modules only as it checks. |
| `src/haute/_quote_store/__init__.py` | The quote store's surface: `open_book` (the book of an environment's database that holds a form's tables, in a SQL schema and signed in as a `SignIn` says, opened through `open_engine`, a database not reached naming named_by, `HOST_VARIABLES` unless given), `offline_plan` and `render_sql` (the SQL Haute writes) with their `Plan` and `Step`, `open_engine`, `store_url`, `HOST_VARIABLES`, `StoreLocation`, `SignIn`, `resolve_store` and `connected_store`; the book's `QuoteBook` with `AppQuote`, `OpenQuote`, `PricedOutputs`, `Source`, `QuotePage`, `ActivityEntry`, `RelatedQuote` and `QuoteActivity`; `input_rows`, `details_of`, `with_details`, `without_app_details`, `as_written`, `check_reference` and `identity_label`; and the errors `QuoteStoreError`, `QuoteStoreMismatchError`, `QuoteNotFoundError`, `StaleQuoteError`, `QuoteLockedError`, `QuoteNotReadyError`, `WriteSupersededError`, `ReferenceTakenError`, `QuoteValuesError` with its `MisfitCell`s, `QuoteStoreEnvironmentError` and `QuoteStoreUnreachableError`. Imported where a store is used, never on the scorer's import path. |
| `src/haute/_quote_store/_schema.py` | The store's tables, derived from the form: `store_schema` (a `StoreSchema` in one `MetaData` of the SQL schema it is given: a `StoredTable` for each of the form's tables in the schema's order, found by `by_id`, `details` and `of_role`, and Haute's `ACTIVITY_TABLE`), each table through `_stored` (the form's name, its columns the form's in order, each typed through `_TYPES` (`column_type` for one type), the identity bounded by `REFERENCE_LENGTH`, the quote_details columns the app keys or indexes bounded through `_BOUNDED_DETAILS` and those it sets on every write, `_SET_DETAILS`, never null, every other column of the project's nullable; quote_row on a many-row table; the store's own columns, `STORE_COLUMNS`, on quote_details; the identity, with quote_row in a many-row table, the primary key, and every table but quote_details referring by it to its quote's row; each constraint and index named by `_named` from the table's stable id, so a rename keeps it) and `_activity_table` (haute_activity: a row for each thing done to a quote, its operation key unique, indexed by the quote); `structure` (what of the form decides the tables, by stable id: each table's name, role and rows and each column's name, type and whether it is the index, what a plan is made between); `store_columns` (each table's columns the store keeps that the form does not, each by its name and its `StoreColumnKind` through `_kind`); `UtcDateTime`, a moment held in UTC on every engine, DATETIME2(6) on SQL Server; the types pinned on SQL Server, so offline SQL renders as the connected engine's does: text of any length NVARCHAR(max) (`_text`), a Decimal FLOAT(53) (`_double`), a Date DATE (`_date`), and the identity and Haute's other exactly compared texts NVARCHAR of twice their length under `EXACT_COLLATION` (`_exact`); haute_activity's activity_id an identity column, which takes a value with no grant of its own; the bounds `PRINCIPAL_LENGTH`, `RELEASE_LENGTH`, `STAGE_LENGTH`, `RELATION_LENGTH`, `ACTION_LENGTH`, `OPERATION_LENGTH` and `NAME_LENGTH`. |
| `src/haute/_quote_store/_migrate.py` | The SQL Haute writes for the people who own a store's database: `offline_plan` (the plan from one form's tables to another's, or to a form's from none, with no database) and `plan` (pure: the `Plan` from the form's structure to the structure the tables have, an earlier form's or a database's catalogue read into one by `src/haute/_quote_store/_compare.py`, given the quotes holding values in each table and column `wanted_counts` names, or none, by stable id, through `_plan` and `_plan_columns`, a count a counted plan needs and was not given a fault, `_count`, and, for a provider, its `name_problems` among the refusals: what the form removed kept (`keep table`, `keep column`), each type, rows or role change a step whatever it holds (`retype column`, `reshape table`), a table or column taking a removed one's name refused, and, from none, `activity`, haute_activity made; each `Step` a `StepKind`), `_emit` (the plan's DDL through Alembic's `Operations`, offline, each step in the store's SQL schema: each rename in two phases through `_named`'s temporary names, tables before columns, then what is made, a retype in place through `_retype_in_place` (the type before the database's own name for it, sql_type, where the plan was made from a catalogue, else the store's for the earlier form's, `_structure_column`), a reshape's note (`_reshape_note`) and a kept table's or column's commented DROP (`_removed_note`), haute_activity last when the plan makes it; `_column`) and `render_sql` (a plan as the SQL the people who own a database run: Alembic's offline rendering in the dialect `PROVIDER_DIALECTS` gives the provider, every value written in, each name in the SQL schema given, the whole of it one transaction, `_SCRIPT_TRANSACTIONS`, SQL Server's in one batch under XACT_ABORT and a TRY ... CATCH rolling back an error sp_rename raises and goes on from; headed by each refusal (`Not written`) and each of the plan's `notes` (`Not changed here`); a retype as the engine's ALTER in place, PostgreSQL's USING <column>::<type>, with a note that a value which does not convert stops it, and on SQLite a note alone; a change of rows or role and each name too long for the engine as a note, each line of a note its own comment (`_commented`), so no name ends one, and each note on a table or column a counted plan counted quotes in saying how many, `held`; a kept table or column as its DROP commented out, naming the table as the script's renames leave it; nothing of Haute's beyond the tables). |
| `src/haute/_quote_store/_providers.py` | The engines the store runs on: `PROVIDERS`, each a `Provider` (its SQLAlchemy dialect and driver, the longest name it keeps whole and whether that counts bytes, what an Entra ID token is asked for, the distributions its driver needs in a release's image): `sqlite`, `sqlserver` through pyodbc and `SQLSERVER_DRIVER`, and `postgresql` through psycopg; `provider_of` (a URL's provider, a dialect the store does not run on or a driver its provider does not connect through refused); `server_url` (a server's database as a URL holding no sign-in: on SQL Server an ODBC connection string, odbc_connect, of the driver, the server as its clients take it through `_odbc_value`, the database and Encrypt=yes; on PostgreSQL the host, its port and the database); `SignIn` (`managed-identity`, `service-principal`, `password` or `entra`, with what each reads, its secret never shown, and its `credential` from azure-identity for an Entra ID sign-in, `ENTRA_SIGN_INS`: `entra`'s the analyst's own `InteractiveBrowserCredential`, made once for the process by `_analyst_credential`, so a sign-in in the browser is asked for once); `connect_hook` (SQLAlchemy's do_connect hook signing each new connection in: on SQL Server the integrated sign-in and any user SQLAlchemy put in the ODBC string taken out through `_without`, then an Entra ID token asked of the credential as the connection is made, packed by `_access_token` into `SQL_COPT_SS_ACCESS_TOKEN`, or the user and password added; on PostgreSQL the role and the token or password as its connect arguments) and `make_server_engine` (an engine with that hook and pool_pre_ping); `name_problems` (each name longer than a provider keeps whole, PostgreSQL's 63 bytes); and `missing_privileges` (each privilege a started app's sign-in lacks on the store's tables, `_privileges`: quote_details read, written and changed, the form's other tables read, written and emptied and haute_activity read and written; asked of SQL Server through HAS_PERMS_BY_NAME and of PostgreSQL through has_table_privilege, each name quoted by `_quoted` in the store's SQL schema; none on SQLite). |
| `src/haute/_quote_store/_rows.py` | A quote's values as the store's rows and back: `input_rows` (each input table's rows from the values through `quote_request_rows`, so the cells the sheets hide are left out and a many-row table's rows keep their numbers on the sheet as quote_row, each value read by `json_value` as a request is, the identity on every row; any value its column's type does not hold refused as one `QuoteValuesError` naming each `MisfitCell` by its table's and column's labels, `_label`), `output_rows` (each output table's rows from the pricing's document, a value that is not finite null through `_finite`, the identity on every row, a many-row table's rows numbered in the pricing's order), `rendered_outputs` (the stored outputs rendered as the pricing rendered them, through `prune_document` and `priced_tables`) and `cells` (the stored rows as the sheets' values through `_row_cells` and `_cell`: an Integer its digits, a Decimal the shortest text that reads back as it, a Date its ISO text and true a tick, false and null no cell; a many-row table's rows at their quote_row places, a gap a blank row; a preset row whose value is no longer allowed left out and named), with the types `Values`, `Rows` and `PricedTables`. |
| `src/haute/_quote_store/_store.py` | The store's database: `store_url` (a SQLite path as a SQLAlchemy URL); `make_engine` (on a server, `make_server_engine` signed in as the `SignIn` given; on SQLite, SQLAlchemy's recipe for pysqlite: the driver's own transactions turned off as each connection is made, `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=DELETE` on it, a mode other than `delete` refused as `QuoteStoreError`, so a file left in write-ahead logging is put back to a rollback journal and one another connection holds that way is refused before anything is written; BEGIN on each transaction's start, BEGIN IMMEDIATE on one `writing` begins, which marks it `_WRITE`; a SQLite older than `SQLITE_MINIMUM` refused); `writing` (a write transaction, committed when its block ends and rolled back when it raises, DDL with it); `unreached` (a driver's error raised before any statement: a connection a server refused or never made; or Microsoft Entra ID refusing a sign-in's token as a connection is made, `_entra_refused`, a credential that cannot sign in or a browser sign-in left or cancelled) and `reaching` (such an error from a server said as a `QuoteStoreUnreachableError` naming what places the database and signs it in, named_by, the host's variables (`HOST_VARIABLES`) unless given, the driver's own text, which names where the database is and who signs in, dropped); `open_engine` (an engine on an environment's database, signed in as it is given, holding the form's tables in its SQL schema as its own catalogue holds them, read by `src/haute/_quote_store/_catalogue.py::read_catalogue` for the form's tables and haute_activity and judged by `_catalogue_differences`: each table and column the store uses there, of its kind, taking a null where the store's does and none where it does not, on SQL Server each exact text under the collation its type pins (`src/haute/_quote_store/_catalogue.py::column_problems`), keyed as the store keys it and, but for quote_details, referring to its quote's row (`::table_problems`), and every other column of those tables taking a null; then each privilege `missing_privileges` finds the sign-in lacks; refused otherwise as a `QuoteStoreMismatchError` naming each difference and what resolves it, `_resolve`: changing the tables in SQL, from the SQL the schema pane writes, in every environment alike); the errors `QuoteStoreError` and its `QuoteNotFoundError`, `StaleQuoteError`, `QuoteLockedError`, `QuoteNotReadyError` (with `missing`), `WriteSupersededError` (with `landed_at`, `changed_by` and `changed_at`), `ReferenceTakenError` (with the quote holding the reference), `QuoteValuesError` (with its `MisfitCell`s), `QuoteStoreEnvironmentError` (an environment, a location or a sign-in that cannot be resolved), its `QuoteStoreUnreachableError`, and `QuoteStoreMismatchError` (with its `differences`) |
| `src/haute/_quote_store/_catalogue.py` | A database's tables as the store finds them: `read_catalogue` (through SQLAlchemy's Inspector on a connection, the tables of a SQL schema it is given, those named or all it holds, so an incomplete table of the form's is compared rather than taken for missing, each a `CatalogueTable` of its `CatalogueColumn`s (name, kind by `kind_of`, the first of `_KINDS` its type is, its type as the database names it through `_type_name`, a type it cannot render by its kind, whether it takes a null and its collation), its primary key's columns and whether it refers by the identity to quote_details; with `count`, through `_counted`, one statement per table holding both columns of the quote's `IDENTITY`, None where a table lacks either, the quotes holding a row in it and a value in each column, quotes not rows, every quote's in the identity's), a `Catalogue` holding names, kinds and counts alone, never a value; `column_problems` (each way a column differs from the store's, by kind, `type`, `collation` or `null`, and what follows its name in a message) and `table_problems` (`key` and `refers`), which the app's check and the comparison share. |
| `src/haute/_quote_store/_compare.py` | The schema pane's Compare: `read_store_catalogue` (the store's tables of the database at a `StoreLocation` with their counts, under its sign-in, nothing written: through `_reading_engine`, a SQLite file opened read only, never made where it is missing nor its journal changed; a server not reached said by `reaching`, with `_WAKING`, a database that pauses when idle refusing the first connection while it wakes, and any other refusal of the database's said as a `QuoteStoreEnvironmentError` naming the location's named_by, never what the driver said) and `compare` (a form holding quote_details against a `Catalogue`, on a provider, pure: each of the form's tables the database's of its name, else one the default branch's form names by its id and the database holds and the form does not; within a table each column the database's of its name, else one the analyst paired (`Rename`, taken first, a pairing that no longer applies left out of what is answered), else one the default branch's form names by its id, the identity never renamed; the database's tables read into the structure a `plan` is made from, each column typed as the form's type its kind is held as (`_FORM_TYPES`), else its kind, with the database's type name, sql_type, and a many-row table one holding quote_row, through `_match_table` and `_live_column`; each table of the store's (holding both columns of `IDENTITY`) and each column of the form's tables the form does not have an `Extra` and planned under a `database:` id, quote_row and the store's own columns never extras; the counts given to the plan unless a table or column was read uncounted, a table lacking an identity column marked `UNCOUNTED` so its notes say why its quotes were not counted; each `Difference` by the form's table and column ids (`missing`, `renamed`, `type`, `rows`, and, noted in the SQL as no statement changes them, `null`, `key`, `refers`, `collation` and `store`, the store's own columns and haute_activity's, through `_Matcher`), haute_activity missing a difference of no table that the plan makes; a `Comparison` of the differences, extras, the pairings taken and the plan, which `matches` while there is no difference); `_dialect`, a dialect with no driver loaded, what a type's collation is read from. |
| `src/haute/_quote_store/_environment.py` | Where a quote store is: for a hosted app `resolve_store` (the environment named, one of those declared, else a `QuoteStoreEnvironmentError` naming those that are; its database from the host's variables through `_given`, `STORE_URL_ENV` or the parts `STORE_SERVER_ENV`, `STORE_DATABASE_ENV` and `STORE_SCHEMA_ENV`, both refused and neither refused; a provider of `FILE_PROVIDERS` taking its file's path from `STORE_DATABASE_ENV` through `store_url`, or a URL of its own provider that its engine can open, naming no server, port or sign-in (`_file_location_url`), and no server or schema; a server provider of `PROVIDERS` taking `STORE_SERVER_ENV` and `STORE_DATABASE_ENV`, or a URL, and optionally the schema), `StoreLocation` (the environment, the database's URL and the SQL schema, which its `repr` leaves out, the sign-in, `SignIn`, and named_by, what places it and signs it in as a database not reached is said, `HOST_VARIABLES` for a host's), no message naming a server, a database or a URL; on the analyst's machine `connected_store` (an environment the project declares at the database its connection names, `connection_of`: the environment declared, else naming those that are; `connections.toml` holding a connection of its name and of its provider; a file's path from the project root through `store_url`, a server's URL by `server_url` and its schema, a server or database left empty refused naming the key, `_filled`; named_by the connection and the variables its sign-in reads); on a server, a URL given whole checked through `_server_location_url` (no sign-in in it, by `src/haute/_credential_security.py::validate_credential_free_uri` and, in an ODBC string, `_ODBC_SIGN_IN`; its provider's dialect and driver, `provider_of`), the parts made a URL by `server_url`, and the sign-in read by `_sign_in` from the variables the environment names, as a connection's is, through `_read_sign_in`, one holding nothing refused naming it, `_HELD`, and, for a connection, `.env`. |
| `src/haute/_quote_store/_book.py` | The underwriter app's book of quotes: `QuoteBook` over a store's engine and the `StoreSchema` of one form, written by an app running the Haute version it was opened for: `now` (the clock, read once in the route before a save is priced), `managed` (the details the app sets on a write at an instant by an actor: the identity, assignee, archived and stage as the quote has them, when it was made and by whom from its row, or the instant and the actor for a new one, when it was changed and by whom, each date the UTC date of its time through `_day`, and the Haute version), `get` (the quote's quote_details row through `_get` and `_quote`, with who marked it final and who archived it and when, from its newest `finalised` and `archived` activity rows, `_last`), `read` (an `OpenQuote`: the quote, its values and its outputs read together through `_open`, `_rows` and `cells`, read again when its version moved during the read, `READS` times at most), `page` (a list a page at a time, its counts and the next page's cursor, through `_view_filter`, `_search_filter` (a word matched, whatever its case, against references and against the name, insured and broker; a word `ABC-1/2`, `_REFERENCE_OPTION`, matching that reference's option alone), `_cursor_filter`, `_order`, `_sort_key` and `_encode_cursor`, the cursor holding the sort key as the database made it), `activity` (a `QuoteActivity`: the quote's haute_activity rows in order, the quote it was made from and those made from it), `people`, `replayed` (the quote a write's operation key wrote, read whole, through `_replayed`, when that write landed: a save the quote has moved on from since refused as `WriteSupersededError`, the key of another person, quote or kind of write refused), `create` (through `_insert`: an alternative under its source's reference and the next option, `_next_option` holding the source's option 1 first; any other quote under the reference keyed, checked by `check_reference` and refused by `_refuse_taken` when another quote holds it, and option 1; its quote_details row at version 1 with the store's own columns, its input rows and its `created` activity row through `_record`; a key repeated answering the quote it made as it stands, and a creation that lost a race to its own key's or the reference's first write answered so), `save` (one compare-and-swap on the quote's row over its version, a draft not archived, moving the version on with when and by whom it was saved and the release that priced it, every other table's rows deleted and inserted again through `_replace_rows`, the outputs from `PricedOutputs` or none unpriced, and the `saved` activity row, answering the quote as written; a swap that moved no row looks the key up again and only then says why through `_refuse_change`: gone, moved on (`StaleQuoteError` naming who did what and when, `_DONE`), archived or final), `assign`, `archive`, `restore`, `finalise` (a draft priced on the running release with a value in every required column, `_missing`, else `QuoteNotReadyError`) and `amend` (a final quote back to draft, given a reason), each one compare-and-swap through `_change` with its activity row, `set_note` (the handover note on the row with who wrote it and when, moving no version, so it never conflicts; refused on an archived quote) and `close`; a quote name past `NAME_LENGTH` refused through `_check_name`; `details_of`, `with_details`, `without_app_details` (the quote_details cells the app sets taken out of what a page sends, unread) and `as_written` (values as a write keeps and prices them, the app's details set over them); `identity_label` (a reference and option as the app shows them, `ABC-1/2`); `check_reference` (a keyed reference trimmed, in NFC, 1 to `REFERENCE_LENGTH` characters, no `/`, no control or format character, and not `.` or `..`, refused saying why; two references are one only when they are the same, case included); the types `Stage`, `Relation`, `View`, `Sort`, `Action`, `Source`, `PricedOutputs`, `AppQuote` (its `label`, its `title` the quote name, else the insured, else the label, and `priced`), `OpenQuote`, `QuotePage`, `ActivityEntry`, `RelatedQuote` and `QuoteActivity`; `PAGE_SIZE`, `MAX_PAGE_SIZE`, `MAX_PEOPLE`. |
| `src/haute/_workbench_form.py` | The form's canonical shape and its text as one document, which the release ships and the quote store keeps; the form folder's files are read and written by the folder module (`_workbench_folder.py`, Control flow 16 and 19), which builds and takes these models: the pydantic models `FormSpec`, `FormSchema`, `SchemaTable`, `SchemaColumn` (its `options_source` an `OptionsSource`, the `file` and `column` of the params file its `options` were read from, None for none, or for values typed in, which only an older form holds, and its `row_per_value`, whether it gives a many-row input table a row for each of them), `Page` (its widgets held inside its `width` by an after-validator), `Section` (its widgets held inside its height by an after-validator), `TableInputWidget`, `CollectionWidget` (its `spacers` held in order and within its fields by an after-validator, and its `column_looks` checked as a Table's are, `_check_column_looks` serving both), `ChartWidget`, `HeadingWidget`, `ImportWidget` (every field but `show_when` written, its `source`'s too; its `source` by `format` a `CsvImport`, an `ExcelImport`, its `header_row` 1 to `HEADER_ROW_MAX` (100), or a `SqlImport`, whose `parameters` are `QueryParameter`s, each name an identifier (`PARAMETER_NAME`) held once by an after-validator; its `mode`, an `ImportMode`; and its `column_names`, `ColumnName`s, each column once and each name not blank, none without a `table`, held by an after-validator), `ExportWidget` (every field but `show_when` written, its `target`'s too; its `tables` held once each and its `target` by `format` a `CsvExport` or an `ExcelExport`, each a `_FileExport` with its `file_name`, `headers` and `values`, or a `SqlExport`, whose `table_names` are `TableName`s, each a table it exports, once, its name not blank, held by an after-validator), `FieldRef`, `ShowWhen`, `ConditionGroup`, `Condition`, `RowTest`, `RowsCondition`, `RowCount` and `RowTotal` (every field written, unknown fields refused, a condition's kind told by its fields through `_condition_kind`), `ConditionTest` (a condition's sixteen tests), `DAYS_MAX` (the most days a Date's test against another Date adds or takes away, 36,500), `CountTest` (a count's or a total's six), `TotalKind` (a total's four), `Match` (how many of a `show_when`'s or a group's conditions must hold: `all`, `any` or at least a whole number of them), `ChartType` and `ChartLegend` (how a Chart draws, and where its legend sits), `HeadingSize` and `HeadingAlign` (how large a Heading's text is, and where it sits across its width), Haute's quote_details table (`QUOTE_DETAILS_ID`, `QUOTE_DETAILS_COLUMNS`, each column's id, name and type in order, `QUOTE_DETAILS_IDS`, `QUOTE_DETAILS_MANAGED`, the columns the app sets, and `QUOTE_DETAILS_STORE_ONLY`, those of them no request holds (the stage, the assignee, whether archived, when and by whom the quote was last changed and the Haute version), every other quote_details column in every request whatever the sheets hide, `quote_details_table`, `with_quote_details`, a form holding it first, added when it has none, `QUOTE_DETAILS_NAMED_BEFORE`, the names an earlier Haute gave its columns, which `_named_today` gives today's, `QUOTE_DETAILS_RETIRED`, the columns an earlier Haute made that it makes no more, `FormSpec`'s before-validator `_brings_quote_details_up_to_date` bringing a table as an earlier Haute wrote it to today's by id, each column renamed, the retired dropped, those held kept with what the project gave them, the missing added and all in today's order, a column of another id left after them, and two of one id the whole left as it was, for the checks to refuse, the caller's data left as it was, and `_check_quote_details`, which `FormSpec` runs on any table of its id or name, refusing with `QUOTE_DETAILS_REFUSAL` another table under the name, its structure changed (a key, an index or a column giving its rows included), or a rule (allowed values from a params file included) or a default on a column the app sets, refused), the quote's identity on every other table (`IDENTITY_COLUMNS`, `IDENTITY_IDS`, `IDENTITY_NAMES`; `FormSpec`'s before-validator `_gives_every_table_the_quote_identity` gives each table but quote_details the two at its head unless it holds a column of either id or name, the caller's data left as it was; `_check_identity`, run on every other table, refusing identity columns that are not its first two as Haute makes them, with nothing of their own set; `identity_clashes`, each table's own column under either name, said with what resolves it, which deploy's form check and so `haute app build` refuse), `WorkbenchFormError`, `FormDocument` (a form with its folder's revision and its `repairs`, what reading it grew), `blank_form` (one blank sheet, sheet_0001 "Sheet 1", `BLANK_SHEET_WIDTH` (1280) wide, holding one section, section_0001 "Section 1", `BLANK_SECTION_HEIGHT` (560) tall, named after the project, its schema Haute's quote_details table alone), `render_form` (the form as one document's text: every field, indented; what a release ships and the quote store keeps) and `form_revision` (the content hash of that text's bytes, a release's and a stored form's revision). |
| `src/haute/_workbench_folder.py` | The form folder: the form as the files of the pipeline folder's `workbench/` (Control flow 16 and 19). Its files' names, `INDEX_FILE` (`form.json`), `SCHEMA_DIR`, `SHEETS_DIR`, `SAMPLE_DIR` and `LAYOUT_SUFFIX`, and the folder format, `FOLDER_VERSION` (1); `sheet_name` (a sheet's title made its file's name: lower case, each run of characters other than ASCII letters and digits one underscore, none at either end, `SHEET_NAME_MAX` (64) at most); `_Names` (the schema's tables and columns by id and by name, each reference turned either way, a name the schema does not hold read as an unresolved id, `UNRESOLVED_MARK` and the name, which `is_unresolved` tells). Writing: `folder_name_problems` (a table name that is no identifier, one Windows keeps or another table's whatever its case; a column name that is no identifier or its table's twice; a sheet title that makes no name, a reserved one or another sheet's; a file's path past `PATH_MAX` (240) characters; and each name the quote store cannot keep, `store_name_problems`), `FormFolderNameError` (each in `problems`, which the save answers 422), `folder_files` (every file of a form by its path from the folder, as bytes: the index, a schema file per table, `schema/quote_details.json` holding only what the project sets on Haute's table, a sheet file and a layout file per sheet and a sample file per input table with rows, refusing a reference or a sample value under an id the schema does not hold that is no unresolved one, and a sample row that would hold a name twice: a column's own value and one kept under the name the column has taken since) and `render_folder_file` (through `_dumps`: two spaces' indent, a list or a mapping of numbers on one line, LF and a final newline). Reading: `form_folder_lock` (the form folder's `FileLock`, one per project in a process, `haute-form-locks/<hash>.lock` in the system temp folder, the hash the sha256 of the project root resolved and case-normalised), `FolderSnapshot` (`files`, `revision`), `folder_revision` (the content hash of the canonical JSON of each file's path and its bytes' content hash, sorted by path), `read_folder_unlocked` (every file read once between two listings that agree, `_READ_TRIES` (3) tries, a link or junction in the folder refused and a missing folder refused saying to restore it from version history), `read_folder` (the same under the lock), `folder_index` (the index, read first, then `_check_inventory`, refusing a file the index does not name, one it names that is missing, one named other than in lower case, a sample of no input table, a table or sheet named twice, and a `schema` that does not start with quote_details; a save takes it too before it deletes anything), `form_from_files` (the form the files hold, with what reading it grew: `folder_index` first, then each file read against its model, a key given twice refused (`_no_repeated_keys`), and the form's errors placed in the file that holds them (`_place`)) and `read_form_document` (a `FormDocument` of the folder as it is now). `unresolved_references`: each place the sheets name a table or column the schema does not hold, by sheet file and component, the table by its name and the column as `table.column`, which `haute app build` and deploy refuse. |
| `src/haute/_workbench_folder_files.py` | The form folder's file models (Key types): `_File` (unknown keys refused), `_derived` (a file model made from one of the form's models: its fields in its order but those dropped, each replaced one taking a name for an id, each required one losing its default), the conditions' files (`ConditionFile`, `RowTestFile`, `RowsConditionFile`, `RowCountFile`, `RowTotalFile`, `ConditionGroupFile`, `ShowWhenFile`), `QueryParameterFile`, a component's file per kind (`TableComponentFile`, `CollectionComponentFile`, `ChartComponentFile`, `HeadingComponentFile`, `ImportComponentFile`, `ExportComponentFile`, discriminated by `type` as `ComponentFile`) and their parts (`ColumnLookFile`, `SqlImportFile`, `ColumnNameFile`, `SqlExportFile`, `TableNameFile`), `SectionFile`, `SheetFile`, `RectFile`, `LayoutSectionFile`, `LayoutFile`, `ColumnFile`, `TableFile` (refusing two columns of one name, the quote's identity columns, which Haute gives every table, and a rule on an output table's column but Required, `INPUT_ONLY`), `QuoteDetailsColumnFile`, `QuoteDetailsFile` (Haute's columns in its order, each once, the settings the form refuses on a column the app sets refused here too: `DETAILS_ANY`, `DETAILS_TYPED`, `DETAILS_NAMES`, `DETAILS_IDS`), `IndexFile` (its `schema` the tables' names, `sheets` the sheets' file names) and `SAMPLE` (a sample file's rows, one at least); `NAME_PATTERN` (an identifier) and `PLACEMENT` (the fields a layout file holds instead). Under type checking the derived models are `Any`, which mypy cannot follow through `create_model`. |
| `src/haute/_workbench_params.py` | The params files a column's allowed values come from: `PARAMS_FOLDER` (`params`, in the pipeline folder's `files/`), `PARAM_VALUES_LIMIT` (5,000), `ParamFileError` (a `WorkbenchError`, so a route answers it 409 with its message), `ParamFile` (`columns`, the header, and `values`), `read_param_file` (the header of a CSV directly in the params folder, named by an ordinary name ending `.csv` and checked through `ordinary_path_problem`, a hard link read as its file, and, given a column, its distinct values: decoded from its bytes as UTF-8 (a byte order mark dropped) or else Windows-1252 (Excel's "CSV (Comma delimited)" on Windows), never as a CSV reader makes them out, which replaces a byte it cannot read, then read through `pl.read_csv` as text, no schema inferred, a column name holding U+FFFD (a character an earlier save lost) refused, trimmed, blanks left out, in the order the file first gives them; refused, saying why, for a project with no pipeline folder, a name that is not a CSV in the folder, a file missing, empty, in neither encoding or not read as a CSV, and a column not in its header, holding no values or more than the limit) and `with_param_options` (a form with each input column's `options` read again from the params file its source names, an output column's left as they are, a file or column that can no longer give them raised naming the column). |
| `src/haute/_workbench_tables.py` | The workbench's tables as the pipeline holds them: the models `WorkbenchTable` (`name`, `rows`, `columns`; `one_row`, `frame_schema`, `quote_dtype`) and `WorkbenchColumn` (`name`, `type`; `dtype`), `COLUMN_DTYPES`, `WorkbenchTablesError`, `parse_workbench_tables` (a node's `tables` config read strictly, naming the spot that is not as the workbench gives it), `check_tables` (every name held to `is_frame_label`, unique across tables whatever its case and within a table, and kept by the quote store as `store_name_problems` says: at most `STORE_NAME_LENGTH` characters, none starting haute_ but Haute's own, no table starting sqlite_, no two columns differing only in case and no many-row table's column named `QUOTE_ROW`), `port_tables` (the tables with a column), `quote_schema` (the schema of one quote holding tables with every column, a response's), `IDENTITY_SHAPE`, `carries_identity` (whether a node's table leads with the quote's reference and option, by place, name and type: every table but quote_details), `request_columns` (the columns a request gives a table, the identity left to quote_details), `request_schema` (the schema of one request holding tables, of those columns), `input_tables` and `output_tables` (the schema's tables of the role, in schema order, checked), `preset_column` (the column giving a many-row input table a row for each of its allowed values: the first so marked, not the index, with options; None for none), `preset_rows` (a table's rows as that column gives them, the rule `presetRows` in `frontend/src/utils/sheetValues.ts` holds too), `sample_quote` (the sample typed while building as one request holds it, a table's rows as `preset_rows` reads them and nothing while nothing but their values is typed, the defaults a new quote starts with not typing either (`_starting`), nor the cells it is told are untyped, the app's own details, which still decide what the sheets show, as `quoteEligible` holds in the view, each value as its column's type holds it (`_typed`: whole digits in an int column read exactly, through `int`, however large), the values the sheets hide left out but quote_details', held whole without the details no request holds, `QUOTE_DETAILS_STORE_ONLY`, through `_quoted`), `quote_request` (the request `sample_quote` reads from values typed into a form, the underwriter app's), `quote_request_rows` (that request whatever is typed, with each many-row table's rows' numbers on the sheet, which the quote store keeps as quote_row), `finite_document` (a priced quote's document with each value that is not finite null, pruned through `prune_document` as a response is) and `priced_tables` (that document as a pricing answers its tables, each table's rows by name, JSON-safe) and `workbench_tables`, which gathers the three as a `WorkbenchTables`. |
| `src/haute/_workbench_visibility.py` | Which sheets, sections, components and fields show for values typed into the sheets, and so which input columns a quote leaves out: `Visibility` (`pages`, `sections` and `widgets`, the ids of the sheets, sections and components that show, `fields`, the fields that show of the Tables and Collections that show, each a component id, a table id and a column id, and `hidden`, the input columns left out, each a table id and a column id), `visibility(spec, rows)` (everything shown, then anything whose conditions fail hidden, a section with its sheet, a component with its section and a field with its component, a field's conditions its look's `show_when`, round after round, until nothing more hides, the fields of Tables and Collections alone counting as on a component, each with its look's `show_when`, through `_entered`, since a Chart takes nothing typed and a Heading has no fields, and of those the fields that show alone) and `condition_fits` (whether a condition can hold as written: a `Condition`'s column in a one-row table, input or output (`_one_row_column`), an output's with no `field`, a test its type takes, the values its test takes, each one its column could hold, or, given a `field` (`_other_column`, a one-row input table's column), a test of one value with no values typed and a field of the column's type, `days` only so for a Date (`_fits`, given the other column); a condition on a list's rows, `RowsCondition`, `RowCount` or `RowTotal`, on a many-row table, input or output (`_list_table`), each of its tests of a row so on a column of the table other than the index, an output table's without a `field`, and a total's column a number column other than the index and its value a number), a group holding as its `match` says (`_matches`), a list's rows passing its tests read by `_passing_rows` and a total by `_total`, and a many-row table's rows read by `_typed_rows`, the rows `preset_rows` gives with anything but the index typed in them. `frontend/src/utils/sheetVisibility.ts` applies the same rules in the view, both held to `frontend/src/utils/__tests__/fixtures/sheetVisibility.json`. |
| `src/haute/_workbench_nodes.py` | The workbench nodes' tables, given from the form wherever the pipeline runs (Control flow 7 and 9): `WORKBENCH_NODE_TYPES`, `read_project_form` (the form a project's folder holds, read through `read_form_document`, refused naming the node while the workbench is not enabled), `project_form_tables` (its tables, sample and response tables, `workbench_tables`), `given_config` (a node's config with what it is given: a Workbench Input its `tables` and `sample`, a Workbench Output its `tables`, as the tables route answers them, over anything it held), `form_owner` (the node a form is read for, the Workbench Input first; None for a graph without a workbench node, which reads no form), `with_tables` (a graph and its submodels' definitions with every workbench node given the tables, copied, never changed), `with_form_tables` (the same from a project's folder, or from a form a request carries) and `connection_errors` (each connection out of a Workbench Input or into a Workbench Output on a table it was not given, naming it). |
| `src/haute/_workbench_input.py` | The Workbench Input's reading: `WorkbenchInputError`, `workbench_input_tables` (its ports from the tables its config is given: the tables with a column, none refused saying what to add), `workbench_table_labels`, `read_quote` (each table's frame from a quote, typed as declared, a part that is not of its shape or a value that is not of its type refused naming the spot, each value checked by `_json_values.json_value`, the rule a flat Quote Input's request shares: an integer within Int64's range, a float the column holds exactly; with quote_details among the tables, the quote's reference and option read from it, `_identity`, refused when missing, blank or an option below 1, and put on every row of each table that `carries_identity`, `_identified`, a row giving either itself refused naming the spot), `STAND_IN_REFERENCE` and `STAND_IN_OPTION` (PREVIEW and 1), `with_stand_in` (a quote with the stand-in identity in its quote_details), `null_quote` (one quote with nothing filled in, one row of nulls per table of the columns a request gives it, under the stand-in), `workbench_table_frames` (its frames without a request: the sample under the stand-in read whole through `read_quote` as a request is, a many-row table it does not hold no rows and a one-row table its row of nulls, the `null_quote` while there is no sample, then cut to the demanded ports and columns) and `workbench_request_frames` (a deployed request's frames: its one quote, from the records as sent, read through `read_quote`). |
| `src/haute/_workbench_output.py` | The Workbench Output's tables and response: `WorkbenchOutputError`, `workbench_output_tables` (the tables its config is given, read through `parse_workbench_tables`, the ports those with a column, none refused saying what to add), `column_fits` (whether a frame column's dtype fills a column of a declared type), `frame_columns` (the columns of a table its frame fills: all but the identity of a table that `carries_identity`), `WorkbenchOutputTables` (its result: its tables' frames by name, carrying the tables they fill), `fill_workbench_tables` (the tables filled from the frames by port, each column from the frame's column of its name cast to its declared type, nulls for a column missing or of a type that does not fit, for the identity of a table that `carries_identity` whatever the frame holds, and for a table nothing is connected to, a connected one-row table checked for its one row when it is read), `quote_identity` (the quote's reference and option as a Workbench Input's frames by table hold them in quote_details, lazily; None without), `workbench_response` (the response for one quote: each table under its name, built from the tables as a quote holds them, every row of a table carrying the identity given the one-row identity frame's, null without one) and `as_response` (a response node's result as the frame a request is answered with, given the identity: deployed scoring passes the Workbench Input's quote tables', a pipeline's run its source's and its score the frames it was given). |
| `src/haute/routes/workbench.py` | `router`, the workbench routes under `/api/workbench`: `workbench_status` (`GET /api/workbench`), `get_workbench_tables` (`GET /api/workbench/tables`) and `post_workbench_tables` (`POST /api/workbench/tables`, the same of a form in the request), both through `_tables_response` (the tables of the form holding quote_details, `with_quote_details`, and the response tables, checked as the pipeline takes them, a `WorkbenchTablesError` answered as the public contract 422, and the columns the quote store keeps in each table that the form does not, through `store_columns`), `get_workbench_form` (`GET /api/workbench/form`, the form with its folder's revision, read through `_read_document` under `repository_mutation`) and `put_workbench_form` (`PUT /api/workbench/form`, through `_save_form` as one `project_write` (the save lock and the repository's lock): the form holding quote_details rendered by `folder_files`, a `FormFolderNameError` answered 422, and the files read back through `form_from_files`, files that would not read back as a form answered 422 too; then, under `form_folder_lock`, `StaleDocumentRevisionError` from `_stale` when the folder is missing or its revision is not the base revision quoted, answered as the pipeline save answers it; 409 with nothing written when the folder holds a file that is not the form's (`folder_index`) or a path it would write or delete is no ordinary file of the project's (`ordinary_path_problem`, worded by `PATH_PROBLEMS`); else each file whose bytes changed staged as a write and each the form no longer has as a delete, all rolled back and answered 409 when one fails, and every written and deleted path captured on the save ledger through the pipeline save's capture, answering the form, its new revision and the capture's commit, warnings and identity flag) `store_sql` (`POST /api/workbench/store-sql`, the body a `WorkbenchStoreSqlRequest` read within the editor's request bounds: the form the view holds and what the SQL starts from, `branch` or `none`; for each environment `haute.toml` declares, its name, provider and the SQL `render_sql` writes for `offline_plan` from the form the project's default branch holds, read through `src/haute/_workbench_folder.py::form_at_ref` with nothing checked out, or from none, answered as a `WorkbenchStoreSqlResponse` naming the branch read, null when it holds no form and the SQL starts from none; a form that is not one 422, and one whose table or column names the tables refuse (`input_tables`, `output_tables`, through `_checked_form`) the public contract 422, before any SQL is written), `store_catalogue` (`POST /api/workbench/store-catalogue`, a `WorkbenchStoreCatalogueRequest` naming an environment: its database's tables through `connected_store` and `project_environ`, read by `read_store_catalogue`, answered as a `WorkbenchStoreCatalogueResponse` of `WorkbenchCatalogueTable`s; an environment not declared or with no connection, a location or sign-in that cannot be resolved and a database not reached or read answered 409 naming what to set), `store_compare` (`POST /api/workbench/store-compare`, a `WorkbenchStoreCompareRequest`: the form the view holds, checked as `store_sql` checks it, compared by `compare` with the tables the request carries, as the catalogue answered them, the analyst's `WorkbenchStoreRename`s and the form the default branch holds, on the provider of the environment's connection (`connection_of`), answered as a `WorkbenchStoreCompareResponse`: each `WorkbenchStoreDifference` and `WorkbenchStoreExtra`, the pairings taken, `matches`, the SQL `render_sql` writes from the plan in the connection's SQL schema, and the branch; no database read) and `get_param_file` (`GET /api/workbench/params`, a params file's header and, given a `column`, its distinct values, through `read_param_file`, a file or column that cannot give them answered 409 with why, read from the working directory whether or not the workbench is enabled, since Banding's Import from params (frontend-node-editors) reads it too); the others read `haute.toml` through `_enabled_config` (404 while the workbench is not enabled), and all but the status the form, from the working directory on every request, off the event loop. |
| `frontend/src/api/app.ts` | The underwriter app's routes, through the shared request machinery and validated by the generated `workbench-app` contract, which the builder never loads, each body typed by the generated model of the route that reads it (`workbench-app-requests`): `fetchAppRelease` (`GET /api/app/release`), `fetchAppForm` (`GET /api/app/form`), `priceAppQuote` (`POST /api/app/price` with the form revision, the release and the values, once, waiting `PRICE_TIMEOUT_MS`), and the quotes, each named by its reference, URL-encoded, and its option through `quoteRoute`: `fetchAppQuotes` (a page of a `QuotesQuery`, after a cursor), `createAppQuote` (a `QuoteCreation` with a `QuoteSource`, its `QuoteRelation`, and the reference keyed, none for an alternative), `openAppQuote` (the quote with what it holds), `saveAppQuote` (a `QuoteSave` over a version), `changeAppQuote` (a `QuoteChange` over a version with its `QuoteChangeFields`, a note over none, answering with the quote as it left it), `fetchAppQuoteActivity` and `fetchAppPeople`. Each call's failure is heard by the listener `watchQuotesClosed` sets, through `heard`, before it is thrown on, so a closed store's refusal closes the quotes whichever call met it. |
| `frontend/src/app/entry.tsx` | The underwriter app's page: `UnderwriterApp` in an error boundary, with Haute's styles. |
| `frontend/src/app/openRelease.ts` | `openRelease`: the release's form in the form store (`install`), read as at its revision, Preview showing the app's quote in the view store (`underwriterApp`), the quote_details columns the app sets (`APP_SET_COLUMNS`) locked in the preview store, a quote pricer in the pricing store that prices through `priceAppQuote` on that revision, answering the tables with the request priced as the values the server typed, a refusal for another release (`releaseMoved`) noting it in the app store for the Reload notice before the failure is said, a refusal closing the quotes noting them closed (`noteQuotesClosed`), and the preview store opened on a new quote (`newQuoteValues`) until a quote opens. |
| `frontend/src/app/UnderwriterApp.tsx` | `UnderwriterApp`: reads the release and the form (`fetchAppRelease`, `fetchAppForm`), opens it through `openRelease` and keeps it in `useAppStore`; while the app store's `quotes` are open, `Routed` draws the page `useAppRoute` names (`QuotesPage`, `QuoteView`, or no page at an unknown address, through `RoutedPage`; `/quotes/new` but an alternative's the Quotes page at its default list with the window opened by `AskedNewQuote`) and the `NewQuoteWindow` over it, offering back as it opens a creation kept in the tab (`offerKept`); while they are closed, `QuotesClosed` draws under `AppHeader` a `Notice` that the quotes are unavailable, with the refusal's message, and asks for the release at once and again every `QUOTES_RECHECK_MS` (10 seconds), through `waitForJob`, the quotes opening once it answers them open; the page has the app's routes' listener close the quotes on a closed store's refusal (`watchQuotesClosed`) while it shows, first leaving the field being typed in, so its text is committed and kept in the open quote's draft; why the sheets could not be opened in their place; the page's `QuestionDialog` and the `ToastContainer`; and drops its quote pricer when it goes. |
| `frontend/src/app/AppHeader.tsx` | `AppHeader`: the app's bar, the pipeline's name (a link to the Quotes page through `followLink` while the quotes are open) over the release, what the page puts there, and who is signed in. |
| `frontend/src/app/route.ts` | The app's routes: `AppRoute`, `routeOf` (`/`, its list in the query through `quotesQueryOf`; `/quotes/new`, the Quotes page with the New quote window, filled from a quote to duplicate or renew when the query names one (`from`, `option` and `as`), or an alternative made at once; `/quotes/{reference}/{option}`, the reference read back through `decoded`, an address that does not decode and an option below 1 missing), `quotesPath`, `quotePath`, `newQuotePath`, `navigate` (asking the guard `setLeaveGuard` set before moving, unless forced, and answering whether the page `moved`, was `here` already, so nothing that follows the address runs again, or was `kept` by the guard), Back and Forward through `onPopState` (the history traversed back to where the page was while the guard is asked and on once it allows, each entry the app makes holding its place, `placeOf`, so the entries stay as they were and Forward leads where it did), `useAppRoute` (through `useSyncExternalStore`) and `followLink` (a link followed in the app, a press with a modifier left to the browser). |
| `frontend/src/app/useAppStore.ts` | `useAppStore`: the `release`, its `form` and `revision`, by whose sheets the Quotes page's columns are labelled; `releaseChanged` (`noteReleaseChanged`), set when a price or a save finds the page drawn for another release, which the quote's Reload notice reads, whatever quote is open; `quotes`, the release's quote store as last answered (`WorkbenchAppQuoteStore`), set from each fetch of the release (`setQuotes`) and closed by `noteQuotesClosed` when a route answers 503 with a quote store's refusal (`frontend/src/app/quoteValues.ts::quotesClosedBy`), which the app's routes' listener calls on each failure (`frontend/src/api/app.ts::watchQuotesClosed`, set by the page); and `people` (`loadPeople`, none when they cannot be read). Its questions are the page's (`useQuestionStore` in [frontend-shared](../frontend-shared/low-level.md)). |
| `frontend/src/app/useQuoteListStore.ts` | `useQuoteListStore`: the list loaded (`load`, the same query again only when asked, its rows kept meanwhile; a load since asked wins), `reload` (the list shown now, again, as an archive's Undo asks), its `counts`, `next` page (`more`) and why it could not be read. |
| `frontend/src/app/useOpenQuoteStore.ts` | `useOpenQuoteStore`, the quote open: `openRouted` (the quote a route names opened through `openQuote`, unless it is the alternative just made, `adopted` by its label, on the sheets already, which it forgets either way), `openQuote` (the quote as the store keeps it, put on the sheets through `show`: its values in the preview store through `open`, with `savedPrice` while it is read only or was priced on this release, else with none, so a quote that may change is priced on this release as it opens; what it holds kept as `savedValues`, `baseline` and `savedTables` through `holding`, the rows that no longer fit in `dropped`; read only as `readOnlyReason` says: `archived`, `final`, and `reader` when `canEdit` says the principal may only read quotes, which also holds a new quote read only and refuses every save and change), `openNew` (an alternative made as it is asked for and opened: its source read through `openAppQuote`, its values without the details the app set for the source (`withoutAppDetails`), sent through `createAppQuote` naming no reference, under the operation key `heldOperation` holds in the tab under `creationKey` (`askedFor` naming what was asked), so a reload before the answer asks with it again, `releaseOperation` letting it go once answered or refused (`apiErrorRefused`), and the quote it made held as `adopted`, what of its source no longer fits named in `dropped`; nothing made for one who may only read quotes, nor once another quote was opened before the source was read), each newest load winning; `save` (nothing while read only, while a save or a change is being made (`busy`, which also turns off Save and every control that changes the quote), or while nothing but the details the app sets and the defaults is typed (`quoteEligible`); `saving` from the press, the preview store's pricing settled first (`settle`), a quote opened meanwhile dropping the save; `shown` the preview store's price when its basis is the values' `basisOf`, else null; each write held in `pending` as a `PendingWrite` from the moment it is sent (`prepare`), its `operationKey` bound to what it sends (the values, the version and priced or not: its `basis`), kept after an answer that never came or a server failure, a status of 500 or more, and in the draft, and let go once answered or refused; a save whose basis differs from a write still held first repeating that write exactly (`sendWrite`), its answer taking the quote on without touching the sheets (`adoptQuote`), then saving the values over the version it moved the quote to (`nextSave`) under a key of their own, a repeat that landed and was superseded since (`writeSuperseded`) let go as done, the values saved as asked unless it was made over the version they are edited over, when the newest is offered instead; `saveAppQuote`; the answer adopted through `saved`, the values and price on the sheets unless typed over since, the draft cleared); `change` (nothing while `busy`; given the quote it was asked about, `asked`, made only while that quote is the one open (`sameQuote`), else refused in `error` naming it, since a question can be answered after the page moved on; a change but a note letting a held save go, which never landed or the change would have been refused, and taken on through `adoptChange`: the details the app sets read from the quote's `details` by `appDetails` and put on the sheets, what was typed kept, a price current for the values kept current, and the version it moved the quote to the one the values are edited over); `base`, the version the values on the sheets are edited over, which a save and a change name and a note does not move; each save, change and read bound to the load it started under (`loads`, a `latest` ticket), its answer dropped once another quote was opened, a save that landed meanwhile dropping the draft of what it saved (`forgetSaved`); `keepDraft` (the values kept in this browser while a quote open and ready differs from what is saved, with where each sits on the sheets, `cellPlaces`, dropped once it does not unless a draft is offered back, again after a save for what was typed during it, and as a change answers, before it may hold the quote read only, for what was typed before and during it); `close` (the quote left: what is typed kept, any answer still to come dropped); a change that lets a read-only quote change again (Restore, Amend) offering back what this browser kept of it; a quote being opened holding nothing on the sheets and taking no typing, and nothing saved or kept until it is read; a save's answer typed over since putting the details the app set on the sheets all the same (`takeAppDetails`, through `withAppDetails`, which a change's adoption shares); a failure read by `failed` into `conflict` (a stale save or change, `isStaleRevision`, or a repeat superseded, with the quote a stale refusal carries, read by `conflictQuote` through `apiErrorRawDetail`), `refusal` (a save's values not of their columns' types, or Mark final's fields without a value, as `refusalOf` names them), the app store's release changed (409 without a code, `releaseMoved`) or `error`, the write held let go only for a refusal, a status below 500 (`apiErrorRefused`); the draft this browser kept offered as `recovered` (`openDraftKey`, by the open quote's label, none while no quote is open) while the quote may change and differs from what the sheets show or holds a write whose answer was lost (kept though the values are as they opened, so a quote saved as it opened is written once), a draft offered and left unanswered kept whole, and the write it holds repeated first by a Save made without restoring it (`heldWrite`), so it is written once, restored onto the sheets' columns alone (`onSheets`) with the details the app sets as the quote has them and what had no place named in `dropped` through `lostPlaces`, the write it held held again, or discarded; `activity`, read while `historyOpen`, the one asked for last shown, its failure too (`activityAsked`); and `dismiss`. |
| `frontend/src/app/quoteValues.ts` | `normalisedValues` and `valuesKey` (one quote however its blanks were left), `onSheets` (values kept to a form's input columns), `sameQuote` (one quote: the same reference, case included, and option), `withAppDetails` and `withoutAppDetails` (values with the quote_details cells the app sets taken from others, or without them), `appDetails` (those cells from a quote's `details`, by column name through `QUOTE_DETAILS_COLUMNS`: a whole number as its text, a date as its ISO text, a tick for true and no cell for false or a blank), `savedPrice` (its basis through `basisOf`), `releaseMoved` (a 409 without a code of the store's: a page drawn for another release), `writeSuperseded` (a repeated save that landed, the quote changed since), `refusalOf` (the places a `Refusal` names: a save's cells not of their columns' types, each through `misfitPlace`, or Mark final's fields without a value), `listColumns` and `listValue` (the quote_details columns the Quotes page lists, insured, broker and inception date, labelled as the release's sheets label them, else through `readableName`, a date as a day and a number through `formattedNumber`), `STAGE_LABEL`, `fullTime` (a moment in full, for a title on hover; how long ago comes from the shared `relativeTime`), the `Draft` kept in the browser, with where each value sat on the sheets it was typed into (`places`, by `columnKey`, named `table.column` through `cellPlaces`; `lostPlaces` names what a restore onto other sheets leaves out) and the write a save still holds (`draftKey`, `readDraft`, `writeDraft`, `clearDraft`, none when the browser keeps none or a draft holds no `places`, a held write that is not one read as none), `operationKey`, `creationKey`, `heldOperation` and `releaseOperation` (an alternative's operation key held in the tab's session storage until its answer comes, a new one each time where the tab keeps none), `KeptCreation`, `keptCreationKey`, `keepCreation`, `readKeptCreation` and `forgetCreation` (a creation sent from the New quote window kept whole in the tab's session storage until answered, refused or discarded), `REFERENCE_MOST` and `referenceProblem` (a reference's rules as the server checks them: trimmed and in NFC, 1 to 40 characters, no `/` or control character, and not `.` or `..`, kept as keyed, case included), `asRenewal` (a renewal's values, `is_renewal` ticked), `activityText` with `RELATION_TEXT` (what an activity entry says was done, a quote's making naming what it was made from), and `quotesClosedBy` (the refusal a 503 carries when its error code is one of `QUOTE_STORE_CLOSED`, the quote store's, else null, so a busy pricing's 503 is no closed store). |
| `frontend/src/app/QuotesPage.tsx` | `QuotesPage`: the rail (a `Collapsible` of `RailItem`s for the lists and one for the stages, each with its count), the list's name, the search (`/`, after a pause, and one reached from the address taking its place), the assignee in Team, the order (Newest saved, Reference, Insured), Refresh and New quote (`N`, `openNewQuote`, the New quote window over the list, a press with a modifier left to the browser; keys pressed in the window its own; neither `/` nor `N` acting from a control `isFormControl` names, a select's type-ahead included), and the table of each quote's label (its reference and option), title, `listColumns` read from its details and the rest (placeholder rows while loading, a failure with Retry, an empty list saying why), each row opening its quote, its reference a link, with Open, Duplicate (the New quote window filled from the quote) and Archive (with Undo) or Restore on hover or focus, each answer, the archive's, its Undo's and the restore's, reloading the list shown when it comes through `reload`, never the list it was pressed in, and Load more. |
| `frontend/src/app/QuoteView.tsx` | `QuoteView`: opens the quote its route names through `openRouted`, `/quotes/new?…&as=alternative` making the alternative it asks for through `makeAsked` and moving to the quote's address once made, unless the page moved on meanwhile; Duplicate and Renew opening the New quote window over the quote (`openDerived`), and Save mine as a new quote over it filled from the sheets (`openSaveAsNew`), each leaving the quote as it is; `QuoteBar` (back, the label, stage and Unsaved, the name, its quote_details cell (`NAME_COLUMN`) typed in the bar through `setCell`, Save (Save unpriced when the last pricing of the values failed, off when nothing is new, the values as saved and the price the one saved on this release, or nothing but the details the app sets and the defaults is typed, `quoteTyped`) and Mark final, which asks, off while the quote has unsaved values or no price of this release, the server naming any required field without a value, or Amend, which asks why; the `Menu` of Duplicate, Renew, Create alternative, Archive (with Undo) or Restore and Copy link; History; `ZoomInOut`); `QuoteLine` (who saved it and when, the release that priced it, where it came from, a link to it, and who it is with, reassigned or a name typed); `QuoteNotices` (a release changed, through `ReleaseNotice`, a conflict, a refusal naming each place, through `Places`, a failure, a draft not kept, a draft offered back, and why the quote is read only, each with what can be done; Open the newest moving to the quote's address, whose route opens it, or opening it itself when the page is `here` already, so it is read once); the leave guard and `beforeunload` while values are unsaved; the draft kept through `keepDraft` as values are typed, and as they are typed back to the saved ones, so a draft of values since undone is dropped, and at once as the quote is left or the page goes; the release-changed notice read from the app store; Undo of an archive restoring the quote archived, through `undoArchive`, whichever is open by then; Mark final, Amend, Archive, Restore, the assignee and the History panel's note each changing the quote they were asked about (`change`'s `asked`), and a question still open (the page's `useQuestionStore`, answered null) dropped as the route moves to another quote and as the page is left, Mark final and the leave guard asking through `confirmed` and Amend and Assign through `ask` with a reason; leaving for the Quotes page closing the quote (`close`), so a save answered after opens nothing; Save, Mark final, Amend, the menu's changes, the assignee and the note off while a save or a change is being made (`busy`); Ctrl+S (`saveOpen`), a field being typed in committed first. |
| `frontend/src/app/useNewQuoteStore.ts` | `useNewQuoteStore`, the New quote window: `openNew` (from `newQuoteValues`), `openDerived` (a `WindowOrigin`, its source read through `openAppQuote` without the details the app set for it, `withoutAppDetails`, its reference blank, a renewal through `asRenewal`, the source's rows that no longer fit in `dropped`, each newest fill winning, `fills`) and `openSaveAsNew` (the open quote's sheets, unsaved values and all, its draft let go once the quote is made); `windowFields` (the reference, required, then the columns of quote_details the underwriter types, in the table's order) and `windowProblems` (the reference through `referenceProblem`, each other column through `cellProblem` but whether it is required, which marking the quote final asks, whatever the sheets show); `setReference`, `setDetail`; `create` (every field checked, `checked` marking an empty reference, then the whole `KeptCreation` made once, `creationValues` the window's details over the base, kept in the tab under `keptCreationKey` before `send` sends it); `send` (answered: let go, the page moved to the quote unless it moved on while the creation was sent, Back included, or the leave guard kept it on a quote with unsaved values, a creation the page did not move to toasting "Made <label>" but for Save as new, which toasts "Saved as <label>" whatever happens; refused, a status below 500 (`apiErrorRefused`): let go, the window typing again with the reason, `takenBy` naming, through `apiErrorRawDetail`, the quote holding a reference taken, `releaseMoved` asking a reload; unanswered or 500 or more: kept, `unknown`); `tryAgain` (the kept creation sent exactly as it was); `offerKept` (a kept creation offered back, `offered`, before any window opens); `close`, `leave` (the page moved on, Back and Forward too) and `discard`; nothing while one who may only read quotes is signed in. |
| `frontend/src/app/NewQuoteWindow.tsx` | `NewQuoteWindow`, on `ModalShell`: the window's fields drawn by the sheets' `FieldBoxes` in a `SheetValuesContext` of its own, nothing locked so the reference takes typing, its problems marked (an empty reference once Create was pressed); Create (off while a field does not fit, its title naming the first, on while a field is being typed in, the press committing it and checking every field) and Cancel; while its creation is unanswered no typing; Try again with Close or, offered back, Discard; a refusal's reason with Open it for a reference taken (`openTaken`) and Reload for a release moved; what no place on the current sheets took. |
| `frontend/src/app/HistoryPanel.tsx` | `HistoryPanel`, on `SidePanel` up to `halfWindowWidth`: the note (`NoteEditor`), the related quotes, each a link to it, and the activity through `activityText`, newest first, each part a `Collapsible`. |
| `frontend/src/app/ui.tsx` | `StageChip`, `Notice`, `ReleaseNotice` (the page drawn for an earlier release, with Reload, saying whether what is typed stays in the browser), `Collapsible` (a titled part, open until its title is pressed, its count beside the title when given: the Quotes page's rail and the History panel's parts), `Menu` (Escape or a press elsewhere closes it, arrows move through it), and the classes `BUTTON`, `ICON_BUTTON`, `FILL_BUTTON` with `FILL_STYLE`. |
| `frontend/src/api/workbench.ts` | `fetchWorkbenchStatus`: `GET /api/workbench` through the shared request machinery, validated by the generated `workbench` contract, whose validators load with the first response. `fetchWorkbenchTables`: `GET /api/workbench/tables`, validated by the same group's `WorkbenchTablesResponse`. `fetchWorkbenchFormTables`: `POST /api/workbench/tables` with a form, validated by `WorkbenchTablesResponse`. `fetchWorkbenchForm`: `GET /api/workbench/form`, validated by `WorkbenchFormResponse`. `saveWorkbenchForm`: `PUT /api/workbench/form` with the form and its `base_revision`, in one attempt (a retry of a save that landed but lost its answer would read as stale), validated by `WorkbenchFormSaveResponse`: the form at its new revision with the save's capture. `fetchParamFile`: `GET /api/workbench/params` with a file and, given one, a column, validated by `WorkbenchParamFileResponse`: the file's header and the column's distinct values. `fetchStoreSql`: `POST /api/workbench/store-sql` with the form the view holds and where the SQL starts, validated by `WorkbenchStoreSqlResponse`. |
| `frontend/src/stores/useWorkbenchStore.ts` | `useWorkbenchStore`: `enabled`, whether the project's workbench is, `environments`, the quote store's environments `haute.toml` declares, and `formPath`, where its form is as `haute.toml` names it; `load`, which fetches the status, leaves the store untouched while the workbench is not enabled (so the editor never re-renders for it) and reports a failure as one error toast; `activeView`, which view shows (`EditorView`: `pipeline` or `workbench`), and `showView`, which refuses the workbench's view while the workbench is not enabled; `tables`, the workbench's tables, sample and response tables from the newest fetch that succeeded, and `storeColumns`, the columns the quote store keeps in each table that the form does not, from the same fetch; `refreshTables`, which numbers its fetches so that only the newest publishes them or toasts its failure; `awaitTables`, which settles with whether the newest fetch published (a superseded fetch taking the newer one's outcome); `formDirty`, the form store's `dirty` mirrored by that store for the editor's navigation guards; and `formChanges`, counted by the WebSocket client (`frontend/src/hooks/useWebSocketSync.ts`) at each frame saying the form's file changed on disk and at each connection, a change while disconnected having gone unannounced, which the form store follows. |
| `frontend/src/stores/useStoreCompareStore.ts` | `useStoreCompareStore`, the schema pane's Compare, neither saved nor undone: `environment` (null for none), `read` (`CatalogueRead`: idle, reading, failed with its message, or read with the tables `fetchStoreCatalogue` answered), `renames` (the pairings, as the last comparison took them, `sameRenames` leaving an unchanged list as it is) and `compared` (`Compared`: none, failed, or compared with the form it was asked of and `fetchStoreCompare`'s answer); `choose` (an environment read afresh, its pairings cleared, or none), `readAgain` (keeping the pairings), `pair` (replacing a pairing of either column), `unpair`, `compare` (the form against what was read, nothing before it lands) and `reset`; the newest read and comparison alone landing (`latest`) |
| `frontend/e2e/workbench.spec.ts` | The workbench's journey in a browser against the real server (Playwright, Chromium): the workbench switched on for the end-to-end project and its sheets written, the scaffold's Quote Input and Quote Response replaced through `POST /api/pipeline/save` by a Workbench Input, a Transform and a Workbench Output, the workbench nodes' configs empty, the view opened, the schema read, a Collection dragged out of the palette onto the sheet and given its fields, drawn while building as a new quote, the sample typed and priced live in Preview with no Price or Clear, a column added to the schema, and one Save writing the component, the sample and the column to the form folder (`rating/workbench/`: the sheet's file, the table's schema file and its sample file) and then the pipeline, the Workbench Input's and Workbench Output's config files (`rating/config/workbench_input/quote.json` and `rating/config/workbench_output/response.json`) holding nothing. |
| `frontend/e2e/workbenchProject.ts` | The journeys' project as a workbench project: `FORM_FILES` (the form as a pricing team starts it, a policy table in, a pricing table out and one blank sheet), `readFormFile` and `writeFormFiles` (the form folder's files, `FORM_FOLDER`, read and written as a save writes them), `enableWorkbench` (the workbench switched on in `haute.toml` and the form written) and `buildWorkbenchPipeline` (the scaffold's Quote Input and Quote Response replaced through `POST /api/pipeline/save` by a Workbench Input, a Transform and a Workbench Output, neither workbench node's config holding anything). |
| `frontend/e2e/appServe.ts` | The underwriter app through `haute app serve` on the journeys' project: `buildAppPage` (the app's page built from the frontend as it stands into a folder of the journey's own, since the browser job builds no frontend and the app answers its page with a 503 without it), `startAppServe` (the command started in the project on a free loopback port, answered as an `AppServe` once its health check answers, with the release it serves, refused with the command's output when it stops first or does not answer in time; its `stop` ending the whole process tree) and `readStore` (dev's quote store, `storeFile`, read with plain SQL, opened read only). |
| `frontend/e2e/app-quotes.spec.ts` | The underwriter app's quotes in a browser through `haute app serve` (Playwright, Chromium): the journeys' project made a workbench project, a Collection on its sheet and its test quotes the form's, its release built and served; a quote made in the New quote window, typed, priced and saved, its rows read from dev's SQLite file with SQL (`quoteRows`): the values typed in the policy table and the premium in the pricing table, under its reference and option, saved by the analyst's git identity on the release served; marked final; amended giving a reason, changed and saved again, a draft again with the new value and haute_activity holding each step; and a column added to the form, in its table once the app starts again, the quote opening at its address. |
| `frontend/src/utils/canonicalJson.ts` | `canonicalJson`, JSON with every object's keys sorted, so two values holding the same data compare equal whatever order their keys were written in: the form store's `dirty` and the workbench store's "the nodes are given the same" both compare through it. |
| `frontend/src/utils/workbenchTables.ts` | The rules for a workbench node's ports, which neither node holds: `WorkbenchTables` (the newest fetch: `tables`, `sample`, `responseTables`, `fetch`), `PricedSample` (the sample priced: each output table's rows by its label), `workbenchTablePorts` (tables' ports: those with a column, by name, in order, once), `workbenchNodeTables` (the tables a node of a type takes from a fetch: a Workbench Input's input tables, a Workbench Output's response tables, none before the first fetch or for another node) and `workbenchNodePorts` (their ports), `workbenchDisconnections` (the connections in a graph to a table a fetch does not have, by the node losing them, each out of a Workbench Input on none of its ports or into a Workbench Output on none of its, one through a submodel's boundary left for the run to refuse; null when every connection has its table), `dtypeFits` (whether a frame column's dtype, as previews render it, fills a column of a declared type: the backend's `column_fits` through `castFamily`), `carriesIdentity` (whether a table leads with the quote's reference and option, by place, name and type, as the server's `carries_identity`) and `columnFill` (how a Workbench Output's table's column at a place is filled, a `ColumnFill`: `haute` for the identity of a table that carries it, `missing` while nothing is connected to its table or the connected frame has no column of exactly its name, `mistyped`, with the frame's dtype, when `dtypeFits` refuses it, `filled` otherwise, and `unknown` while the connected frame's columns are). |
| `frontend/src/hooks/useWorkbenchConnections.ts` | The hook the editor shell (`frontend/src/App.tsx`) calls to hold the workbench nodes' connections to the tables fetched: a fetch when the workbench is enabled and on each `executionGeneration`, recorded against the generation (`adoptionFetch`); while the document can change, on every newest fetch and every change of the shown graph's `edges` (an undo, a redo, a paste), `holdConnections` removes the connections `workbenchDisconnections` finds from the graph the editor shows, through `commitEdges` (`setEdgesRaw`, no undo step), reporting each node's in one warning toast ("Disconnected 1 edge from <node>: the workbench no longer has the table it read.", or "filled" for a Workbench Output's); it holds a document only to a fetch made since that document was adopted, answering false, nothing removed, while none has landed, so a save refuses rather than prune against an earlier document's form, and a failed fetch, which leaves the tables of the one before, removes nothing more. |
| `frontend/src/panels/editors/WorkbenchInputEditor.tsx` | The Workbench Input's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Input: the tables of the newest fetch read-only (`workbenchNodeTables`), the node holding none, with each name's `apiInputLabelIssue`, and a note on what previews run on, chosen by whether the fetched sample has values. It exports `WorkbenchTableCard`, one table read-only (its name, its rows per quote and its columns, or `NO_COLUMNS_NOTE` for a table without any, which is no port), and `WorkbenchTablesHeader`, the section's title, "Tables from the workbench", its "Edit in Workbench" button while the workbench is enabled (`showView`) and a note while there are no tables, which the Workbench Output's panel shares. |
| `frontend/src/panels/editors/WorkbenchOutputEditor.tsx` | The Workbench Output's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Output: under `WorkbenchTablesHeader`, each table of the newest fetch (`workbenchNodeTables`) with its name's `apiInputLabelIssue` against the document's reserved labels, as the Workbench Input's panel judges them, `NO_COLUMNS_NOTE` for a table without columns, else the node connected to its port (from the `targetHandle` of the panel's input sources) or "Not connected", and a box per column, its name and type, its border chosen by `columnFill` from the connected frame's columns and dtypes (the input source's `columns`): red (`--danger`) while it is `missing`, orange (`--caution`) while it is `mistyped`, its title naming which, and the plain border while it is `filled`, `unknown` or `haute`, the quote's identity, which the response fills. The panel edits nothing. |
| `frontend/src/stores/useWorkbenchFormStore.ts` | `useWorkbenchFormStore`, the form while the view edits it: `form` and `revision` as read (`load`, once; `reload`, again, dropping edits and history), each read a turn in the store's queue (`serial`), through `fetchWorkbenchForm`, with `status` and `loadError`; `change` (an edit that undo reverses, on the graph store's `appendHistoryEntry` and its history cap), `undo`, `redo`, `undoStack`, `redoStack`, `savedForm` and `dirty` (the form and the saved form compared through `canonicalJson`, whatever order the file writes their keys in; mirrored onto the workbench store's `formDirty` for the editor's guards); a form read anew (`readAnew`) is as saved, unless reading it grew a section or a sheet (`repairs`), when it is unsaved, a warning toast saying what grew; `save`, which takes its turn after the saves and syncs before it (a turn that finds the form being read again saves nothing and says so: "Not saved: <form> is being read again."), writes the form as it stands through `saveWorkbenchForm` with the revision, adopts the new revision, toasts "Saved → <form>", reports the capture through `reportSaveCapture` (which keeps the file when the capture waited on a git identity or failed), and fetches nothing itself, the project save it is the form step of fetching the tables afresh next; `flush`, the project save's form step: `save` while the form holds unsaved edits, else true without a request; `sync`, run after each change of the form's file on disk the server announces (the workbench store's `formChanges`) and each adoption of the pipeline's document (`executionGeneration`), both subscribed at module load, and after any save in flight: the file read again through `fetchWorkbenchForm`, nothing at the same revision, a changed file adopted with history dropped while the form is as saved, else `stale` set, and a failed read one error toast; `stale`, set by a save refused as stale (`isStaleRevision`, one error toast, the edits kept), unless the save before it lost its answer and the file holds exactly what that save sent (`unanswered`), when the file's revision is taken as the base and the save made again, or by a sync that found the file changed under unsaved edits, and cleared by a reload; `saving`; `reads`, counted at each read anew (`load`, `reload`, a sync adopting a changed file), on which the view store fits the sheets and the page's question is answered no; `install(form, revision)`, a form read elsewhere put in place as saved, without history, a gesture or a lost answer, as the underwriter app puts its sheets, no read, so the app's view keeps its zoom; and the gesture setters `pushSnapshot` (the form recorded for undo, the redo stack cleared, a burst of nudges' first), `setFormRaw` (the form replaced without history, `dirty` recomputed, except while a gesture is under way, whose end works it out once rather than at each step, `flush` and `sync` working it out afresh meanwhile, so Save with the pointer held saves the form as dragged and a changed file leaves it stale), `beginGesture(before, stop)` (a gesture under way on the form as pressed, `stop` ending its pointer tracking) and `endGesture` (the gesture finished: the form as it found it recorded for undo, the redo stack cleared, and nothing while the form is as it found it, compared through `canonicalJson`, so a drag held at a limit or brought back where it began records nothing and keeps redo; `dirty` worked out either way), so a drag is one undo step; `change`, `pushSnapshot`, `undo` and `redo` finish a gesture under way first, stopping it, so its later steps never write over them and its undo step comes before theirs, and a form read anew drops it. |
| `frontend/src/stores/useWorkbenchViewStore.ts` | `useWorkbenchViewStore`, the view's own state, neither saved nor undone: `mode` (`build` or `preview`), `schemaOpen` (the schema's pane is open, in either mode, kept as the mode or the sheet changes), `underwriterApp` (the sheets are the underwriter app's, set by `openRelease`: Preview shows the preview store's quote rather than the form's sample), `pageId` (null until chosen; `activePage` reads the form's first, and `shownPage`, in Preview, the sheet chosen while it shows, else the first that shows, else null), `selectedId`, `sheetSelected` (the showing sheet's own panel is open, never with a component selected), `sectionId` (the selected section, its panel open, never with a component selected or the sheet's panel open), `zoom` (25% to 200% in whole percents, `ZOOM_STEP` 10%), `fitted` (the sheets have been fitted since the form store last read a form, cleared when the form store's `reads` moves and never by a save or an undo; a zoom chosen since is kept), `creating` (a component or a section being dragged out of the palette, its `PaletteType`, with the pointer), `movingSection` (a section whose header is being dragged, and the place it would move to), `heldSize` (the sheet's size as pressed, its width and its height as drawn while building, while one of its handles or a section's is dragged, else null), `panelWidth` (360 to start, 320 to half the window), `schemaCollapsed` (the schema's tables shown collapsed, by id), `schemaDetails` (the column whose label and rules are open, or null) `schemaScroll` (how far the schema editor is scrolled) and `schemaSearch` (the text filtering it, `""` for none), so the pane opens again as it was closed, `schemaWidth` (the schema's pane's, `SCHEMA_FULL_WIDTH` to start: 654, the editor's 640px at full stretch beside a 10px scrollbar and the 4px drag handle), `copied` (the component Ctrl/Cmd+C copied, as it was then, with the section it was in, or null; kept while the editor is open), and the `sheet` and `viewport` elements and the schema's pane, `schemaPane`; `previewSections` (a sheet's sections that show for a `SheetVisibility`, each with only its components that show, in order); `showMode` (either mode, nothing selected), `setSchemaOpen` (the schema's pane opened or closed, the selection kept), `showPage` (a sheet, in either mode, nothing selected), `select` (a component, closing the sheet's panel and the section's), `selectSheet`, `selectSection`, `setZoom`, `zoomBy`, `fitZoom` (the viewport's width less the padding over the showing sheet's width, in Preview the sheet's `shownPage` gives for the sample, or the app's quote, 100% while none shows, through `sheetGeometry.fitZoom`, clamped through `setZoom`), `setCreating`, `setMovingSection`, `setPanelWidth`, `toggleSchemaCollapsed`, `setSchemaDetails`, `setSchemaScroll`, `setSchemaSearch`, `setSchemaWidth`, `setCopied`, `setSheet`, `setViewport` and `setSchemaPane`; and `widgetPanes`, the pane each component's properties panel last showed, by component id (a `WidgetPane`, `config` or `display`), set through `setWidgetPane`, a component the form no longer has (deleted, or read away) forgotten as the form store's form changes. |
| `frontend/src/stores/useWorkbenchPricingStore.ts` | `useWorkbenchPricingStore`, the sample priced live in Preview: `pricer` (the view host's `Pricer`, null while the view is away), `price` (a `SamplePrice`: the tables, the values as the server typed them and the basis priced for), `error` (why the last pricing failed, until one succeeds) and `pricing`; `setPricer` (null also cancels), `schedule` (`PRICING_DELAY_MS` after the last call, while a pricer is set), `cancel` and `priceNow`, through the `pricingLoop` the preview store shares, the pricing itself the store's own: an answer after the pricer changed dropped, nothing priced, leaving no price and no error, while the sample has nothing typed (`quoteEligible`), and a price stamped with `previewBasis`, the form's `pricingBasis` and the editor's pricing context. `PricingContext` and `pricingContext()`: the document's `executionGeneration`, the graph's `structuralFingerprint` with its submodels' definitions (`submodelsText`), the active source and the data epoch (`useDataEpochStore`); `contextKey` makes one string of it; `usePricingContext()` subscribes to each part and answers that string, so a price made in another context shows stale in Preview, Preview prices again. `priceForm(form, pricer)`, the pricing itself (the form's tables through `fetchWorkbenchFormTables`, then the pricer, answering the tables with the typed sample as `PricedValues`). `quotePricer` and `setQuotePricer`: a host's `QuotePricer`, the underwriter app's, which prices a quote typed into a form in place of the pipeline; null in the builder. |
| `frontend/src/stores/useWorkbenchPreviewStore.ts` | `useWorkbenchPreviewStore`, the underwriter app's quote, never undone: `quote` (rows by table id, as typed), `price` (a `SamplePrice` for the quote), `error` (why there is no price, as the bar says it), `pricing`, `readOnly` (a quote that may not change: `setCell` and `setRows` change nothing and nothing is priced) and `locked` (the columns the app sets: `setCell` sets none of them, and they are not what makes a quote typed); `setCell` (`withCell`) and `setRows`, each scheduling a pricing; `open` (a quote in place of the one there, with its price, read only or not, a pricing waiting or under way dropped, and one scheduled while the quote may change and has no price); `setReadOnly` (a pricing scheduled when the quote is let change), `setLocked`; `schedule`, `priceNow` and `settle` through the shared `pricingLoop`, `open` cancelling what waits; its pricing prices nothing while the quote is read only, the form is not read or no `quotePricer` is set, leaves no price and no error while nothing is typed (`quoteEligible` with its locked columns), prices nothing again while the price is for the quote's `valuesBasis`, and otherwise prices through the `quotePricer` whatever the cells hold, keeping the price with `valuesBasis` of the whole schema, the columns the quote hides and the quote, or "Pricing failed: …", unless another quote was opened or the quote pricer changed meanwhile, when the answer is dropped; `settle` prices at once what waits for the pause and waits for the pricing under way, again for a value entered meanwhile, until nothing waits and nothing runs, as a save does before it reads the price. |
| `frontend/src/stores/pricingLoop.ts` | `pricingLoop(priceOnce, delayMs)`, the loop Preview's sample and the app's quote are priced through: `schedule` (once typing pauses, the pause restarting with each call), `cancel` (what waits and the one more asked for dropped, a pricing under way left to finish), `priceNow` (one pricing at a time, and once more when asked again meanwhile) and `settle` (what waits priced now, then waited for until nothing waits and nothing runs); `priceOnce` is the store's own. |
| `frontend/src/stores/useDataEpochStore.ts` | `useDataEpochStore`, the data the pipeline reads as an `epoch`, `moved` by every write of a data file through the Files panel and every Data Input import that completes, so a price made on the data before is stale; a file changed outside Haute is not seen. |
| `frontend/src/workbench/priceSample.ts` | `priceSample(graph, form, workbench, source)`: the Workbench Output found (none: an error naming what to add), a Workbench Input required at the top level or in any submodel definition (none: an error saying to replace the Quote Input the sheets never fill, or to add one), and the ports of the response tables the form gives (`workbenchTablePorts` of `workbench.response_tables`) previewed in one request through `previewNode` with `portLabels` and `workbenchForm`, the document sent as the editor holds it, so the pipeline runs once however many tables it fills, every workbench node of the request on the form's tables and sample; each table's rows read from the answer's `port_previews` (none asked for, nothing sent), a failure rejecting with the server's reason or the preview's error, and a table missing from the answer refused by name. |
| `frontend/src/utils/sheetGeometry.ts` | Placing sections and components on a sheet, in unzoomed pixels: `GRID` (8), `snap`, `moveRect` (where a dragged component is on the sheet before it lands, unsnapped), `resizeRect` (by a `Handle`, the opposite edges kept, no smaller than a minimum), `grownRect` (grown or shrunk from the bottom-right corner by what is given, unsnapped, as a nudge moves, never under its least size), `freePlace` (where a component added without a pointer goes in a section: the first grid place, row by row from the top and left to right, a grid step in from the section's top and left and `SECTION_FIT_GAP` clear of every component, else below them all, at the left edge when as wide as the sheet), `placeAt` (where a new component's top-left corner is on the sheet before it lands, unsnapped), `contentRight`, `MIN_SHEET_WIDTH` (240, the widest of the kinds' least widths), `leastSheetWidth` (the least a sheet holding components can be across: as far right as they reach, and `MIN_SHEET_WIDTH`), `BLANK_SHEET_WIDTH` (1280, a new form's sheet, as the server's blank form has it), `contentBottom` (how far down components reach, 0 for none), `MIN_SECTION_HEIGHT` (48), `SECTION_FIT_GAP` (16), `NEW_SECTION_HEIGHT` (160), `SECTION_HEADER` (24, the bar above each section while building), `EDGE_HANDLE` (40 along an edge and 8 across it, the selected section's handle and each of the sheet's), `BLANK_SECTION_HEIGHT` (560, a new sheet's one section, the whole sheet until another is added, as the server's blank form has it), `leastSectionHeight` (the least a section holding components can be: as far down as they reach, and `MIN_SECTION_HEIGHT`), `sectionsHeight` (sections one under the next, their heights summed, with no bars), `leastSheetHeight` (the least a sheet's sections can be tall, its last as short as its components let it be: the others' heights summed and the last one's `leastSectionHeight`), `fittedSectionHeight` (`SECTION_FIT_GAP` below its lowest component, never under `MIN_SECTION_HEIGHT`, which an empty one is), `stackSections` (each section with its `top`, the heights above it summed and a `header` above each, as a `Stacked`), `stackBottom` (where a stack ends, its heights summed), `cutRun` (the run of overlapping components a line across a section at a height would cut through, from the top of the highest to the bottom of the lowest, or the height to itself while it cuts none), `clearLine` (the line across a section nearest a height that cuts through none of its components: the nearer edge of the run `cutRun` gives), `sectionAt` (the stacked section whose band holds a height on the sheet: the first above them all, the last below them all), `sheetHeight` (the stack's bottom, nothing under the last section, or `MIN_SECTION_HEIGHT` for a stack of none), `fitZoom` (the zoom at which a sheet's width fits the viewport across, in steps of 5%, never past 100%), `readingOrder` (components row by row from the top, each row left to right: a row the topmost component not yet read and every one whose top is above its middle; a tie kept in the order given), `SHEET_PADDING` (32) and `HANDLES`. |
| `frontend/src/utils/quoteIdentity.ts` | The quote's identity at the head of every schema table but quote_details: `IDENTITY_COLUMNS` and `IDENTITY_NAMES`, as the server names them, `isIdentityColumn` (by id), `identityCount` (how many lead a table) and `sheetColumns` (a table's columns the sheets can show and test, the identity left out); and Haute's quote_details table, `QUOTE_DETAILS_ID`, `QUOTE_DETAILS_COLUMNS` (each column's id, name and type in order, as the server names them), `QUOTE_DETAILS`, each column's id by its name read from them, typed so a name the table no longer has does not compile, which is how the app names the columns it reads (the reference, the quote's name, the renewal tick and the Quotes page's listed columns, `QuoteDetailName`), and `QUOTE_DETAILS_MANAGED`, the columns the app sets, named through `QUOTE_DETAILS`, which `workbenchForm.ts` exports again, held here as `workbenchForm.ts` and `sheetVisibility.ts` import each other and both read them. |
| `frontend/src/utils/workbenchForm.ts` | Pure operations on the form, each returning a new form (a layout change, `setSectionHeight`, `updateWidget` and `placeWidget`, through `editLayout`, copying only the sheet, the sections and the component it changes, so every other part stays the object it was): `newId` (a table's or a column's id: 64 random bits after the table's or the column's prefix, so two ids the view makes meet with a chance under one in 10^12 for a form of ten thousand; the server refuses a form holding any id twice), `newPartId` (a sheet's, a section's or a component's id, as the form folder's files name them: its kind, `sheet`, `section`, `table`, `collection`, `chart`, `heading`, `import` or `export`, then `_` and four random lowercase letters or digits, drawn again while the form holds it; `duplicateWidget` and `pasteWidget` give copies one), `TABLE_ROWS_MAX` (50) and `COLLECTION_COLUMNS_MAX` (12), the limits the properties panel takes, as the server's form has them, `uniqueName`, `readableName`, `allWidgets`, `allSections` and `pageWidgets` (a sheet's components, section by section); the sheets, `createPage` (named in turn, as wide as the last sheet, `BLANK_SHEET_WIDTH` for none, always shown, holding one section `BLANK_SECTION_HEIGHT` tall), `addPage`, `renamePage`, `movePage` (to another place in the tabs' order), `setPageShowWhen`, `setPageWidth` (whole pixels, never under `leastSheetWidth`, the form as given while the sheet is that wide already or for a width the file cannot hold), `setPageHeight` (its sections' heights summed, `sectionsHeight`, set through its last section's `setSectionHeight`, so never under `leastSheetHeight`) and `removePage` (never the last); the sections, `findSection` (a section with its sheet and its place on it), `createSection` (named in turn on its sheet, "Section N", `NEW_SECTION_HEIGHT` tall, always shown, empty), `addSection` (at a place on the sheet, or the end), `splitSection` (a new section started at a line down a section, the components whose top is there or below moving into it and keeping their places, the section keeping the height above the line but never less than what stays in it reaches), `renameSection`, `setSectionShowWhen`, `setSectionHeight` (whole pixels, never under `leastSectionHeight`), `fitSection` (`fittedSectionHeight`), each the form as given while the section is that tall already, so no edit is recorded, `moveSection` (to another place on its sheet) and `removeSection` (with its components; never a sheet's last); the components, `findWidget` (a component with its sheet, its section and its place in it), `createWidget` (a Table 720 by 200 with 3 rows and `TABLE_OPTIONS`, numbered, compact, lined between rows, striped, not fixed, its header as written and framed, its text 12px, its header the same size, bold, level and unwrapped, thin solid lines and a thin frame, each colour the theme's, and no column looks, a list of its own, a Collection 720 by 120 with 3 columns and `COLLECTION_OPTIONS`, its labels above, comfortable and framed, its text 12px, its labels the same size, as written, neither bold nor wrapped, at their fields' start and two fifths of a field wide beside their boxes, its boxes' borders thin and solid, their values placed as their types put them and each box as wide as its label leaves, a thin frame, each colour the theme's, with no spacers and no column looks, each a list of its own, a Chart 480 by 280 with `CHART_OPTIONS`, its rows numbered, of columns, upright, unstacked, its legend at the bottom and no data labels, each with no title and no fields, or a Heading 480 by 48 with `HEADING_OPTIONS`, medium and aligned left, with no text or description; always shown, its keys in the file's order; a Table no smaller than 240 by 96, a Collection than 160 by 72, a Chart than 240 by 160, a Heading than 80 by 24, `widgetKinds.ts`), `keptInside` (where a component may sit in a section as near a place as it can, never across its top or bottom, at its top when taller than it, and so across its sheet, never past its right edge, at its left when wider than it), `addWidget` (into a section, kept inside it and its sheet's width), `updateWidget` (a `WidgetPatch`, a Table's `rows` or options, a Collection's `columns` or options, a Chart's options and a Heading's `text`, `description` and options each refused on the other kinds, the look a Table and a Collection share (`SharedLook`: `density`, `frame`, `font_size`, `header`, `header_font_size`, `header_bold`, `header_wrap`, `header_color`, `header_font_color`, `frame_color`, `frame_weight` and `column_looks`, the keys both kinds' options hold) refused on the rest, and a `title` or `fields` refused on a Heading), `placeWidget` (a component moved to a place in a section, its own or another of its sheet's, a section of another sheet refused, kept inside it and its sheet's width, at the end of the other's components so drawn over them), `moveWidget` (a component moved within its section, kept inside it and its sheet's width, as a nudge moves it), `removeWidget`, `duplicateWidget` (`COPY_OFFSET`, 16px, below and to the right, kept inside its section and its sheet's width, just after its original in the form's order, so just over it, a new id) and `addWidgetInFreePlace` (a new component of a kind and size, no wider than its sheet, into a section at `freePlace`, the section growing to hold it below its foot, with its id); `pasteWidget` (a copy of a component copied before into a section, where it sat, moved `COPY_OFFSET` down and to the right while a component in the section sits at exactly that place, kept inside the section and its sheet's width through `addWidget`, last in its order, a new id, and what it shows or tests of a column the schema has lost since left out, its fields' conditions included, as removing the column took it out of every component then); a component resized past its section's bottom, or one taller than the section it is put in, growing the section to hold it, and one resized past its sheet's right edge, or wider than the sheet, widening the sheet (`holdWidget`: the section's height at least the component's `y` plus `h`, the sheet's width at least its `x` plus `w`), never shrinking either; what they show, `showsFields` (whether a component shows schema columns: a Table, a Collection or a Chart, never a Heading, which `shownFields` gives none, `toggleField` and `moveInOrder` refuse, and a column or table removed passes by), `rowsShown` (the tables a Table's or a Collection's fields come from: many-row or one-row), `tableGrain`, `chartGrain` (a Chart's, from the schema alone: the grain of its first field's table, else of its `category`'s, else null), `plottable` (a number column other than an index, what a Chart plots), `labelColumns` (the columns that can label a Chart's rows: those of the many-row tables of its grain, other than an index), `orderItems` (a component's order: its fields, each an `OrderItem`, with a Collection's spacers among them where they sit, which the operations below write back whole, a Collection showing no fields keeping no spacers), `toggleField` (a field shown at the end of the order, after any spacer there, or one no longer shown taken out with its place in the count, so the spacers after it keep theirs, and a Table's or a Collection's look for a column it no longer shows dropped, as every operation writing the order back drops it, so removing a column or a table drops its looks too), `moveInOrder` (a field, or a Collection's spacer, from one place in `orderItems` to another), `addSpacer` (a Collection's, after its last field) and `removeSpacer` (the spacer at a place in `orderItems`), the two refused on the other kinds ("Only a Collection has spacers"), a Table's columns' and a Collection's fields' looks, `PLAIN_COLUMN_LOOK` (none of its own, both its aligns `auto`, always shown), `COLUMN_WIDTH_MIN` and `COLUMN_WIDTH_MAX` (24 and 1000 pixels, as the server's form has them), `LABEL_WIDTH_MIN` and `LABEL_WIDTH_MAX` (1 and 99, the percentage of its field a Collection's label beside its box can take, as the server's form has them), `columnLook` (a column's look, its `show_when` included, or `PLAIN_COLUMN_LOOK` itself while it has none), `hasOwnLook` (whether a look sets anything but its `show_when`) and `setColumnLook` (a column the Table or the Collection shows given a look over the one it had, a `show_when` left with no conditions written as always, `all` of none, a new one after the others, one that comes to change nothing taken away, and `clear` putting back all but its `show_when`; refused on a Chart or a Heading, or a column it does not show), and `shownFields` (each field with its column in the schema given, or null); `widgetName` (the kind with its title, a Heading with its text, or the kind alone while blank) and `widgetProblems`, what stops a component showing what it should (no fields, a Heading's text blank, a column gone, a table of the other kind, tables whose rows do not line up (keyed differently, or, in a Table, many-row input tables whose rows a column gives differently or one a column gives beside one it does not), a Chart's field that is not `plottable`, the column labelling a Chart's rows gone or not lining up, a pie of many rows with more than one field) and what stops its conditions, or a field's, doing what they say, each a `WidgetProblem` naming its component, a field's naming the field by its label too, and `pageProblems`, a sheet's, each a `PageProblem` naming its sheet, and `sectionProblems`, a section's, each a `SectionProblem` naming its section (`sectionName`, `Section "Cover"`): a condition that cannot hold as written (`conditionFault`), a value that is not one of its column's allowed values, a `show_when` or a group whose number `match` asks for more conditions than it holds, and a sheet, section, component or field nothing could show again once hidden (`neverShown`); `conditionText` and `showWhenText`, a condition and a `show_when` read as a sentence by the columns' labels, a field tested against by its label and its days as `+ 365 days` or `- 30 days`, for the eye's title; the sample, `withCell`, `withSampleCell` (one cell, empty rows added up to it), `withSampleRows` (several tables' rows as one edit), `newQuoteSample` (the sample started again as a new quote, a copy of `newQuoteValues`, the form itself while it is one already) and `isNewQuoteSample` (whether the sample holds a new quote's values and nothing else, by `canonicalJson`, so a row left empty in a grid counts), `padRows` (rows made up to a length with empty ones) and `setTableRows` (the rows a Table's grid starts with, through `updateWidget`, and its grid started again in the sample at them: the input tables it shows, `gridInputTables` (those of the many-row columns it shows that are typed into), keeping their rows up to the last with a value typed in any column of any of them, empty rows making up the rest, or none while nothing is typed); `pricingBasis`, the schema, the columns `sheetVisibility` hides for the sample and the sample as one string (`valuesBasis`, memoised per form); `linedUpOutputs`, each many-row output table shown lined up through `pricedRows` with a grid's rows of the first many-row input table shown keyed alike, read through the quoted schema, which a Table's grid and a Chart share; `answeredOutputs`, output tables shown without an input table lined up with the first one's answer, its rows as priced and each other's row keyed like each by the first's key columns (none without keys), nothing before a price, which a Table of output columns alone and a Chart of them share; `createSchemaTable` (leading with the quote's identity, `IDENTITY_COLUMNS`), `createSchemaColumn` (for a place in its table, the end unless given, of the type of the column above it, Text first in the table or under the identity or the index) and `createIndexColumn` (`row_number`, an Integer, `index`); `addSchemaTable`, `updateSchemaTable` (a table made an output losing the rules only typing takes and its sample rows), `moveSchemaTable` (to another place in the schema, never quote_details, and nothing above it), `removeSchemaTable` (its columns taken out of the widgets that show them, a Collection's spacers keeping their places among the fields left, and the conditions that test them, its rows out of the sample, a Chart labelling its rows by one of them numbering them again), `setSchemaTableRows` (keys and a row for each value cleared and the index dropped for one row, the columns' defaults cleared for many), `addSchemaColumn` (at an index), `moveSchemaColumn` (never the identity nor the index after it, and nothing above them), `updateSchemaColumn` (nothing of the identity), `setRowPerValue` (a column giving its table a row for each of its allowed values, every other column of the table then unticked, or not), `changeSchemaColumnType` (the range and the number format dropped for a column no longer a number, the allowed values, with the params file they came from and a row for each, for one that `takesOptions` no longer: True/false or Date, and the default for one that becomes or stops being True/false, or becomes a Date while it is not a day (`isDay`)) and `removeSchemaColumn` (taken out of the widgets, a Collection's spacers keeping their places, the conditions and the sample, a Chart labelling its rows by it numbering them again), both leaving the identity as it is; `gridInputTables`, `plottable`, `labelColumns` and the conditions' columns leaving the identity out, through `sheetColumns`; `fieldUses` (the components showing a table's columns, or one of them, a Chart's `category` included), `conditionUses` (the sheets, sections and components whose conditions, or whose fields' conditions, test a table's columns, or one of them, or test a column against one), `buttonUses` (the Imports and Exports using a table, filling it, giving a parameter one of its columns or exporting it, or one of its columns, reading it or giving it a parameter) and `fieldColumn`; `columnNameProblem` (an ASCII identifier that is not one of `PYTHON_KEYWORDS`, the server's `is_frame_label`); and `schemaProblems`, what would stop the schema being the pipeline's tables, each a `SchemaProblem` naming its table: a table's name through `apiInputLabelIssue` with the document's reserved labels, a column's name through `columnNameProblem`, unique in its table, a many-row table's key, an input column's range and allowed values, allowed values on a column that cannot have them, allowed values typed in rather than read from a params file (`options_source` null), a second column giving a table its rows, a one-row input column's default (`defaultProblem`: a tick for True/false, else text `cellProblem` passes), and a table without the identity whose own column takes either of its names, in the server's words (`identityClash`), a table without the identity named for nothing else. `Widget` and `ColumnType` are derived from the generated form types. Haute's quote_details table: `QUOTE_DETAILS_ID`, `QUOTE_DETAILS_COLUMNS`, `QUOTE_DETAILS` and `QUOTE_DETAILS_MANAGED`, from `quoteIdentity.ts`, `QUOTE_DETAILS_REFUSAL`, the server's sentence for another table under its name, which `schemaProblems` gives such a table alone, and `isQuoteDetails`. The Imports and Exports: `ButtonWidget` and `isButton`, `showsFields` (a Table, a Collection or a Chart; neither a Heading nor a button), `createWidget`'s Import (`IMPORT_SOURCES`, each format's settings as a new Import starts them, a CSV first) and Export (`EXPORT_TARGETS`, likewise), `setImportFormat` and `setExportFormat` (a format's settings started afresh, an Export's `file_name`, `headers` and `values` carried between a CSV and a workbook), `updateImportSource` and `updateExportTarget` (a format's own settings, refusing one the format lacks, the form as given while nothing changes), `setImportTable` (the column names dropped), `setImportMode`, `setColumnName` (blank taking it out), `setImportQuery` (the parameters its query stops holding dropped), `queryParameters` (the `:name`s a query holds, in order, once each, outside its quoted text and comments, a `::` cast none), `bindParameter`, `importColumns` (the columns of a table an underwriter types: neither the identity nor the index, nor in quote_details the managed columns), `parameterColumns` (a one-row input table's columns), `toggleExportTable` (an unticked table's name dropped), `setTableName` (blank taking it out), `fileNameTokens` (each `{name}` a file name holds) and, in `widgetProblems`, `importProblems` and `exportProblems`; removing a table or a column and pasting a component drop what an Import or an Export used of it (`dropButtonUses`). |
| `frontend/src/utils/sheetValues.ts` | The values typed into a sheet, the sample or a quote (`RowsByTable`): `cellKey`; `filled` and `rowFilled`, a value and a row as the server counts them (a tick or text other than blank, in any column but the index); `presetColumn` (the column giving a many-row input table a row for each of its allowed values: the first with `row_per_value`, not the index, with options; null for none), `presetRows` (a table's rows as that column gives them, the server's `preset_rows`: each value's row the first holding it there, the rows holding nothing there filling the rows left in order, each holding its value, any other row left out; the rows themselves without one) and `withPresetRows` (the values with every such table's rows read so, the values themselves while no table has one); `parseTypedNumber`, a number as the server types it, a currency sign, separators and spaces allowed, and finite, as the server keeps text that is not, and `parseTypedWhole`, an Integer column's whole number as the server types it, digits alone exactly however large, held with `filled` to `frontend/src/utils/__tests__/fixtures/typedText.json`, which the server's typing reads too; `formattedNumber`, a number column's value in its format through `formatPivotNumber` (typed text read by `parseTypedNumber`), null in General with automatic decimal places, outside a number column or for a value that is not a number; `isDay`, a real calendar day written YYYY-MM-DD, year 1 or later, as the server's dates are; `cellProblem`, what breaks an input column's rules in a value (required, not a number, not a whole number, not a day for a Date (`isDay`), below `min`, above `max`, not one of the options; a value of another type left in a column since retyped, a tick in a column that is no longer True/false or text in one that now is, which the server sends as it is and the Workbench Input refuses; never the index); `startingValue` (what a new quote starts a column with: its default, filled, in a one-row input table, else null), `newQuoteValues` (a new quote's values: each one-row input table's row of its columns' starting values, a table with none left out, memoised per schema) and `quoteTyped` (anything typed in the schema's current input tables, a value in a column or table since removed not counting, as the server leaves it out, nor a value in the column giving a table its rows, nor a cell holding its column's starting value, the rows read through `presetRows` so a row left out counts for nothing; `quoteEligible` asks it of the columns the sheets show) and `quoteProblems`, the problems by cell over each input table's rows, a one-row table's one row always and a many-row table's filled rows only; `valuesBasis`, the whole schema (the server declares a hidden column too, its value left out), the columns hidden, sorted, and the values as one string; and `pricedRows`, a priced output table's rows (`PricedRows`) lined up with a grid's rows of an input table keyed alike, each filled grid row taking the next typed row and the output row whose key columns hold the same values as text. Each reads the columns of the schema it is given, so given `quotedSchema` (`sheetVisibility.ts`) each leaves out what the server leaves out of the quote: a hidden cell counts for nothing, is never checked and does not make a row filled. |
| `frontend/src/utils/sheetChart.ts` | What a Chart plots and how it draws, pure: `ChartSeries` (a series: its `key`, its field's; its `name`; its `values`, each a number or null for a gap; and its `labels`, each value as its column shows it), `ChartData` (`rows`, `one` or `many`; `categories`, what names each column, point or slice; `series`; `axis`, the column whose format the value axis takes, or null for compact numbers; and `stale`, a price for other values plotted or naming its rows), `sheetChartData(schema, widget, values)` (the values a Chart's fields hold on the sheet, a field it cannot plot or whose rows do not line up left out: an input's typed text read by `parseTypedNumber`, a column the quote leaves out a gap, an output's from the price; of one-row tables, one series of a point per field, named by their labels; of many-row tables, a series per field over the rows typed into the input tables it shows (`rowFilled` through the quoted schema), their outputs through `linedUpOutputs`, or, with output tables alone, over the first one's answer, the others matched by key; each row named by its value in `category`, or by its number), `hasValues` (whether it has a number to plot), `chartMessage(widget, data)` (what a Chart says in place of its plot: "Nothing to chart yet" with no number to plot, and for a pie, whose slices share out a whole, its first value below 0, named, or that every value is 0; null while it plots) and `sheetChartOptions(widget, data, tokens, reducedMotion)` (the ECharts option: columns, lines or areas over a category axis, turned for `horizontal` with the first row at the top and stacked for `stacked`, or a pie, a slice per point of the first series; the legend where `legend` says with two series or more, or for a pie; data labels and a rich-text tooltip, on the panel's colours, giving each value as its column shows it, a one-row Chart's points named by the axis alone; the value axis in the shared format, else `formatChartNumber`; the theme's colours, decals and reduced motion; each series identified by its field and how it is drawn, and the legend by its place, since a new option merges into a component of the same id: a series of new values moves, and one drawn otherwise, stacked or not or of another type, or a legend moved, is drawn anew). |
| `frontend/src/utils/sheetVisibility.ts` | The conditions on the sheets and components, and which show for values typed into the sheets, by the rules `src/haute/_workbench_visibility.py` applies on the server, both held to `frontend/src/utils/__tests__/fixtures/sheetVisibility.json`: `columnKey` (a column's key in a set: its table id and column id, the one spelling of it, which a component's shown fields, its Order and its looks, a chart's quoted columns, the condition select, a draft's places and the New quote window's fields all take); `basisOf` (what a pricing of values on a form's sheets is for, `valuesBasis` of the schema, the columns the sheets hide for those values, read through `sheetVisibility` unless given, and the values: the sample's `pricingBasis`, the preview store's pricing, `useQuoteValues` and the app's stores all take it); `fieldKey` (a field's key in a set: its component's id and its column's `columnKey`); `CONDITION_TESTS`, each test's values (none, one, or one or more); `COUNT_TESTS`, a count's or a total's tests, in the editor's order; `TOTALS`, a total's kinds and their words; `isGroup`, `isListCondition`, `isRowsCondition`, `isRowCount` and `isRowTotal`, which kind an entry of a `show_when` is, told by its fields as the server tells it; `listColumns`, the columns a condition on a list's rows tests, those its tests of a row are tested against included; `leafConditions`, a `show_when`'s conditions with each group's in its place; `testsFor`, the tests a column's type takes, in the editor's order; `testLabel`, a test's words for a type ("is after" for a Date's `above`); `valueFits`, whether a value is one a column could hold (text other than blank; a number by `parseTypedNumber`, whole for an Integer; a real `YYYY-MM-DD` day); `conditionFault`, what stops a condition, one on a list's rows included, holding as written, or null, a test against another field faulted by the field (`otherFault`: gone, not a one-row input table's, or in a test of a row not its table's or its index, or of another type), then by the test, which must take one value with none typed, and by days added but to a Date; `testFields`, the fields a test of one value may be tested against (a one-row condition's: the one-row input tables' columns of its column's type but itself; a test of a row's: its table's but the index and itself); `dayNumber`, a real day's number, which a Date against another Date compares, the other's plus `days`; `onPrice`, whether a condition tests an output table's column or rows; `takesNothingTyped`, whether a sheet, a section, a component or a field takes nothing typed (a Heading, a Chart, a Collection or a Table of output columns alone, a field of an output column, a section or a sheet holding only those), the only holders a condition on the price applies to; `withPrice(spec, rows, visibility, price)`, the sheets, sections, components and fields `visibility` shows less those taking nothing typed whose `show_when` fails once its conditions on the price test `price` (its tables by name, each value read as typed text through `priceRows`, every output empty while `price` is null), the others tested as the rounds left them, a section with its sheet, a component with its section and a field with its component, `hidden` left as it is and `visibility` itself returned while the form holds no such condition; `SheetVisibility` and `sheetVisibility(spec, rows)` (memoised per form and values: everything shown, then anything whose conditions fail hidden, a section with its sheet, a component with its section and a field with its component, round after round, until nothing more hides, the fields of Tables and Collections alone counting as on a component, through `fieldKeys`, the columns a component has values typed into, since a Chart takes nothing typed and a Heading has no fields, and of those the fields that show alone, its `fields` their `fieldKey`s); `quotedSchema`, the schema without the columns hidden, the quote's tables as the server reads them, the columns of quote_details the app sets kept whatever the sheets hide; `quoteEligible(form, values, locked)`, the one rule for whether a quote has anything typed to price or save: `quoteTyped` of the columns `quotedSchema` keeps, the locked ones (the app's) aside through `withoutColumns`, the rows read through `withPresetRows`, which Preview's pricing and its marks, the app's pricing, its Save and save body all ask, and the server's rule is held to by `frontend/src/utils/__tests__/fixtures/typedQuotes.json`; `neverShown`, the sheets, sections, components and fields (by `fieldKey`) nothing could show again once hidden, each with the condition it waits on (a section, as a sheet, never shown again by its own components, a component by its own fields, and a field by itself); and the editor's steps, `testableColumns` (a table's columns a condition can test: an input table's, a many-row table's but its index, and an output table's, tested on the price), `totalColumns` (a many-row table's number columns but its index, which a total takes), `newCondition` (the first column of a one-row input table, else any row of a many-row one with a test of its first column but the index, its type's first test and no value, or null without either), `newRowTest` (a test of a table's first column but the index), `withConditionColumn` (a one-row table's column for any condition, keeping its test of that column, a list's test of a row on it included, while the type takes it, the values while they fit, and the field it is tested against, a test of a row's column as that column of its table, with its days, while it is still one `testFields` gives), `withConditionRowsOf` (a table's rows for any condition: one on them kept, a one-row table's condition on a column of it a test of a row, tested against the same field, with its days, while that is a column of the table `testFields` gives, else any row with `newRowTest`), `withListKind` (which rows: any or every keeping the tests and adding `newRowTest` while there is none, a count at least 1 and a total of the first number column greater than no value, each keeping the tests), `withTestColumn` (a test of a row given another column, as `withConditionColumn` keeps a test), `withConditionTest` (another test, given its column, the values kept while they fit it, and the field and days while it takes one value) and `withTestField` (a test of one value given another field to test against, its values and, but for a Date, its days dropped, or given back values typed, its field and days dropped). |
| `frontend/src/workbench/widgetKinds.ts` | `WIDGET_KINDS`, each kind of component's label, icon, colour (`COMPONENT_COLOR`, the entry colour, for a Table and a Collection; `NODE_GROUP_COLORS.explore`, Explore's, for a Chart; `NODE_GROUP_COLORS.structure`, the structure colour, for a Heading; `NODE_GROUP_COLORS.data`, the Data Input's and Data Output's, for an Import and an Export, each 160 by 40, never under 80 by 32), hint, starting size and minimum; `SECTION_KIND`, a section's palette entry (`PaletteKind`: its label, icon, the structure colour and hint); `PALETTE_KINDS`, what the palette offers, a section and then the components; `paletteKind`, an entry by what it adds; `FORMAT_MARKS`, the mark of each format an Import or an Export reads or writes (`FileText` for a CSV, `FileSpreadsheet` for a workbook, `Database` for a database), on the button and its Format. |
| `frontend/src/workbench/sheetInteractions.ts` | The pointer interactions on a sheet while building, each section under its bar: `buildStack` (a sheet's sections stacked with `SECTION_HEADER` above each), `dropAt` (where a component released at a pointer position lands, a `Drop` in the section `sectionAt` gives for its top edge among `buildStack`, kept inside it and the sheet's width, through the view store's sheet and viewport, null off the viewport), `sectionDropAt` (where a section released there starts, a `SectionDrop`, its `line` down the sheet and its `place`: inside a section a split at the grid line nearest the pointer moved clear of the section's components (`clearLine`) and never less than `MIN_SECTION_HEIGHT` down it (nearer its top than that, in before it or at the first clear line that far down, `cutRun`'s bottom, whichever is nearer the pointer), so the line drawn is where the section starts, on a section's bar or at its very top in before it, at its very bottom after it, and below the last section at the end), `startCreate` (a palette item dragged onto the sheet, a focused field left first, `creating` following the pointer; on release a component added to the drop's section (`addWidget`) and selected, or a new section (`createSection`) split off where it was dropped (`splitSection`) or put in at its place (`addSection`), and selected; a click adds nothing), `createWithoutPointer` (what a palette item makes added by its keys: a component into the selected section, else the selected component's, else the sheet's first, at the first place free there (`addWidgetInFreePlace`, at its kind's size), a section after the selected one or the selected component's, else at the sheet's foot; what is added selected, one undo step, a component's id answered), `startTransform` (a component moved, or resized by a handle: a field focused elsewhere left first, so what was typed into it commits as its own undo step (`leaveField`), selected on press, the form replaced on each move through `setFormRaw` by a form made afresh from the form as pressed, so only the section it lands in grows and none above it moves: a move places it (`placeWidget`) in the section `sectionAt` gives for its top edge, its place snapped to the grid once, in that section's coordinates (`landing`, as a drop is), and kept inside the section and the sheet's width; a resize through `resizeRect` within its section, through `updateWidget`; a gesture of the form store's from the press (`beginGesture`, the tracking `track` returns as its stop), recorded once released or cancelled (`endGesture`)), `startSectionResize` (a selected section's handle dragged, a focused field left first: its height through `setSectionHeight` from the height as pressed, snapped, one undo step, a gesture as a move is, holding the sheet's size as pressed in `heldSize` until the gesture stops), `startSheetResize` (one of the showing sheet's handles dragged, a focused field left first: its right edge's sets its width through `setPageWidth`, its bottom's its height through `setPageHeight`, from the size as pressed, snapped, one undo step, a gesture as a move is, holding `heldSize` as a section's handle does) and `startSectionMove` (a section's bar dragged, a focused field left first, a gesture whose stop also clears `movingSection`, so an edit or an undo during the drag ends it, moving nothing: `movingSection` following the pointer, the place it would move to after every other section whose middle is above the pointer, `sectionPlace`, `moveSection` on release, one undo step; a press that does not move moves nothing), each following the pointer (`track`) in the sheet's coordinates taken from the live sheet at each step, so a scroll under a still pointer moves what is dragged too, until a release, or, without one, a cancel, a release outside the window (a move with no button down) or the window losing the focus, a palette drag's stop kept so the next drag ends it; and `deleteSection` (a section removed with its components through `removeSection`, once the page's question is answered yes (`confirmed`) when components are in it; never a sheet's last; the section's panel closed with it). `leaveField` is exported too, for a press on the empty sheet. |
| `frontend/src/workbench/SheetCanvas.tsx` | `SheetCanvas`, the showing sheet in its scrolling viewport: the viewport registered with the view store along with the sheet, the sheet as wide as its `width` and as long as `sheetHeight` gives (of the stack drawn), scaled by the zoom from the viewport's top-left corner, the viewport scrolling over no less than the view store's `heldSize` while it holds one, the viewport scrolling across it while wider, the sheet's values given to the components through `SheetValuesContext` (`useNewQuoteValues` while building, in Preview `useSampleValues`, or `useQuoteValues` while the view store's `underwriterApp` says the sheets are the underwriter app's, only the one in use made), its sections stacked (`stackSections`, each under a `SECTION_HEADER` bar while building and none in Preview), each a strip as wide as the sheet, a group named by its title while building, holding, while building, its `SectionBar` first and then a `SheetWidget` per component with the schema, its first problem and the values' `visibility` of its fields, in `readingOrder` within the section, section by section, so a screen reader and Tab follow the layout, each section's bar before its components, each given its `layer` in the form's order across the sheet (the selected one above all), editable and selectable while building alone; while building every section and component of `activePage`, those its values' `visibility` hides `hidden`, the selected section outlined in the accent colour, its bar given its first `sectionProblems` entry and its conditions as a sentence, the bars sharing one `useRefocusAfterMove`, and `SectionMoveLine` where a dragged bar's section would go, and while the sheet's own panel is open (`sheetSelected`) the sheet's handles, at the middle of its right edge and under the middle of its bottom, each pressed through `startSheetResize`; in Preview only the sections and components that show of the sheet `shownPage` gives (`previewSections`), the sheet, its sections and the fields that show held together while a pointer is pressed on the viewport (`useHeldWhilePressed`), the viewport scrolled to keep the underwriter's place as they move (`useKeptPlace`), the sheet sized to them, or "No sheet shows for this quote." while it gives none; `DropGhost` where a component being dragged in would land, or the line across the sheet where a section would start, "New section from here" for a split (`sectionDropAt`), while building, above every component; the sheet fitted on first show, and a press on the empty sheet deselecting (a press on the viewport's scrollbar, outside its client box, only scrolling), a focused field left first (`leaveField`) so what was typed into the panel commits before it closes, the sheet's and the section's panels closed with it. |
| `frontend/src/workbench/SectionHeaders.tsx` | `SectionBar`, a section's bar while building, drawn first in its section, so Tab and a screen reader meet it before the section's components, across the sheet `SECTION_HEADER` tall just above it: a button named by its number and title, titled with the title alone, `data-selected` while its section is selected, with a grip, its title, `ShowWhenMark` while it has conditions (struck through while the section is hidden, the bar faded) and a warning mark titled with its first `sectionProblems` entry, the conditions as a sentence and the problem also its descriptions to a screen reader, as a button's marks are not; a press on it starting `startSectionMove`, kept from the sheet's own press, and its own keys: Enter or Space `selectSection`, Alt+Up and Alt+Down `moveSection` a place, the moved bar focused again through the `refocus` the sheet's bars share, Delete or Backspace `deleteSection`, asking first when components are in it, while another section remains; and under the middle of the selected section's bottom a handle titled Height, starting `startSectionResize`, a double-click fitting the section (`fitSection`), at the `layer` given, over every component. `SectionMoveLine` is the line across the sheet where a dragged bar's section would go (`movingSection`), at the `layer` given. |
| `frontend/src/workbench/steadyPreview.ts` | Preview held steady under the underwriter, two hooks: `useHeldWhilePressed(value, active, viewport)`, the value as it was when the primary pointer button was pressed on the viewport while `active` (a press on a select, whose menu opens where it is and takes the release, holding nothing), until a turn after the press ends (`LET_GO`: its release or cancel, or a context menu, which may take the release, caught on the window's way down; or the window's own blur), so the press's click lands first, else the value as it is; and `useKeptPlace(viewport, stack, zoom, key)`, after a draw that moved the sections drawn (`stack`, as `stackSections` stacks them) on the same sheet at the same zoom (`key` naming the sheet), the viewport scrolled by as much as, at the zoom, the section holding the focus moved, or else the first section reaching below the viewport's top, as drawn before the draw, from the scroll position a scroll listener last recorded, since the browser clamps it as the sheet shortens, and nothing when that section is no longer drawn; the canvas turns the viewport's own scroll anchoring off. |
| `frontend/src/workbench/SheetWidget.tsx` | `SheetWidget`, one component on the sheet, memoised, so it is drawn again only when what it is given changes, placed in its section from the section's top-left corner: its `WidgetBody` with the fields `shownFields` gives of it and the schema, while building each that the values' `visibility` hides marked `hidden` and each with conditions given them as a sentence (`marks`), and in Preview those that show alone, `inert` while `editable`, cells and all, so a press on a cell is the frame's, in a frame that selects it on press (`startTransform`, which blurs a field focused before so the sheet's keys reach the component), taking the focus, and on focus (`tabIndex` 0, the frame named by `data-widget`, its id) and drags it, and grows eight `ResizeHandle`s once selected, and in Preview does nothing but scroll; stacked by its `layer` as its `z-index`, so where two overlap the one later in the form's order (added later, or a copy over its original) is drawn over the other whatever their reading order; the frame accented when selected, dashed in the warning colour naming a problem in its `title` and to a screen reader (`aria-describedby`); while building, a component with conditions carries `ShowWhenMark` in its top right corner, or its top left on a right-aligned Heading, clear of the text, titled with its `conditions` as a sentence, struck through while `hidden`, and a `hidden` one is drawn faded. |
| `frontend/src/workbench/WidgetBody.tsx`, `frontend/src/workbench/sheet/sheetColours.ts`, `frontend/src/workbench/sheet/fieldCells.ts`, `frontend/src/workbench/sheet/Cell.tsx`, `frontend/src/workbench/sheet/HeadingBody.tsx`, `frontend/src/workbench/sheet/FieldBoxes.tsx`, `frontend/src/workbench/sheet/FieldGrid.tsx`, `frontend/src/workbench/sheet/RowMenu.tsx` | `WidgetBody`, a component on a sheet, its parts each in a module of `sheet/` (the colours, the field and cell helpers, among them how labels are set (`headerText`), their colour (`labelFont`) and each field's own look by its key (`looksByKey`), a cell and a field's label, a Heading, a Collection's boxes and a Table's grid): a Heading as `HeadingBody`, its text alone at its size (`HEADING_STYLES`: an `h2`, `h3` or `h4` and its classes) with its description, while it has one, under it in a paragraph, both aligned as its `align` says (`HEADING_ALIGN`), with no frame, wrapping and centred in its height, or with no text "Type its text in the panel on the right" on one line while building, its description not drawn, and nothing in Preview; any other in a frame with its title, a Table or a Collection without one while its `frame` is off, and a Table's or a Collection's frame and the rule under its title in its `frame_color`, else the theme's border, the frame at its `frame_weight` (`LINE_WIDTH`), and in the sheets' colours, the form's `theme` from the form store (`sheetColours` resolving each, null to the editor's own: the background and border of a framed component, a Chart's too, a Table's header strip in the components' background, or the sheet's while it is unframed, while one is set, the font through `labelColor`, the title, the values and a Heading's text and description, and the input and output cells' fills), with no fields "Choose its fields from the schema in the panel on the right" while building and its frame alone in Preview, which has no panel, else `ChartBody` for a Chart, `FieldBoxes` (a Collection's fields as labelled boxes, its columns across, each label above its box or beside it as `labels` says, a tick box in a box of the input's look as the others are, the boxes as tall as a grid's rows at its `density` (`ROW_HEIGHT`) and the gaps between them by it too (`BOX_GAPS`), each of its `spacers` a gap across its whole width where it sits among them (`orderItems`), as tall as `SPACER_HEIGHT` says at its `density`, the fields after it starting a new row, and in Preview, where it is given the fields that show alone, each spacer placed among them and gone with the fields after it, up to the next spacer, while it has some and all of them hide (`spacersShown`); while building, a field `hidden` faded and a field with conditions carrying `ShowWhenMark` after its label, struck through while hidden; its look as a Table's is set: its values at its `font_size`, each `Cell` given the field's `textStyle`, its weight and place across the box (the field's `align`, else `box_align`), its labels as a Table's header's are (`headerText`, shared with `FieldGrid`: capitals and their size and weight), bold only while `header_bold`, wrapping whole words while `header_wrap`, else cut off, in the field's label font, else the Collection's, else `labelColor`'s (`labelFont`, shared with `FieldGrid`), across its field as the field's `header_align`, else the Collection's, says, at its start while both are `auto`, a label with a fill, the field's or the Collection's `header_color`, padded as a cell and, beside its box, as tall as it; beside, a label `label_width` percent of its field wide, else `LABEL_SHARE` of its field while its box has no width, either at most `LABEL_MOST`, which leaves the gap and a box `COLUMN_WIDTH_MIN` wide, else what the box leaves; each box, an input's `Cell` given it as its `box` and an output's alike (`boxStyle`), bordered in `box_style` at `box_weight` (`LINE_WIDTH`) in `box_color`, else the theme's border, filled in the field's `cell_color` over the input or output fill, in the field's `font_color` over the theme's font, the field's `width`, else `box_width`, wide, else as wide as its label leaves, never wider than its field, a narrower box under its label placed across the field by its box's place (`BOX_PLACE`), as is an output's value across its box (`VALUE_PLACE`); each field's own look (`column_looks`, by its field's key through `looksByKey`, shared with `FieldGrid`, else `PLAIN_COLUMN_LOOK`) over the Collection's, a label or a box taking what the other leaves (`TAKES_THE_REST`)) or `FieldGrid` (a Table's fields as the columns of a grid starting with its rows, in Preview the fields that show alone, while building a column `hidden` faded, its header and its cells, and a column with conditions carrying `ShowWhenMark` after its label, struck through while hidden: while the input tables it shows (`gridInputTables`) hold no rows, its rows empty, the first value typed into one keeping them; once they hold rows, every one, never fewer than its rows while `fixed_rows`; showing a table a column gives its rows (`presetColumn`), those rows, fixed, whatever `rows` and `fixed_rows` say, as many as the input table shown holding most (so beside a table whose rows differ, every row of each, a table's cells past its own rows taking no typing and its index blank there), that column's cells each row's value as text, in its column's format, and every value typed set through `setRows`; the index column numbering them, `RowMenu` across the input tables shown, its Delete Row taking a row of those shown, so below its rows too, deleting the last starting it again, and an insert putting empty rows (`padRows`) at the row or after it in one `setRows`; `RowMenu`, opened by a right-click (`contextmenu`, its default prevented) on a row, transposed on a row's value cells and its number, at the pointer, kept inside the window, and drawn in a portal on the document's body, as the sheet's zoom scales and places anything inside it: a `menu` of Insert, a `menuitem` opening a submenu of Insert Row Above, Insert Row Below, Insert Multiple Rows Above and Insert Multiple Rows Below (Column, Columns, Left and Right transposed) on a hover, a click, Enter or the right arrow, and Delete Row, the arrow keys moving among a menu's entries, the left arrow or Escape closing the submenu, Escape the menu, giving the focus back to what held it, and a press outside it, a scroll, or the window's resize or blur closing it; a Multiple entry swaps the menu for a `ModalShell` dialog, Insert Rows Above (as the entry says), its one field, Rows, taking a whole number from 1, Insert disabled until it holds one and Enter inserting; a press in the menu or the dialog goes no further (`typeHere`); its options give the `#` column (`row_numbers`), its rows' height (`ROW_HEIGHT` by `density`, each `Cell` given it), its lines (`gridlines`) in its `grid_color`, else the theme's, at its `grid_weight` (`LINE_WIDTH`, which the frame's `frame_weight` takes too, its colour `frame_color` over the theme's border on the frame and the title's rule) and the gridlines in its `grid_style`, the header's rule and the output's line solid, its text at its `font_size` (each `Cell` given the column's `textStyle`), its header's labels in capitals or as written (`header`), at `header_font_size`, else the text's size, a pixel smaller in capitals, bold unless `header_bold` is off, wrapping whole words while `header_wrap`, turned as `header_orientation` says (`TURNS`, `writing-mode` and a turn, at the header's foot and centred, a turned label wrapping past `TURNED_WRAP_LENGTH`), the group names level, in its `header_color` strip and `header_font_color`, its labels across their column as its `header_align` says; each column's own look (`column_looks`, by its field's key, else `PLAIN_COLUMN_LOOK`) over the Table's: its label's fill and font, each label cell given the header's weight itself, as a header cell's own bold wins over its row's, its cells' fill over the input and output cells' fills, font, over an index's muted numbers and a dash too, and weight, its `align` on its values, and on its label its `header_align`, else the Table's, else its `align` while both are `auto` (`labelAcross`), its `width` on its label and cells (`widthOf`) but transposed, the grid `w-max` once every column has one, else `w-full`, and transposed its look on its field's line; the shading of every other row (`striped`), while `fixed_rows`, no `RowMenu`, the browser's own menu opening instead, and while `transposed`, the grid on its side: a line per field, its label a row header at its start, kept in view to the left in the header's fill, look and rule, and a column per row, numbered in a header row kept in view at the top, left out while `row_numbers` is off; its stripes and gridlines on its lines, a full grid's on its columns too, and an output field ruled off above its line (`ruleAbove`); while `groups`, and while a field shown is in a schema group, `groupRuns` splits the fields into runs, each run of fields side by side in one group and each field in none alone: over the labels a row of `th`s, one spanning each run (`colSpan`), its name centred and ruled under inside its edges and its line before it its first field's, and a blank cell over the `#`; transposed, a column of `th`s before the labels, one spanning each run's lines (`rowSpan`), its name centred, `GROUP_COLUMN_WIDTH` wide and kept in view at the left, the labels kept in view beside it and the corner spanning both; the rules along the edges of what stays in view are its cells' inset shadows, the header's `ruleUnder` and, transposed, the labels' `ruleRight` and a grouped label's `ruleLeft`, the label kept over the group column's last pixel, since a table's own borders there stay behind as the grid scrolls under them). Each input column's `Cell` is the control its type calls for (a checkbox, a select of the options, a held value the options no longer give shown as an option of its own and marked, a date committed on blur or Enter as text is (a `CommittedTextField` of type `date`), else a `CommittedTextField`), holding the sheet's value from `useSheetValues` and changing it through its `setCell` or `setRows`, `aria-required` for a required column, outlined with its problem as the title when the values' `problems` name it by `cellKey`; a press in one types there and goes no further (`typeHere`). A text field shows its column's `placeholder` while empty and a number in its column's format (`formattedNumber`) while not focused, the text as typed while it is; a select's empty option shows the placeholder, else nothing, and a number's options show its format, each holding the option as typed; a checkbox's cell in a grid takes the row's height. A label from its column with a star for a required input and an `Info` mark titled with its `help`, a gone column "Missing column" while building, its field not drawn in Preview or the app (`drawnFields`), its spacer going as a hidden field's does; an output column shaded, showing the values' `price` through `formattedNumber`, else `formatValue`: in a Collection its table's one row, in a Table the row `linedUpOutputs` lines up with the grid row through the first input table shown that is keyed alike (`tableGrain`), or, in a Table of output columns alone, a row for each of the first output table's answer (`answeredOutputs`), its starting rows of dashes before a price, none added or deleted, read as the values' `quoted` schema has it so the grid's filled rows are the quote's, a dash for none, dimmed (`data-stale`) while `price.basis` is not the values' `basis`. A quote held read only (`readOnly`) shows its cells disabled and a grid without a `RowMenu`; a `locked` column's cells are disabled alone. |
| `frontend/src/workbench/sheet/ButtonBody.tsx` | `ButtonBody`, an Import or an Export on a sheet, which a component's body draws for them: a `button` filling the component's box, `disabled`, so in Preview and the app a press does nothing, faded there (`opacity-60`), while building inert as every component's picture is, its format's mark (`FORMAT_MARKS` in `widgetKinds.ts`) beside its label, centred, framed in the sheets' border colour on the components' background in their font (`sheetColours`). |
| `frontend/src/workbench/ChartBody.tsx` | `ChartBody`, a Chart on a sheet: its values from `useSheetValues` through `sheetChartData` of the form store's schema alone, so a drag elsewhere on the form draws it again for nothing, what `chartMessage` says in place of its plot, "Nothing to chart yet" with nothing to plot or why a pie cannot be drawn, else a host the lazy runtime (`createComboChart`, imported once, every draw waiting on it sharing the one import in flight (`loadChartMaker`), and kept for every later draw; an import that fails tried again on the next) draws `sheetChartOptions` into: one chart per mount and per change of theme or reduced motion, each new option merged into it with its series, axes, grid and legend replaced, resized by a `ResizeObserver` and disposed on unmount; dimmed (`data-stale`) while it plots a price for other values; its summary and its values as a table for a screen reader; a runtime that fails to load, or options it refuses, as an alert in its place. |
| `frontend/src/workbench/ChartSettings.tsx` | `ChartSettings`, a Chart's Chart section in its panel, in the kit's labelled rows (`LabelledRow`), each change one edit through `updateWidget`: Type (`chart_type`: Column, Line, Area or Pie); Rows, what labels the rows of a Chart whose `chartGrain` is many-row ("Row number", or one of `labelColumns`, by table and label, through `category`); Legend (`legend`); and Stacked (`stacked`) and Horizontal (`orientation`), disabled for a pie, and Data labels (`data_labels`). |
| `frontend/src/workbench/PageTabs.tsx` | `PageTabs`, the sheets' tabs: one per sheet (`showPage`), the showing one marked and the one Tab stops at, Left and Right going to the tab before or after, round the ends, and Home and End to the first and the last, each showing its sheet, in Preview too; a plus adding a sheet (`createPage`, `addPage`); a double-click, or F2 on the tab, renaming one in a `CommittedTextField` (a blank name kept out), the tab taking the focus back as Enter or Escape ends it; a tab dragged onto another moving its sheet there (`useListReorder`, `movePage`), and Alt+Left and Alt+Right on a tab moving its sheet a place, the moved tab focused again (`useRefocusAfterMove`), both through `moveSheet`, which first pins `pageId` to the sheet showing while none was chosen, the selection left as it is, so a move never changes which sheet shows; the showing sheet's settings mark (`SlidersHorizontal`, titled Sheet settings, `selectSheet`), its tab having no delete of its own (the sheet's panel deletes it); a sheet with conditions marked with `ShowWhenMark`, a hidden one's tab faded, and a sheet's first `pageProblems` entry as a warning mark titled with it; in Preview (the view store's `mode`, the underwriter app's too) the tabs only switch sheets, those of the sheets that show (`useSheetVisibility`), the one `shownPage` gives marked. |
| `frontend/src/workbench/PropertiesPanel.tsx`, `frontend/src/workbench/panel/Section.tsx`, `frontend/src/workbench/panel/SheetSettings.tsx`, `frontend/src/workbench/panel/SectionSettings.tsx`, `frontend/src/workbench/panel/HeadingSettings.tsx`, `frontend/src/workbench/panel/FieldsSettings.tsx`, `frontend/src/workbench/panel/TableLook.tsx`, `frontend/src/workbench/panel/CollectionLook.tsx`, `frontend/src/workbench/panel/lookParts.tsx`, `frontend/src/workbench/panel/lookChoices.ts`, `frontend/src/workbench/panel/ColourRow.tsx`, `frontend/src/workbench/panel/FieldOrder.tsx`, `frontend/src/workbench/panel/TableFields.tsx` | `PropertiesPanel`, each of its panels and their parts in a module of `panel/`, the showing sheet's panel while `sheetSelected`, keyed by the sheet (its Title, through `renamePage`, a blank one kept out, Show when, through `setPageShowWhen`, and Layout, Width in a `ValidatedTextField` refusing anything but whole pixels no fewer than `leastSheetWidth`, through `setPageWidth`, and Height, its sections' (`sectionsHeight`), in one refusing anything but whole pixels no fewer than `leastSheetHeight`, through `setPageHeight`, and at its foot Delete sheet, disabled while it is the only sheet, asking first (`confirmed`), naming the sheet and how many components go with it (`pageWidgets`), the schema and the sample staying; yes removes it (`removePage`) and shows the first sheet left (`showPage`), closing the panel), the selected section's while `sectionId` names one (`SectionSettings`: Title through `renameSection`, a blank one kept out; Show when through `setSectionShowWhen`; Layout, Height in a `ValidatedTextField` refusing anything but whole pixels no fewer than `leastSectionHeight`, through `setSectionHeight`, with Fit beside it (`fitSection`); Add section below (a `QuietAction`; `createSection`, `addSection` after it, the new one selected) and Delete section (`deleteSection`, asking first when components are in it, the panel closing; disabled while it is the sheet's only section)), else the selected component's, on the kit's `SidePanel` (its width in the view store), a Table's and a Collection's in two panes under its header, Haute's `PreviewPanelTabs` (`WIDGET_PANES`, as wide as each other, in the kind's colour, `idPrefix` `component`) over a `tabpanel`: Config (Order and Fields) and Display (Title, Show when, Layout and Look), the pane the view store's `widgetPanes` holds for the component, Config until another is chosen, and a Chart's in one list: its kind, Title (`CommittedTextField`), Show when (`ShowWhenEditor`, through `updateWidget`), Layout (the rows a Table's grid starts with, through `setTableRows`, or a Collection's columns, in a `ValidatedTextField` holding the range (`wholeNumber`), and a Table's Fixed rows and Transposed checkboxes), a Table's look (`TableLook`, its selects' choices in `lookChoices.ts`: Look, Density and Text size selects (`SelectRow`) and Row numbers, Groups and Striped rows checkboxes; Header, Case, Size (Auto, null, or a size) and Direction selects, Fill and Font `ColourRow`s beside their labels, and Bold and Wrap checkboxes; Lines, Gridlines, Style and Weight selects and a Colour; Frame, a Frame checkbox and, while framed, a Weight select and a Colour; and Columns, a row for each of its fields in its order, each opened and closed (`aria-expanded`) to its look (`ColumnLookFields`, a group named for its label: Header's Fill, Font and an Align select, Cells' Fill, Font and an Align select, each Align named for its label and whose it is (a `SelectRow` given a `name`), unless transposed a Width `ValidatedTextField`, blank for Auto or `wholeNumber` from `COLUMN_WIDTH_MIN` to `COLUMN_WIDTH_MAX`, and Bold, each through `setColumnLook`, Clear (a `QuietAction`) while it has a look (`hasOwnLook`), and Show when, a `ShowWhenEditor` of its `show_when` through `setColumnLook`), a column with a look marked with a dot and one with conditions with `ShowWhenMark`; `ColourRow`, Display's too, a colour's label, above its `ColourSwatches` (the shared form components' Explore colour control) or `beside` them, offering `SHEET_SWATCHES` (the neutrals, then Haute's hues), a swatch or Automatic as one `change`, a colour of its own shown on the sheet at each step of its pick through the form store's gesture (`beginGesture` at its first step, `setFormRaw` for each, made from the form as the pick found it, `endGesture` as the picker is left), so the whole pick is one undo step, a pick the store's undo, redo, another edit or a form read anew stopped first (`stopped`) committing nothing as its picker is left), a Collection's look (`CollectionLook`, from the same choices: Look, Density and Text size selects; Labels, a Position select (Above or Beside), while beside a Width in percent, Case, Size and Align selects, Fill and Font `ColourRow`s, and Bold and Wrap checkboxes; Boxes, Style and Weight selects, a Colour, an Align select and a Width; Frame, as the Table's; and Fields, its fields in its order opened to their looks as a Table's Columns are, each a group with Label's Fill, Font and Align, Box's Fill, Font and Align, a Width, Bold, Clear and Show when, through `setColumnLook`), the parts the two share in `lookParts.tsx` (`HeaderRows`, Case, Size, Fill, Font, Bold and Wrap, a Table's Direction or a Collection's Align among them; `FrameRows`; `ColumnLooks` and `ColumnLookFields`, their headings given; and `WidthRow`, a width in a `ValidatedTextField`, blank for Auto or `wholeNumber` from `COLUMN_WIDTH_MIN` to `COLUMN_WIDTH_MAX` pixels, or, given `share`, from `LABEL_WIDTH_MIN` to `LABEL_WIDTH_MAX` with a % after it), a Chart's Chart (`ChartSettings`) and Order (`FieldOrder`, a row for each of the component's `orderItems`, moved through `useListReorder` and Alt+Up/Alt+Down (`moveInOrder`), a field's numbered among the fields with a cross through `toggleField`, a spacer's (`SpacerRow`) with a cross through `removeSpacer`, each row (`OrderRowFrame`) moved by the keys given the focus again where it lands (`useRefocusAfterMove`), and a Collection's Add spacer (`addSpacer`)), each once the component shows a field, and Fields (the kit's `SearchField`, "Search fields", over its tables while there are any, its text the panel's own, kept as its panes change; while it holds text (`columnMatches`, or the name made readable, `readableName`, as the list shows a column with no label) each `TableFields` shows only the columns it matches, open whatever `useCollapsedSet` says, its collapse toggle disabled, a table matching none is left out, and "No fields match" stands for none; `TableFields` per schema table of the component's kind, every table for a Chart, collapsible (`useCollapsedSet`), a checkbox per column through `toggleField`, a Chart's columns that are not `plottable` disabled, greyed with its unticked columns disabled when `tableGrain` does not match the fields already chosen, a Chart's `chartGrain`), or a pointer to the schema when there is no table of the kind. Each component's settings are keyed by the component, as a section's are by the section, so a value refused for one never shows for the next. A Heading's panel is `HeadingSettings` instead: Text and Description (each a `CommittedTextField`, its `text` and `description` through `updateWidget`), Show when, and Look, a Size select (`HEADING_SIZES`: Large, Medium or Small) and Align, a `ToggleButtonGroup` of `HEADING_ALIGNS` (Left, Centre and Right, each with its icon, in the Heading's colour: a radio group the arrow keys move through, choosing the alignment it has recording nothing). |
| `frontend/src/workbench/panel/ButtonPanes.tsx`, `frontend/src/workbench/panel/ImportSettings.tsx`, `frontend/src/workbench/panel/ExportSettings.tsx` | An Import's and an Export's panels, which the properties panel opens for them, keyed by the component: `ButtonPanes`, the two panes as a Table's are (`WIDGET_PANES` through `PreviewPanelTabs`, in the kind's colour, the view store's `widgetPanes`), Display holding Label (a `CommittedTextField` through `updateWidget`) and Show when (`ShowWhenEditor`), and Config the panel's own, opening with Format (`FormatToggle`, a `ToggleButtonGroup` of CSV, Excel and SQL in the kind's colour, choosing the format it has recording nothing); `ImportSettings`, Config through `setImportFormat`: a CSV's Delimiter (`SelectRow`, `IMPORT_DELIMITERS`), a workbook's Sheet (`CommittedTextField`, placeholder First) and Header row (`ValidatedTextField`, `wholeNumber` 1 to `HEADER_ROW_MAX`), or a database's (`DatabaseSettings`) Connection (`CommittedTextField`), Query (`CommittedTextArea`, through `setImportQuery`) and Parameters (a `ParameterRow` per `queryParameters`, a select of `parameterColumns` grouped by table, through `bindParameter`, and one with a cross for each parameter given a column that the query does not hold); Into (a select of the input tables, through `setImportTable`); Rows (`IMPORT_MODES`) while the table has many rows; and Columns (`ColumnNames`, a row per `importColumns`: `COLUMN_TYPES`' icon, the label, and a `CommittedTextField` whose placeholder is the column's name, through `setColumnName`, and one with a cross for each name given a column it cannot fill); `ExportSettings`, Config through `setExportFormat`: a file's File name (`CommittedTextField`), a CSV's Delimiter (`EXPORT_DELIMITERS`), Headers and Values (`SelectRow`s), or a database's Connection; and Tables, a `ConfigCheckbox` per schema table with its role (`toggleExportTable`), and to a database a `CommittedTextField` beside each one ticked, its placeholder the table's name (`setTableName`), and a Missing table row with a cross for each table it exports that the schema no longer holds. |
| `frontend/src/workbench/ShowWhenEditor.tsx` | `ShowWhenEditor`, a sheet's, a section's, a component's or a field's Show when in its panel, given it as `showWhen`, an `onChange` that commits the whole new one as one edit, and `priced`, whether what it is on takes nothing typed (`takesNothingTyped`), which alone offers the output tables' columns and rows, tested on the price, beside the input tables', never against another field: "Always" with no conditions; Add condition (the kit's `QuietAction`, `newCondition`, disabled with a note while the schema has no input table with a column to test) and Add group (a `ConditionGroup` of `newCondition`, `all` under a Show when of `any` and `any` under one of `all`, in a frame, `role="group"`, with its own match sentence while it holds two or more, its own Add condition and a cross, a group whose last condition is removed going with it); a row per condition of its column (a select, `optgroup` per table, of the one-row input tables' columns by label and of each many-row input table's rows, "Rows of" the table, through `withConditionColumn` and `withConditionRowsOf`, the column's type icon beside it; a condition whose column or table is gone, or whose table has switched between one row and many, keeps its place there, not offered: "Missing column", its column's label or "Rows of" its table), a one-row table's condition's test (`testsFor`, `testLabel`) and value, and a cross; beside a test of one value, while `testFields` gives any, a select of what it is tested against (`Against`), "Value" or one of those fields, by table, through `withTestField`, a field chosen taking the value's place and, for a Date, a `ValidatedTextField` of the days added to it, a whole number from `-DAYS_MAX` to `DAYS_MAX`, refusing anything else; a condition on rows (`ListFields`) with its rows select (Any row, Every row, Number of rows, and `TOTALS`, through `withListKind`), a total's number column beside it (`totalColumns`), its tests of a row, each a column select of `testableColumns`, through `withTestColumn`, a test, a value and a cross, none on the only test of any or every row, Add test (`newRowTest`), and a count's or a total's test (`COUNT_TESTS`) and its number, a `ValidatedTextField` taking a whole number, 0 or more, for a count and a number for a total, and refusing anything else; a value (`ConditionValue`: nothing for a test without values; for one value the cell's control, a select of the allowed values, a `CommittedTextField` of type `date`, committed on blur or Enter, or a `CommittedTextField`; for one or more a `ConfigCheckbox` per allowed value, else a field of the values comma-separated, blanks dropped: a `CommittedTextField` for text, and for an Integer or Decimal column a `ValidatedTextField` refusing, through `listedNumbersValid`, a number written with a thousands separator, which the split would cut in two) choosing a test through `withConditionTest`; and, with two or more, "Show when [all|any|at least] of these are true", the match a select in the sentence (`MatchSentence`), at least starting at 2 with its number a select beside it, 1 to as many conditions as there are or to the number while it asks for more, the sentence shown with one condition too while the match is a number (`sentenceShown`). `ShowWhenMark`, the eye a sheet's tab, a section's header, a component and a field carry while building: `Eye`, or `EyeOff` while hidden, titled with the conditions read as a sentence. |
| `frontend/src/workbench/WorkbenchPalette.tsx` | `WorkbenchPalette`, the view's left column in the kit's palette shell: a `PaletteItem` per `PALETTE_KINDS` entry (`paletteKind`), the components and a section, in its kind's colour, starting `startCreate` on press while building, a button in the tab order adding its kind on Enter or Space (`createWithoutPointer`, `aria-keyshortcuts` "Enter"), a new component then given the focus once drawn (found by its frame's `data-widget`), so the arrows move it, and disabled in Preview (titled "Build to lay out the sheets", out of the tab order), and the switcher under them; sharing `useUIStore.paletteOpen`, collapsed to the reveal strip (labelled "Show component palette") with the compact switcher. |
| `frontend/src/workbench/ViewSwitcher.tsx` | `ViewSwitcher`, the Pricing and Workbench buttons at the bottom of the left palette while the workbench is enabled (nothing otherwise), pressing `activeView` and calling `showView`; `compact` is the column of icon buttons beside the collapsed node palette. |
| `frontend/src/workbench/WorkbenchView.tsx` | `WorkbenchView`, the view over the area below the toolbar while it is active: given `resolveGraph` by the host, it sets the pricing store's pricer (`priceSample` on the resolved document with the settings store's active source) while mounted and schedules a pricing when the form's `pricingBasis` or the editor's pricing context (`usePricingContext`) changes while Preview shows, and when Preview shows again, and cancels what waits when Preview is left; `WorkbenchPalette`, then the form read on first show (`load`), "Loading the workbench…", a read failure with Try again (`reload`), the stale banner with Reload, and once the form is read `PageTabs` over `SheetCanvas`, with `SchemaPane` laid over the canvas's left part while the view store's `schemaOpen`; `useWorkbenchShortcuts` with the host's `onSaveShortcut`, the toolbar's Save; the lazy Git, Assistant and Files (`FilesPanel` on `useUIStore.filesPlace`) panels as asides, else `PropertiesPanel` beside the sheets while building; and `DragChip`, which follows the pointer while a component is dragged out of the palette until the sheet's ghost takes over. |
| `frontend/src/workbench/WorkbenchDisplay.tsx` | `WorkbenchDisplay`, Display in the toolbar, `disabled` in Preview, its box closing as it is disabled (`aria-haspopup` `dialog`, `aria-expanded` and pressed while open) and the box it opens under it, as the pipeline toolbar's Source dropdown opens one (a press outside it closing it through `useClickOutside`, which hears the press as it begins, so a component's press, which cancels the mouse events after it, closes it too, and Escape, its default prevented so the sheet's own Escape does not run, giving the focus back to Display; the box, `aria-modal`, takes the focus as it opens, so no key pressed in it reaches the sheet's shortcuts, which leave a modal dialog's keys to it): the sheets' colours, Sheet background, Component background, Borders, Font, Input cells and Output cells, each a `ColourRow` (the panel's, below) of a `THEME_COLOURS` key, its label above Explore's colour control, a swatch or Automatic through `change` with `setThemeColour`, a colour of its own shown on the sheet at each step of its pick, so the whole pick is one undo step. |
| `frontend/src/workbench/WorkbenchToolbar.tsx` | `WorkbenchToolbar`, the toolbar while the view shows, its controls the same in both modes, each disabled rather than gone where it has no use: `ToolbarBrand`; `SourcePipelineControls` ([frontend-shared](../frontend-shared/low-level.md)), the pipeline toolbar's Source over Pipeline, with the host's `wsStatus` and `sourceSelectionTrusted`; `UndoRedo` on the form store's history; `ZoomInOut` (`zoomBy`), these three where the pipeline toolbar has its own, its canvas actions starting with Undo/Redo and the zoom in a group spelled as this one is; a `ToolbarColumn` of Build over Preview (`showMode`); a `ToolbarColumn` of Schema over `WorkbenchDisplay`: Schema (`Database`), pressed while `schemaOpen`, opening and closing the schema's pane (`setSchemaOpen`) in either mode, disabled until the form is read, and Display and the box of the sheets' colours it opens, disabled in Preview; a `ToolbarColumn` of Reset (`RotateCcw`), disabled while building, until the form is read and while the sample is a new quote's already (`isNewQuoteSample`), pressing it putting `newQuoteSample` through `change`, one undo step; in Preview, the pricing store's `error` as "Pricing failed: …", and no Price or Clear; and `ProjectControls` with the host's `onSave` and `onCommit`, the pipeline toolbar's own Save and Commit, and its `editingDisabled`, so both are off, and the Assistant while no turn runs, exactly when the pipeline toolbar's are. |
| `frontend/src/workbench/SchemaEditor.tsx`, `frontend/src/workbench/schema/schemaText.ts`, `frontend/src/workbench/schema/TableBlock.tsx`, `frontend/src/workbench/schema/ColumnRow.tsx`, `frontend/src/workbench/schema/ColumnDetails.tsx`, `frontend/src/workbench/schema/AllowedField.tsx`, `frontend/src/workbench/schema/DefaultField.tsx`, `frontend/src/workbench/schema/IndexControl.tsx`, `frontend/src/workbench/schema/IconButton.tsx`, `frontend/src/workbench/schema/DragGrip.tsx` | `SchemaEditor`, the editor in the schema's pane, the kit's `SearchField` ("Search fields", the view store's `schemaSearch`) over its tables, which alone scroll, to the view store's `schemaScroll` as it mounts, keeping it as they scroll; while the search holds text (`columnMatches`: the column's name or label holding it, case aside) each `TableBlock` shows only the columns it matches, open whatever `schemaCollapsed` says, its collapse toggle disabled, a table matching none is left out, "No fields match" stands for none, the columns the quote store keeps are not shown, and nothing structural is offered (no grips, Add table, Add column or index checkbox, Alt+Up and Alt+Down and Enter's next column doing nothing); under each table's own columns those the quote store keeps in it, from the tables fetch's `storeColumns`, each a `StoreColumnRow` (what it holds as a type marker, its name and a lock, with nothing to change); its parts each in a module of `schema/` (what they share, a table's block, a column's row and details, the allowed values, the default, the index, the icon button and the grip): a block per table (`DragGrip`, the grip it is dragged by (`useListReorder`, `moveSchemaTable`), alone draggable so selecting a name's text never starts a drag, the block shown as it is dragged and the line above the block it would go to; its name in a `CommittedTextField`, Alt+Up and Alt+Down in it moving the table, its name field keeping the focus (`useRefocusAfterMove`, one for the tables), role and rows selects, the column count with the table's problems, Delete, asked first (`confirmed`) when a sheet shows its columns, a condition tests them or an Import or an Export uses it (`fieldUses`, `conditionUses`, `buttonUses`, counted in those words by `usedBy`), collapsible (the view store's `schemaCollapsed`)) with a row per column (`DragGrip`, its grip, dragging it within its table alone (`useListReorder`, one for each table, `moveSchemaColumn`), none for the index; the type marker, an `IconSelect` of `COLUMN_TYPE_OPTIONS`, or the fixed Integer of the index; the name, committing on blur or Enter, Enter adding the next column in the last column's alone and Alt+Up and Alt+Down moving it, its name field keeping the focus (`useRefocusAfterMove`, one for each table); the summary on its own line under the name, from the name's left edge; the key toggle in a many-row table; Label and rules opening `ColumnDetails` (one column's at a time, the view store's `schemaDetails`), its boxes holding no hint text: the label, the Group (its table's other groups offered through a `datalist`), Help, for a number column other than the index the Format (`NUMBER_FORMAT_LABELS` of `PIVOT_NUMBER_FORMATS`), the decimal places (Auto or 0 to `PIVOT_DECIMAL_PLACES_MAX`) and Separators (1,000), ticked under General making it a Number, Required for any column but the index and those the app sets, and, for an input column, the Default in a one-row table (`DefaultField`: a checkbox for True/false, a select of the allowed values while there are some, a date for a Date, else a text field, trimmed; blank or unticked is `""`), the Placeholder (not True/false or Date), a Min to Max range in `ValidatedTextField`s that refuse a non-number, and Allowed values (`AllowedField`: never typed in, but read from a CSV in the pipeline's params folder, listed through `listProjectFiles` (`pipeline`, `params`) in a select whose blank choice is none, values typed in as an older form holds them shown as a choice of their own ("N values typed in") so the blank choice can take them away, disabled while there is no CSV to choose and nothing typed in, a folder that could not be listed saying why under it with a Retry that lists it again: a file chosen asks for its column from its header (`fetchParamFile`), and a column chosen reads its distinct values, set with `options_source` as one edit; the refresh icon reads them again, an edit only when they changed; the blank file takes them away, with a row for each; a read that fails shows the server's message as an alert under them, and one overtaken by a later choice, answering once the details close, or answering after the column's source changed in the form meanwhile (an undo, a redo, the form synced), does nothing), and in a many-row table while it has allowed values, Rows, "A row for each" (`setRowPerValue`); Remove, asked first (`confirmed`) when a sheet shows it, a condition tests it or an Import reads it or gives it a parameter), Add column (the kit's `QuietAction`) and, for a many-row table, the index checkbox, unticked asking first while a sheet shows the index; then Add table. The fields are the kit's dense ones (`COMPACT_FIELD_CLASS`). A new table's or column's name takes the focus and is selected. Problems come from `schemaProblems` with the document's `reserved_api_input_frame_labels`. Haute's quote_details table (`isQuoteDetails`) keeps its structure: its name read only beside a lock (`DETAILS_TITLE`), its role and rows disabled, no Delete or Add column, no grip on it or its columns, nothing dropped on it, each column's type a fixed marker and its name read only, no Remove, Enter and Alt+Up and Alt+Down doing nothing, and the columns the app sets (`QUOTE_DETAILS_MANAGED`) taking a label, a group and help alone. A column's summary (`columnSummary`), shown only when it says something, is an input column's rules, or whether an output column is required (required, the range through `formattedNumber`, the default in a one-row table, through `formattedNumber` for a number or "ticked" for True/false, the allowed values counted with the file they came from, and "a row each" for the column giving its table's rows), then, in any table, a number column's format (`formatSummary`, nothing for the index or for General with automatic decimals): `FORMAT_NAMES`' name (a currency's sign and code), its decimal places when chosen, and "no separators" while separators apply and are off. Given `readOnly`, in Preview, every table is fixed as quote_details' structure is, without its lock or its note (no grip and no Add table, its name read only, its role and rows disabled, no Delete, Add column or index checkbox, each column's type a fixed marker, its name read only, its key toggle disabled, no Remove, Enter and Alt+Up and Alt+Down doing nothing), and `ColumnDetails` is a disabled `fieldset`; a table still collapses and a column's details still open. |
| `frontend/src/workbench/SchemaPane.tsx` | `SchemaPane`, the schema's pane while `schemaOpen`: placed over the sheet's viewport from its left edge, under the sheet tabs, above the sheet's layers, and registered with the view store as `schemaPane`; on the kit's `SidePanel` with `side` `left`, titled Schema with the `Database` icon, its Close calling `setSchemaOpen(false)`, its width the store's `schemaWidth` (`setSchemaWidth`, 320 to half the window or `SCHEMA_FULL_WIDTH`, whichever is wider), its `actions` Compare's `CompareControls` and SQL, disabled in Preview, which opens `StoreSqlWindow` (`setStoreSqlOpen`), holding `ComparePanel` above `SchemaEditor`, `readOnly` in Preview; it runs `useStoreComparison` while it shows |
| `frontend/src/workbench/StoreCompare.tsx` | The schema pane's Compare: `CompareControls` (a select, Compare with, of none and each of `useWorkbenchStore`'s `environments`, choosing through `choose`, and, while one is chosen, Read again, `readAgain`, disabled while reading); `ComparePanel` (while an environment is chosen: reading, the read's or the comparison's failure as an alert, the database matching the form, each difference of no table, the extras under Not in the form with their quotes, an extra column of the form's table offering `PairSelect`, Rename to…, each column of its table the comparison marks missing, which `pair`s them, and the pairings under Renamed, each unpaired by `unpair`); `DatabaseMark` (a table's, or with `column` a column's, differences in the newest comparison of the form the view holds, `useDifferences`: one chip, its kind's word (`MARKS`) or how many, each message in its title, a rename's alone in the accent colour) |
| `frontend/src/workbench/useStoreComparison.ts` | `useStoreComparison`, the pane's hook: `compare` the form the view holds as it, what was read or the pairings change, while the read has landed, and `reset` the comparison as the pane goes |
| `frontend/src/workbench/StoreSqlWindow.tsx` | `StoreSqlWindow`, the tables' SQL while it is open: on the kit's `ModalShell` under a `ModalFormHeader` titled SQL, the SQL `fetchStoreSql` answers for the form the form store holds and the start chosen, `branch` (the form as the default branch holds it) while the answer names a branch, else `none`; on the tab of the environment `useStoreCompareStore` compares, while its comparison is of the form the view holds, The <environment> database first, `database`, the comparison's SQL; a tab for each environment the answer names, with its engine, the SQL read only in a monospace block, and Copy (`navigator.clipboard.writeText`); a failure said in its place, and no environment declared said so |
| `frontend/src/workbench/columnTypes.ts` | `COLUMN_TYPES`, each column type's label, icon (the step editor's for the kind) and colour (`getDtypeColor` of its dtype), `COLUMN_TYPE_OPTIONS` for the marker, and `isNumeric`. |
| `frontend/src/workbench/useWorkbenchShortcuts.ts` | `useWorkbenchShortcuts`, the view's window-level shortcuts through `hooks/keyboardTargets.ts`: Ctrl/Cmd+S runs the host's save, `onSave` (a focused field blurred first), Ctrl/Cmd+Z undoes and Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes outside a text field; outside a control (`isFormControl`) and outside the schema's pane (the view store's `schemaPane`), whose keys are its own, Ctrl/Cmd+1 fits the sheet, Escape closes the sheet's panel or the section's and, with a component selected, Escape deselects, Delete or Backspace removes, Ctrl/Cmd+D duplicates (`duplicateWidget`, the copy selected), Ctrl/Cmd+C copies it into the view store's `copied`, as it is, with its section, a toast naming it (`widgetName`), the browser's own copy of any text selected left to happen, as the pipeline editor's Ctrl/Cmd+C leaves it, and the arrows nudge by `GRID` or, with Shift, a pixel, kept inside its section (`moveWidget`), with Ctrl/Cmd resize by as much from the bottom-right corner (`grownRect` through `updateWidget`, never under its kind's least size in `WIDGET_KINDS`, its section and sheet growing to hold it, as the handle there does), a burst of either within 800ms one undo step through `pushSnapshot` and `setFormRaw`, a drag under way finished first (`endGesture`) inside a burst too, and a nudge or resize that changes nothing (held at its section's top or bottom, the sheet's left or right edge, or its least size) recording nothing, Delete, Backspace, Ctrl/Cmd+D, Ctrl/Cmd+C and the arrows only from the sheet (inside the view store's `viewport`) or with nothing focused (`onSheet`), never from a panel, the toolbar or a tab; selected or not, with a component `copied`, Ctrl/Cmd+V pastes it (`pasteWidget`, one `change`) into the selected section, else the selected component's, else the section it was copied from while that is on the sheet showing (`activePage`), else the sheet's first, the copy selected; a section header's own keys are the gutter's; in Preview, Ctrl/Cmd+S, the undo and redo keys and Ctrl/Cmd+1 alone apply; keys in a modal dialog are left to it. |
| `frontend/src/workbench/useSheetValues.ts` | `SheetValues`, what a sheet's cells hold and show (`rows`, `setCell`, `setRows`, `price`, `basis`, `problems` by cell key, `visibility`, the values' `sheetVisibility`, `quoted`, the schema as the values' quote reads it, `quotedSchema`, `readOnly`, and `locked`, the columns whose cells take no typing), given to the components through `SheetValuesContext` and read with `useSheetValues`; each of the three made only while `active` (the sheet uses one at a time) and kept the same object through a change that leaves what it holds and shows alone, as a drag makes (its rows on the schema and the values alone, its `visibility` kept while it shows and hides the same things), so the components on the sheet are not drawn again; `useNewQuoteValues`, a new quote's while building: `newQuoteValues` of the schema, read through `withPresetRows` as each source's values are, no price, no problems, setters that type nothing, the visibility of those values with the conditions of the sheet being built (`activePage` of the view store's `pageId`) set aside, so its own conditions fade nothing on it, and not read only, so its cells are drawn as a new quote's; `useSampleValues`, the sample in Preview, edited through `change` with `withSampleCell` and `withSampleRows`, the pricing store's `price`, `previewBasis` with the context `usePricingContext` gives, and `quoteProblems` of the quoted schema and the sample once anything is typed (`quoteEligible`); `useQuoteValues`, the underwriter app's, the preview store's `quote`, `setCell`, `setRows`, `readOnly` and `locked`, its `price`, `basisOf` the quote with the columns its visibility hides, and `quoteProblems` once anything but the locked columns and the defaults is typed (`quoteTyped`) while it may change; `NO_VISIBILITY`, nothing shown, the visibility of values not yet read and of the New quote window, which draws a quote's details apart from any sheet, and the preview store's `NOTHING_LOCKED` for values whose cells all take typing; `useSheetVisibility(previewing)`, for what draws outside the sheet, the tabs: a new quote's (`newQuoteValues`) while building, in Preview the sample's, or the quote's while the view store's `underwriterApp` is set. Each one's visibility is the rounds' through `withPrice` with the price it shows: none while building, so every output is empty there, the pricing store's in Preview and the preview store's in the app, the last while a new one is on its way and none after a pricing that failed, `useSheetVisibility`'s too, so a tab follows its sheet. |

`src/haute/schemas.py` defines `WorkbenchStatusResponse`, `WorkbenchTablesResponse` (with
its `WorkbenchStoreColumn`s), `WorkbenchFormFile`, `WorkbenchFormResponse`,
`WorkbenchFormSaveRequest`, `WorkbenchFormTablesRequest` and `WorkbenchParamFileResponse`
(a params file's `columns` and a column's `values`), and the underwriter app's
`WorkbenchAppReleaseResponse`, `WorkbenchAppFormResponse`, `WorkbenchAppPriceRequest`,
`WorkbenchAppPriceResponse`, `WorkbenchAppQuote`, `WorkbenchAppQuotesResponse`,
`WorkbenchAppQuoteResponse`, `WorkbenchAppQuoteSource`, `WorkbenchAppQuoteCreateRequest`,
`WorkbenchAppQuoteSaveRequest`, `WorkbenchAppQuoteChange` with `WorkbenchAppQuoteAssign` and
`WorkbenchAppQuoteAmend`, `WorkbenchAppQuoteNote`, `WorkbenchAppQuoteChangeResponse`,
`WorkbenchAppQuoteActivityEntry`, `WorkbenchAppRelatedQuote`,
`WorkbenchAppQuoteActivityResponse` and `WorkbenchAppPeopleResponse`;
`scripts/generate_api_contracts.py` lists the editor's responses as the `workbench`
response group and the app's eight as `workbench-app`, which
the builder never loads, and the bodies the app sends as the `workbench-app-requests`
request group, which only the tests load, so the form's models are generated types too;
`frontend/src/api/types.ts` re-exports them.
`src/haute/routes/_error_handlers.py` answers a `WorkbenchError` as 409. `src/haute/deploy/_config.py` lists the `[workbench]` table,
with `WORKBENCH_TOML_KEYS`, in the `haute.toml` schema its whole-file check accepts.
`src/haute/_scaffold.py` renders a workbench project's `haute.toml`, `workbench_haute_toml`
(`[project]`, `[deploy.container]` with the base image on the project's Python, `[test_quotes]`,
`[ci]` and the table, `workbench_table`, its `[workbench.app] host`, `HOSTED_ENVIRONMENTS`'
`[workbench.app.<name>]` with `resource_group` and `container_app` empty, and the quote store's
`WORKBENCH_ENVIRONMENTS` declared on the one provider, `STORE_USERNAME_ENV` naming the role on
`postgresql`), `connections_toml`, their connections on that provider with the analyst's
sign-in (`connection_sign_in`, each environment's variables `HAUTE_<ENVIRONMENT>_<KEY>`),
`connection_variables`, the variables a project's `connections.toml` names, which
`app_env_example` adds to `.env.example` beside the host's credentials, the workbench's
workflows, `workbench_ci_files` (`.github/workflows/ci.yml` and `release.yml`, or
`.gitlab-ci.yml`, or `azure-pipelines.yml`: the validate job ending in `haute app build`, and
on the default branch alone, a manual run's included, the publish job logging in to the
registry with the host's credentials, running `haute app build --push` and rolling staging to
the reference it prints with `haute app rollout staging --image`, then running `haute app
impact` and keeping `impact_report.md` as the job's artifact when it was written, the job
passing or failing (GitHub's `actions/upload-artifact` under `if: always()`, GitLab's
`artifacts` with `when: always`, Azure DevOps' `publish` under `condition: succeededOrFailed()`),
and a production job run by
hand, GitHub's `release-production.yml`, GitLab's manual `deploy-production` job and Azure
DevOps' `DeployProduction` stage (`trigger: manual`), rolling production with `haute app
rollout prod --from staging`; every job that rolls out given the host's credentials, an hour
(publish) or forty-five minutes (production), and serialised per environment: GitHub's
`concurrency` group, GitLab's `resource_group`, Azure DevOps' `lockBehavior: sequential`),
`ci_credentials` (the
`TARGETS` key whose credentials a project's CI reads: a workbench project's host, refused
naming `[workbench.app] host` when it names none), and the blank form as `starter_form`;
`src/haute/cli/_init_cmd.py` writes them for `haute init` answered for workbench pricing, and
`src/haute/cli/_ci.py` the workflows and `.env.example` again for `haute ci update`;
`src/haute/_git_setup.py` seeds the pipeline folder's `workbench/` and `connections.toml` into an unborn repository's root commit.

The view's host is in files other components own. `frontend/src/App.tsx`
([frontend-graph-canvas](../frontend-graph-canvas/low-level.md)) hides and fences the
pipeline editor while `activeView` is the workbench's, swaps the lazy `WorkbenchToolbar` for
`Toolbar`, renders the lazy `WorkbenchView` over the pipeline region with
`resolveWorkbenchGraph` (the whole document from its refs, through `resolveGraphFromRefs`),
gives both toolbars the same `requestSave`, `requestCommit`, `wsStatus` and `sourceSelectionTrusted`, and the view `requestSave`
for Ctrl/Cmd+S, and ends the palette column with `ViewSwitcher` while the workbench is
enabled;
`frontend/src/hooks/useKeyboardShortcuts.ts` registers nothing while its `enabled` is false,
and `frontend/src/hooks/keyboardTargets.ts` holds the keystroke rules both shortcut hooks
share. `frontend/src/components/ProjectControls.tsx`,
`frontend/src/components/SourcePipelineControls.tsx` and
`frontend/src/components/form/IconSelect.tsx` ([frontend-shared](../frontend-shared/low-level.md))
are the toolbar's right-hand group, its Source/Pipeline column and the icon-over-select
control the view reuses;
`frontend/src/hooks/useListReorder.ts` (frontend-shared) is the drag-reorder hook the fields'
order and the sheets' tabs share with the step editor's cards; `frontend/src/haute-ui/PaletteShell.tsx`'s reveal
strip takes the label the component palette gives it;
`frontend/src/stores/useGraphStore.ts` exports `appendHistoryEntry`, the one history rule;
and a Chart draws through Explore's chart modules
([frontend-preview-explore](../frontend-preview-explore/low-level.md)):
`frontend/src/panels/explore/chartRuntime.ts`, the lazy ECharts runtime, which registers
Pie for it and passes a new option's merge settings through, and
`frontend/src/panels/explore/chartTheme.ts`, the chart theme read from the page's tokens.

The Workbench Input reaches across Haute. `src/haute/_types.py` declares
`NodeType.WORKBENCH_INPUT`, its `workbench_input` decorator name, `WorkbenchInputConfig` and
`REQUEST_INPUT_NODE_TYPES`; `workbench_input` is a decorator of `NodeRegistry` in
`src/haute/pipeline.py`, so `Pipeline` and `Submodel` both have it; `src/haute/_config_io.py`,
`src/haute/_config_validation.py`, `src/haute/_cache.py` and `src/haute/node_defaults.json` give
it its folder, config shape, cache classification and default; `_build_api_input` in
`src/haute/_builders.py` and `_gen_api_input` in `src/haute/_codegen_builders.py` are
registered for both request inputs. Every check under `src/haute` that asks whether a node
reads the request tests membership of `REQUEST_INPUT_NODE_TYPES` (in execution, projection,
seeding, input preparation, sizing, data points, standalone nodes, parsing, identities,
recovery, saving, tracing, scoring and deploy), and every mapping keyed by node type that
has a Quote Input entry has the same Workbench Input entry, except the two in
`src/haute/execution.py` that name the file each node type reads,
`_SOURCE_PATH_CONFIG_BY_NODE_TYPE` and `_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE`: a
Workbench Input reads none. Its tables are `haute._workbench_tables`' and its reading
`haute._workbench_input`'s: its frames without a request, read from its sample, come from
`workbench_table_frames`, through `resolve_workbench_input_from_config` in
`src/haute/_node_apply.py`; its ports from `workbench_table_labels`, which reads the tables
alone; a deployed request's frames from `workbench_request_frames`, which
`_score_graph_lazy` in `src/haute/deploy/_scorer.py` injects; and its request's schema, for
deploy, from `quote_schema` of its ports. The generic readers of a request input's tables
each decide by type: `_declared_api_input_port_columns` in `src/haute/projection.py`,
`_declared_api_input_frame_schema_items` in `src/haute/_execute_lazy.py`,
`_validator_issues` in `src/haute/_node_config_recovery.py`, `_resolve_api_input_table` in
`src/haute/_data_points.py`, `recoverable_request_input_source_handles` in
`src/haute/_editor_identities.py` (recovery and the document's identities), the test-quote
scoring in `src/haute/deploy/_validators.py` and the table-name clash check in
`src/haute/routes/_save_pipeline.py`. `src/haute/_graph_utils.py`, which
`haute._types` imports, holds the same set as plain values, `REQUEST_INPUT_KINDS`, and
`is_frame_label`, the one rule a Quote Input's table labels, a workbench table's and
column's names and the editor's source handles follow.
`src/haute/_graph_shape.py` holds `SINGLETON_NODE_GROUPS` and `validate_singleton_groups`,
which save (`src/haute/routes/_save_pipeline.py`) and deploy (`resolve_config` in
`src/haute/deploy/_config.py`) enforce and the assistant's capability manifest
(`src/haute/assistant/_catalog.py`) reads; `src/haute/assistant/_ops.py` refuses to author a
Workbench Input, and its node card is `src/haute/assistant/assets/node_cards/workbenchInput.json`.
A Workbench Input makes no input-cache request: `inputSnapshotSource`
(`frontend/src/utils/inputSnapshotSource.ts`) gives a snapshot source only to a request input
with a structured `path`. In the editor, `frontend/src/utils/nodeTypes.ts` holds the Workbench
Input's `NODE_TYPE_META` entry, `REQUEST_INPUT_TYPES`, `isRequestInputType`,
`singletonTypesOccupiedBy` and `singletonLimitMessage`, and every request-input check uses
`isRequestInputType`; `frontend/src/panels/NodePalette.tsx` shows the Workbench Input in the
Quote Input's place while the workbench is enabled and drags it with its plain default
config; `requestInputSourceHandles` and `requestInputFrameColumns` in
`frontend/src/utils/apiInputPorts.ts` derive a request input's frames and their columns by
its type, a Workbench Input's from the newest fetch, and `edgeInputName` there, as
`edgeInputIdentity` in `frontend/src/utils/editorIdentities.ts`, names an edge out of a
Workbench Input by its handle, which an identity request for it sends none of;
`prepareNodeUpdate` in `frontend/src/utils/nodeUpdatePlan.ts` prunes none of its
connections, which `useWorkbenchConnections` holds; `filterIncomingEdges` in
`frontend/src/utils/graphHelpers.ts` keeps a synced connection on any named port of a
workbench node, for that hook to judge; and `NodeConfigEditor` renders `WorkbenchInputEditor` for a Workbench Input and `ApiInputEditor`, the
table editor, for a Quote Input.

The Workbench Output reaches across Haute the same way. `src/haute/_types.py` declares
`NodeType.WORKBENCH_OUTPUT`, its `workbench_output` decorator name, `WorkbenchOutputConfig`,
its place in `SINK_ONLY_NODE_TYPES`, and `RESPONSE_NODE_TYPES`, the Quote Response and the
Workbench Output; `workbench_output` is a decorator of `NodeRegistry`; `src/haute/_config_io.py`,
`src/haute/_config_validation.py`, `src/haute/_config_builder.py`, `src/haute/_cache.py` and
`src/haute/node_defaults.json` give it its folder, config shape, config-folder parsing, cache
classification and default. `_build_workbench_output` in `src/haute/_builders.py` and the
standalone runner in `src/haute/_standalone_nodes.py` both call
`assemble_workbench_output_from_config` in `src/haute/_node_apply.py`, the first with the
target handles of the node's incoming edges and the second with the target ports of its
`pipeline.connect` calls, which `Pipeline` passes to the node it runs; `_gen_output` in
`src/haute/_codegen_builders.py` is registered for it. The checks that mean "the pipeline's
response" test membership of `RESPONSE_NODE_TYPES`: `Pipeline._resolve_output_node`,
`find_output_node` in `src/haute/deploy/_pruner.py`, the trace pass-through in
`src/haute/_trace_correlation.py`, and `SINGLETON_NODE_GROUPS`; `as_response` in
`src/haute/_workbench_output.py` turns either's result into the frame a request is answered
with, and `src/haute/projection.py` covers it with the generic contract rule. The assistant's
`src/haute/assistant/_ops.py` refuses to author a Workbench Output as it refuses a Workbench
Input, and its node card is `src/haute/assistant/assets/node_cards/workbenchOutput.json`. In
the editor, `frontend/src/utils/nodeTypes.ts` holds its `NODE_TYPE_META` entry,
`RESPONSE_TYPES` and `isResponseType`, which `singletonTypesOccupiedBy` and
`singletonLimitMessage` use for the response pair; `frontend/src/nodes/PipelineNode.tsx` renders
its tables as target port rows through `FramePortRows`; `frontend/src/panels/NodePalette.tsx`
shows it in the Quote Response's place while the workbench is enabled and drags it with its
plain default config; `validatePipelineConnection` in
`frontend/src/utils/connectionValidation.ts` takes a connection to it only on a port of the
fetched response tables; `useWorkbenchConnections` removes the connections whose port a fetch
no longer has; and `InputSource` in
`frontend/src/panels/editors/_shared.tsx` carries each input's `targetHandle` for its panel.

## Key types and data structures

- **`AppQuote`** (`src/haute/_quote_store/_book.py`, frozen): a quote as its quote_details
  row holds it: `reference`, `option`, `details` (the row by column name, dates as ISO text,
  blanks left out), `stage` (`draft` or `final`), `assignee`, `archived`, `version` (the
  version a change names, moved on by every write but a note), `saved_at`, `saved_by`,
  `priced_release` (the release whose pricing the output tables hold, `None` unpriced),
  `source` (a **`Source`**: `reference`, `option` and `relation`, `duplicate`, `renewal` or
  `alternative`), `note`, `note_by` and `note_at`; and, read from its activity, `final_by`,
  `final_at`, `archived_by` and `archived_at`. Its `label` is `ABC-1/2`, its `title` its quote
  name, else its insured, else its label, and `priced` whether it holds a pricing.
  **`OpenQuote`**: `quote`, `values` (by table id, rows keyed by column id, as cell text),
  `outputs` (each output table's rows by name as the pricing rendered them, `None` unpriced)
  and `dropped` (each preset row whose value is no longer allowed, `table row N (value)`).
  **`PricedOutputs`**: `release` and `document` (the pricing's response document, every value
  that is not finite null). **`QuotePage`**: `quotes`, `next_cursor` (an opaque cursor of the
  sort, its key and the last quote's identity) and `counts` (`mine`, `team`, `archived`, and
  `draft` and `final` within the list asked for). **`QuoteActivity`**: `entries`
  (**`ActivityEntry`**: `action`, one of `created`, `saved`, `assigned`, `archived`,
  `restored`, `finalised`, `amended` and `noted`, `actor`, `at`, `version` (`None` for a
  note), `assignee`, `note`, `reason` and `priced_release`), `source` and `derived`
  (**`RelatedQuote`**: `reference`, `option`, `relation`, and its `label`).
- **The store's tables** (`src/haute/_quote_store/_schema.py`): a **`StoreSchema`**
  (`metadata`, `tables`, each a **`StoredTable`** of the form's table and its SQLAlchemy
  table, `many` for a many-row one, and the `activity` table). Each table of the
  form's schema is a table of its name: its columns the form's, in the form's order, each
  type a SQL type (Text, Integer as BIGINT, Decimal as a double, True/false as a boolean, Date
  as a date), every one of the project's nullable; `quote_reference` (VARCHAR(40)) and
  `quote_option` (BIGINT), with `quote_row` (BIGINT) in a many-row table, NOT NULL and its
  primary key; every table but quote_details referring by its identity to its quote's
  quote_details row. quote_details also holds the store's own columns, which no request
  holds: `quote_version` (NOT NULL), `saved_at` (a UTC moment, NOT NULL), `saved_by` (NOT
  NULL), `priced_release`, `source_reference`, `source_option`, `source_relation`, and the
  note, `handover_note`, `note_by` and `note_at`; the details the app sets on every write
  (`stage`, `assignee`, `archived`, `created_date`, `created_by`, `modified_date` and
  `modified_by`) are NOT NULL. A database holds no record of its tables' shape. The
  structure a plan is made between (`structure`): the tables by stable id with each one's
  name, role, rows and columns, each by id with its name, type and `index`, and, read from a
  catalogue, its `sql_type`. **`Plan`** (`src/haute/_quote_store/_migrate.py`, frozen):
  `steps`, `refusals`, `recorded` (the structure the tables have once it is carried out),
  `before` (the structure it was made from, `None` for a database holding no store),
  `activity` (whether it makes haute_activity) and `notes` (what no statement of it changes).
  **`Step`**: `kind`, the `table` and `column` by their names after it and by their ids,
  `was` (the name before a rename) and `quotes` (how many quotes hold values in what it
  concerns, `None` when nothing was counted). **`Catalogue`**
  (`src/haute/_quote_store/_catalogue.py`, frozen): its `tables`, each a **`CatalogueTable`**
  (`name`, `columns`, `primary_key`, `refers`, `quotes`) of **`CatalogueColumn`**s (`name`,
  `kind`, `type`, `nullable`, `collation`, `quotes`); its API shape the
  **`WorkbenchCatalogueTable`** and **`WorkbenchCatalogueColumn`** of `src/haute/schemas.py`,
  their counts `None` when the table lacks `quote_reference` or `quote_option`. **`Comparison`** (`src/haute/_quote_store/_compare.py`, frozen):
  `differences`, each a **`Difference`** (`table` and `column`, the form's ids, `kind`, a
  `DifferenceKind`, and `message`), `extras`, each an **`Extra`** (`table` and `column`, their
  names in the database, `table_id`, the form's table's, and `quotes`), `renames`, each a
  **`Rename`** (`table` and `column`, the form's ids, and `was`, the database's name), the
  `plan`, and `matches`. **`Connection`** (`src/haute/_connections.py`, frozen): `name`,
  `provider`, `database`, `server`, `schema`, `auth` and `variables`. `haute_activity` holds
  `activity_id` (generated), `operation_key` (unique), `quote_reference`, `quote_option`,
  `quote_version` (null for a note), `occurred_at`, `actor`, `action`, `assignee`, `note`,
  `reason` and `priced_release`, indexed by the quote and its order.
- **`WorkbenchConfig`** (`src/haute/_workbench_config.py`): `enabled`, `project_root`,
  `app_host` (the `[workbench.app]` table's `host`, `None` without the table),
  `app_edit_role` (its `edit_role`, stripped, `None` when unsaid), `store` (the
  `[workbench.store.<name>]` tables, each a **`StoreEnvironment`**: `name`, `provider`, `auth`,
  `None` for a file's database, and `variables`, each sign-in key with the variable it names,
  an optional key named only when given, `()` for none), `form_folder` (the
  pipeline folder's `FORM_FOLDER`, the pipeline folder the parent of
  `resolve_pipeline_file(project_root)`) and `form` (`form_folder` relative to the project
  root, posix, `rating/workbench` in a new project). `read_workbench_config(project_root)`
  returns a disabled config when `haute.toml` or its `[workbench]` table is absent, and raises
  `WorkbenchConfigError` (a `WorkbenchError` and a `ConfigError`) when the file cannot be
  read, is not UTF-8 text or cannot be parsed, the table is not a table, holds a key outside
  `WORKBENCH_TOML_KEYS` (`form` with "[workbench].form was removed: the form is the
  pipeline folder's workbench/ folder; delete the key"), lacks `enabled`, gives `enabled` a
  non-boolean, or gives `app` anything but a table holding `host`, one of `APP_HOSTS`, and
  `edit_role`, a name; and, while enabled, when the project has no pipeline file or the
  form folder resolves outside the project root (a `workbench` symlink or junction leading
  out of it).
- **The form folder's files** (`_workbench_folder.py`, the folder module): strict pydantic
  models of the files, separate from `FormSpec`, each `extra="forbid"`, read from JSON that
  refuses a key given twice (`object_pairs_hook`). The keys that say what a thing is are
  required in the file models, with no default, though `FormSpec` gives some of them one:
  the index's `version`, `name`, `schema` and `sheets`; a table's `id`, `role`, `rows` and
  `columns`; a column's `id`, `name` and `type`; a sheet's and a section's `id` and
  `title`, with their `sections` and `components`; a component's `id` and `type`; every
  layout number. Of the other fields, one with a default in `FormSpec`'s
  model has the same default in its file model and is left out at it; a field with none
  (an `ImportWidget`'s `label`, `source`, `table`, `mode` and `column_names`, an
  `ExportWidget`'s `label`, `target` and `tables`, their sources' and targets' fields, a
  group's `match` and `conditions`, a test's `op`) has none in the file either. The
  models, each key in this order:
  - `IndexFile`: `version` (the literal 1), `name`, `theme` (a `Theme`, left out while every
    colour is None), `schema` (the tables' names as spelled, in order, `quote_details`
    first) and `sheets` (the sheets' file names, in tab order).
  - `TableFile`: `id`, `role`, `rows`, `columns` (each a `ColumnFile`); the table's name is
    its index entry and its file is that name in lower case. `ColumnFile`: `id`, `name`,
    `type`, then `SchemaColumn`'s other settings in its order; in an output table's file
    `required`, `min`, `max`, `options`, `options_source`, `row_per_value` and
    `default_value` are refused. `QuoteDetailsFile`: `columns`, each a `name` among
    `QUOTE_DETAILS_COLUMNS` and, as `_check_quote_details` allows them, `label`, `group`,
    `help`, `placeholder`, `number_format`, `decimal_places` and `use_grouping` on any
    column and `required`, `min`, `max`, `options`, `options_source` and `default_value` on
    a column outside `QUOTE_DETAILS_MANAGED`, each column's keys in `SchemaColumn`'s
    order; a column named twice is refused, and the file lists columns in Haute's order.
  - `SheetFile`: `id`, `title`, `show_when`, `sections`; `SectionFile`: `id`, `title`,
    `show_when`, `components`; a component file per kind, discriminated by `type` (`table`,
    `collection`, `chart`, `heading`, `import`, `export`), holding the kind's `FormSpec`
    fields but `x`, `y`, `w` and `h`, in the kind's order.
  - `LayoutFile`: `width`, `sections` (by section id: `height`, `components`, by component
    id an `x`, `y`, `w`, `h` of strict whole numbers).
  - The sample: a list of rows, each row a mapping of column names to `Cell`s.

  References, each field as the file spells it and the `FormSpec` field it fills: a field
  in `fields` and a Chart's `category` are `"table.column"` strings (a `FieldRef`); a
  one-row condition is `{"column": "table.column", "op", "values"}` (`Condition.table` and
  `column`), with `"field": "table.column"` (`Condition.field`) while it is tested against
  another field and `"days"` while they are not 0, each written only then; a column look's `column` is `"table.column"` (`ColumnLook.table` and
  `column`); a query parameter is `{"name", "column": "table.column"}`; a rows condition is
  `{"table": "<table>", "rows", "where"}`, a count `{"table", "where", "op", "count"}` and a
  total `{"table", "total", "column", "where", "op", "value"}`, the total's `column` and
  each `where` test's `column`, and its `field` while it has one, bare names of that table,
  a test's `days` written as a condition's are; an Import's `table` is a table
  name or null and its `column_names` each `{"column": "<bare>", "name"}`; an Export's
  `tables` are table names and a database target's `table_names` each `{"table", "name"}`.
  `"table.column"` splits at its one dot (an identifier holds none). Reading turns each
  name into the id the schema gives it, the table's first and then the column within it. A
  name the schema does not hold becomes an unresolved id, `?` and the name as written: an
  unknown table's reference fills the table with `?<table>` and the column with
  `?<column>`, a known table's unknown column fills the table's id and `?<column>`, and a
  sample value under an unknown name is kept under `?<column>`. `FormSpec` refuses a table
  or column id that starts with `?`, so an unresolved id binds to nothing, and the writer
  writes it back as the name after the `?`. Writing turns each id into its table's or
  column's name, leaves out each setting at its default (`exclude_defaults=True` on the
  file models) and renders the text with `render_folder_file`: two-space indent, keys in
  the models' order, `ensure_ascii=False`, a list of numbers or a layout entry on one line,
  other lists one item a line, LF and a final newline. A golden folder in the tests pins
  the bytes of the test project's form and the `FormSpec` it reads as.
- **`FormSpec`** (`src/haute/_workbench_form.py`): `version` (the literal 1), `name`,
  `theme` (a `Theme`, the sheets' colours: `sheet_color`, `background_color`,
  `border_color`, `font_color`, `input_cell_color` and `output_cell_color`, each
  `#RRGGBB` in capitals or null for the editor's own),
  `data_schema` (written and read as `schema`; a `Schema` of `tables`), `pages` (at least
  one `Page`: `id`, `title`, `show_when` and `sections`, at least one `Section`: `id`,
  `title`, `height` (1 or more, in pixels), `show_when` and `widgets`, every widget inside
  it, its `y` plus its `h` at most `height`, which `Section`'s after-validator holds) and `sample` (`dict[str, list[dict[str, str | bool]]]`,
  by table id then column id, `{}` for none). A `SchemaTable` has `id`, `name`, `role`
  (`input` or `output`), `rows` (`one` or `many`) and `columns`; a `SchemaColumn` has `id`,
  `name`, `type` (`int`, `float`, `str`, `bool` or `date`), `label`, `group` (`""` for
  none), `key`, `required`, `min`,
  `max`, `options`, `options_source` (an `OptionsSource`, its `file` and `column`, or
  null), `row_per_value`, `index`, `help`, `placeholder`, `default_value` (a `Cell`, text or a
  tick, strictly; `""` for none, as is `false`), `number_format` (`general`, `number`,
  `percent`, `currency_gbp`, `currency_usd` or `currency_eur`), `decimal_places` (0–10 or
  null) and `use_grouping`. A widget is a `TableInputWidget` (`type` `tableInput`, `title`,
  `fields`, `rows` 1–50, `row_numbers`, `density` (`compact` or `comfortable`), `gridlines`
  (`rows`, `all` or `none`), `striped`, `fixed_rows`, `header` (`caps` or `plain`),
  `frame`, `transposed`, `groups`, `font_size` (a `FontSize`: 10, 11, 12, 13, 14, 16 or
  18), `header_font_size` (a `FontSize` or null), `header_bold`, `header_wrap`,
  `header_orientation` (a `HeaderOrientation`: `horizontal`, `up`, `down` or `stacked`),
  `header_color`, `header_font_color`, `header_align` (a `ColumnAlign`), `grid_color`, `grid_weight` (a `LineWeight`: `thin`,
  `medium` or `thick`), `grid_style` (a `LineStyle`: `solid`, `dashed` or `dotted`),
  `frame_color`, `frame_weight` and `column_looks`, a list of `ColumnLook` (`table`,
  `column`, `header_color`, `header_font_color`, `header_align`, `cell_color`, `font_color`,
  `bold`, `align` (each a `ColumnAlign`: `auto`, `left`, `center` or `right`), `width`, `COLUMN_WIDTH_MIN` to
  `COLUMN_WIDTH_MAX` (24–1000) or null, and `show_when`, a `ShowWhen`, always by default);
  a look for a column it does not show, a second look for a column, or a look that changes
  nothing (`_PLAIN_LOOK`, always shown) refused by name), a
  `CollectionWidget` (`type` `collection`, `title`, `fields`, `columns` 1–12,
  `labels` (`above` or `beside`), `density`, `frame`, `font_size`, `header`, `header_font_size`, `header_bold` (false by default), `header_wrap`, `header_color`, `header_font_color`, `header_align` (a `ColumnAlign`), `label_width` (a percentage of its field, `LABEL_WIDTH_MIN` to `LABEL_WIDTH_MAX` (1–99), or null), `box_style` (a `LineStyle`), `box_weight` (a `LineWeight`), `box_color`, `box_align` (a `ColumnAlign`), `box_width` (`COLUMN_WIDTH_MIN` to `COLUMN_WIDTH_MAX`, or null), `frame_color`, `frame_weight`, `column_looks` (as a Table's, refused alike) and `spacers` (`[]` by default: each
  spacer the number of its fields before it, 0 or more, in order, two at one place a wider
  gap, none past its last field; one out of order or past it refused)), a `ChartWidget` (`type` `chart`,
  `title`, `fields`, `category` (a `FieldRef`, or null to number the rows), `chart_type` (a
  `ChartType`: `column`, `line`, `area` or `pie`), `orientation` (`vertical` or
  `horizontal`), `stacked`, `legend` (a `ChartLegend`: `bottom`, `top`, `left`, `right` or
  `none`) and `data_labels`) or a `HeadingWidget` (`type` `heading`, `text`, `description`
  (`""` for none), `size` (a `HeadingSize`: `large`, `medium` or `small`) and `align` (a
  `HeadingAlign`: `left`, `center` or `right`), and no `title` or `fields`), each with `id`, `x`, `y` (0 or more, from its section's top-left corner), `w`, `h` (1 or more) and a `show_when` after its `fields`, a Heading's after its `description`; a `FieldRef`
  names a `table` and a `column` by id. A `ShowWhen` is `match` (`all`, `any` or a
  strict whole number, 1 or more, for at least that many; `all` by default) and
  `conditions` (`[]` by default: always shown), each a `ConditionGroup` (`match`, the
  same, and `conditions`, one or more, none of them a group) or one condition: a
  `Condition`, a one-row table's, or a condition on a many-row table's rows, each naming
  its `table` and the tests a row must pass, `where`, each a `RowTest` (a `Condition`'s
  `column`, `op`, `values` and `days`, its table the condition's, and `field`, the id of
  another column of that table, the same row's, or `None`): a `RowsCondition` (`table`,
  `rows`, `any` or `every`, and `where`, one or more), a `RowCount` (`table`, `where`,
  `op`, a `CountTest`: `equals`, `not_equals`, `above`, `at_least`, `below` or
  `at_most`, and `count`, a strict whole number, 0 or more) or a `RowTotal` (`table`,
  `total`, a `TotalKind`: `sum`, `min`, `max` or `average`, `column`, `where`, `op`, a
  `CountTest`, and `value`, text). `_condition_kind`, the union's discriminator, tells
  them by their fields: `conditions` a group, `total` a total, `count` a count, `rows` a
  `RowsCondition`, else a `Condition`, so a group among a group's conditions has no kind
  there and is refused. A `Condition` is `table`
  and `column` (ids), `op` (a `ConditionTest`: `ticked`, `not_ticked`, `filled`, `empty`,
  `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`, `ends_with`,
  `one_of`, `not_one_of`, `above`, `at_least`, `below` or `at_most`), `values` (text,
  strictly, `[]` by default), `field` (a `FieldRef`, another field tested against in
  place of values, or `None` by default) and `days` (a strict whole number from
  `-DAYS_MAX` to `DAYS_MAX`, 0 by default, added to another Date field's day). The shape is all the reader
  holds a condition to: whether its column, test and values fit is `condition_fits`'s
  question, asked where conditions are tested. Every model forbids unknown fields, and
  `model_dump(mode="json")` writes every field, defaults included.
- **`Visibility`** (`src/haute/_workbench_visibility.py`) and **`SheetVisibility`**
  (`frontend/src/utils/sheetVisibility.ts`): `pages`, `sections` and `widgets`, the ids of
  the sheets, sections and components that show (a section only on a sheet that shows, a
  component only in a section that shows), `fields`, the fields that show, each of a
  component that shows (a component id, a table id and a column id on the server, a
  `fieldKey` in the view), and `hidden`, the
  input columns left out of the quote, each a table id and a column id (`columnKey` in
  the view): columns other than the index on at least one Table or Collection, none of
  which shows them as a field that shows.
- **`WorkbenchTable`** and **`WorkbenchColumn`** (`src/haute/_workbench_tables.py`): the
  pydantic models of a workbench node's tables, unknown fields refused: a table is `name`,
  `rows` (`one` or `many`) and `columns`, a column `name` and `type` (`int`, `float`, `str`,
  `bool` or `date`); `one_row`, `frame_schema` (the columns' dtypes, `COLUMN_DTYPES`, held
  equal to the Quote Input's) and `quote_dtype` (a `pl.Struct` of them, in a `pl.List` for a
  many-row table) read off a table. `parse_workbench_tables(value, owner=...)` reads a
  config's list strictly and `check_tables` holds every name to `is_frame_label`, unique
  across tables whatever its case and within a table; `port_tables` are the tables with a
  column, `quote_schema` the schema of one quote holding tables with every column (a
  response's) and `request_schema` that of one request, each table of the columns
  `request_columns` gives it, the identity of a table that `carries_identity` left to
  quote_details. **`WorkbenchTables`**:
  `tables`, `sample` and `response_tables`, as `GET /api/workbench/tables` serves them; the
  generated contract types the tables as `WorkbenchTable`, which `frontend/src/api/types.ts`
  re-exports.
- **`WorkbenchStatusResponse`** (`src/haute/schemas.py`): `enabled` and `form` (the path as
  `haute.toml` names it while enabled, else `null`). **`WorkbenchTablesResponse`**: `tables`,
  `sample` (an object, `{}` for none), `response_tables` (`[]` for none) and `store_columns`
  (the columns the quote store keeps in each table that the form does not, by table id, each
  a **`WorkbenchStoreColumn`**: `name` and `kind`, `int`, `str` or `datetime`).
- **Store state** (`frontend/src/stores/useWorkbenchStore.ts`): `enabled` (false until a
  status says otherwise), `formPath`, `activeView` (an `EditorView`: `"pipeline"` or
  `"workbench"`) and `tables` (`WorkbenchTables | null`: `tables`, `sample`,
  `responseTables` and `fetch`, the fetch's number).
- **`Release`** (`src/haute/_workbench_app.py`): `id`, `form_revision`, `pipeline_name`,
  `haute_version`, `created_at`, `host`, `edit_role` and `environments` (each declared
  environment by name: its `provider`, its `auth` when it has one and the names of its sign-in's
  variables), as `release.json` holds them. **`StoreLocation`**
  (`src/haute/_quote_store/_environment.py`): `environment`, `url`, the database's, and
  `sql_schema`, which its `repr` leaves out, `sign_in`, `None` for a file, and `named_by`,
  what places it and signs it in, as a database not reached is said. **`SignIn`**
  (`src/haute/_quote_store/_providers.py`, frozen): `auth`, `username`, `client_id`,
  `tenant_id` and `secret`, which its `repr` leaves out. **`Provider`** (frozen): `name`,
  `dialect`, `driver`, `name_limit`, `limit_in_bytes`, `token_scope` and `packages`. **`WorkbenchAppReleaseResponse`** (`src/haute/schemas.py`): the
  release without its environments, with `principal`, who
  the host signed in, `quotes`, the app's quote store as its gate checks it
  (**`WorkbenchAppQuoteStore`**: `open`, and while closed the refusal's `error_code`,
  `message` and `differences`, each null or empty while open), and `can_edit`, whether
  the principal may change quotes.
  **`WorkbenchAppFormResponse`**: `form` and `revision`.
  **`WorkbenchAppPriceRequest`**: `form_revision`, `release` and `values` (each input
  table's rows by id, as Preview's quote holds them). **`WorkbenchAppPriceResponse`**: `tables` (each output
  table's rows by name) and `request` (the request priced).
  **`WorkbenchAppQuote`**: an `AppQuote` with its `label` and `title`, its times as ISO 8601
  text and its source as `source_reference`, `source_option`, `source_label` and `relation`.
  **`WorkbenchAppQuoteResponse`**: `quote`, `values`, `tables` (null unpriced), `request`
  (the request its values price as), `dropped` and `moved` (a save's prices, made again on
  the server, not the ones the page showed). **`WorkbenchAppQuoteCreateRequest`**:
  `operation`, `form_revision`, `release`, `values`, `source` (`WorkbenchAppQuoteSource`:
  `reference`, `option`, `relation`) and `reference` (the reference keyed in the window,
  which every creation but an alternative's names). **`WorkbenchAppQuoteSaveRequest`**:
  `operation`, `form_revision`, `release`, `version`, `values` and `shown` (the prices the
  page showed, null to save unpriced). A change's body is its `version` and what it changes
  (`WorkbenchAppQuoteAssign`'s `assignee`, `WorkbenchAppQuoteAmend`'s `reason`); a note's is
  its `note` alone. **`WorkbenchAppQuoteChangeResponse`**: `quote`.
  **`WorkbenchAppQuoteActivityResponse`**: `entries` (`WorkbenchAppQuoteActivityEntry`, an
  `ActivityEntry` with its time as text), `source` and `derived` (`WorkbenchAppRelatedQuote`:
  `reference`, `option`, `label`, `relation`). A refusal's `detail` is `message` and
  `error_code`, with `quote` for a stale one, `missing` for a quote not ready, `landed_at`,
  `changed_by` and `changed_at` for a write superseded, `reference`, `option` and `label`
  for a reference taken, and `cells` (`table`, `row`, `column`) for values not of their
  types.
- **`FormDocument`** (`src/haute/_workbench_form.py`): `spec`, `revision` (the folder's,
  `folder_revision`) and `repairs` (each growth reading the folder made, said, `()` for
  none). **`WorkbenchFormFile`** (`src/haute/schemas.py`): `form` (a `FormSpec`) and
  `revision` (the folder's revision, required). **`WorkbenchStoreSqlRequest`**: `form`
  and `start` (`branch` or `none`). **`WorkbenchStoreSqlResponse`**: `branch`, the
  default branch whose form the SQL starts from, null when it starts from none, and
  `environments`, each a **`WorkbenchStoreSqlEnvironment`**: `name`, `provider` and
  `sql`. **`WorkbenchFormResponse`**: the file
  with `repairs` (`[]` for none). **`WorkbenchFormSaveRequest`**: `form` and
  `base_revision` (the revision the form was read at, required: a folder is read before
  it is saved).
  **`WorkbenchFormSaveResponse`**: the file as written with the save's capture, the
  `SaveCapture` fields (`warnings`, `git_sha`, `identity_required`, `uncaptured`) every
  write answers.
- **Form store state** (`frontend/src/stores/useWorkbenchFormStore.ts`): `form`, `revision`,
  `status` (`idle`, `loading`, `ready` or `failed`), `loadError`, `savedForm` (the form as
  read or last saved, serialised), `dirty`, `undoStack`, `redoStack`, `stale` and
  `saving`.
- **`SampleValue`** and **`SampleRow`** (`frontend/src/utils/workbenchForm.ts`): text or a
  tick, and a row of them keyed by column id. **`Pricer`**, **`PricedValues`** and
  **`SamplePrice`** (`frontend/src/stores/useWorkbenchPricingStore.ts`): a function of a
  `WorkbenchTablesResponse` to a `PricedSample`; a `PricedSample` with `sample`, the values
  priced as the server typed them; and that with its `basis`. **`RowsByTable`** and
  **`PricedRows`** (`frontend/src/utils/sheetValues.ts`): rows by table id, and a priced
  table's rows. **`SheetValues`** (`frontend/src/workbench/useSheetValues.ts`): `rows`,
  `setCell`, `setRows`, `price`, `basis`, `problems`, a message by cell key, `visibility`
  and `quoted`.
  **Preview store state** (`frontend/src/stores/useWorkbenchPreviewStore.ts`), the
  underwriter app's quote: `quote`, `price`, `error`, `pricing`, `readOnly` and `locked`.
- **`SchemaProblem`** (`frontend/src/utils/workbenchForm.ts`): `tableId` and `message`.
  **`WidgetProblem`**: `widgetId` and `message`. **`PageProblem`**: `pageId` and
  `message`. **`SectionProblem`**: `sectionId` and `message`. **`WidgetPatch`**: a partial `Rect` with
  `title`, `fields`, `show_when`, a Table's `rows` and `TableOptions` (`row_numbers`, `density`,
  `gridlines`, `striped`, `fixed_rows`, `header`, `frame`, `transposed`, `groups`, `font_size`,
  `header_font_size`, `header_bold`, `header_wrap`, `header_orientation`, `header_color`,
  `header_font_color`, `header_align`, `grid_color`, `grid_weight`, `grid_style`, `frame_color`,
  `frame_weight`, `column_looks`), a Collection's `columns` and `CollectionOptions` (the
  look it shares with a Table, `labels`, `label_width`, `box_style`,
  `box_weight`, `box_color`, `box_align`, `box_width` and `spacers`), a Chart's `ChartOptions`
  (`category`, `chart_type`, `orientation`, `stacked`, `legend`, `data_labels`), or a
  Heading's `text`, `description` and `HeadingOptions` (`size`, `align`). **`FieldsWidget`**: a component that
  shows schema columns, any but a Heading, as `showsFields` narrows one.
  **`ShownField`**: `key` and `found`, the field's table and column or null.
  **`ChartSeries`** and **`ChartData`** (`frontend/src/utils/sheetChart.ts`): a series'
  `key`, `name`, `values` (a number, or null for a gap) and `labels` (each value as its
  column shows it, or null); a Chart's `rows` (`one` or `many`), `categories`, `series`,
  `axis` (the column whose format the value axis takes, or null) and `stale`.
- **`Rect`** and **`Handle`** (`frontend/src/utils/sheetGeometry.ts`): `x`, `y`, `w`, `h` in
  unzoomed pixels; one of the eight handles, `n`, `s`, `e`, `w` and their corners.
  **`Stacked`**: a section with its `top` on the sheet, the sum of the heights above it.
  **`Drop`** (`frontend/src/workbench/sheetInteractions.ts`): where a component dragged in
  lands, `sectionId`, `rect` (in the section) and `at` (the same on the sheet).
- **View store state** (`frontend/src/stores/useWorkbenchViewStore.ts`): `schemaOpen`, `pageId`,
  `selectedId`, `sheetSelected`, `sectionId` (the selected section, its panel open),
  `zoom`, `creating` (`type`, `clientX`, `clientY`, or null), `movingSection` (a section
  whose header is dragged, `id` and `to`, the place it would move to, or null),
  `panelWidth`, `schemaWidth`, `copied` (a `Copied`: the component Ctrl/Cmd+C copied,
  `widget`, as it was, and `sectionId`, the section it was in, or null), `sheet`, `viewport`
  and `schemaPane`. **`WIDGET_KINDS`** (`frontend/src/workbench/widgetKinds.ts`): per
  kind, `label`, `icon`, `color`, `hint`, `size` and `min`.
- **`REQUEST_INPUT_NODE_TYPES`** (`src/haute/_types.py`): the frozenset of `NodeType.API_INPUT`
  and `NodeType.WORKBENCH_INPUT`; `REQUEST_INPUT_KINDS` (`src/haute/_graph_utils.py`) holds their
  values and `REQUEST_INPUT_TYPES` (`frontend/src/utils/nodeTypes.ts`) is the editor's twin.
  **`WorkbenchInputConfig`**: `tables` and `sample`.
- **`SINGLETON_NODE_GROUPS`** (`src/haute/_graph_shape.py`): `(REQUEST_INPUT_NODE_TYPES, "Quote
  Input or Workbench Input")` and `(RESPONSE_NODE_TYPES, "Quote Response or Workbench Output")`.
- **`RESPONSE_NODE_TYPES`** (`src/haute/_types.py`): the frozenset of `NodeType.OUTPUT` and
  `NodeType.WORKBENCH_OUTPUT`; `RESPONSE_TYPES` (`frontend/src/utils/nodeTypes.ts`) is the
  editor's twin. **`WorkbenchOutputConfig`**: `tables`, the response's tables in the
  Workbench Input's shape.
- **A Workbench Output's tables** (`src/haute/_workbench_output.py`): `WorkbenchTable`s read
  by `parse_workbench_tables`, the ports those with a column. A column's declared type
  admits a frame's column of that dtype or `pl.Null`, and also any integer dtype for `int`,
  any integer, float or decimal dtype for `float`, and `pl.Categorical` or `pl.Enum` for
  `str`; the column is cast to the declared dtype (`COLUMN_DTYPES`).
- **`assemble_workbench_output_from_config(*dfs, config, base_dir=None, ports=None)`**
  (`src/haute/_node_apply.py`): `dfs` are the incoming frames in edge order and `ports` the
  target port of each, aligned; without `ports` it refuses to run. It returns
  `WorkbenchOutputTables`, a `dict` of lazy frames by table name with the tables as `.tables`.

## Control flow

1. **Status.** The editor shell calls `useWorkbenchStore.load()` once. `GET /api/workbench`
   reads `read_workbench_config(Path.cwd())` in the thread pool and answers `enabled` and
   `form`. An enabled status is stored; a disabled one changes nothing; a failure shows an
   error toast naming the cause and the store stays as it started.
2. **The tables, served.** `GET /api/workbench/tables` reads the config the same way, answers
   404 while it is disabled, reads the form with `read_form_document` in the thread pool (through `_saved_tables_response`), builds
   `workbench_tables(spec)`, whose `input_tables` and `output_tables` check their names with
   `check_tables`, answering a `WorkbenchTablesError` as the public contract 422 through
   `contract_error_http_exception`, and answers `WorkbenchTablesResponse` with the tables,
   the sample (unchecked) and the response tables. `input_tables` and `output_tables` make a
   `WorkbenchTable` per schema table of the role, in schema order: its name, its rows per
   quote and its columns, each its name and type, the index column an `int`. `sample_quote`
   takes the columns the sheets hide for the sample (`visibility(spec, spec.sample).hidden`,
   item 32) and walks the input tables: for each, the sample's rows (one for a one-row
   table; a many-row table's as `preset_rows` reads them: each value of the column giving
   its rows taking the first row holding it there, the rows holding nothing there filling
   the rows left in order, each holding its value, any other row left out), each row with
   values typed, the whole quote empty while nothing is typed in it but such values, a hidden column's value never read (a hidden
   True/false column not set to `False` either), a record left out when nothing is typed
   in it but such values, the index set to the row's
   number as the rows stand, a non-empty string coerced for an `int` or `float` column
   (currency signs, thousands separators and spaces stripped; text that is not a finite number
   kept as typed), a ticked box `True`, an unticked box in a filled `bool` row `False`; a table
   with no records is left out.
3. **Fetched.** `useWorkbenchConnections` calls `refreshTables()` when the workbench is
   enabled and whenever `executionGeneration` advances, which it does on every document
   adoption and never on a save's acknowledgement, and records the fetch's number against the
   generation; `useWebSocketSync` calls it too on each `workbench_form_changed` frame and each
   connection, as the form folder may have changed on disk meanwhile. Each fetch has a number,
   and only the newest settles: into `tables`, or into one error toast that leaves `tables`
   as it was. A fetch whose tables, sample or response tables differ by `canonicalJson` from
   the last one's raises the node-data epoch (`useNodeDataStore.bumpEpoch`), so every preview
   and reader of node data asks again, as a config change makes them, the workbench nodes
   being given those tables wherever they run though nothing in the graph holds them; the
   first fetch and one bringing the same leave the epoch as it is.
4. **Held.** A workbench node holds no tables, so no fetch changes one. Its ports are
   `workbenchNodePorts` of the newest fetch: `PipelineNode` subscribes to the store's tables
   for a workbench node alone and draws a Workbench Input's source handles
   (`requestInputSourceHandles`) and a Workbench Output's target port rows from them. While
   the document can change, `useWorkbenchConnections` holds the connections of the graph the
   editor shows to them on each newest fetch, from one made since the document was adopted,
   and on each change of the graph's `edges`: `workbenchDisconnections` finds each
   connection out of a Workbench Input whose `sourceHandle`, or into a Workbench Output
   whose `targetHandle`, is none of its ports, wherever the tables moved, a connection
   through a submodel's boundary left as it is; they are removed through `setEdgesRaw`, no
   undo step, the document marked dirty, and reported per node in one warning toast that
   names the node and says the workbench no longer has the table each read or filled. An
   undo, a redo or a paste that puts one back changes the
   edges and has it removed again at once.
5. **Shown.** While the workbench is enabled, `NodePalette` renders the Workbench Input in the
   Quote Input's place in `PALETTE_TYPES` and the Workbench Output in the Quote Response's,
   each dragged with its plain default config (`node_defaults.json`, `{}`), its ports there
   at once when a fetch has landed. `NodeConfigEditor` renders `WorkbenchInputEditor` and
   `WorkbenchOutputEditor` for them, each reading the store's tables.
6. **One request input.** Save's `_validate_singletons` and `resolve_config`, before
   `prune_for_deploy`, call `validate_singleton_groups` on the flattened pipeline. In the
   editor, `singletonTypesInDocument`, the drop check and the paste check count occupied
   singleton types through `singletonTypesOccupiedBy`, which gives both request inputs for
   either, and a refused drop toasts `singletonLimitMessage`.
7. **Given its tables; frames without a request.** A workbench node's config file holds
   neither `tables` nor `sample`: `reject_form_given_keys` (`FORM_GIVEN_KEYS` in
   `src/haute/_config_validation.py`) refuses either where a config file is read
   (`_normalise_loaded_config`, which the parser and a standalone run share), where the parser
   builds a node's config from its decorator (`_build_node_config`), where save checks a
   posted graph's configs (`_validate_declared_config_keys`, before anything is written) and
   where a config file is written (`_prepare_config_for_sidecar`), and recovery drops both as
   retired fields (`_field_hints` in `src/haute/_node_config_recovery.py`). The keys stay
   declared on `WorkbenchInputConfig` and `WorkbenchOutputConfig`, as the config a run builds
   a node from holds them, given by `with_form_tables`: in `flatten_executable_graph`, which
   every route running a browser graph calls, from the working directory's form folder, read
   anew for each request, or from the request's `workbench_form` (`PreviewNodeRequest`); in
   `flatten_with_form_tables`, the assistant's for a graph it resolves or dry-runs
   (`_PreparedGraph.build`, its tools' schema and profile reads, its data checks, and
   `_brief_flat`, whose brief cache keys the given tables beside the project revision); in
   `handle_run` for `haute run`; and in `resolve_config` for deploy (item 9). Each copies the
   graph, so neither the request's graph nor a parsed one changes, and a graph without a
   workbench node reads no form. The given configs are what the node cache keys and the
   graph fingerprint read, so a change of the form's tables or sample misses every cached
   result of the nodes downstream of them. `_build_api_input` gives a Workbench Input a source
   that calls `resolve_workbench_input_from_config` with its given config and the ports'
   demanded columns; the standalone runner in `src/haute/_standalone_nodes.py` calls it with
   the node's sidecar path, and `_workbench_node_config` reads the sidecar and gives it the
   tables of the form folder of the project holding `base_dir` (`get_project_root`).
   `Pipeline.score` seeds a Workbench Input with the frames it is given, but first holds it
   to its form (`_check_seeded_workbench_input`): `seeded_workbench_input_ports` reads its
   sidecar and the form's tables (`workbench_input_ports_from_config`, the sample unread),
   and a connection's `source_port` none of them names raises `ExecutionError`.
   `workbench_table_frames` reads the ports
   with `workbench_input_tables` (`parse_workbench_tables`, then `port_tables`; no table, or
   none with a column, raises `WorkbenchInputError` saying what to add). With no sample
   (absent, `None` or `{}`) each port is a one-row `LazyFrame` of its `frame_schema` and null
   values. With one, it reads the sample whole before it projects, through `read_quote`: the
   sample must be a `dict`; each one-row table's part a `dict` or `None` (its one row, of
   nulls when `None`), each many-row table's a `list` or `None` (its rows, none when `None`)
   whose entries are `dict`s; each declared column's value is read by `_value` as its type
   holds it (`str`: a `str`; `bool`: a `bool`; `int`: an `int` that is not a `bool`; `float`:
   an `int` or finite `float` that is not a `bool`, as a `float`; `date`: a `YYYY-MM-DD`
   string `date.fromisoformat` accepts), `None` staying null, and a frame is built per table
   with `pl.DataFrame(columns, schema=frame_schema)`. A refusal names the spot
   (`policy.limit is 'lots', not 'int'`, `items[1] is a value, not a row`, `the sample is a
   list, not an object`), and `workbench_table_frames` raises it as `WorkbenchInputError`
   "The workbench's sample does not fit this Workbench Input's tables: <reason>. Correct it
   in the workbench and save it.", a public contract error, so a preview in its worker
   answers 422 with it. Each port is then its frame cut to the demanded columns
   (`_demanded`: every port whole without a demand; an unknown port or column a
   `ValueError`; an empty demand kept by one carrier column), the null quote's one-row frames
   while there is no sample. `snapshot_backed_inputs`
   leaves the node out, since it has no structured path; `own_facts` in
   `src/haute/_seed_plans.py` reports it cheap and slice-transparent, and the bounded-admission
   check admits it without a read; `api_input_port_metadata` in `src/haute/_ram_estimate.py`
   reads `workbench_table_labels` first, answering no metadata when the tables fail or lack
   the port, then sizes the port from its frame through `_detailed_dataframe_metadata`,
   letting a sample's misfit through.
8. **Its table points.** In `src/haute/_data_points.py` a Workbench Input's table point keeps
   the `api_input_table` kind but `_resolve_workbench_table` resolves it current at once from
   `workbench_table_labels`, never the sample, versioned by its tables, its sample and its
   lineage, with no input identity, so `src/haute/routes/_node_data_service.py` reports it as
   reading directly. Leasing it runs `workbench_table_frames` for the port and projects the
   demand; running it answers "A Workbench Input's tables are read directly; there is nothing
   to cache."; `clearDelegatedData` in `frontend/src/hooks/useNodeDataCache.ts` clears nothing
   for a point that reads directly. For the cache report, `api_input_table_labels` takes the
   labels from `workbench_table_labels` and `api_input_table_digests` gives none, so
   `points_for_graph` reports the tables together as one row that reads directly.
9. **Deploy.** `resolve_config` gives the flattened pipeline's workbench nodes their tables
   once, after `validate_singleton_groups`: from the form `haute app build` read for the
   release it ships, else from the project's folder (`read_project_form`, a disabled
   workbench, a folder that does not exist or a file that cannot be read raised as the reader
   words it), keeping the form on `ResolvedDeploy` as its `workbench_form`, and raises `DeployError`
   naming each connection `connection_errors` finds before anything is pruned or run;
   `_workbench_form_errors` holds that form to the sheets. `shipped_graph` carries the given
   tables in the bundle and leaves every sample out, so a deployed pipeline and a release
   never read a folder. Deploy reads a Workbench Input's tables, never its sample. For a Workbench
   Input, `infer_input_schema` in `src/haute/deploy/_schema.py` returns `request_schema` of its
   ports as dtype strings, through `_workbench_tables` (a `WorkbenchTablesError` becoming a
   `ValueError` naming the node), and `_read_sample_row`, which execution-policy planning and
   the output-schema dry run share, returns `null_quote` of them, one quote with nothing
   filled in (each one-row table an object of nulls, each many-row table a list of one row of
   nulls) under the stand-in identity, typed by that schema. When the dry run falls back to the hard-capped batch worker,
   `_capped_worker_output_schema` passes the sample's schema to `prepare_batch_scoring` as
   `input_schema`, carried on `BatchScoreRequest`, and the worker builds its input frame with
   it, so the quote of nulls keeps its types; a served request passes none. Where
   `_score_graph_lazy` injects the live frame as a Quote Input's output, for a Workbench Input
   it reads the request once, at planning, through `workbench_request_frames`: the records of
   a `QuoteRequest` (`src/haute/deploy/_scorer.py`), which `/quote`, the batch worker and
   `score_test_quotes` hand over as the quotes were sent and MLflow's `predict` as the
   records its signature, a map of anything per table, let through (`_request` in
   `src/haute/deploy/_model_code.py`), or `to_dicts()` of a frame, refused unless exactly one, read
   through `read_quote` into a frame per port, which the memory estimate takes as the
   per-port frames of `runtime_source_frames_by_node` so each consumer is sized from its
   table, a refusal
   raised as "The request does not fit this Workbench Input's tables: <reason>."; the walk
   hands each edge its port's frame by its handle, as it does a multi-frame source's.
   `score_test_quotes` in `src/haute/deploy/_validators.py` therefore scores a Workbench
   Input's test-quote cases one request each and checks each against its own request's one
   row, reported by its row in the file (`_validate_expected_outputs_per_case`; a case
   answered with other than one row fails on its own), where a Quote Input's cases are one
   request.
   Recovery and the document's identities take a request input's source handles from
   `recoverable_request_input_source_handles` in `src/haute/_editor_identities.py`, which
   names none for a Workbench Input: it holds no tables, and each document edge carries its
   input name, its handle, from `edge_input_name`, as the editor derives it.
10. **Connected.** `PipelineNode` renders a Workbench Output's body as `FramePortRows` with
    `direction="target"`, one row per port of the fetched response tables
    (`workbenchNodePorts`), each a target
    `Handle` whose id is the table's name, or "No tables" with none; it has no default input
    and no source handle, and the ports join the signature that re-measures the node's
    handles.
    `validatePipelineConnection` refuses a connection into a Workbench Output whose target
    handle is none of its tables ("Connect to one of <node>'s tables"), one to a table that
    already has a connection ("<table> is already filled by <node>"), and one from a node that
    already fills another of its tables ("<source> already fills <table>; a node fills one
    table"), before the shared input-name check. `onConnect` keeps the handle as the edge's
    `targetHandle`, and save and codegen write it as `target_port`.
11. **Run.** `_build_workbench_output` builds a function of the incoming frames that calls
    `assemble_workbench_output_from_config` with the config and `ports=ctx.target_handles`. It
    is registered opaque, so projection asks its parents for their whole frames: a column a
    frame lacks reaches the node, to be filled with nulls, rather than failing a projection
    contract upstream. With nothing connected it reports itself a source, so the walk calls it
    with no frames and it fills every table as one nothing is connected to. The standalone runner calls it with the node's sidecar and `target_ports`:
    `Pipeline._execute_transform` passes the target port of each incoming `pipeline.connect`,
    through `Node._invoke` and `run_configured_node`. The function pairs each frame with its
    port, raising when a port is missing, is none of the tables' names or repeats one, reads
    `workbench_output_tables`, and calls `fill_workbench_tables`. A table nothing is connected
    to is a `pl.LazyFrame` of its columns' dtypes holding one row of nulls when it is one-row
    and no rows when it is many-row. Otherwise, against its frame's `collect_schema()`, each
    column is the frame's column of its name cast to its declared dtype when the frame has it
    and `column_fits` its dtype, and a typed null literal otherwise; the table is the frame
    selected to those columns in order, so a literal takes the frame's length. A connected
    one-row table is wrapped in `limited_python_scan` of a producer that collects it through
    `execution_collect` and raises unless it has exactly one row. Nothing is collected while
    the tables are built, so a schema-only walk reads them as it reads any frame.
12. **Its tables, previewed.** The walk keeps the result as a multi-frame bundle, as it keeps
    a Workbench Input's, so the preview of a Workbench Output with several tables shows the
    table named by the request's `port_label`, its `frame_columns` listing every table for the
    preview's table picker, and one with a single table shows that table.
    `previewPortLabel` in `frontend/src/hooks/usePipelineAPI.ts` asks for the first of
    the fetched response tables' ports (`workbenchNodePorts`). `is_node_output` in `src/haute/_seed_plans.py` never makes a
    Workbench Output a node-output snapshot point, as it never makes a request input one: a
    bundle is not one frame, so a preview with shared snapshots never captures it, though two
    inputs make it a join point. The executor renders only a Quote Response's preview with
    `render_output_document`. `execute_trace` in `src/haute/trace.py` traces a cell of a
    Workbench Output's table from the node connected to that table
    (`_workbench_output_trace_target`): the trace request's `port_label` names the table (a
    Workbench Output with one table needs none), and the target becomes the source of the edge
    whose `targetHandle` is that table, at the same row, its frame the one that edge's
    `sourceHandle` names when the source emits several (a Workbench Input fed straight to a
    table). The column and the clicked row's values keep their names, those of the frame's
    columns that fill them. A column filled with nulls has no value to trace and fails: at once
    when nothing is connected to its table, and once the traced frame is built when it lacks
    the column of that name or holds it with a dtype `column_fits` refuses
    (`_execute_trace_core`'s `table_fill`, a `_TableFill` of the declared types of the
    columns the frame fills, `frame_columns`, and the refusal), as does the quote's reference
    or option of a table leading with them, which the response gives whatever the frame
    holds, and the clicked values of columns the frame does not fill, nulls in the table,
    are left out of matching its row. The listed seeds
    are cut to that node's lineage, since the preview listed every table's. Without a table
    to name, it refuses the bundle as it refuses any multi-frame target. Each refusal starts
    "Target node" and names the Workbench Output, and the trace route answers it with 400.
13. **The response node.** `Pipeline._resolve_output_node` returns the one node whose
    `_node_type` is in `RESPONSE_NODE_TYPES`, raising `ExecutionError` naming them when there
    are several; `find_output_node` picks the response node the same way; trace correlation
    carries values through a response node unchanged. Where the pipeline answers a request,
    `as_response` turns the response node's result into the frame it answers with:
    `Pipeline.run` and `Pipeline.score` before collecting, and `_score_graph_lazy` in
    `src/haute/deploy/_scorer.py`, which a deployed pipeline's `/quote`, test quotes, the
    output-schema dry run and batch scoring share, before selecting `output_fields`. For a
    Workbench Output it calls `workbench_response` with the quote's identity (`quote_identity`
    of the Workbench Input's frames: `_score_graph_lazy`'s `quote_tables`, `Pipeline.run`'s
    source output, the frames `Pipeline.score` was given): the response's schema is `quote_schema`
    of its tables, and a `limited_python_scan` whose producer collects each table through
    `execution_collect` (a one-row table's scan having checked its one row), takes a one-row
    table's row as an object and a many-row table's rows as a list of objects, and builds the
    one-row response with `pl.DataFrame([document], schema=...)`. A Quote Response's result is
    its document already, so `_quote_response_content` renders either unchanged.
14. **One response node.** `validate_singleton_groups` counts both response node types as one
    group. In the editor, `singletonTypesOccupiedBy` gives both for either, so the palette greys
    out the one it offers and the drop and paste checks refuse a second, with
    `singletonLimitMessage` naming "Quote Response or Workbench Output".
15. **Scaffolding.** `handle_init` answered for workbench pricing writes `haute.toml` through
    `workbench_haute_toml` (the `[workbench]` table, `enabled = true`, with the app's host, its
    hosted environments' Container Apps and the store's environments on one database), and
    writes the folder files of `blank_form(name)` (one sheet, "Sheet 1", Haute's quote_details
    alone, no sample: `form.json`, `schema/quote_details.json`, `sheets/sheet_1.json` and
    `sheets/sheet_1.layout.json`) to the pipeline folder's `workbench/`, reporting both in its
    summary, having held every path of the folder to `ordinary_path_problem`
    (`_form_folder_problem`) before writing anything, so a pipeline folder reached
    through a junction refuses the init with exit 1 and nothing written. A project that
    already has a `haute.toml` is refused without `--force`, `--workbench` included;
    `--force --workbench` rewrites the scaffold but keeps a form folder and a
    `connections.toml` that are already there. `_SEED_PATHSPECS` carries `workbench/` and `*/workbench/*`, so
    `set_working_branch`'s unborn-repository seed stages the form with the pipeline's
    files, and the generated deploy workflows need no trigger of the form's own: the
    pipeline folder they watch holds it.
16. **The form, read.** `GET /api/workbench/form` reads the config through `_enabled_config`
    (404 while disabled) and `read_form_document` (the folder module) in the thread pool. A
    read holds the form folder's lock, a `FileLock` of the project's own (a file under the
    system temp folder named by the hash of the project root's resolved, case-normalised
    path), which every save holds from its stale check to its last write and every reader
    holds while it reads, in the editor's server, in another server process and in the
    CLI (`haute app build`, deploy) alike, so no reader sees a save half written; the
    server's routes also read under `repository_mutation`, which a branch switch holds. It
    lists the folder (`os.scandir`, recursively, entries' names as the disk spells them),
    refuses a missing folder, reads `form.json` and then each file the index names, each
    file's bytes once into memory, and lists the folder again: when the second listing or
    any file's size and modification time differ from the first, a writer that takes no
    lock (git outside Haute, an editor) changed it, and the read starts again, refusing
    after three tries ("<folder> changed while it was being read: try again"). It refuses any file the listing holds that the index does
    not name (a sample file for a table the schema does not hold or for an output table
    included) and any file whose name on disk is not its name in lower case. It checks each file against
    its model, builds the `FormSpec` (the tables in the index's order, each sheet's
    components placed from its layout file, names turned into ids, quote_details and the
    identity columns added through `FormSpec`'s validators as before), grows a section or
    sheet too small for a component in it (`_grown_to_hold`, a merge or a hand edit can leave
    one so) and says each growth in `repairs`, and answers the form with the folder's
    revision (`folder_revision`: `content_hash_bytes` of the canonical JSON of each file's
    path from the folder and the content hash of the bytes the read took, sorted by path,
    every file in the folder counted) as `WorkbenchFormResponse`. The form store reads a form
    with repairs as unsaved, without history, a warning toast naming the form and each
    growth ("<form> opened with its layout fixed, unsaved: …"), so the next Save writes
    it. The other readers (the saved tables, the deploy checks, `haute app build`) take the
    grown form as it is read.
17. **The view.** `useWorkbenchStore.showView("workbench")` (the switcher, or a panel's
    "Edit in Workbench") sets `activeView`. `App` reads it as `pipelineActive`: the pipeline
    region (`data-testid="pipeline-view"`) gets `invisible` and `inert`,
    `useKeyboardShortcuts` is given `enabled: false` and registers nothing, the Ctrl/Cmd+Enter
    effect returns early, React Flow's `deleteKeyCode` and `panActivationKeyCode` become
    null, a store subscription closes the context and connection-drop menus on leaving,
    `WorkbenchToolbar` replaces `Toolbar` and `WorkbenchView` renders over the region, both
    lazily. The view calls `useWorkbenchFormStore.load()`, which fetches the form unless
    `status` has left `idle`, and registers `useWorkbenchShortcuts` with the host's
    `requestSave`.
18. **Edited.** The schema editor calls `change(update)` with a pure operation from
    `utils/workbenchForm.ts`; the store keeps the previous form on `undoStack`
    (`appendHistoryEntry`), clears `redoStack` and recomputes `dirty` against `savedForm`;
    `undo` and `redo` move forms between the stacks. Problems are recomputed from the form
    on each render with the document's reserved labels.
19. **Saved.** Save in either toolbar, and Ctrl/Cmd+S in either view, call the host's
    `requestSave` (`App.tsx`): the git readiness gate, then `saveProject`, the project's
    save (item 27), whose form step is the store's `save`. `save` takes its turn after the
    saves and syncs before it and PUTs the form with `revision` as `base_revision`. `_save_form`, under `save_lock` and `repository_mutation` and holding the form folder's lock until its last write, reads `folder_revision` and refuses
    with `StaleDocumentRevisionError` when it is not `base_revision` (a missing folder
    included), and refuses 409 with nothing written a folder holding a file its index
    does not name (`folder_index`, the read's inventory), so it deletes only the
    form's own files. Before taking the lock it renders every file of the form (`folder_files`:
    path from the folder to bytes; it raises `FormFolderNameError`, naming each problem,
    for names the folder cannot hold, answered 422 before anything is written) and
    reads them back (`form_from_files`), answering 422 with nothing written files that
    would not read back as a form, as a reference kept under a name the schema did not
    hold, which another column of its component's has taken since, can leave them; it
    then
    compares each with the bytes the stale check's read took, and checks every path it will write,
    delete or make a folder at with `ordinary_path_problem` (none through a link, a
    junction, a hard link or a reserved name, inside the project or out of it), refusing
    409 with nothing written when one fails. It then stages, through the pipeline save's
    `_stage_artifact_write_bytes` and `_stage_artifact_delete`, a write of each file whose
    bytes differ and a delete of each of the form's files on disk that the new form does
    not have (a table's schema and sample files, a sheet's file and layout file, a sample
    whose table's rows went), never a file the on-disk index did not name; file names
    being lower case, a write and a delete never name one file through two spellings. Any
    failure rolls every staged file back (`_rollback_artifacts`) and answers 409 with the
    cause; a file the rollback could not put back is named in the same message by its path
    in the folder (`schema/` and `sample/` hold files of one name), to restore from version
    history, and one another writer changed meanwhile is left as that writer left it and
    logged (`rollback_skipped_external_change`). `_generate_commit_message` names a form file by the folder's part
    and its name, `workbench/schema/perils`, `workbench/sheets/br_coverages`,
    `workbench/sheets/br_coverages layout`, `workbench/sample/perils`, `workbench/form`,
    three at most as for any save. `capture_save_in_ledger` (`haute.routes._save_pipeline`, the pipeline save's
    capture, given every written and deleted path relative to the project) commits the
    files on the ledger when the clone has a working branch, and the response answers
    the new revision with the capture's `git_sha`, `warnings`, `identity_required` and
    `uncaptured`. The store adopts the revision, setting `savedForm` and recomputing
    `dirty` (edits made meanwhile stay unsaved), toasts the save, reports the capture
    through `reportSaveCapture` (the commit on the branch indicator, the history nonce, the
    files kept when the capture was skipped or failed, each warning toasted, the identity
    prompt unless dismissed) and
    fetches nothing itself: the project save fetches the tables afresh next (item 27), so
    the Workbench Input's and Workbench Output's ports are the saved form's before the
    pipeline is saved. A 409 whose detail
    starts with `stale_document_revision` sets `stale` and toasts; `reload` reads the form
    again and clears it.
20. **The sheets, shown.** Once the form is read, in either mode, `WorkbenchView` renders `PageTabs` over `SheetCanvas`, which registers its viewport and
    sheet with the view store, measures the viewport, fits the zoom once, gives the
    components the sheet's values (`useNewQuoteValues` while building, in Preview
    `useSampleValues`, or `useQuoteValues` in the underwriter app, through
    `SheetValuesContext`, only the one in use made), and draws `activePage(form, pageId)`'s
    sections while building, stacked by `stackSections` each under its bar, `SectionBar`,
    drawn first in it, each `SheetWidget` with the schema, from which it takes its
    `shownFields`, the first of its `widgetProblems` and `hidden`
    when the values' `visibility` leaves it out (as it does every component of a hidden
    section), or in Preview `previewSections` of `shownPage(form, pageId,
    visibility.pages)`, held by `useHeldWhilePressed` and kept in place by `useKeptPlace`,
    or the note when that is null; either way each section's components in `readingOrder`,
    section by section, each with its `layer`, its place among the page's components
    (`pageWidgets`) from 1, or one more than the page has components while selected, and
    `DropGhost` one above that. While `schemaOpen`, `SchemaPane` lies over the viewport's
    left part and `onSheet` in `sheetInteractions.ts` takes a point inside it for off the
    sheet, so nothing dropped there lands on the sheet under it; `showPage` from a tab
    shows that sheet, in either mode.
21. **A component added.** `startCreate` on a palette item tracks the pointer, publishing
    `creating` on each move: `DragChip` follows it while `dropAt` finds no place, and
    `DropGhost` draws the drop's `at` once it does. Released on the sheet, the item becomes
    `createWidget(type, drop.rect)` added to `drop.sectionId` through `change` with
    `addWidget`, which keeps it inside the section, and selected; released elsewhere, or
    without moving, nothing is added. The palette's Section goes the same way through
    `sectionDropAt`, its ghost the line where it would start: released on the sheet,
    `createSection` of the showing sheet goes in through `splitSection` at the drop's line
    or `addSection` at its place, in one `change`, and is selected.
22. **Moved and resized.** `startTransform` on a frame or a handle selects the component
    and tracks the pointer, a field focused elsewhere left first so what was typed into it
    commits, as its own undo step, before the form is read; each move replaces the
    form through `setFormRaw` with a form made from the form as pressed: for a move,
    `moveRect` of the component's rect on the sheet (its section's top added) by the
    pointer's travel divided by the zoom, then `placeWidget` into the section `sectionAt`
    gives for its top edge among the stacked sections as pressed, its `y` in that section
    snapped and kept inside it (`keptInside`); for a resize, `resizeRect` of the rect in its
    section, through `updateWidget`, which grows the section past its bottom. Either
    changes only the section the component ends in, which moves nothing above it, so the
    component stays under the pointer. The press begins a gesture of the form store's
    (`beginGesture`, with the form as pressed and what stops the tracking); released or
    cancelled, `endGesture` records the form as pressed, so the gesture is one undo step,
    and nothing, redo kept, while the form is as pressed (a click, a drag held at a limit or
    brought back where it began). An undo, a redo or an edit during the drag finishes it
    first, recording it and stopping the tracking, so the undo reverses the drag and the
    release does nothing more. The
    keyboard's nudges go through `moveWidget`, one snapshot per burst, and a nudge that
    moves nothing records nothing. A section's edge, `startSectionResize`, replaces the form
    the same way through `setSectionHeight` of the height as pressed and the pointer's
    travel, a gesture as a move is; its header, `startSectionMove`, publishes
    `movingSection` while it is dragged and calls `change` with `moveSection` once on
    release, a gesture too, so an undo or an edit during the drag ends it and clears the
    line, and the release moves nothing.
23. **Properties.** `PropertiesPanel` finds the selected component in the form and edits it
    through `change` with `updateWidget`, `setTableRows` (a Table's rows), `moveInOrder`,
    `toggleField`, and a Collection's `addSpacer` and `removeSpacer`; the fields offered
    are the schema tables whose `rows` match `rowsShown(widget)`, each usable when its
    `tableGrain` matches the grain of the first field already shown, or for a Chart every
    table, each usable when its `tableGrain` matches `chartGrain(form, widget)`, its columns
    that are not `plottable` disabled, with `ChartSettings` editing its options; a Heading's
    panel is `HeadingSettings`, its text, description, size and alignment through
    `updateWidget`. While `sheetSelected`
    it edits `activePage(form, pageId)` instead, through `renamePage`,
    `setPageShowWhen`, `setPageWidth` and `removePage`, and while `sectionId` names a section of the form, that section,
    through `renameSection`, `setSectionShowWhen`, `setSectionHeight`, `fitSection`,
    `addSection` and `removeSection`. `ShowWhenEditor` hands every change up whole, so each
    is one `change` (item 33).
24. **The sample, typed.** While building, `SheetWidget` holds each component's body
    `inert`, so its cells take no typing and a press lands on the frame, and the values are
    `useNewQuoteValues`', a new quote's. In Preview a `Cell` commits a value through the
    sheet's values' `setCell`, `change` with `withSampleCell(spec, tableId, row, columnId,
    value)`, row 0 for a Collection's one-row tables and the grid's row otherwise; a delete
    and `RowMenu`'s entries call `setRows`, `withSampleRows`, with every input table the grid shows,
    so one edit changes them all. While those tables hold no rows, a value typed into one
    calls `setRows` instead, each table made up to the grid's rows (`padRows`) and the value
    set in its own (`withCell`), so the rows the grid starts with stay, as every value typed
    does in a grid showing a table a column gives its rows (`presetColumn`), so what is
    written is its rows as shown, each holding its value; a delete takes the row out of the
    rows shown, each table made up to them first.
25. **Priced.** The view sets the pricing store's pricer on mount and clears it on unmount,
    and calls `schedule` when `pricingBasis(form)` changes while Preview shows, and when
    Preview shows again, and `cancel` when Preview is left, so nothing waiting or asked
    for meanwhile is priced while building; the basis holds the whole schema, the columns the sheets hide and the
    sample, so a column renamed or retyped, hidden or not, and a condition, a field or a
    value that changes which columns are hidden change it too. After
    `PRICING_DELAY_MS`, `priceNow` runs one pricing, or, while the sample has nothing typed
    in the cells the sheets show (`quoteFilled`), sets no price and no error and prices
    nothing: `fetchWorkbenchFormTables(form)`
    (`POST /api/workbench/tables`, `_tables_response` on the posted form), then the pricer,
    `priceSample(resolveGraph(), form, workbench, activeSource)`, which sends the document
    unchanged with the form as `workbench_form` and previews the Workbench Output's ports in
    one request, one run of the pipeline on the form's tables and sample (both through
    `priceForm`, the pricer given the form that was posted for its tables); the store keeps `{basis, tables, sample}`, the
    sample as the server typed it, or the reason. A request made while one runs sets a
    flag, and the run prices once more when it finishes.
26. **Shown.** `FieldBoxes` reads the values' `price` and shows
    `price.tables[table.name][0][column.name]` for an output column; `FieldGrid` shows, for
    a many-row output column, the row `linedUpOutputs` lines up with each grid row through
    `pricedRows` (the typed rows from `price.sample[input.name]`, the output rows from
    `price.tables[output.name]`, the input table the first shown with the output table's
    `tableGrain`), and a grid of output columns alone a row for each of the first output
    table's answer (`answeredOutputs`, as a Chart of them), its `rows` rows of dashes
    before a price, so it reads as not yet priced or connected; both dimmed when `price.basis` is not the values' `basis`;
    `WorkbenchToolbar` shows `error`. A Chart plots them (item 34).
27. **The project saved, and committed.** `saveProject` (`App.tsx`) is the one save of the
    project: from Save and Ctrl/Cmd+S through `requestSave` and from Commit through
    `requestCommit`, both behind the git readiness gate (the working-branch or divergence
    modal, whose confirmation runs the pending save or commit), and from a move's Save
    first, the Git panel's save before a switch and the identity prompt's retry directly.
    Project saves take turns (`projectSaveTurn`, a `serial` queue, as the form store's
    are): a save asked for while one runs waits for it and then runs the save as it
    is by then (`saveProjectNowRef`). The save, `saveProjectNow`, refuses on the fence,
    read live each time (`refused()`): before the form is saved, after the form's save,
    after the tables fetch and after the pending commits, since each is a wait during
    which a submodel can open, the assistant's turn start or the document change. The
    fence: inside a submodel (`parentGraphRef`), during the assistant's turn
    (`useUIStore.assistantTurn`, "The assistant is working on the pipeline: wait for its
    turn to finish, then save."), and a document whose `can_mutate` or `can_save` is false
    or whose graph is not synchronised (`documentReadOnlyReason`, the pipeline save's own
    refusal). After the first check it captures the files earlier writes left off the
    ledger (`captureUncaptured`, a failure toasted and the project saved all the same).
    Between the checks, while the workbench is enabled, it saves the form
    through the form store's `flush` (the store imported lazily, so a project that never
    showed the view saves nothing) and fetches the tables afresh (`refreshTables`, then
    `awaitTables`; a fetch that failed refuses the project's save with "The pipeline was not
    saved: the workbench's tables could not be read.", and no pipeline is saved); then
    waits for the pending commits (`waitForPendingCommits`, an update still resolving its
    identity landing or failing first, a failure saving nothing), holds the workbench
    nodes' connections to the tables fetched at once (`holdConnections`, which removes one an
    undo put back while the save waited; false, with the same toast and nothing saved, while
    no fetch since the document's adoption has landed) and saves the pipeline through
    `handleSave`. Commit
    (`flushSaveThenMilestone`) opens the milestone modal once that save resolves true.
28. **Synced.** The server's file watcher announces each batch of changes to the files in the
    form folder, a file added or deleted included, once
    (`workbench_form_changed`: an edit by hand, a pull or merge outside Haute, a save from
    another window, or the view's own save), which `useWebSocketSync` counts on the
    workbench store's `formChanges`, with each connection, since a change while
    disconnected went unannounced. The form store subscribes to that count and to
    `executionGeneration` at module load and runs `sync` on each advance, after any save in
    flight: `fetchWorkbenchForm`, then nothing at the same revision (the view's own save),
    the form adopted (history dropped) while it has no unsaved edits, else `stale` set (the
    banner's Reload reads it); a failed read is one error toast and leaves the form as it
    is.
29. **Preview.** Preview in `WorkbenchToolbar` calls `showMode("preview")`:
    `WorkbenchView` renders `PageTabs`, read only, over `SheetCanvas`, which gives the
    components `useSampleValues` (the form's sample, edited through `change`, the pricing
    store's `price`, `pricingBasis`, and `quoteProblems` of the quoted schema and the sample
    once anything is typed) and draws the sections that show, stacked with no gap where a hidden
    one was and no section bars, each `SheetWidget` that shows neither editable
    nor selected, with no ghost; a press on the viewport holds what is drawn until a turn
    after its release (`useHeldWhilePressed`), and a draw that moves the sections scrolls
    the viewport by as much as the section holding the focus, or the first reaching below
    the viewport's top, moved (`useKeptPlace`);
    `WorkbenchPalette` disables its items, `WorkbenchToolbar` its Display, `SchemaPane`
    gives `SchemaEditor` `readOnly` while it is open, and `useWorkbenchShortcuts` leaves
    the editing keys alone, undo and redo applying. Build calls `showMode("build")`;
    either leaves `schemaOpen` as it was.
30. **Priced as typed.** There is no Price and no Clear, and Reset starts the sample
    again as a new quote (`newQuoteSample`), which `quoteEligible` does not price: the
    sample is priced as item 25
    says, whatever its cells hold, `quoteProblems` marking cells and never stopping it,
    and the server leaves the hidden values out of the quote it makes (item 2). In the
    underwriter app, `openRelease` sets the view store's `underwriterApp`, so `SheetCanvas`
    gives the components `useQuoteValues` (the preview store's `quote`, `setCell` and
    `setRows`, its `price`, `valuesBasis` of the schema, the columns the quote hides and
    the quote, and `quoteProblems` once anything but the locked columns is typed while
    the quote may change); `setCell` and `setRows` schedule the store's `priceNow`
    `PRICING_DELAY_MS` later, which prices through the `quotePricer`, and a save first
    `settle`s it.
31. **Switched.** After a branch switch in place (`GitPanel`'s lane menu, `BranchManager`)
    has succeeded, `discardFormEdits` (`hooks/useProjectDirty.ts`) reads the form again
    when it held unsaved edits the user chose to discard, so the destination's form shows;
    a switch that failed keeps them, and a form as saved is left to the adoption's sync.
32. **Shown when.** `visibility(spec, rows)` on the server and `sheetVisibility(spec,
    rows)` in the view decide alike, `rows` the values by table id as the sample holds
    them. Everything starts shown. Each round, a sheet is hidden whose conditions fail, a
    section whose sheet is hidden or whose conditions fail, a component whose section is
    hidden or whose conditions fail, and a field of a Table or a Collection whose component
    is hidden or whose conditions fail (its `column_looks` entry's `show_when`, always without
    one), where a `show_when`'s
    conditions pass when it has none, when `match` is `all` and every one holds, when it
    is `any` and one does, or when it is a number and at least that many do (`_matches`
    on the server, `matches` in the view), a group holding by its own `match` over its own
    conditions in the same way. A condition on an output table's column or rows
    (`_on_price` on the server, `onPrice` in the view) holds in the rounds whenever it fits,
    whatever the price: the view applies it afterwards (`withPrice`), and the server, which
    decides what a quote leaves out, never does. Any other condition holds when it fits (`condition_fits`, or
    `conditionFault` null: its table an input table with one row per quote that has its
    column; its test one `testsFor` the column's type gives, True/false `ticked` and
    `not_ticked`, Text `filled`, `empty`, `equals`, `not_equals`, `contains`,
    `not_contains`, `starts_with`, `ends_with`, `one_of` and `not_one_of`, Integer and
    Decimal all but the four on text within text and `above`, `at_least`, `below` and
    `at_most`, Date those but `one_of` and `not_one_of`; as many values as the test takes;
    and each value one the column could hold; or, tested against another field, a test of
    one value, no values typed, a field of the column's type in a one-row input table (in a
    test of a row, of the same table other than its index), and `days` only for a Date),
    its column, and the field it is tested against, are among the fields still shown of a Table
    or a Collection still shown (a Chart's fields never count, and a Heading has none: `_entered`
    on the server, `fieldKeys` in the view), and its value passes. The value is the first row's cell, read as `_record`
    reads it: text stripped, a number through `_typed` (`parseTypedNumber` in the view,
    and for an Integer `parseTypedWhole`, digits alone exactly however large and any other
    number when it is whole), a tick `True`, anything else for a True/false
    column unticked, and empty while not `filled`. `ticked` passes `True`, `not_ticked`
    anything else; `filled` a filled value and `empty` any other; `equals` a filled value
    equal to the condition's, typed alike (numbers as numbers, text and days as text),
    `one_of` one equal to any of them, and `not_equals` and `not_one_of` anything the first
    two fail, empty included; `contains`, `starts_with` and `ends_with` a filled value
    holding the condition's text, at its start or its end, both stripped and compared
    exactly, and `not_contains` anything `contains` fails, empty included; `above`,
    `at_least`, `below` and `at_most` a filled value of the column's type compared with the
    condition's (numbers as numbers, days as their `YYYY-MM-DD` text). Tested against
    another field, the condition's value is that field's: the first row's cell of its
    one-row table, or, in a test of a row, the same row's cell (`_passes` and `passes`
    take it as `other`), which, unless a value of the column's type, leaves nothing to test
    against, so that `not_equals` and `not_contains` alone pass; a Date then compares day
    numbers (`toordinal` on the server, `dayNumber` in the view), the other day's plus
    `days`, so no day is ever made past the calendar's ends. A condition on a list's rows
    holds when it fits, each column it tests (`listColumns`: its tests of a row's, the
    columns they are tested against included, and a total's), is among those fields (for a count without
    tests, one of its table's columns other than the index, `countedKeys`), and its rows
    pass: of `_typed_rows` (`typedRows` in the view), the table's rows through
    `preset_rows` (`presetRows`), each with a `filled` value in a column other than the
    index (`rowFilled`), whatever `hidden` will hold, those passing every test of its
    `where` (`_passing_rows`); a `RowsCondition` when any of them pass, or, for `every`,
    when all of them do and there is at least one; a `RowCount` when their number passes
    its test against `count`; and a `RowTotal` when the `sum`, `min`, `max` or `average`
    of the numbers its column holds in those rows (`_total`: a cell blank, not a number or
    too large for a float left out, each read as a float, as the view reads it, a whole
    number past 2^53 too, added in order, as the view adds them, rather than by `sum`, which compensates
    floats' errors from Python 3.12, and taken to 15 significant digits; the sum of none 0
    and the others of none empty, failing) passes its test
    against `value`, both read as numbers. Rounds end when one hides nothing; hiding never shows anything, so
    there are at most as many rounds as sheets, sections, components and fields. `hidden` is
    then every input column other than the index that is among the fields of a Table or a
    Collection and among none that shows.
33. **Conditions edited.** The panel's `ShowWhenEditor` builds each change from the
    `show_when` it was given and hands the whole of it to `updateWidget`,
    `setSectionShowWhen`, `setPageShowWhen` or, for a field, `setColumnLook` through one
    `change`: Add condition appends `newCondition(form)`
    (none without an input table that has a column to test), to the Show when or to a
    group, Add group appends a `ConditionGroup` of one `newCondition(form)` and the match
    the Show when's is not (`all` under a number), a cross removes a condition or a group,
    a group losing its last condition going with it, each match select sets its `match`,
    at least as 2, and the number beside it the number chosen, a column chosen goes
    through `withConditionColumn` (a `Condition`; the
    test kept while `testsFor` the new type includes it, else the type's first; the values
    kept while their number fits the test and each fits the column, else none), a table's
    rows through `withConditionRowsOf`, a list's rows select through `withListKind`, a
    test of a row's column through `withTestColumn`, Add test appending `newRowTest`, a
    cross removing a test of a row, a total's column as chosen, a count's number as the
    whole number typed and a total's as the number typed, a test
    through `withConditionTest` (none for a test without values, the first for one, all
    for one or more, each kept only while it fits the column), and a value is committed
    as the list it makes, a comma-separated text split and its blanks dropped, a number
    column's held back with its fault while a number in it has a thousands separator.
    `removeSchemaColumn` and `removeSchemaTable` drop the conditions testing what they
    remove, in every sheet, section, component and field, groups included
    (`withoutConditions`), a field's look left changing nothing taken away:
    a column its `Condition`s, its tests of a row, a `RowsCondition` left with none going
    too, and the totals of it, a condition or a test of a row tested against it counting
    as testing it; a table every condition on it; and a group left with none
    going too; and `conditionUses` gives the schema
    editor's confirmations what they would drop. `pageProblems`, `sectionProblems` and
    `widgetProblems` report each
    condition's `conditionFault`, a group's included, a value outside its column's `options` (with options),
    a number `match` over more conditions than its `show_when` or group holds, a condition
    on the price on what does not take nothing typed (`takesNothingTyped`), named by its
    output and never applied,
    and `neverShown` (a least fixed point: a sheet, section, component or field can show
    again when its conditions pass counting only those each of whose columns, a condition on
    the price always, or for a count
    without tests one of its table's columns but the index, is a field that can show again
    of a Table or a Collection that can show again, outside it: not on a sheet's or a
    section's own components, not on a component itself, and for a field another field,
    its component's or any other's; its sheet too for a section, its section for a
    component, and its component for a field; any condition not fitting can never
    pass) gives the ones nothing could show again,
    reported unless a condition of theirs already is, as waiting on a column on no Table or
    Collection or only on components, or for a field only on fields, that hide with it.
34. **Charted.** `SheetCanvas` gives a Chart its `shownFields` as it gives any component,
    and `WidgetBody` draws it with `ChartBody`, which reads the form store's form and the
    sheet's values through `useSheetValues`. `sheetChartData` plots by `chartGrain`. Of one-row tables it makes one
    series with a point per field, named by the fields' labels. Of many-row tables it makes
    a series per field over the rows: while the Chart shows a many-row input table (a
    field's or `category`'s), the positions up to the longest of those tables' held rows at
    which `rowFilled`, read through `quoted`, holds for any of them, its many-row output
    tables' values lined up by `linedUpOutputs`; with output tables alone, the rows of the
    first field's table's answer, another output table's row the one holding the same key
    values as text (`answeredOutputs`, as a Table of them shows them). An input value is the cell's typed text through `parseTypedNumber`, a
    gap for a tick, text that is not a number, a blank, and a column `quoted` leaves out; an
    output value is the price's, a gap unless it is a finite number. A row is named by its
    value in `category`, an input's as typed (a number in its column's format through
    `formattedNumber`, a tick "Yes", else "No") and an output's as an output cell shows it,
    or "—" for none; without a `category`, or one gone or not lining up, by its number, its
    position in the grid or the answer from 1. A field that is not `plottable` or whose
    table does not line up is left out, and a pie keeps its first series. `axis` is the
    column whose `number_format`, `decimal_places` and `use_grouping` every plotted
    column shares, else null; `stale` is set while the Chart plots an output column, or
    names its rows by one, and `price.basis` is not the values' `basis`. In place of its
    plot `ChartBody` shows what `chartMessage` says: "Nothing to chart yet" with no number to
    plot, and for a pie "A pie can't show a value below 0: <row> is <value>" for its first
    value below 0, else "Every value is 0, so there is no pie to draw" while none is above
    0; otherwise it imports `chartRuntime.ts` lazily, once, every draw waiting on
    it sharing the one import in flight, its chart maker kept for every later draw so each
    new option is set as the values change rather than after another import (an import
    that fails is forgotten, so the next draw tries again), creates one chart
    in its host per mount and per change of theme or reduced motion (watched as `ComboChart`
    watches them, the tokens read through `chartTheme.ts`) and sets `sheetChartOptions` on
    it, each new option merged with `series`, `xAxis`, `yAxis`, `grid` and `legend`
    replaced, so a series identified by its field moves to its new values, while one drawn
    otherwise (stacked or not, another type) or a legend moved is drawn anew. A
    `ResizeObserver` resizes it, unmounting disposes it, and a failure to load the runtime
    or to set the options is an alert in its place. `ChartSettings` changes its options
    through `updateWidget`, and the Rows select offers `labelColumns` once `chartGrain` is
    many-row.

35. **Rolled out.** `handle_app_rollout` reads the workbench config and the environment's
    `AppEnvironment`, then hands `rollout` everything it needs, every refusal before any change
    (the preflight): a host other than `azure-container-apps`, an environment the config does
    not name or whose `resource_group` or `container_app` is empty, the commit git names for
    HEAD in the project (`git rev-parse HEAD`, refused when it names none), `credentials()`,
    and the image: `--image` held to `DIGEST_REFERENCE`, or `--from`'s through
    `promoted(source_host, commit)`, which reads the source app's `state()` and refuses a
    record missing or partial, an image not matching `DIGEST_REFERENCE`, a commit other than
    HEAD's, and a source whose latest revision is not active on that image. `rollout` reads
    `host.state()` and refuses an app keeping more than one revision active, not running
    exactly one container, with no public address or no Entra ID client ID, an image reference
    longer than `TAG_VALUE_LIMIT` (256) characters and an app whose tags, with the record's
    three added, would pass `TAG_LIMIT` (50), then asks `host.token(client_id)`, refused when
    Azure gives none. When the latest revision is active, provisioned and on the image, nothing
    is rolled: a failed or degraded running state fails the run, and so does the check, with
    nothing undone. Otherwise the image the app serves is kept as the one to go back to: its
    serving revision's, Azure's latest ready revision while it is active (not its template's,
    which a failed update of Azure's own can leave naming an image no revision serves, nor
    another active revision's on its way out, which takes no traffic; none when the latest
    ready revision is not active), then, with `host.limit(ROLLOUT_TIMEOUT_SECONDS)`, within the
    recovered
    scope, every active revision deactivated (`deactivate`) and `set_image(image, suffix,
    timeout)` sets it with a revision suffix of its own (`_suffix`: `h`, the UTC time to the
    second as twelve digits and six random hex digits from `secrets`, so two rollouts in one
    second never share one, and every rollback gets another) and answers the revision,
    `AzureContainerApps` spending the timeout from its first request (each request's
    connection and read bounded by `REQUEST_TIMEOUT_SECONDS`, retried at most twice through a
    retry policy that waits no longer than ten seconds whatever `Retry-After` asks, and given
    the SDK's `timeout`, its retries and their waits included, of at most a minute and what is
    left of the bound `limit` set, a request asked once it is spent refused), Azure's update
    waited on with
    `LROPoller.wait` no longer than what is left, and the revision Azure answers refused unless
    its name ends with the suffix, since the resource Azure answers may already hold another
    change; the revision is then polled every `POLL_SECONDS` until it is provisioned within the
    deadline, a `Failed` provisioning, a `Failed` or `Degraded` running state or the end of
    the deadline, provisioned or not, failing it. `hosted_check(fetch, address, token)` polls `GET /health` signed
    in until it answers 200 with a release or `HEALTH_TIMEOUT_SECONDS` pass; sends
    `GET /api/app/release` with `forged_principal()` and no token, which must answer 401, 403
    or a 3xx whose `Location`, parsed, is the host's sign-in: `/.auth/login` or under it on
    the app's own address (or relative), or `login.microsoftonline.com` itself, over https; and sends it again with `Authorization: Bearer`, which must
    answer 200 for the release `/health` named with `quotes.open`. A `QuotesClosedError`
    (`quotes.error_code` `quote_store_mismatch`, carrying the release and the differences)
    leaves the release running. Any other failure, the update's included (refused, failed or
    past its deadline), starts one recovery deadline, `RECOVERY_TIMEOUT_SECONDS`, and
    `host.limit` with it, which the ownership read, the update (`AzureContainerApps` never
    waiting past the bound) and the wait all share; `_roll_back` reads `state()`: when the
    latest revision ends with this rollout's suffix (the revision it made) or is still the one
    it started from (the update refused or not yet applied, or a deactivation Azure applied
    and answered with a failure), it deactivates this rollout's revision when there is one and
    sets the kept image again with a suffix of its own, or, with none kept, leaves the failed
    revision stopped, waiting on it within the deadline, a recovery that fails (its ownership
    read included) naming its failure beside the original and the app's state, read with the
    bound lifted (`_last_state`), or why that read failed too; when
    a third revision is the latest, nothing is undone and the run fails saying another change
    replaced it. The release that stays, open or closed, is recorded
    (`record(Recorded(image, release, commit))`, the app's other tags kept) only while its
    revision is still the latest. The guard is best effort, a read before each write: Azure
    takes no conditional update of a Container App, so a change landing between the read and
    the write is not seen; the generated jobs, serialised per environment, are what keep two
    rollouts of one app apart.
    The command raises `QuotesClosedError`'s message as a Click error when the quotes are
    closed, or prints the release id last.

36. **Impact measured.** `handle_app_impact` makes the refusals before any host is reached:
    the workbench config read, `app_environment(config, "staging")` and `"prod"` (a host
    other than `azure-container-apps`, an environment not named or with `resource_group` or
    `container_app` empty) and `credentials()`, each a Click error with no report;
    `--sample` outside 1 to `MAX_SAMPLE` is a usage error (exit 2). `impact(prod, staging,
    sample=..., say=...)` then answers an `ImpactReport` and never raises for an app: a
    `WorkbenchError` (an `ImpactError`, or the `RolloutError` Azure's read or token raises)
    is the verdict `stopped` with its message, and the quotes listed but not reached are
    `not_compared`. Its deadline is `IMPACT_TIMEOUT_SECONDS` from its start, checked before
    each request, each wait and each of Azure's reads and tokens; a request runs to its
    own bound,
    `REQUEST_TIMEOUT_SECONDS` (30), or `PRICE_REQUEST_SECONDS` (75) for `POST
    /api/app/price`, and Azure's calls to `AzureContainerApps`' own (60 a call), its
    `ClientSecretCredential` built with the client's bounds, so a token request's
    attempts connect and read within `REQUEST_TIMEOUT_SECONDS`, retried at most twice,
    each wait at most ten seconds. Every
    request goes through one retry: a 503 is asked again after `Retry-After` (5 without one,
    10 at most), `BUSY_TRIES` tries in all, a wait that would pass the deadline not taken.
    The deadline passing once quotes are listed leaves the quote being priced and those after
    `not_compared` with `timed_out` (the run passes); before, it is `stopped`.

    `impact` reads production's `state()`, then staging's, and reaches staging (`_side`): an
    app with no address or no Entra ID client ID refused, `token(client_id)`, then `GET
    /api/app/release` signed in (200 naming its `id` and `form_revision`, its
    `quotes.open` true, else refused naming the environment and the app's status and
    reason) and `GET /api/app/form` (200 with a `form`). A production app without `RELEASE_TAG` among its tag names is `no_baseline`,
    production not reached; one with the tag and no whole `Recorded` is refused (incomplete
    record). Production is reached the same way. The same release id on both is
    `same_release`, nothing priced. Otherwise `shared_fields(prod's form, staging's form)`,
    read from each form document's `schema.tables` (`role` `input` and `rows` `one` when
    left out, so a form another Haute version wrote reads alike), then `GET
    /api/app/quotes?view=team&stage=final&sort=saved&limit=N` on production, `N` the sample
    left and 200 at most, following `next_cursor` until the sample is listed or none is
    left, each page's quotes added to the run as it is listed, so a run that stops reports
    them `not_compared` (a page answered other than 200, or whose `quotes` is not a list
    of quotes each with a reference and an integer option, or whose `next_cursor` is
    neither text nor null, stops the run); none listed is `no_baseline`.

    Each listed quote in order: `GET /api/app/quotes/{reference}/{option}` on production
    (the reference percent-encoded) for its `values`, then `POST /api/app/price` with
    `{"form_revision", "release", "values"}` on production, then on staging, each with its
    own release and form revision, the values unchanged. An answer of 0, 401, 403, any
    3xx (the transport follows no redirect), 409 or 503 (after its tries) from either app, to any of the three, stops the run naming the
    app and its status and reason. Opening: 404 is `not_compared`, any other answer but 200
    `baseline_failed`. Production's pricing other than 200 is `baseline_failed`; staging's
    422 is `refused` and any other but 200 `failed`, each with a reason that is the status
    and what it means (413 too large, 422 values refused, 500 the pipeline failed, 504 too
    long, 507 too much memory; otherwise the app's own error), never the answer's body. An
    open answered 200 without a `values` object, or a pricing answered 200 whose `tables`
    is not an object of tables, each a row, a list of rows or null (production's held so
    before staging is asked), stops the run naming
    the app. Both 200 is `compare_quote(fields, prod's tables, staging's tables)`:
    `moved`, `unchanged` or `no_longer_compares`.

    `compare_quote` takes each shared table's rows from an answer's `tables` by name (a
    table left out holds no rows; a row a column is left out of holds it empty). A one-row
    table compares its first rows, an absent one as empty. A many-row table with shared keys
    keys each side's rows by the tuple of their key values, a number by its value (`1` and
    `1.0` one key; true and false never numbers), a key value null (`a row ...
    leaves its key ... empty`) or held twice (`... holds the key ... twice`) answering why
    the quote no longer compares; matched rows compare column by column, a key only one side
    holds is a `Move` with no column (`before`, `after`: whether production and staging hold
    it), production's order first, then staging's. Without shared keys, rows compare by
    position when the counts match, labelled `row N`, else the quote no longer compares
    naming both counts. `moved(before, after)`: both numbers (an `int` or `float`, never a
    `bool`): `before` zero moved when `after` is not, else when `abs(after - before) >
    abs(before) * 1e-8`; one a number and the other not: moved; otherwise `before != after`.

    The command writes `render_report(report)` to `REPORT_FILE` in the working directory as
    UTF-8 with LF line ends, prints the counts, and exits 1 with the verdict's why when the
    run `stopped`, or naming each `failed` quote's reference, option and reason, when
    `report.passed` is false. `render_report` names each `Released` (the commit to twelve
    characters, `unrecorded` without one), why the run fails, the sample, the fields only
    one release has, the outcomes' counts, each shared field that moved with the quotes it
    moved in and its largest rise and fall in percent (over moves whose values are both
    numbers, production's other than zero, percent `(after - before) / abs(before) *
    100`), each moved quote's moves (production's value, staging's, and when both are
    numbers the difference, with its percent when production's is not zero; rows added
    and removed), then the quotes of
    each other outcome with their reasons; a cell's `|` escaped and its line breaks spaced.

## Edge cases and invariants

- The workbench routes are under `/api/`, so the session-cookie check applies, and they are
  included with the feature routers, before the `/api` 404 guard and the SPA catch-all.
- Nothing is cached: each request reads `haute.toml` and the form from disk, so a change to
  either reaches the next request, and a server never serves a stale form after a branch
  switch.
- A disabled workbench changes nothing in the editor: the store is never set, no fetch is
  made, and the palette offers the Quote Input and the Quote Response. A workbench node the
  pipeline already has is refused wherever it runs, naming the node (`read_project_form`), a
  preview reporting it at the node as the configuration error it is.
- The form folder resolves inside the project root: a `workbench` folder, or a file in it,
  that is a symlink or junction to somewhere outside the project is refused, so the
  workbench never reads or writes a file outside it; a save and a workbench `haute init`
  also hold every path they write or delete to `ordinary_path_problem`, so a junction
  inside the project, at `workbench/schema/` say, is refused too and neither its target nor
  anything else is written.
- A setting at its default is left out of its file: `"required": false` written by hand
  reads as not required and is gone after the next save, so a file has one spelling as
  Haute writes it. The defaults are the folder format's (`version` 1): a release of Haute
  that changed one would read every folder differently, so it takes a new `version`.
- Ids are the form's identity and names its spelling: a rename, by the editor or by hand in
  a schema file, changes the name and, in the editor, every reference, and leaves the id,
  so every reference and the quote store's record of the column follow it; a column deleted
  and made again under its old name takes a new id and is a new column. A reference
  written to a name the schema does not hold reads as an unresolved id (`?` and the name),
  shown in the editor as a component's or condition's missing column, written back as it
  was, and refused by `haute app build` and deploy; a column later named so resolves it at
  the next read. Nothing resolves a reference through an old name.
- A sheet's file name follows its title: a retitle that changes the name moves both of the
  sheet's files and its entry in the index, and the sheet keeps its id. Two sheets whose
  titles give one name cannot both be saved.
- Two branches each adding a component to one sheet give them ids of four random
  characters each, so a merge that keeps both rarely meets two of one id; when it does,
  the read refuses the form naming the id, as a merge that copies a component by hand is
  refused.
- A table's files are named by its name in lower case, which no other table's name shares
  (names are unique whatever their case), and the index keeps the name as spelled: a
  rename that changes only the case changes the index entry and no file's name, and a
  read refuses a file whose name on disk is not in lower case, on every platform alike.
  Sheet names are lower case already.
- A sample's rows keep their places: an empty row stays, since `sample_quote` numbers an
  index column by place and `preset_rows` fills a column's preset values into the rows
  holding nothing there in order, so taking a row out would renumber or move the rows
  after it. Each row's values are written in the table's column order whatever order they
  were typed in, so two saves of the same values are the same bytes; only a table whose
  sample holds no rows at all has no file. A value under a column name its table does not
  hold is kept under an unresolved id and written back as it was; the sample's readers
  iterate the schema's columns, so nothing prices it, as a value under a column dropped
  was never priced. A column that takes the name since takes the value at the next
  read, as a reference does; a row holding that column's own value too would hold the
  name twice, which the save refuses, naming the row.
- Table and column ids starting with `?` are refused by `FormSpec`, so the unresolved ids
  a read makes (`?` and a name) can bind to nothing.
- Only the workbench nodes take the workbench's tables: a fetch never changes a Quote Input,
  whose connections still follow a rename by position as `migrateApiInputEdges` allows.
- A fetch changes no node: the workbench nodes' ports follow the newest fetch at once, and
  only their connections to a table it no longer has are removed, outside the undo history,
  so an undo brings back no tables, and a connection it brings back is removed again.
- A fetch that completes while the document is read-only removes nothing until the document
  can change; one that completes while a submodel is open holds the submodel's connections,
  and the top level's when it shows again.
- An empty `tables` list is a valid answer, and so is one whose tables have no columns yet:
  the Workbench Input has no ports, and `workbench_table_labels` and `workbench_table_frames`
  refuse to make any, each saying what to add.
- A Workbench Input's sample is read whole whatever a reader asks of it, so every reader finds
  the same misfit; resolving its points reads the tables alone and never does. A missing or
  `None` part reads as in a request: under a `pl.List` the table has no rows, and under a
  `pl.Struct` the fields under it are null while the row and its other values stay.
- A Workbench Input's table point is never built or cleared; the input cache, its routes and
  the Quote Input's snapshots are as they are without it.
- A Workbench Input inside a submodel definition takes the tables a top-level one does, from
  the server where it runs and from the store in the editor; its connections are held in the
  submodel's graph while it shows, a connection through the submodel's boundary left for the
  run to refuse.
- Fetched tables and samples are compared as JSON with object keys sorted, so a refetch that
  brings the same raises no node-data epoch.
- No workbench node holds its tables, so the history never holds them: an undo or a redo
  changes no port, and a connection it puts back to a table the newest fetch lacks is removed
  again at once.
- A Workbench Output's connections are bound by `targetHandle`, never by position or input
  name: reordering its tables moves no connection, and an upstream rename leaves its bindings
  as they are.
- A fetch never changes a Quote Input's or a Quote Response's connections.
- An empty `response_tables` list is a valid answer: the Workbench Output has no ports, and
  running it fails.
- The one-row tables' rows are put side by side only once each has been shown to hold exactly
  one row, so no row is paired by position with another quote's.
- `haute init --force` answered for automated pricing writes a `haute.toml` without the table
  and leaves the form folder and `connections.toml` in place; `haute init` in a project that
  has a `haute.toml`, without `--force`, writes nothing, `--workbench` included.
- The form's revision is the hash of its folder's files, each path and its bytes: a hand
  edit that changes only whitespace is a change, since the view read other bytes, as is a
  file added to the folder or taken out of it, and a save that writes the same files has
  the same revision.
- The form is read once per store life, so unsaved edits and history survive switching
  views; a reload is explicit (the stale banner's Reload, or Try again after a failed read).
- A save queues behind the one running and writes the form as it stands then, quoting the
  revision the previous save gave, so two quick saves never race on the file; a sync
  queues the same way, so it never reads the file from before a save in flight and takes
  it for another branch's.
- A form save checks the file's revision, writes it and captures it under the repository's
  lock (`repository_mutation`), which a branch switch holds too, so no switch lands between
  the check and the capture.
- A form save on the ledger is the pipeline save's capture: no repository, no capture; no
  working branch chosen, or a failed capture, is a warning on a 200, the file written and
  named in `uncaptured`; the file is tracked from its
  first captured save, so a milestone's sweep takes a hand edit to it.
- A save saves the form only while it holds unsaved edits: a save
  or a milestone from the pipeline editor, the form as saved, makes no form request and
  no form toast. A form save refused, as stale or failed, saves no pipeline and refuses
  the milestone, as a refused pipeline save refuses the milestone: the pipeline is never
  saved over a form the view does not show. A save of a document that cannot be
  saved, one asked for inside a submodel, or one asked for during the assistant's turn,
  is refused before the form is saved, with the pipeline save's reason, so the form is
  never saved ahead of a pipeline that is not; the fence is read as the save runs, not
  as it was asked for, since a save may have waited its turn, and again after each wait
  within it, so a fence that closes while the form is being saved or the tables fetched
  refuses the rest of the save (the form then saved, the pipeline not, until the next
  Save).
- Project saves take turns: a second save asked for while one runs waits for it, then saves
  the project as it stands.
- A save saves the form before the pipeline and fetches the tables in between, holding the
  workbench nodes' connections to them, so the pipeline it records runs on the form it
  records and connects only to tables it has; when the tables cannot be fetched, no
  pipeline is saved and no milestone asked for.
- Discard on a switch drops the form's unsaved edits only once the switch has succeeded,
  by reading the file again then; the form store itself never drops a dirty form on a
  document adoption.
- A form save runs under the pipeline save's lock: of two saves quoting one revision, the
  second reads the first's and is refused; the ledger capture never races a pipeline
  save's on the git index.
- The editor's navigation guards (a move, a switch, an archive, a delete) read the form's
  unsaved edits through the workbench store's `formDirty`, mirrored by the form store, so
  a form edited and not saved is asked about as the canvas is.
- An answer to a Preview pricing is published only while it is the pricing asked for last
  and the view's pricer is the one it ran with: Clear and leaving the view drop it.
- Fit clamps through `setZoom`: a sheet too wide to fit at the minimum fits at the minimum.
- A sync adopts a changed file only while the form is as saved, and drops the history, since
  an undo would bring the other branch's form back; with unsaved edits it marks the form
  stale, and the save that would overwrite the file is refused as before.
- The pipeline's document, its live sync and its history go on while the view shows; only
  its keyboard and React Flow's document-level keys are fenced.
- Fields are held by table and column id, never by name, so renaming a column keeps it
  on the sheet; a column or table removed from the schema is dropped from the fields that
  showed it, and a field whose column is gone otherwise is shown as missing and reported.
- A Chart's `category` is held as a field is: removing its column or its table sets it to
  null, numbering the rows again, and a change that leaves it out of line (its table moved
  between one row and many, or keyed otherwise) is reported by `widgetProblems`, the rows
  numbered meanwhile.
- A Chart takes nothing typed: `visibility`, `sheetVisibility` and `neverShown` never
  count its fields, so a column only Charts plot is never hidden and a condition on it
  never holds, and a value only a hidden Table or Collection takes is left out of the quote
  and plotted as a gap by a Chart that shows.
- A Chart plots the values the sheet's components are given: building a new quote's
  defaults, editor Preview the form's sample, and the underwriter app its quote. It holds
  no values of its own, and nothing it draws reaches the form, the sample or the quote.
- A Chart's runtime is imported only from `ChartBody`, beneath the lazy workbench view, so
  the startup bundle never preloads `vendor-charts`; registering Pie grows that chunk, which
  the bundle gate caps.
- A Heading holds no values: it has no `fields` in the file, `showsFields` is false for it,
  so `shownFields` gives it none and no field operation reaches it, and `visibility`,
  `sheetVisibility` and `neverShown` count nothing of it, while it hides by its own
  `show_when`, its section's and its sheet's as any component does. Its text and description are kept as
  typed; only `widgetProblems` and `HeadingBody`, to tell whether it has text or a
  description, and `widgetName` read them stripped of the spaces around them. Its
  description is its text's: with no text a Heading is the problem it is however its
  description reads, and its description is not drawn.
- A section holds its components: every operation that adds one to a section or moves one
  into it or within it keeps it inside the section (`keptInside`), never across a border,
  and a resize past the bottom, or a component taller than the section it is put in, grows
  the section to hold it (`holdWidget`), so the form the view holds always fits `Section`'s
  validator, between any two pointer moves
  too, and a save from the middle of a gesture is a form like any other. A section never
  shrinks of its own accord: only its edge, its height field and Fit set its height, never
  below `leastSectionHeight`. Its components' places are measured from its top-left
  corner, so moving or resizing a section moves nothing in the form but its own height,
  and moving one moves nothing but the order of the sheet's sections. A sheet holds its
  components across in the same way: every such operation keeps one inside the sheet's
  width (`keptInside`), and a resize past its right edge, or a component wider than the
  sheet, widens it (`holdWidget`), so the form always fits `Page`'s validator too. A sheet
  never narrows of its own accord: only its width field sets its width, never below
  `leastSheetWidth`.
- A component dragged is placed from the form as pressed on every move, so the sections
  above the one it lands in keep the heights they had and it stays under the pointer;
  the section it left keeps its height.
- The components are drawn in `readingOrder` within each section, section by section, so
  the page holds them as they sit, while
  each one's `z-index` is its place in the form's order: a move changes what a screen reader
  reads first and where Tab goes next, never what covers what. A component moved past
  another in the page keeps the keyboard's focus, since React gives the focus back to an
  element it moves within a commit.
- The view store is read against the form: a `pageId` the form no longer has reads as the
  first sheet, and a `selectedId` or a `sectionId` the form no longer has as no
  selection. In Preview a
  `pageId` naming a hidden sheet reads as the first sheet that shows, and is kept, so the
  sheet chosen shows again once its conditions hold.
- A press that does not move records nothing; the tracker's listeners are removed on
  release, pointer cancel included.
- The last sheet cannot be removed: `removePage` refuses it and the tabs offer no cross
  for it. Nor can a sheet's last section: `removeSection` refuses it, its header's Delete
  does nothing and its panel's Delete section is disabled.
- In Preview nothing moves under a pressed pointer: `useHeldWhilePressed` keeps the
  sheet showing and the sections and components drawn when the press began, the values in
  them live, and lets them go a turn after the press ends, after the click a release makes.
  A press ends on its release or cancel, on a context menu, which may take the release, or
  as the window loses the focus, never as a field inside it blurs. A press that begins
  outside the viewport, or of any but the primary button, holds nothing.
- `useKeptPlace` scrolls only between two draws of one sheet at one zoom in Preview, by
  the anchor's move times the zoom, from the scroll position recorded before the draw; a
  sheet switched, a zoom changed or an anchor that is no longer drawn scrolls nothing.
- The sample's values are text or a tick as typed; nothing on the client types them, so
  the posted form always fits `FormSpec` and the server's rules decide what a value is.
- `pricingBasis` is memoised per form object and covers the whole schema, the columns the
  sheets hide and the sample alone: a move, a resize, a rename of a sheet or a component
  changes nothing in it, while a column renamed or retyped, hidden or not (the server
  declares a hidden column too, its value left out), and a condition, a field or a value
  that hides or shows a column do. Preview's basis is the same of the quote.
- What shows is decided by hiding alone (item 32): a condition's value is read as typed,
  and the only thing that changes between rounds is which components show, which only
  shrinks, so a condition that fails stays failed, the rounds end, and the answer does
  not depend on the order of sheets, sections, components or conditions. A many-row
  table's rows and their values are read as typed, hidden or not (`_typed_rows`), so the
  rows passing a condition's tests, their number and their total are the same each round,
  and a group, `all`, `any` or at least a number of conditions that only fail
  more, only fails more; no `none`, `at most` or `exactly` match exists, which would not. A sheet's or a
  section's own column inside it shows it while its value passes (it was shown when the
  rounds began) and hides it otherwise, which `neverShown` reports, since nothing could
  show it again; so does a field whose conditions test its own column.
- A hidden value is left out where the server makes the quote and nowhere else: the
  sample in the form and the quote in the preview store keep every value as typed, so
  showing a column again brings its values back, and an undo restores a hidden value as
  any other. A column on no component is never hidden, as before conditions; a condition
  on one never holds, since no component that shows has it.
- `sheetVisibility` and `visibility` are one rule in two languages, as `parseTypedNumber`
  and `_typed` are: each test file reads the cases in
  `frontend/src/utils/__tests__/fixtures/sheetVisibility.json` (forms, values, and what
  must show and be hidden) and holds its own side to them, so a change to either must
  change the cases and pass both.
- One pricing runs at a time, and a pricing never writes into the store after the pricer
  it ran with was replaced or cleared.
- Three sets of values, each with one home: Build draws a new quote's defaults, held
  nowhere; editor Preview's sample is the form's (`sample`, saved with it, undone as any
  edit of the form is); and the underwriter app's quote is the preview store's alone,
  never in the form and never on an undo stack. A column removed from the schema leaves
  its values in the sample or the quote unread, since `quoteProblems` and the server read
  the schema's columns, not the values' keys.
- `rowFilled` and `filled` mirror the server's `_record` and `_filled`: a row counts when
  any column but the index holds a tick or text other than blank, so a grid's filled rows
  are the typed rows the pricing answers with, in order, and the index is the grid's row
  number.
- `presetRows` and `preset_rows` are one rule in two languages, as `sheetVisibility` and
  `visibility` are: each test file holds its own side to the cases in
  `frontend/src/utils/__tests__/fixtures/presetRows.json` (a table, its rows, and the rows
  read), and every reader of a table's values on either side reads them through it, so a
  grid's rows are the rows the pricing answers with.
- A Table's output rows are matched by every key column's value as the server typed it,
  compared as text; a key missing or null on either side matches nothing. A price for
  other values is shown dimmed on every row, and may line up with the grid differently
  until the next pricing.
- `cellProblem` is stricter than the server in one place: a fraction in a whole-number
  column is a problem here, where the server would keep the float for the pipeline to
  refuse. `parseTypedNumber` reads what the server reads, a finite decimal with a sign, a
  point or an exponent once the currency sign, separators and spaces are gone (`1e999` is
  text to both), and nothing else:
  the server applies the same rule (`_NUMBER` in `src/haute/_workbench_tables.py`) before
  `float`, so neither reads what Python's `float` would and the other refuses (an
  underscore, a digit outside ASCII). A Show when's tests read an Integer's numbers through
  `parseTypedWhole`, as `_typed` reads them, digits alone exactly however large; a
  cell's problems and a Chart's values read them through `parseTypedNumber`.
- `columnNameProblem` holds a column's name to the server's `is_frame_label`, an ASCII
  identifier that is not a Python keyword, so a keyword is refused in the schema editor
  before the tables route refuses it; the Workbench Output's panel judges a table's name
  with the document's reserved labels, as the Workbench Input's does.

## Error handling

- The quote store's errors are `WorkbenchError`s (`src/haute/_quote_store/_store.py`), so a
  route answers them as the workbench's, and none names a connection URL, a server or a
  database: `QuoteStoreMismatchError` (`quote_store_mismatch`) refuses a catalogue that
  differs from the tables the store derives, or a sign-in lacking a privilege the store
  needs (each difference named, with what resolves it), in every environment alike; it
  closes the app's quotes, never stopping the app, and
  `QuoteStoreClosedError` (`src/haute/_workbench_app_store.py`) carries it, or a
  `QuoteStoreEnvironmentError`, to each route the closed app refuses (503);
  `QuoteNotFoundError` (`quote_not_found`) names the quote; `StaleQuoteError`
  (`stale_quote`) names who changed the quote since the version the change named, what they
  did and when, and nothing is written; `QuoteLockedError` (`quote_locked`) refuses a change
  a quote does not take as it stands, saying what would let it: a save or change to an
  archived quote (restore it), a save to a final one (amend it, giving a reason), restoring
  one not archived, amending one not final, marking final one final already;
  `QuoteNotReadyError` (`quote_not_ready`) refuses marking final a quote not priced, or
  priced on another release, naming it, or one with a required column holding no value,
  naming each in `missing`; `WriteSupersededError` (`write_superseded`) answers a save
  repeated under its key after the quote moved on, naming when it landed and who changed the
  quote when; `ReferenceTakenError` (`reference_taken`, 409) refuses a new quote's reference
  another quote holds, naming that quote; `QuoteValuesError` (`quote_values_invalid`, 422)
  refuses a whole save holding values their columns' types do not, naming each cell's table,
  sheet row and column. A creation's reference missing or breaking its rules, an
  alternative's naming one, a reused operation key, a cursor the list did not give, a page
  size past `MAX_PAGE_SIZE`, an amendment without a reason and a SQLite older than 3.35 are
  `QuoteStoreError`s (`quote_store_invalid`). `QuoteStoreEnvironmentError`
  (`quote_store_environment`) refuses an environment the release does not declare, naming
  those it does, a database's location given twice or not at all, or one its provider cannot
  take, naming the variables to set and never their values; on the analyst's machine an
  environment `haute.toml` does not declare, one `connections.toml` has no connection for or
  one of another provider, a server or database left empty and a sign-in variable holding
  nothing, naming the file, the key or the variable (`connected_store`); a server database
  that cannot be reached or refuses the sign-in (`src/haute/_quote_store/_store.py::reaching`,
  around every connection the app's gate and the editor's Compare open, a
  `QuoteStoreUnreachableError`, `quote_store_unreachable`), naming what places it, the host's
  variables or the connection and its variables, and none of the driver's text; and tables
  the editor cannot read (`read_store_catalogue`). A `[workbench.store.<name>]` table that is
  not one, a location or a secret's value in it, and an unknown key are
  `WorkbenchConfigError`s, each naming the key and where it belongs, as is a `connections.toml`
  that cannot be read or holds a secret's value, `managed-identity` or an unknown key
  (`ConnectionsError`).
- `WorkbenchConfigError` and `WorkbenchFormError` are `WorkbenchError`s; `_error_handlers.py`
  answers any `WorkbenchError` as 409 with its message, logged as a warning. The message names
  the key or the file and says what to set; the path is kept in the error's context for the
  log. `WorkbenchConfigError` is also a `ConfigError`, so a caller that reads `haute.toml` for
  another purpose and catches `ConfigError` catches it too.
- `read_form_document` (the folder module) raises `WorkbenchFormError`, its message naming
  the folder as the status names it and the file from the folder: for a folder that does
  not exist ("<folder> does not exist: restore it from version history (a project starts
  its workbench with haute init --workbench)"), a folder without `form.json`, a file the
  index names that is missing, a file in the folder the index does not name ("<file> is
  not part of the form: list it in form.json or remove it"), a name the index gives twice
  or two it gives that differ only in case, a table or column name that is not an
  identifier, a file whose name on disk is not its name in lower case, a sheet's file
  without its layout file or the reverse, a sample file for a table the schema does not
  hold or for an output table ("<file> is the sample of no input table: rename it after
  its table or remove it"); for a file that cannot be read (chaining the `OSError`), is not
  UTF-8 text (chaining the `UnicodeDecodeError`), is not JSON or holds a key twice
  (chaining the `JSONDecodeError`, or raised with the key), or does not fit its file model
  (chaining pydantic's `ValidationError`, its first error in the message with the file and
  the place); for an identity column written, a table's file naming two columns alike,
  a setting on `quote_details` that is
  Haute's, a layout entry for a section or component the sheet does not hold or a section
  or component without one, a sample file holding no rows; for a folder that changed each
  of the three times it was read ("<folder> changed while it was being read: try again");
  and for a `FormSpec` the files build that does not fit it (its first error, as before). A section a component
  reaches below, or a sheet one reaches past, is grown to hold it before `FormSpec` is
  checked (`_grown_to_hold`), when its height or width is a whole number above 0 and the
  component's place is whole numbers, and said; anything else wrong there is left to the
  shape's errors. A sample cell is text or a tick, strictly (`Cell`): a number or a null
  does not fit.
- `folder_files` raises `FormFolderNameError` (a `WorkbenchFormError`) before anything is written
  when the form holds names the folder cannot hold, listing each: two tables of one name
  whatever their case, two columns of one name in a table, a table or column name that is
  not an identifier, a table name or a sheet's file name Windows keeps (`con`, `prn`, `aux`,
  `nul`, `com1` to `com9`, `lpt1` to `lpt9`, whatever their case), a sheet title that gives
  no name or another sheet's, a file whose full path would pass 240 characters, an output
  column holding a rule only an input column takes (`INPUT_ONLY`, every rule but
  Required), a reference or a
  sample value under an id the schema does not hold that is no unresolved one, and a
  sample row that would hold a name twice ("row <n> of <file> would hold <name>
  twice"); `put_workbench_form` answers it 422 with each place and reason, answers
  422 too files that would not read back as a form ("its files would not read back
  as a form: " and the read's message), and answers 409, with the read's message, a
  folder holding a file its index does not name. `haute app build` and deploy
  raise on unresolved ids (`?` and a name) through `unresolved_references`, which lists each
  one's file and place and names it as the files spell it, a table by its name and a
  column as `table.column`.
- `get_workbench_tables` raises `HTTPException` 404 while the workbench is not enabled, and
  answers a `WorkbenchTablesError` through `contract_error_http_exception`, the structured
  422; the request client retries a 5xx like any idempotent request before the store sees
  the failure. `put_workbench_form` answers an `OSError` from a staged write or delete as 409 naming
  the file ("<form> could not be written: <reason>"), every staged file rolled back first,
  as the pipeline's write routes answer one.
- `fetchWorkbenchStatus` and `fetchWorkbenchTables` throw `ApiError` or a contract error like
  any typed request; the store turns either into one error toast.
- `_save_form` raises `StaleDocumentRevisionError` (`haute.routes._save_pipeline`) with a
  message of its own; `put_workbench_form` answers it as the pipeline save does, 409 with
  `"<code>: <message>"`, logged as a warning with both revisions. A body that is not a
  form is FastAPI's 422.
- `capture_save_in_ledger` degrades a failed capture to a warning and a missing identity
  to `identity_required`, as for the pipeline save, and `put_workbench_form` answers 200
  with them; nothing in the capture fails the save. `sync` reports a read that failed in
  one error toast and leaves the form as it is.
- A save whose capture was skipped or failed names the form's file in `uncaptured`;
  `reportSaveCapture` keeps it, and `POST /api/git/capture` takes it once an identity is
  set or at the next Save, the form not written again.
- A project save whose fetch of the tables failed (the store's one toast says why), or whose
  `holdConnections` answers false, toasts "The pipeline was not saved: the workbench's
  tables could not be read." and saves no pipeline.
- `fetchWorkbenchForm` and `saveWorkbenchForm` throw `ApiError` or a contract error like
  any typed request; the form store shows a read failure in the view (`loadError`) and a
  save failure as one error toast, telling a stale refusal by the detail's code.
- The preview store's `priceNow` turns a failed pricing into "Pricing failed: " and the
  reason, never a toast; a quote that breaks a rule is priced all the same.
- `priceSample` rejects with an `Error` naming the reason: no Workbench Output, no Workbench
  Input anywhere in the document (naming the Quote Input to replace when it has one), the
  preview's error, a table missing from its answer, or the server's reason for a failed preview request
  (`apiErrorMessage`); the pricing store keeps it as `error` and the toolbar shows it.
- The pure operations fail loud on a caller's mistake: `removePage` on the last sheet,
  `removeSection` on a sheet's last section ("A sheet's last section cannot be removed"),
  `moveSection` to or from a place the sheet does not have,
  `updateWidget` giving another kind `rows` or another of a Table's options ("Only a Table
  has <key>"), `columns` or another of a Collection's options ("Only a Collection has
  <key>"), a Chart's options ("Only a
  Chart has <key>") or a Heading's `text`, `description` or options ("Only a Heading has <key>"), giving a
  Heading a `title` or `fields` ("A Heading has no <key>"), `toggleField` and `moveInOrder`
  on a Heading ("A Heading shows no fields"), `addSpacer` and `removeSpacer` on another
  kind ("Only a Collection has spacers"), `removeSpacer` at a place in the order holding a
  field ("No spacer is at <place>"), and any operation naming
  a sheet, a section or a component the form does not have, each throw an `Error` naming
  it. A section's height that is not a safe whole number is no mistake of that kind:
  `setSectionHeight` leaves the form as it is, and the panel's field refuses it first.
- A condition is never an error: one that does not fit never holds, `visibility` and
  `sheetVisibility` raise nothing for it, and the view reports it as a problem.
- Nor is a Chart's problem: `sheetChartData` raises nothing for a field it cannot plot,
  leaving it out. `ChartBody` turns a runtime that fails to load, or options ECharts
  refuses, into an alert in the Chart's place naming the reason, until options set
  cleanly; the rest of the sheet is drawn as ever. Nor is a Heading with no text, which
  `widgetProblems` reports. Only a
  `show_when` outside the form's shape fails, as `read_form_document`'s
  `WorkbenchFormError` or a body FastAPI refuses.
- `validate_singleton_groups` raises `ValueError` naming the group and how many it found;
  save answers it as a 400 and `resolve_config` lets it propagate. The assistant's
  operations raise `OpValidationError` for a Workbench Input they may not author.
- `WorkbenchTablesError` (`src/haute/_workbench_tables.py`, `error_code`
  `workbench_tables_invalid`, one of `PUBLIC_CONTRACT_ERROR_TYPES`) is a workbench node's
  tables the pipeline cannot take: tables not as the workbench gives them ("The <node>'s
  tables are not as the workbench gives them (<where>: <what>).", the spot from pydantic's
  first error, which only a node built without them or a bundle not of Haute's making
  holds), a name that cannot be a port's
  or a frame column's, or a name used twice, across tables whatever its case. The tables
  route answers it 422 through `contract_error_http_exception`, and a preview's worker
  answers it 422 as it answers any public contract error. `WorkbenchInputError`
  (`src/haute/_workbench_input.py`, `error_code` `workbench_input_invalid`, listed in
  `PUBLIC_CONTRACT_ERROR_TYPES` itself, since a preview's worker reports an error by its
  exact class), its subclass, is a Workbench Input with no tables, or with tables that have
  no columns yet, a sample or a request that does not fit, and a request of other than one
  quote; `api_input_table_labels` turns a `WorkbenchTablesError`
  into `NodeDataPointInvalidError`, an error row in the cache report, and deploy's
  `_workbench_tables` into a `ValueError` naming the node.
- `WorkbenchOutputError`, an `ExecutionError` with no public error code, carries every
  failure of a Workbench Output's run: no tables, a connection on a port that is none of its
  tables or on a port another connection has, and a connected one-row table without exactly
  one row. A table nothing is connected to, a column its frame lacks and a column whose dtype
  does not fit are filled with nulls and raise nothing. It names the table. A
  preview shows it on the node, `run()` and `score()` raise it, and a deployed pipeline answers
  it as its other internal errors, a 500 carrying the message. Tables the pipeline cannot
  take raise `WorkbenchTablesError` from `parse_workbench_tables`, and tables that have no
  columns yet `WorkbenchOutputError`, saying what to add.
- The assistant's operations raise `OpValidationError` for a Workbench Output they may not
  author, as for a Workbench Input.
- `reject_removed_config_keys` (`src/haute/_config_validation.py`) raises `ConfigError` for a
  Workbench Output config holding the removed `mapping` (`_REMOVED_COLUMN_MAPPING_KEYS`,
  carrying `removed_config_keys`), so `load_node_config` refuses it where the standalone
  runner reads the sidecar, which checks no undeclared key, as well as in parsing and save.
- `DeployConfig.from_toml` raises `ValueError` for a key under `[workbench]` outside
  `WORKBENCH_TOML_KEYS`, as for any unknown `haute.toml` key.

## Testing

- A new quote keyed in a window (WB-32's outcome): `tests/test_quote_book.py` covers
  references one only when they are the same, case included, `café` keyed composed and
  decomposed one, `.`, `..`, `/` and a blank refused, a reference taken refused naming its
  quote, and two alternatives made at once taking two options;
  `tests/test_workbench_app_quotes.py` covers a creation under the reference keyed, another
  case another quote, a taken reference answered 409 with the quote holding it, an
  alternative taking its source's and refused naming one, a reference missing, holding `/`,
  too long or `..` refused 422 with nothing made, and a required detail left empty made and
  saved; `frontend/src/app/__tests__/NewQuoteWindow.test.tsx` covers the window's fields in
  the table's order, Create off until the reference fits while a required detail may stay
  empty, the reference marked once Create is pressed, the quote made over a new quote's
  defaults and taken to its address, a reference taken as keyed, case kept, and opened
  encoded, Cancel and `Escape`, no typing while the creation is unanswered, an answer
  arriving once the page moved on leaving it where it is, a reference in use refused with
  Open it, a page of an earlier release asking a reload, a lost answer sent again exactly
  as it was, after a reload too, a kept creation discarded, a renewal filled from the quote
  as it is kept with `is_renewal` ticked, a source that could not be read, one window at a
  time, Save as new, and nothing for a reader; `frontend/src/app/__tests__/QuoteView.test.tsx`,
  `frontend/src/app/__tests__/QuotesPage.test.tsx`,
  `frontend/src/app/__tests__/UnderwriterApp.test.tsx`,
  `frontend/src/app/__tests__/useOpenQuoteStore.test.ts` and
  `frontend/src/app/__tests__/quoteValues.test.ts` cover Duplicate and Renew filling the
  window over the quote without touching its sheets, Save as new filling it from them,
  `/quotes/new` and its derived addresses, an alternative made at once from its source's
  values, the reference's rules, the kept creation's storage and the Reference order's
  label.
- A quote's administration in `quote_details` (WB-31's outcome):
  `tests/test_workbench_form.py` covers a form saved by an earlier Haute, before
  is_renewal, before the option and as WB-30 left it, taking today's columns by id in
  today's order, its reference renamed, `valid_until` and `currency` dropped and the
  labels it gave kept, today's columns left out or out of order taken back in order, a
  column of another id refused, and a rule or a default on the administration refused;
  `tests/test_workbench_tables.py` covers every column of quote_details but the six no
  request holds priced whatever the sheets hide, and those six in no request, and, with
  `frontend/src/utils/__tests__/sheetVisibility.test.ts`, a stage alone counting as no
  typing, as no request holds it, a quote name the sheets hide counting, as a request holds
  it, and a value shown by a detail the app sets counting while that detail does not
  (`frontend/src/utils/__tests__/fixtures/typedQuotes.json`); `tests/test_quote_book.py`
  covers assigning, archiving and restoring keeping the values and prices;
  `tests/test_workbench_app_quotes.py` covers a priced save's request holding the quote's
  creation and none of the details no request holds, what a page sends of them overruled
  and an option no number never read, a value shown by a condition on who made the quote
  saved for its maker and refused as untyped for another whatever the page claims, and one
  instant stamping a save, its request and its activity;
  `frontend/src/workbench/__tests__/WidgetBody.test.tsx` covers a field whose column is
  gone named while building and not drawn in Preview, its spacer with it.
- The quote's reference and option (WB-30's outcome): `tests/test_workbench_form.py`
  covers every table but quote_details taking the identity at its head, the identity
  refused when renamed, retyped, moved, given details or taken out, and a table's own
  `quote_reference` or `quote_option` kept, its clash named and cleared once renamed, and a
  form naming quote_details' reference `reference` read as `quote_reference`;
  `tests/test_workbench_tables.py` covers the response's and the request's schemas, a
  request's identity on every row, a request without it, with an option of 0 or giving it
  in another table refused naming the spot, the quote of nulls and the sample under the
  stand-in, and the identity priced whatever the sheets hide; `tests/test_workbench_output.py`
  covers the response giving every row the quote's identity though the pipeline dropped or
  replaced it, the Workbench Output's own tables holding it blank, `run()` under the
  stand-in and `score()` without quote_details answering it blank;
  `tests/test_deploy_validators_gaps.py` covers a clash refused by deploy's form check;
  `tests/test_quote_book.py` covers alternatives made at once taking two options, and a
  creation repeated with its key making one quote and an alternative one option;
  `tests/test_workbench_app_quotes.py`, on a pipeline reading `quote_details`, covers an
  untyped quote made as a draft, a save priced under the quote's identity, an alternative's
  label and its search by `Q-000001/2` through the routes, and a price differing only in
  the identity not moved; `frontend/src/app/__tests__/useOpenQuoteStore.test.ts` and
  `frontend/src/app/__tests__/QuoteView.test.tsx` cover a quote made as it is asked for, its key held across a lost
  answer and let go on a refusal, and the page moving to its address unless it moved on;
  `frontend/src/utils/__tests__/workbenchForm.test.ts`,
  `frontend/src/workbench/__tests__/SchemaEditor.test.tsx`,
  `frontend/src/workbench/__tests__/PropertiesPanel.test.tsx`,
  `frontend/src/workbench/__tests__/ShowWhenEditor.test.tsx` and
  `frontend/src/__tests__/editors/WorkbenchOutputEditor.test.tsx` cover a new table's locked identity, the index after it,
  the clash, the identity never offered to the sheets, and the Output panel never flagging
  it.
- `tests/test_workbench_app_quotes.py` covers the release saying whether the app keeps
  quotes and a storeless app's quote routes answering 503, as they do a store the app cannot
  reach, naming none of what the driver said; a priced save priced again on
  the server and kept in the form's tables, the app's details set over what the page sent,
  its values read back as cells, its request naming the quote's identity and its creation
  and none of the details no request holds, and plain SQL over the file reading it; a save
  whose prices moved kept with the server's and saying so; an unpriced save keeping no
  prices; one instant stamping a save, its request and its activity; a value shown by a
  condition on who made the quote saved for its maker and refused as untyped for another,
  whatever the page claims; an option no number never read; a value not of its column's
  type refusing the whole save, priced or not, naming each cell, with nothing written; a
  creation under the reference keyed, another case another quote, a reference taken
  answered 409 with the quote holding it, and an alternative taking its source's and
  refused naming one; a reference missing, holding `/`, too long or `..` refused 422 with
  nothing made; a quote whose reference an address escapes opened and saved; a required
  detail left empty made and saved, and Mark final then naming it; an untyped quote made as
  a draft and an empty save refused; a quote name past 200 characters refused naming the
  limit; a creation and a save from a page of another release or sheets refused with
  nothing kept; a quote opened with what it holds, and an unknown one 404; a save over a
  quote moved on refused with the quote as it is now, naming who moved it; a final and an
  archived quote's save refused; a save repeated with its key writing once; a save repeated
  after a colleague's, from a page of another release, answered as superseded naming both,
  nothing priced or written again; a creation repeated with its key making one quote, and
  an alternative one option; a key reused for another quote refused; the lists paged,
  searched and counted through the route, a stage the store does not have and a bad cursor
  422; the activity naming who did what and the quotes made from a quote, and the people;
  an alternative the next option of its source's reference; a price differing only in the
  identity not moved; the changes naming their version, a note moving none; Mark final,
  archive, restore and amend, an amendment without a reason 422; Mark final refused on an
  unpriced quote and on a price of another release served on the same store; a write from
  another site refused and the app's own accepted; the app's pages its one page; an app on a
  database without its form's tables, hosted and in dev, starting with its quotes closed;
  the roles read
  from the principal Azure passes, under its own claim type or an app role's, a header that
  is not one refused; with an edit role, one holding it creating quotes and a reader refused
  creating, saving, noting and every change (403), with nothing written, but listing and
  pricing them, and a request whose roles cannot be read refused (401); a locally served
  app refusing a foreign Host, with and without same-origin fetch metadata, and a forwarded
  header (400) before the release, the form, the quotes, a pricing or a creation, the store
  left without a quote, and serving, pricing and saving for `127.0.0.1` and `localhost`; a
  local reader refused; no log or answer of the app naming where its database is; a
  hosted app with no environment named, one naming an undeclared environment and one whose
  location cannot be resolved each starting with its quotes closed, naming why, and the
  store of the one named at the database its variables locate, the release's environments
  in no answer; a release carrying the environments its project declares, its `app.py`
  passing `hosted_store` unresolved; an app started on a database missing a table or a
  column, holding one of another kind, unreachable, or whose sign-in lacks a privilege
  starting, `GET /health` and the form answered, `GET /api/app/release` answering its
  quotes closed with the refusal's code, message and differences, and every quote route
  and `POST /api/app/price` answering 503 with it, nothing written; the tables put right
  opening its quotes on the first request past the recheck bound and none before it, on a
  clock the test moves; a column dropped under a running app refusing the next save 503
  with the difference, a statement failing on tables as checked raised as it was; and a
  database that stops answering closing the quotes as unreachable, the app answering
  `GET /health` while the gate is held by a check; a SQLite URL naming a server, or a port
  that is no number, starting the app with its quotes closed, the URL in no answer.
- `tests/test_quote_book.py` runs on SQLite and on each SQL Server or PostgreSQL server the
  environment names, a database of each test's own, a server's tables made from the SQL
  Haute writes (tests/_store_servers.py), and covers a saved
  quote as rows of the form's own tables read with plain SQL, a many-row table's rows under their sheet rows with a blank row between them
  counted, an unticked box in a filled row false and the outputs beside the inputs on the
  quote's identity; saving again replacing the rows and an unpriced save keeping no prices;
  a value not of its column's type refusing the whole save naming each cell, nothing
  written; an empty required input, a value below its minimum and a non-finite output each
  saving; a save repeated under its key writing once, one repeated after a colleague's
  superseded naming them, one save sent twice at once one write, and two saves under two
  keys over one version landing once and refusing the other as stale; a creation repeated
  under its key making one quote and an alternative one option; a key reused for another
  quote or by another person refused; Mark final needing a price of the running release and
  a value in every required column, naming each missing, a required column in a many-row
  table held in every row (one row empty refusing it, no rows, zero and false passing, a
  null tick refusing it) and text of spaces alone no value; a final quote taking no save
  until it is amended with a reason; assigning, archiving and restoring keeping the values
  and prices; a change over a version moved on refused naming who moved it; references one
  only when they are the same, case included, `café` composed and decomposed one, `.`, `..`,
  `/` and a blank refused, and a reference taken refused naming its quote; two alternatives
  made at once taking two options; a quote made from one the app does not keep refused; a
  quote opening with its cells as text (digits, the shortest text of a Decimal, an ISO date,
  a tick, an Integer past 2^53 exactly) and its rows in their places, a gap a blank row; its
  outputs opening as the pricing rendered them, a non-finite one empty, and an unpriced quote
  with none; a load that raced a change read again; a preset row whose value is no longer
  allowed named as dropped; the Quotes page's lists, searches, counts and pages; and a
  quote's activity naming who did what and the quotes made from it.
- `tests/test_quote_store.py` covers a database whose tables were made from the SQL Haute
  writes opened from its catalogue alone in any environment, dev's as any other's, and a
  copy of another environment's tables opening as it stands; a database that does not hold
  the form's tables not opened, naming each table missing; the check refusing a catalogue
  that lacks a column or a table, holds a column of another kind, keys a table otherwise or
  leaves it referring to no quote, or holds a column the release does not write that takes
  no null, naming each, with changing the tables in SQL from the schema pane's SQL as the
  way forward in every environment, dev's included, while a column the SQL side added that
  takes a null is no difference; DDL in a write that fails rolled back whole; and foreign
  keys held on every connection.
- `tests/test_quote_store_migrate.py` covers a plan naming each step by stable id, a removed
  table kept; and the SQL Haute writes (`offline_plan` and `render_sql`): from none, every
  table with its keys and references and haute_activity; a column added taking a null; a rename, a change of case and a swap of two
  names through temporary names, never a drop and an add; a retype as the engine's ALTER in
  place with its note, PostgreSQL's USING cast, and a note alone on SQLite; a change of a
  table's rows or role, a name too long for the engine and a table or column taking a
  removed one's name as notes, a name of many lines commented on each; a removed table or
  column as a commented DROP, a column's naming its table as two swapped names leave it; one
  transaction, every value written in; and, on SQLite, tables made from that SQL found fit
  by the app's check and the SQL for a form's change run on them keeping every quote's
  values (on each server in `tests/test_quote_store_providers.py`).
- `tests/test_connections.py` covers `connections.toml` absent holding no connection and
  each connection read in the file's order; a secret's value, a URL, `managed-identity`, an
  unknown key, a provider not the store's, a variable that is no name, a name the file does
  not take, a file's sign-in and a malformed file each refused naming why and never the
  secret; a value refused, a password in a variable's place or a location of another kind,
  never said; an Entra ID sign-in on PostgreSQL naming its role; `.env`'s values under the
  environment's; a SQLite connection at its path from the project root and a server's
  signing in from its variables with nothing of them in its URL or `repr`, named by the
  connection and its variables; an environment not declared, with no connection, of
  another provider, a server left empty and a variable holding nothing each refused naming
  what to set, never the server; a connection's schema read without its sign-in; and the
  analyst's Entra ID sign-in made once in the browser for the process.
- `tests/test_quote_store_compare.py` covers, on SQLite and, marked `store_servers`, on each
  server the environment names: a database made from the form's SQL matching it; the quotes
  holding a row and a value counted by table and column; the form compared after a rename by
  the default branch's id, a retype, an added column, a removed one and a table the form
  never had, each marked by the form's ids with the quotes it concerns, the extras listed
  with theirs, and its SQL, run on a server, leaving the app's check finding nothing and the
  renamed column's values kept; a column the analyst pairs renamed keeping its values, a
  pairing the database does not allow not taken; a database holding no store taking every
  table and haute_activity; and, on SQLite, a column the form does not have that takes no
  null marked and noted, tables that cannot be read said naming the connection, the
  catalogue holding no value read, a file in write-ahead logging read with its journal left
  as it was and a missing one not made, an Entra ID sign-in refused said as a database not
  reached naming no account, and a store table lacking an identity column compared rather
  than made again.
- `tests/test_quote_store_schema.py` covers each schema table derived as a table of its name
  and its columns' types, the project's columns nullable and the identity and `quote_row`
  not; the tables keyed by the quote's identity and referring to its details; quote_details
  holding the form's columns, then the store's own, those the app sets on every write never
  null; a table's constraints named by its stable id, so a rename keeps them; each table's
  columns the form does not hold named with what they hold; and the structure recording a
  table's name, rows, columns and their types and nothing else; and the store's metadata
  holding the form's tables and haute_activity alone.
- `tests/test_workbench_config.py` covers the table absent (and `haute.toml` absent), enabled
  and disabled, the form folder taken from the pipeline file `[project].pipeline` names
  (`rating/main.py` giving `rating/workbench`, a pipeline at the root giving `workbench`),
  and each refusal (not a table, an unknown key, `form` refused with what replaced it,
  `enabled` missing or not a boolean, an enabled workbench in a project with no pipeline
  file, a `workbench` folder that is a junction or symlink to somewhere outside the
  project (one inside it is the save's and init's to refuse), malformed TOML, a `haute.toml` that is not UTF-8, `app` not a table, a key beside
  `host` and `edit_role`, a host outside `APP_HOSTS` or not a string, an `edit_role` blank
  or not a string, `store` not a table, an environment named dev or not a name, a provider
  outside `STORE_PROVIDERS`, a sign-in on a file, an unknown key, and a database's location
  or a secret's value in the file, each naming the variable it belongs in), `[workbench.app]
  host` and `edit_role` read, the environments read in the file's order with their
  providers, a server provider naming the variables of its sign-in (a service principal's tenant,
  client and secret, a managed identity's client id only when user-assigned, the role an
  Entra ID sign-in takes on PostgreSQL) and refused without them, and that `DeployConfig.from_toml`'s whole-file check accepts the table, with
  `app` and `store`, and refuses an unknown key under it.
- `tests/test_quote_store_environment.py` covers a SQLite environment at the file its
  database variable names and at a whole URL, its URL never in its `repr`; an environment the
  release does not declare refused naming those it does; and a location given twice, not at
  all, with a server or a schema, of another provider, or a SQLite URL naming a server, a
  sign-in or a port that is no number, each refused naming none of the values given.
- `tests/test_quote_store_providers.py` covers a server's location made from its parts with
  no sign-in in it, nothing of it in its `repr`, an Entra ID sign-in reading only what its
  identity needs, a location unfit, holding a sign-in or not a URL refused naming neither,
  a sign-in whose variable holds nothing refused naming it, and a connection the server
  refuses said without the driver's words; a SQL Server connection taking a
  fresh token from a fake credential each time and no integrated sign-in, a password handed
  to each connection, and a server engine checking a connection before use; the types
  pinned on SQL Server, the identity exact in every table, and PostgreSQL's; a 64-character
  name noted in a PostgreSQL script; each declared provider's driver in a release's image;
  each privilege a sign-in lacks named; and, marked
  `store_servers`, on each server the environment names: tables in their own SQL schema
  made from the SQL Haute writes found fit by the app's check, and the SQL for each later
  form run on them keeping every value through a rename, a change of case and a swap,
  adding a column, noting a removed one with its DROP commented out, and changing a Text
  column to an Integer and an Integer to a Decimal in place, each shown in the server's
  catalogue; a script run whole or, a rename failing part way or a value that does not
  convert, not at all, nothing left that could commit; a sign-in the server refuses closing
  a started app's quotes as unreachable, naming neither the server nor the user; a sign-in
  lacking a privilege and, on SQL Server, a text compared exactly under another collation
  each closing the quotes naming it; and a release started apart from its project on tables
  made from the SQL, serving a saved quote, signed in as its `release.json` declares. The CI job `store-servers` (`.github/workflows/ci.yml`) runs those marked on SQL
  Server 2022 and PostgreSQL 16 containers.
- `tests/test_workbench_app.py` covers a release holding the form without its sample (its
  revision the written file's), what it is and its entry point; a quote whose answer is all
  empty priced to no tables rather than failing; the release id unchanged by
  a rebuild by someone else later or by another sample, by where the nodes sit, their
  descriptions, the edges' canvas ids or the nodes' order, and changed by another form, a
  node's config and another runtime; a release whose `release.json`, `form.json` or
  manifest is not one refused naming the file and what is wrong; a pipeline failing on a
  quote answered 500 with its reason; the admission kept through rendering; the app
  commands saying a malformed `haute.toml` as an error, never a traceback; the
  health check without sign-in; Azure's principal by name or by id, and a request without
  it refused 401; a release with no host refusing everything but the health check; a local
  principal signing every request in; the form served without its sample; a quote priced
  through the release's pipeline, typed as a pricing reads it; another form revision refused
  409; an empty quote refused 422; a pricing past the timeout answered 504 and one refused
  admission 503; with every place held by pricings past their timeout, another refused 503
  at once with its retry hint, never reaching the pipeline, and served once one ends; the page and its assets served once built, 503 before, and an asset
  refused without sign-in; a price request past the deploy body limit refused 413 before
  it reaches the pricing, and a body that is not JSON or not a price request refused
  422, each refusal its place and reason without the values sent; the project root set to
  the release; the id unchanged by
  where and for which target it was built, or where the pipeline's file sat, and
  changed by a model retrained or a utility helper edited under the same name; the
  same project checked out twice building the same id; the app served from the
  project pricing on the utility code the release carries after the project's is
  edited; a generated workbench pipeline built into a release that prices a quote
  as the pipeline does, built twice to the same id; a directory of other files, a
  form disagreeing with the pipeline, sheets never saved and a project without a
  workbench refused; the project, a folder holding it and a folder whose `release.json`
  is not a release refused, each left as it was; an earlier release left as it was, and
  nothing left beside it, when its successor fails to build; a release allowing what its params files hold when it is
  built, and refused, the last release left as it is, once a file can no longer
  give a column's values; `haute app build` writing the release; `haute app serve`
  serving it on loopback signed in as the git identity, keeping its quotes in dev's
  database through its connection, a SQLite file whose tables were made from the SQL, its
  quotes open and no store of its own made, and refusing without an identity, a project
  declaring no dev environment or a `connections.toml` holding no `[dev]`, before anything
  is built; a
  pinned base image making the release the image's build context (the Dockerfile
  copying the form and the release, the release's entry point, the same id) and an
  unpinned one refused; `--image` building it with Docker, refused without a
  Dockerfile or without Docker; `--push` refused without a registry, or outside a
  repository where git names no commit, before anything is built, and otherwise building the image as `image_reference` names it, pushing it and
  printing the reference by digest Docker holds for that repository
  last, a push or an inspection that fails refused with Docker's message; the release's app pricing and keeping a quote in
  an interpreter that can import only what the image installs, its store the environment
  its host names, its tables made from the SQL Haute writes; and an app without a quote
  store pricing nothing, its quotes closed naming `HAUTE_ENVIRONMENT`.
- `tests/test_workbench_folder.py`, with its golden folder `tests/fixtures/workbench_folder/`, covers:
  a form round-tripping through `folder_files` and `read_form_document` with the folder's
  revision (the test project's shape: one-row and many-row tables, an index column, Tables,
  Collections, a Chart, a Heading, an Import and an Export, every kind of condition, a
  condition and a test of a row against another field with days, column
  looks, a theme colour, a sample), the files it writes (the index, a schema file per
  table, a sheet file and a layout file per sheet, a sample file per input table with rows,
  none for a table without), each named as the form folder says, each setting at its
  default left out and the keys that say what a thing is always written, references by
  name, the identity columns and quote_details' structure never written, an output
  column's file holding no rule; the same files reading as the same revision, and a hand
  edit, a whitespace change, an added file and a deleted file each as another; a label
  changed writing one file, a component moved writing one layout file, a column renamed
  changing its name, every reference to it and its sample key and keeping its id, a table
  renamed moving its schema and sample files, a sheet retitled moving its two files and
  its index entry, a table deleted deleting its two files, a case-only table rename
  changing the index entry and no file's name; a column deleted and made again under its
  name taking a new id; a default written by hand read and left out at the next save; a
  sample round-tripping with its rows' places, an empty row first and a value after it
  in an indexed table keeping its row number and in a preset table its preset value, as
  `sample_quote` gives them before and after, and a sample value under an unknown column
  kept through an unrelated save and priced nowhere, taken by a column made under its
  name at the next read, and refused at save, naming the row, where a column renamed to
  it holds its own value in the row; each reference kind encoded and
  decoded as the folder module's models say (a field, a Chart's category, a one-row
  condition, a column look, a query parameter, a rows condition, a count, a total, an
  Import's table and column names, an Export's tables and table names), each with a known
  name, an unknown column of a known table and an unknown table, the unresolved ids
  `?`-prefixed in the `FormSpec` and written back as they were, never bound to a column
  whose old name it is, and a `FormSpec` with a `?`-prefixed table or column id refused;
  the golden folder's bytes and the `FormSpec` it reads as; managed quote_details columns
  keeping a number format; every refusal of the read (a missing folder with
  its message, a missing `form.json`, a listed file missing, an unlisted file, a name
  listed twice or twice by case, a name not an identifier, a file name not in lower case,
  a sheet without its layout or a layout without its sheet, a file not UTF-8, not JSON or
  holding a key twice, an unknown key, an identity column written, two columns of one
  name in a table's file, a Haute setting on
  quote_details, a rule on an output column, a layout entry too many or too few, a sample
  file for an output table or a table the schema does not hold, an empty sample file,
  `version` 2), each naming the file and the place; a save paused between its first and
  second file while a reader in another process waits on the form folder's lock and then
  answers the saved form's files and revision whole; a read paused after its first
  listing while a writer that takes no lock changes a file, reading again and answering
  one side's files whole, and a folder changing on each of three tries refused; the file
  models refusing a file without a key that says what a thing is (a table's `role` or
  `rows`, a column's `type`, the index's `version`), each at its default value too; every name `folder_files` refuses (two tables of one name by case, two columns of
  one name, a name not an identifier, `con` and `COM1` as a table and as a sheet's name, a
  title of no letters or digits, two titles of one name, a path past 240 characters),
  nothing written; a junction inside the project at `workbench/schema/` and one to a
  folder outside it each refused before any write, neither destination changed; a write
  that fails half way leaving every file as it was, and one whose rollback cannot put a
  file back naming it; commercial-pricing's form, converted by the one-off script, reading
  back to the same compiled form but for its sheets', sections' and components' ids.
- `tests/test_workbench_form.py` covers the blank form's text, `render_form` and
  `form_revision` as a release and the quote store use them, every field written in the
  one document (a column without `index` or its display
  settings reads back with them at their defaults and is written with them, a Table
  without its options with theirs, a Collection without its options with theirs, a Chart without its options with theirs, a Heading
  without its text, description, size or alignment with them, a sheet or a
  component without `show_when` with it,
  always shown, an absent `sample` written as `{}`), a Heading's text and description read and
  written as typed (empty, only spaces, or with spaces around them), the view's copies of
  the blank sheet's width and section height, the Rows and Columns limits, the decimal
  places and the quote_details sentence the same as the server's, the view's
  quote_details columns, ids, names and types in order, and the columns the app sets,
  named through `QUOTE_DETAILS`, the server's, an id given twice to
  sheets, sections, components, tables or a table's columns refused naming it, an
  unknown field,
  an out-of-range widget or Table option, a Collection's labels or a Table's header it
  does not have, a Collection's spacers out of order or past its last field (two at one
  place read), a theme's colour not `#RRGGBB` in capitals, a colour on a component, a
  chart type or legend the Chart does not
  have, a Table's option on a Chart and a Chart's on a Table, a Table's text size, header
  size, direction, line or frame weight or line style it does not have, a lower-case
  colour, its lines' colour or its header's direction on a Collection, a box style or
  weight a Collection does not have, a label width outside 1 to 99 percent or a box width
  outside 24 to 1000, a Collection read with its look and its column looks, a column look with only a Show when kept, a
  column look, a Table's or a Collection's, for a
  column it does not show, a second for one column, one that changes nothing, a width
  outside 24 to 1000, an alignment it does not have or an unknown key in one, a frame on a Chart or a
  density on a Heading, a size a Heading does not
  have, an alignment a Heading does not have, a `title` or `fields` on a Heading, a
  Heading's `text` or `description` on a Table and its `size` or `align` on a Chart, a
  number format or decimal
  places outside its range, an unknown test, an unknown match, a number match under 1 or
  not a strict whole number, and a value that is not text refused, the
  columns' number formats held equal to the Explore pivot's, a sheet without sections, a
  section's height under 1, empty or not, refused at its height, a sheet without a width
  and a width under 1 refused, a section a component reaches below and a sheet one reaches
  past grown to hold it as the folder is read, each growth said, the files left as they were,
  and `FormSpec` still refusing both (named with the component), a sheet as wide
  as its components reach read, the blank form's one section, a component's place in its section read
  and written as it is, and the
  message for an unreadable, non-UTF-8, non-JSON and misshapen file, and for a sheet
  holding `widgets` of its own, named with its place and its replacement; and Haute's
  `quote_details` table: the blank form holding it alone, a form without it read with it
  first, one holding it read and written as it is, its labels and the typed columns'
  rules kept, each way a `quote_details` table is not Haute's refused (another id, a
  structure changed, a column giving its rows included, a rule or a default on a column the
  app sets, a false default on `archived` read as none), and the editor's copy of its
  columns and of those the app sets held to the server's.
- `tests/test_workbench_visibility.py` holds `visibility` to every case in
  `frontend/src/utils/__tests__/fixtures/sheetVisibility.json`, each a form (with its
  conditions for the case), values, and the sheets, components and columns that must be
  hidden: nothing typed, a box ticked, unticked and untouched, a chain through a hidden
  sheet, a column also on a component that shows, a component's own conditions and its
  sheet's, a section's own conditions and its sheet's, a component in a hidden section,
  a chain through a hidden section, a section testing a column only inside itself, a
  column in a hidden section and on a component that shows, every test on its types (text exact without its spaces, numbers typed with
  currency signs and separators, a value not of its column's type, an empty value,
  days, text within text at its start, its end and anywhere, and not in it, empty
  included), a test against another field (a limit above another limit, text containing
  another field's text, a date more than 365 days after another and days taken away, a
  value the other field does not hold of its type passing `not_equals` alone, each
  failing while either field hides), a test of a row against another column of the same
  row, `all` and `any`, no conditions under `any`, a group `any` under `all` and `all`
  under `any`, a group holding a condition that never holds, a condition in any row and in
  every row over no rows, one and several (a row with only the index typed no row, a
  value not of its column's type failing every row), two tests in the same row and not
  in two rows, a count of each test, a count of the rows passing tests, a sum, least,
  greatest and average over the rows passing tests (blank and text cells left out, the
  sum of none 0, the least of none failing), a count and a
  many-row condition whose column is only on a hidden Table never holding, rows a column
  gives one for each value counted, each condition that does not fit
  (a column gone, a many-row table's for a one-row condition, a one-row
  table's for a many-row one, a count of a one-row table, a total of a Text
  column or against text, a test the type does not take, too many
  or too few values, a blank value, a fraction for an Integer, a day that is not one, a
  field of another type, of a many-row table or gone, a field with values typed too or
  for a test that takes none or several, days without a field or for a column not a
  Date, an output's against another field), a condition on the price holding while what
  shows is decided whatever the price would say (an output's column, a count of an output
  table's rows, on a sheet and on an input Collection alike), so the quote leaves out
  nothing for it, a
  value outside the allowed values still tested, a column on no component, a sheet
  testing a column only on itself, a hidden Table's index never hidden, a Chart taking
  nothing (a column on a hidden Collection and on a Chart that shows hidden, a column only
  a Chart plots never hidden, and a condition on it never holding), and a Heading hidden
  by its own conditions and with its sheet, and shown while they hold; and fields (a
  Collection's field shown by another of its fields, a Table's column hidden whole, a
  field hidden with its component, a column on a field that hides and on one that shows
  elsewhere never hidden, a condition on a field that hides failing, a chain through two
  fields, a field testing its own column), the fields that hide in components that show
  given by each case, none where it says nothing.
- `tests/test_workbench_tables.py` covers typed text read as the view reads it, every case of
  `frontend/src/utils/__tests__/fixtures/typedText.json` (as
  `frontend/src/utils/__tests__/sheetValues.test.ts` does),
  whole digits past a double kept exactly in an int column, whether a quote is typed by
  every case of `frontend/src/utils/__tests__/fixtures/typedQuotes.json` (as
  `frontend/src/utils/__tests__/sheetVisibility.test.ts` does, the app's columns left out
  first), input tables in
  schema order as the pipeline holds them (names, rows per quote and typed columns, the form's keys and rules left behind, the
  frame and quote schemas), each column type's dtype held equal to the Quote Input's, a
  table without columns being no port, every refusal of `parse_workbench_tables` (not a
  list, a key the workbench does not write, a key missing, a type that is not a column's, a
  name that cannot be a port, a name used twice, names differing in case, a column that
  cannot be a frame's, a column named twice), a form's bad table name refused by the node's
  name, the index column, output tables in the same shape, and the sample: typed values,
  currency and separators, ticked and unticked boxes, empty rows left out, a value that no
  longer fits its column kept as typed, the index numbering rows as they stand, an empty
  sample for a form with nothing typed, and the values the sheets hide left out (a hidden
  column's text and tick, a hidden True/false column not set to false, a row with nothing
  else typed left out, the index of a kept row kept, a value a Chart that shows plots
  left out while the Collection that takes it is hidden) while the form's sample keeps
  them; and the rows a column gives a table: `preset_rows` held to every case in
  `frontend/src/utils/__tests__/fixtures/presetRows.json`, and the sample sending every one
  of those rows, a row holding its value alone included, in the values' order, numbered as
  they stand.
- `tests/test_workbench_app_store.py` covers the gate: a store whose tables fit opening,
  one that does not closed with each difference, one unreachable closed as
  `quote_store_unreachable`, no store closed naming `HAUTE_ENVIRONMENT`, and a location
  that cannot be resolved closed with its refusal; a closed gate checking again only past
  `RECHECK_SECONDS` on its clock, the requests in between answered with the last
  refusal, and opening once the tables fit; `recheck` closing an open gate whose tables no
  longer fit and keeping one whose still do; `unreachable` closing it until the next
  check; one check at a time from two threads; and any other error a check meets raised
  as it was. `tests/test_workbench_routes.py` covers `POST /api/workbench/store-sql`: the
  SQL for each declared environment in its engine's dialect, from the default branch's
  form read from git with nothing checked out, a renamed column renamed, from none for
  a project whose default branch holds no form or when asked, no environment declared
  answering none, a body that is not a form 422, a column name holding SQL refused 422 as
  the tables refuse it, and 404 while disabled; read from the default branch while a
  feature branch is checked out holding another form and the folder another still;
  `frontend/src/workbench/__tests__/StoreSqlWindow.test.tsx` the SQL shown per
  environment, the start chosen, the tables Compare read first on its environment's tab,
  Copy, a failure said and none declared;
  `frontend/src/workbench/__tests__/SchemaPane.test.tsx` SQL opening the window while
  building and disabled in Preview; and
  `frontend/src/workbench/__tests__/StoreCompare.test.tsx` Compare listing the declared
  environments, the chosen one's database read once and the form compared with it, each
  table and column marked by its kind with the quotes in its title, haute_activity's
  difference and the extras listed, a match said, the form compared again as it changes
  with no second read, a column paired as a rename and unpaired, Read again keeping the
  pairings, a read refused said with nothing marked, and the comparison closed with the
  pane.
- The quote store's routes: `tests/test_workbench_routes.py` covers
  `POST /api/workbench/store-catalogue` reading dev's tables through its connection,
  changing nothing, and `POST /api/workbench/store-compare` marking a renamed column
  missing until the analyst pairs it, its SQL then renaming it and, run on the database,
  leaving it matching; and an environment not declared, with no connection or whose server
  cannot be reached refused 409 naming what to set and never the server or the sign-in's
  values.
- `tests/test_workbench_routes.py` covers `GET /api/workbench` disabled and enabled, `GET
  /api/workbench/tables` serving the test project's form from its pipeline folder's
  `workbench/` (tables, sample and response tables), 404 while disabled, a missing folder
  answered 409 with its message on both the tables and the form route, 409 with the
  message for a file that is not UTF-8 (on both routes), a misshapen file and a bad
  `[workbench]` table (`form` refused with what replaced it), the structured 422 for a
  table named with a keyword, `GET /api/workbench/form` with every field and the folder's
  revision, a save against the revision adopting the new one and reaching the tables, a
  stale save refused with the pipeline's code and every file untouched (a file edited, added
  or deleted by hand), a save through a junction at `workbench/` refused, a save with no
  folder refused as stale, a save over a folder holding a file its index does not name
  refused 409 deleting nothing, a body that is not a form refused without a write, a form
  whose names the folder cannot hold answered 422 naming each, nothing written, a form
  whose files would not read back answered 422, nothing written, the form routes'
  404 while disabled, a save as one commit on the ledger (its SHA, the written and deleted
  files alone in it, no second commit for the same form, the commit message naming the
  parts, the milestone folding it in and sweeping an edit made by hand and a file added to
  the folder), a save without a working branch captured nowhere, a capture that failed or
  waits on an identity reported on a 200 with the files written, a write that fails half
  way answered 409 naming the file with every file rolled back, two saves against one revision taking turns with the second
  refused, a form saved without Haute's `quote_details` table written with it first and
  every tables answer holding it, a refused form answered 422 with each error's place and
  reason and never the form (a table of the analyst's named `quote_details`), a form
  holding an id twice refused naming it, the repository's lock held through the save's
  write, and the real
  `haute.server` app answering `GET /api/workbench` ahead of its 404 guard and only with
  the session cookie.
- `tests/test_workbench_params.py` covers a params file's header, a column's distinct
  values as written (a leading zero kept, trimmed, blanks left out, once each, in the
  file's order), a byte order mark, a Windows-1252 file as Excel saves one and its
  header offered as written, a header an earlier save mangled refused, what stops
  a file or column giving them (a file missing, empty or in neither encoding, a column not in its header or holding no values, more
  values than the limit, a name that is not a CSV directly in the folder, a project
  with no pipeline folder), and `with_param_options` reading an input column's values
  again, leaving typed values and output columns as they are, and naming the column
  whose file can no longer give them; `tests/test_workbench_routes.py` also covers
  `GET /api/workbench/params` serving a header and a column's values, 409 with the
  message for a column the file does not have, and 404 while disabled.
- `tests/test_workbench_impact.py` covers `haute app impact` against fake hosts and a fake
  transport answering as two apps: the refusals before any host is reached, no report
  written; no `haute-release` tag on production `no_baseline`, production not reached, the
  report written; an incomplete record stopping the run; the same release nothing priced;
  no final quote `no_baseline`; the sample listed across pages, drafts and archived quotes
  not asked for, and cut at `--sample`; each request sent with its own app's release and
  form revision and the values unchanged; busy answers retried after a capped wait,
  stopping the run after `BUSY_TRIES`; production answering 0, 401, 409, 503 or a redirect
  to its sign-in mid-run (its release changed after the run read it) stopping the run with the report written and
  the quotes left not compared; a later page of quotes failing, the quotes listed before
  it kept not compared; a page of quotes, a release, an opened quote or a pricing
  answered 200 in another shape stopping the run; time running out before production's
  token, which is never asked for; `shared_fields` over tables and columns added, removed and
  kept, a table one release has one row per quote and the other many reshaped in both
  directions (a many-row answer of one row included), keys marked on one side alone
  ignored; `compare_quote` on a one-row table, a many-row table matched by key with rows
  added and removed, rows matched by position, counts that differ, a key held twice and a
  key empty, a numeric key written whole on one side and decimal on the other matched,
  true never the key 1, numbers within and beyond the tolerance, from zero, a bool never a number and
  an empty value against a value; production failing a quote left out, staging refusing
  one named, staging failing one exiting 1 with the report written beside the quotes that
  compared; the deadline passing during a request and during a wait, leaving quotes not
  compared and keeping an earlier failure; the report's percentages (none from zero, from
  empty or across kinds, a negative production value's size the denominator) and a
  refusal whose app message holds a distinctive typed value that the report does not
  repeat; and, through two real apps serving releases of different forms on SQLite stores,
  a final quote saved on production priced on both: a column staging adds left blank, one
  it drops left out, and a value staging's Workbench Input refuses named as refused;
  and the credential `AzureContainerApps` builds bounded as its client is.
- `tests/test_workbench_rollout.py` covers `haute app rollout` against a fake
  `ContainerAppHost` and a fake `fetch`: each preflight refusal with nothing changed (another
  host, an environment not named or left empty, an image named by tag, no commit, each unset
  credential named, multiple revision mode, two containers, no public address, no Entra ID
  client ID, no token; `--from` with no record, a partial one, a tag-named image, another
  commit and a source not running the recorded image); the maintenance window's order (every
  active revision deactivated before the image is set, with a suffix of its own, two
  rollouts with the clock fixed to one second getting two suffixes; the image put back the
  one the active revision served, when the template names another; a deactivation Azure
  applied and answered with a failure put back; Azure answering another writer's revision
  refused; the deadline spent from the adapter's first request; a revision provisioned past
  the deadline failed; every request given what is left of its bound and refused once it is
  spent, the retry policy's wait capped at ten seconds; the image put back the serving
  revision's when another active revision is on its way out; a redirect elsewhere than the
  sign-in, by host, path or scheme, no
  refusal; `fetch` against a loopback server stalling, cutting a body short, and cutting an
  error's body short); a re-run of an active,
  provisioned revision on the image rolling nothing, its failed check and a degraded revision
  each undoing nothing; an inactive revision on the image rolled again to a new revision; the
  update refused after the old revision stopped, before a new one exists, putting the
  previous image back; an image reference past 256 characters and an app with no room for
  the record's tags refused before anything changes; an update or a
  provisioning past its deadline, a failed provisioning, a degraded revision, a `/health`
  that never answers, a stalled request, a forged principal answered 200 or redirected
  elsewhere than the sign-in, a signed-in request refused, another release and quotes closed
  for another reason, each rolled back to the previous image on a revision of its own, or left
  deactivated with none, and a rollback that fails naming the app's state; quotes closed by a
  store mismatch left running, recorded and failed naming each difference; another revision
  made latest before the guard looks, never undone nor recorded over; and the record set,
  the app's other tags kept, only after a rollout that stays. The forged principal is held against the real app's middleware, which
  accepts it when it reaches the app unchanged. `AzureContainerApps` is held to the SDK with
  a fake client: the state it reads, the revision suffix and image it sets, the deadline it
  waits on the update, the tags it records and the scope it asks a token for.
- `tests/test_cli_init.py` covers a workbench `haute init` writing the table, its host and
  the one provider for dev, staging and prod, `[deploy.container]` and no endpoint settings,
  `connections.toml` for each provider and sign-in, the blank form folder in the pipeline
  folder and the workbench's workflows, and reporting them, refused with nothing written
  when the pipeline folder is a junction to a folder outside the project, a plain
  `haute init` writing
  none of them, `--force --workbench`, `--force` answered for automated pricing leaving the
  folder and `connections.toml` and pruning the workbench's workflows, `--workbench` in a
  project that has a `haute.toml` refused with nothing written, and the questions asked at a
  terminal; `tests/test_scaffold.py` covers
  `workbench_haute_toml` and `workbench_table` parsed and read as the workbench config reads
  them, with no `form` key, `connections_toml` for each provider and sign-in read as the
  connections reader reads it, the blank folder's files reading as the blank form, the
  workbench's workflows parsed for each provider with no endpoint deploy, and the triggers
  the same as an automated project's.
- `tests/test_api_contracts.py` holds the three workbench paths, five routes, in the API's
  fingerprint.
- `frontend/e2e/workbench.spec.ts` drives the journey in a browser against the real server:
  the workbench switched on for the project and its sheets written, the scaffold's Quote
  Input and Quote Response replaced through the API by a Workbench Input, a Transform and a
  Workbench Output, the view opened, the schema read, a Collection dragged out of the
  palette onto the sheet and given its fields, drawn while building as a new quote, the
  sample typed and priced live in Preview with no Price or Clear, Build a new quote's again,
  a column added to the schema, and Save writing the component, the sample and the column
  to the form folder and then the pipeline, each with its toast, neither workbench node's
  config file holding a copy; its project and pipeline from `frontend/e2e/workbenchProject.ts`.
- `frontend/e2e/app-quotes.spec.ts` drives the underwriter app in a browser through `haute app
  serve` on the journeys' project, its page built by `frontend/e2e/appServe.ts`: a quote made,
  typed, priced and saved, its rows read from dev's SQLite file with SQL, the values in the
  form's input table and the price in its output table under the quote's reference and option;
  marked final; amended giving a reason, changed and saved again, a draft again with the new
  value and each step in haute_activity; and a column added to the form, in its table once the
  app starts again, the quote opening at its address.
- `frontend/src/stores/__tests__/useWorkbenchStore.test.ts` covers loading through the
  generated contract (with the form's path), the untouched store while the workbench is not
  enabled, the failure toast (also for a contract violation), `showView` refusing the
  workbench's view while it is not enabled, and `refreshTables`: nothing while not enabled, the
  numbered fetches and the one that publishes, the sample each keeps, the kept tables with
  one toast when the newest fetch fails, `awaitTables` settling with whether the newest
  fetch published, a superseded fetch taking the newer one's outcome, and the node-data
  epoch raised by a fetch that changes what the workbench nodes are given and by no
  identical refetch.
- `frontend/src/utils/__tests__/workbenchTables.test.ts` covers the ports (each table with a
  column, by name, in order, once; a Workbench Input's input tables and a Workbench Output's
  response tables; none before the first fetch or for another node) and
  `workbenchDisconnections` (a connection kept wherever its table moved, one to a table
  renamed or gone removed by the node losing it, one from no table removed, a Quote Input's
  and one through a submodel's boundary never);
  `frontend/src/hooks/__tests__/useWorkbenchConnections.test.tsx` the fetches on enabling and
  on each adoption, a connection to a table a fetch dropped removed outside the undo history
  and reported, one an undo brings back removed again, nothing before the first fetch or
  against a fetch made for an earlier document, nothing while the document cannot change,
  `holdConnections` for a save before any render, a Workbench Input inside the shown
  submodel held but its boundary connection left, and a Quote Input's connections never;
  `frontend/src/panels/__tests__/NodePalette.test.tsx` the palette's swap while the workbench
  is enabled, dragging the plain default config; `frontend/src/nodes/__tests__/PipelineNode.test.tsx`
  a workbench node's ports from the store; `frontend/src/utils/__tests__/editorIdentities.test.ts`
  a Workbench Input's edge named by its handle and its identity request holding none; and
  `frontend/src/__tests__/editors/WorkbenchInputEditor.test.tsx` and
  `frontend/src/__tests__/editors/WorkbenchOutputEditor.test.tsx` the panels on each fetch's
  tables, none before the first, the sample note, and each column's border for a missing
  column, a mistyped one, a filled one and a frame not previewed yet.
- `frontend/src/__tests__/App.integration.test.tsx` and
  `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` stub the status as not enabled;
  the first enables it for the host's case: the switcher at the bottom of the palette, the
  pipeline editor hidden and inert while the view shows, the toolbars swapped, Ctrl/Cmd+S
  saving the form's edits and then the pipeline, the way back, Commit in the workbench's
  toolbar saving the form's edits, fetching the tables afresh and removing a connection to
  one the form no longer has before saving the pipeline and opening the milestone modal,
  Commit refused with no pipeline saved when that fetch fails, Save in the pipeline toolbar
  saving the form's unsaved edits and then the pipeline, keeping a connection to a table
  the form still has, Save refused before the form is saved while the document cannot be
  saved, two Saves pressed while the first waits on its fetch of the tables saved in turn
  with the pipeline saved by both, Ctrl/Cmd+S
  during the assistant's turn refused before the form is saved, and a turn that starts
  while a Save waits on the form's save refusing the rest of it with no pipeline saved.
  `frontend/src/__tests__/App.workbenchLazy.test.ts` guards that the view, its toolbar, the
  schema editor, the form store and the form's operations are loaded lazily.
- The view: `frontend/src/stores/__tests__/useWorkbenchFormStore.test.ts` covers reading
  through the contract (once), a read that fails or breaks the contract, a form grown to
  open read as unsaved with a warning, saved as read, and a sync adopting one the same way,
  the file read again at each change on disk announced or connection (`formChanges`),
  adopted while clean and stale under unsaved edits, edits with undo
  and redo and `dirty`, the `MAX_HISTORY` cap, a form installed from elsewhere as saved and
  no read, a burst and a gesture as one undo step and
  a gesture that leaves the form as it found it recording nothing, redo kept, `dirty`
  worked out as a gesture ends, a gesture
  under way finished first by an edit, an undo or a redo, a save against the revision (the PUT's body,
  the new revision, the toast, the tables fetched again, the next save quoting it), edits
  during a save staying unsaved, a stale refusal keeping the edits until a reload, any
  other failure's toast, no save, edit or snapshot before the form is read, nothing undone
  or redone with nothing to, the toast naming the form folder the status gives, a save
  whose turn finds the form being read again saying so, a stale refusal after a save whose answer was lost taking the folder's revision when the folder holds what that save sent and staying stale when it holds something else, each read counted and no save or undo, the capture reported (the ledger
  commit, each warning, the identity prompt and the file kept for a capture, a flush
  saving nothing again), `flush` saving only unsaved edits and refused with a refused
  save, `dirty` mirrored onto the
  workbench store for the editor's guards, and `sync` (the same revision, a changed file
  adopted while clean, stale while dirty, no request before the form is read, its turn
  after a save in flight, a failed read's toast);
  `frontend/src/stores/__tests__/useWorkbenchViewStore.test.ts` a sheet shown on the sheets
  section in either mode, either mode shown on the sheets, `activePage`, `shownPage`,
  `previewSections` (the sections that show, in order, each with only its components
  that show), the sheet's panel, a section's panel and a selected component never open
  together, `movingSection`, the pane forgotten for a component the form no longer has,
  the zoom within its bounds and in whole percents
  whether set, stepped or fitted (nothing to fit to without a viewport or a form, the
  sheet chosen while building, and in Preview the sheet that shows for the sample or the
  underwriter app's quote), the sheets fitted after a read of the form and never after a
  save or an undo, and the panel's width kept; `frontend/src/utils/__tests__/workbenchForm.test.ts` the pure operations and
  `schemaProblems`;
  `frontend/src/workbench/__tests__/SchemaEditor.test.tsx` the editor
  (tables and types listed, read only as in Preview, every table fixed, a table still
  collapsing and a column's details opening with their fields disabled, a table added with its name focused, a rename committing as one
  undo step, role and rows with keys dropped, the type marker and the key toggle, Enter
  in the last column adding a column and in another only committing, Alt+Up moving one, Alt+Up moving a table with its name keeping the
  focus and nothing above quote_details, a table or a column dragged by its grip, none on
  quote_details, the index or quote_details' columns, a column never into another table,
  a column's label, help, rules, placeholder and
  number format (separators ticked under General making it a Number, no format on the
  index, no placeholder for True/false or Date), removals with their confirmations (a
  column or table a condition tests asked about too), the index, problems, collapsing,
  and Haute's `quote_details` table kept as it is, its columns taking labels and help and
  the typed ones rules; a column's summary its rules alone, or an output column's
  required, never "row number" or "set by the app", under its name, its range and default in its format and its format named
  in any table; allowed values read from a params file, its file and then its column,
  as one undo step, taken away by the blank file, read again by the refresh icon, a
  file that cannot give them said under them, no box to type them in, and values
  typed in, as an older form holds them, named among the table's problems; a row for each
  allowed value offered on a many-row input column that has them, ticking it unticking
  the table's other, and "a row each" in its summary; no hint text in a column's details; a group typed and offered from the
  table's others; a default by type, a tick, an allowed value or typed, offered on a
  one-row input table's column alone, not on the columns the app sets; Required offered on
  an output column as on an input one and kept as a table is made an output; and the
  columns the quote store keeps in each table shown under the table's own, locked, with
  nothing to change);
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` the view's states, the stale
  banner, the shortcuts and the panels beside it;
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` its controls, Source over
  Pipeline, Undo and Redo and the zoom in the pipeline toolbar's places, Save and
  Commit through the host and off while editing is disabled;
  `frontend/src/workbench/__tests__/ViewSwitcher.test.tsx` the switcher; and the two
  editors' tests the "Edit in Workbench" button.
- The sheets: `frontend/src/utils/__tests__/sheetGeometry.test.ts` covers the grid, moves
  kept on the sheet, resizes from each handle and by the keys (`grownRect`), the first
  place free (`freePlace`: an empty section, beside a component, below one where the sheet
  is too narrow, above another where it fits, below them all, as wide as the sheet), the sheet's least width
  and its height, the fit of its width, the sections stacked, with a bar above each and without, `cutRun` and `clearLine`, the section at a height (above them all, at each section's
  top, inside one, below them all), a section's least and fitted heights, and
  `readingOrder` (row by row, each row left to right, a component starting above the
  middle of the row's topmost one in its row, a tie in the order given, the input
  untouched); `frontend/src/utils/__tests__/workbenchForm.test.ts` also a Collection's
  look (the look it shares with a Table taken, a Table's own refused) and a Table's and
  a Collection's column looks (given, changed keeping the rest, a new one last, one set back to the Table's own or
  cleared taken away, kept as its column moves, dropped as its column is unticked, removed
  or its table removed, a column shown again starting plain, and refused on a Chart or
  a column it does not show; a Show when set, kept by Clear, emptied to always and so
  taken away with a plain look, its conditions dropped with the columns they test and
  pasted without the columns gone, and its uses counted by `conditionUses`), the sheets (named in
  turn, as wide as the last, holding one section, renamed, moved, the last kept, a width
  set and never below what their components reach or 240, the form given back as it was
  for a width that changes nothing or is not a safe whole number, a component added,
  placed, moved or copied kept inside it, and one wider than it or resized past its right
  edge widening it, never narrowing), the sections (created named in
  turn on their sheet, added at a place and at the end, split at a line (the components at
  or below it moving, their places kept, the one above pushed down by what straddles the
  line, which the palette never asks), renamed, their Show when set, a
  height set and never below what their components reach or 48, fitted, the form given
  back as it was for a height or a fit that changes nothing and for a height that is not a
  safe whole number, moved, removed
  with their components, a sheet's last kept, `findSection`), a section growing to hold a
  component added, moved, resized, duplicated or placed into it and never shrinking, a
  component placed into another section drawn over its components and the one it left
  keeping its height, the components (created with their kind's layout, a Table
  with `TABLE_OPTIONS`, a Chart with `CHART_OPTIONS` and a Heading with `HEADING_OPTIONS`,
  updated with the kind guards, a Heading's among them, duplicated, pasted (into its own
  section past a copy already there, into another sheet's section where it sat, kept
  inside a shorter section, the original untouched), removed), the fields
  (toggled, moved, refused on a Heading, which `shownFields` gives none and a column or table
  removed passes by), a Collection's spacers (added after the last field, two at one
  place, moved among the fields, removed, keeping their places as a field is unticked or
  its column or table removed, a field ticked going after one at the end, refused on the
  other kinds), a Chart's `category` (counted by `fieldUses`, set to
  null with its column or table), `chartGrain`, `plottable` and `labelColumns`,
  `widgetName` (a Heading by its text), `widgetProblems` (a Chart's and a Heading's among
  them), and the conditions (a sheet and a component created always shown,
  `setPageShowWhen` and `updateWidget` with `show_when`, a column or table removed taking
  the conditions on it with it, a section's and a Heading's among them, `conditionUses`, a
  section's and a Heading's counted, each condition problem of a sheet, a section and a
  component, a sheet waiting on a column only a Chart plots, a section waiting on a column
  only inside itself, a number match asking for more conditions than it holds, and
  `conditionText` and `showWhenText`); `frontend/src/hooks/__tests__/useListReorder.test.tsx` the shared
  drag-reorder hook; `frontend/src/workbench/__tests__/SheetCanvas.test.tsx` (on the
  fixtures in `frontend/src/workbench/__tests__/fixtures.ts`) the sections stacked under
  their bars and the components drawn in them where the form places them, a Section
  dragged out of the palette (its line, splitting a section where it lands, in before a
  section from its bar, after the last, selected, one undo step, a click adding nothing),
  nothing dropped where the schema's pane covers the sheet, a component or a Section,
  a component added by Enter or Space on its palette item (at the first place free in the
  sheet's first section, beside one and below them all, the section growing, selected,
  focused, one undo step), a Section after the selected component's and a component into
  that section once selected,
  the problem
  frame, selection (the frame taking the focus, a press on the viewport's scrollbar
  keeping it), the sheet read section by section, each bar before its components, a drag
  moving one snapped and divided by the zoom as one undo step, a sideways drag under a
  section whose height is off the grid staying on its row, a drag ended by a release
  outside the window or the window's blur, a drag followed as the sheet scrolls under it, a drag into the section below and back up into the
  one above, kept inside the section it lands in and staying under the pointer, a drag of
  a component that starts in a lower section measured from its section's top, a value typed
  in a cell or a panel's title and not yet committed kept as a component is dragged or
  resized (an undo taking back the drag alone), a Table's rows typed in the panel and not
  yet committed kept as the empty sheet is pressed and the panel closes, a section's title or height typed and not
  yet committed kept as a Section is dropped or its handle dragged (each its own undo
  step), a drag brought back where it began recording nothing and keeping redo, an undo
  during a component's drag recording and undoing it, the pointer no longer followed, a Section
  dropped less than 48 down a section going in before it or at the first clear line that
  far down, its line drawn where it starts, a resize
  by a handle at its minimum (a Collection's,
  and a Heading's 80 by 24) and one past its section's bottom growing it, a component
  dragged right stopping at the sheet's right edge and one resized past it widening the
  sheet, a component dropped out of the palette past the right edge, or beside the sheet
  on its left, kept inside it, its ghost drawn there, and one wider than the sheet widening it, a component
  dragged out of the palette and dropped (the chip, the ghost, a click adding nothing, a
  Chart and a Heading at their sizes; the ghost over a lower section drawn where it lands
  on the sheet), the
  fit (the sheet's width, drawn at it whatever the zoom from the viewport's top-left
  corner, the viewport scrolling
  across it while wider; a very wide sheet at the zoom's minimum), the components in reading order whatever
  the form's order, in Preview too for those that show, the one added later drawn over
  another it overlaps, the selected one over both and the drop's ghost over all, the
  keyboard's focus and the selection kept on a component moved past another, a
  right-aligned Heading's eye on its left, a component the sample hides drawn faded
  with its struck-through eye and one that shows with its eye, one on a sheet a new quote
  hides faded for its own conditions alone, and Preview (nothing
  selected or moved, no drop, no section bars, the quote typed, a component the quote
  hides not drawn, its place in its section left empty, and drawn again once a box is
  ticked, a section the quote hides not drawn and the sections under it moved up to close
  its space, then back down once it shows, a section the sample hides drawn faded while
  building in its place, the sections, their places, the sheet itself and a component's
  fields held while a pointer is pressed, never faded, until a turn after the release, the section holding the
  focus kept in place on the screen as a section opens above it, a Heading's text drawn
  and a blank Heading only its problem's frame, and the note while no sheet shows);
  `frontend/src/workbench/__tests__/SectionHeaders.test.tsx` the bars just above their
  sections while building, each first in its section, numbered and named, a press selecting a section and Enter or
  Space opening its panel, a bar dragged to another place as one undo step and a click
  moving nothing, Alt+Up and Alt+Down keeping the focus and nothing past either end,
  Delete with its confirmation when components are in it and never the last, the selected
  section's handle alone, dragged (snapped, never below what its components reach or 48,
  one undo step, and nothing recorded, redo kept, for a drag held at 48 or brought back
  where it began; Ctrl+Z during the drag recording and undoing it, the release doing
  nothing, so undo and redo keep their direction; Ctrl+Z during a bar's drag ending it,
  nothing moved) and double-clicked to fit, a handle and a bar dragged at the zoom and
  wherever the sheet sits on the screen, a section moved past another only once the
  pointer passes that one's middle, up or down, a bar's own keys kept from the sheet's
  shortcuts, a section's eye struck through and its bar faded while the sample hides it,
  its problem marked, and its conditions and its problem told to a screen reader as the
  bar's description;
  `frontend/src/workbench/__tests__/SheetCanvas.redraws.test.tsx` each step of a drag on a
  form of fifty components, a Table of fifty rows among them, redrawing the component it
  moves alone, and its release nothing;
  `frontend/src/workbench/__tests__/steadyPreview.test.tsx` the value held while the
  primary button is pressed on the viewport and let go a turn after its release or cancel,
  a context menu or the window's blur, never as a field in it blurs, a press outside the
  viewport, on a select or of another button holding nothing, nothing held while not
  active, and the
  scroll kept by the focused section's move at the zoom, else the first section in view's,
  from the position recorded before the draw, none for a section no longer drawn, and none
  on another sheet or zoom;
  `frontend/src/workbench/__tests__/PageTabs.test.tsx` the tabs, adding, renaming,
  moving (a tab dragged onto another, Alt+Left and Alt+Right keeping the focus, nothing past
  either end, none in Preview, the selection kept by a move before any sheet was chosen),
  Left, Right, Home and End going from tab to tab round the ends and showing each, in
  Preview too, Tab stopping at the showing tab alone, the tab focused again after renaming,
  no delete on a tab, read only in Preview, a sheet's eye and its faded tab
  while a new quote hides it, whatever the sample holds, and not while a default shows it, the settings mark opening the
  sheet's panel, a sheet's problem marked, and in Preview a hidden sheet without a tab and
  the first that shows marked in place of a hidden one chosen, by the sample or the
  underwriter app's quote;
  `frontend/src/workbench/__tests__/PropertiesPanel.test.tsx` the next component's own
  values, never a value refused for the one before, a Table's and a Collection's
  two panes (Config first with the order and the fields, Display with the title, Show when,
  layout, look, header, lines, frame and columns, the pane kept for each component), the title and layout, a
  Collection's look (its density and text size, its labels' position, their width only
  while beside and refused outside 1 to 99 percent, their case, size, alignment, fill,
  font, bold and wrap, its boxes' style, weight, colour, alignment and width, its frame's
  weight and colour, and a field's own look from Fields, its label's and box's fills,
  fonts and alignments, width
  and bold, each one undo step), a Table's rows starting its grid in the sample again (fewer, then more, every value typed
  kept, each one undo step), a Table's fixed rows and look, its text size, its header's
  case, size (Auto again too), direction, fill, font, bold and wrap, its lines' style,
  weight and colour (Automatic again too), and its frame's weight and colour, gone while
  unframed, each one undo step, a column's own look from Columns (its columns listed
  closed, one opened to its header's and cells' fills, fonts and alignments, its width
  refused outside 24 to 1000 and blank again, and bold, each one undo step, the column
  marked as having a look, no width while transposed, Clear taking it away, and its Show
  when, a condition added and removed, each one undo step, the column marked with an eye
  and Clear keeping it), a colour
  shown as it is picked and one undo step as the picker is left, a pick an undo finished
  first committing nothing as the picker is left and redone after, a Chart's section (its type, what labels its rows, its
  legend, stacked, horizontal and data labels, those a pie does not take disabled), the
  fields ticked, a Chart's from every table with the columns it cannot plot disabled, a
  table greyed out, the order, a Collection's spacers in it (added, dragged and moved by
  Alt+Down twice keeping the focus, removed, each one undo step, and no Add spacer for a
  Table), the pointer to the schema, a component's Show when, a
  Heading's panel (its text shown and committed as typed, spaces kept and emptied, its
  description likewise, each size's value under its label, its alignment a radio group
  chosen by a click or the arrow keys, which the sheet's own keys never see, its Show when, each one undo step, and no title, layout or fields), the sheet's panel (its title and
  its Show when) closing as a component is selected, its width refused below what its
  components reach or past the safe whole numbers and set as one undo step, its Delete
  sheet disabled for the only sheet and asking first in the editor's own dialog, never the
  browser's (the sheet named, the components in every section counted, Cancel and Escape
  keeping it, Delete removing it as one undo step, the schema kept, the first sheet left
  showing and the panel closed), and the section's panel (its title,
  a blank one kept out, its Show when, its height refused below what its components reach
  or past the safe whole numbers and fitted, a height refused for one section never shown
  for the next, Add section below selecting the new one, Delete section with its
  confirmation, disabled for a sheet's only section);
  `frontend/src/workbench/__tests__/ShowWhenEditor.test.tsx` a date condition's day
  committed once, not per digit, "Always", Add condition
  (and its note with no one-row input column), a column, a test and a value chosen for
  each type and committed as one undo step each, the allowed values ticked for "is one
  of", values typed comma-separated, "1,000" typed into a number column's refused with
  its fault and nothing committed, a test against another field chosen and given back a
  value, offered only fields of the column's type but itself, a Date's days typed and
  refused past 36,500, a test of a row against another column of its row, a condition removed, the match sentence with two or
  more, at least with its number (2 when chosen, 1 to as many conditions as there are,
  shown with one condition while it is a number), a condition whose
  column is gone, Add group (its match the Show when's opposite,
  its own Add condition, its sentence, its removal and its last condition's taking it),
  a table's rows in Any row and Every row with two tests of a row, Add test and a test
  removed, Number of rows (its tests, a whole number committed and anything else refused),
  a total (its kinds, its column and a number committed), and a condition whose table
  switched between one row and many fixed from its column select;
  `frontend/src/workbench/__tests__/useWorkbenchShortcuts.test.tsx` Ctrl/Cmd+S through the
  host with a focused field committed first, the sheet's keys, Escape closing the sheet's
  panel and the section's, their guards, the nudge burst, a nudge kept off its section's
  top and bottom, one held there recording nothing and keeping redo, a drag under way
  finished by a nudge inside a burst, Ctrl/Cmd and the arrows resizing from the bottom-right
  corner (a grid step or a pixel with Shift, a burst one undo step, held at its least size
  recording nothing, the sheet widening past its edge), Ctrl/Cmd+C and Ctrl/Cmd+V (copied with a note,
  pasted as copied past the original, again past that copy, into the selected section,
  onto another sheet, with nothing selected, one undo step each, nothing pasted before
  a copy, from a field, from inside the schema's pane or in Preview), the selection's keys acting
  from the sheet or with nothing focused and never from a panel or a button, and Preview's, undo and redo among them; `frontend/src/utils/__tests__/sheetVisibility.test.ts`
  every case in `frontend/src/utils/__tests__/fixtures/sheetVisibility.json` (as
  `tests/test_workbench_visibility.py` does), `quoteEligible` by every case in
  `frontend/src/utils/__tests__/fixtures/typedQuotes.json`, the visibility memoised per
  form and values,
  `quotedSchema`, the tests each type takes and their words, `valueFits`, each
  `conditionFault`, `withPrice` (a Table of referral reasons, and a section holding it,
  shown while the count is above 0, hidden before the first price and once a price gives
  none, a Heading and an output's field hiding by the price, a many-row output tested as a
  list, an input Collection, and a section holding one, never hidden by the price, `hidden`
  as the rounds left it, and the visibility itself while no condition is on the price),
  `takesNothingTyped`, `neverShown` (a box on its own sheet, a box in its own section, two
  sheets waiting on each other, a box elsewhere, a component in a section that can never
  show left to its section, `any` with one reachable condition, a group's condition, a
  count reached through its table's other columns, a list's tests each needing its
  column, a field shown by its own box but not one shown by another field of its
  component, two fields waiting on each other, shown again through a box elsewhere, a field
  of a component that can never show left to it, and a section waiting on a field that
  cannot show again) and the editor's steps;
  `frontend/src/workbench/__tests__/WorkbenchPalette.test.tsx` the palette (a Section, a
  Heading, a Collection, a Table and a Chart), its collapse with the node palette's and its items
  disabled in Preview; and `frontend/src/workbench/__tests__/WidgetBody.test.tsx` a date
  committed once as one edit, a held value its select no longer allows shown and marked,
  a tick left in a column since retyped marked, a Heading
  drawn as its text with no frame, a heading of its size's level, each size semibold, a
  small one in the secondary colour, its description under it while it has one, each
  alignment, drawn so in Preview too, and with no text the hint on one line while
  building, its description not drawn, and nothing in Preview; a Collection's spacers
  each a gap across its whole width where it sits, the fields after it starting a new row;
  and, through `SheetCanvas`, a field a new quote hides faded while building with its eye
  struck through and its spacer kept, then in Preview a Collection closed up around it, its
  spacer gone with it until a box ticked shows it again, and a Table's columns that hide
  left out of its grid, transposed out of its lines, a row deleted from every table it
  shows, the one whose columns all hide too.
- Charts: `frontend/src/utils/__tests__/sheetChart.test.ts` covers `sheetChartData` (a
  one-row Chart's point per field, typed and priced; a many-row Chart's rows typed into its
  input tables, a row with nothing the quote takes left out, outputs matched by key and
  output tables alone over their answer, the rows named by an input's, an output's or no
  column; a hidden column, text that is not a number, a field it cannot plot and a field
  out of line as gaps; the axis's shared format; `stale`, rows named by priced values
  included), `chartMessage` (nothing to plot, a pie's value below 0 and a pie of 0s, a 0
  or a gap beside others drawn, and columns, lines and areas never refused) and
  `sheetChartOptions` (each
  chart type, stacked and horizontal, a pie of one row and of many, the legend's place and
  when it is drawn, data labels and the tooltip in each column's format, the axis
  formatter, series identified by field); `frontend/src/workbench/__tests__/ChartBody.test.tsx`
  (the runtime mocked) a Chart drawn once and given each new option merged, "Nothing to
  chart yet", no fields pointing at the panel while building and nothing in Preview, a
  pie's value below 0 and its 0s said instead of drawn until it can be, its values for a
  screen reader, nothing while building, the sample in Preview and the underwriter app's
  quote, its dimming, a new option set at once once the runtime has loaded, a resize, a
  redraw on a change of theme, a failure's alert and
  disposal on unmount; `frontend/src/workbench/__tests__/ChartBody.runtime.test.tsx` a
  runtime that fails to load shown as an alert and imported again on the next draw; and
  `frontend/src/panels/explore/__tests__/chartRuntime.test.ts`
  Pie registered and a new option's merge settings passed through.
- The sample: `tests/test_workbench_routes.py` covers `POST /api/workbench/tables` for an
  unsaved form (its tables, sample and response tables, the file untouched, a value on a
  hidden sheet left out of the sample), the structured
  422 and a misshapen body, and 404 while disabled; `frontend/src/utils/__tests__/workbenchForm.test.ts`
  a number column's allowed values read as its cells read them, `withCell`, `withSampleCell`, `withSampleRows`, `setTableRows` (a table's rows kept up
  to the last typed in any column and made up to the rows, none with nothing typed, other
  tables as they were), `pricingBasis` (a layout change
  changing nothing, a condition that hides a column changing it, and a hidden column
  renamed or retyped changing it), `linedUpOutputs` and `answeredOutputs` (the first's
  answer, another matched by its keys, none without keys, nothing before a price); `frontend/src/stores/__tests__/useWorkbenchFormStore.test.ts` the
  sample as part of the form, unsaved until saved and undone like any edit;
  `frontend/src/workbench/__tests__/priceSample.test.ts` the top-level nodes patched and
  the Workbench Output's tables previewed in one request, a table missing from the answer
  refused, the document and its submodels untouched, and
  each refusal, a Quote Input beside the Workbench Output and a Workbench Input inside a
  submodel alone among them; `frontend/src/stores/__tests__/pricingLoop.test.ts` the
  pause, one pricing at a time with one more, cancel and settle; `frontend/src/stores/__tests__/useWorkbenchPricingStore.test.ts` pricing
  as the form stands, a price stale once the pipeline, a submodel, the source, the
  document or the data moves, 300ms after the last change, one at a time with one more for the
  latest and the last values dimmed meanwhile, what waits and the one more asked for
  dropped once cancelled, the reason on failure, nothing priced or said while
  nothing is typed in the cells the sheets show, nothing without a pricer or before the
  form is read, and a late answer dropped;
  `frontend/src/workbench/__tests__/WidgetBody.test.tsx` typing into a Collection's boxes
  and a Table's grid with rows inserted above and below a row, one and as many as typed (the box taking no nought or fraction), each as one undo step, and deleted, through its right-click menu, the menu saying Column transposed, the rows it starts with kept by the first
  value typed, in the sample and in the underwriter app's quote, deleted below them and started again
  once the last goes, a starting row deleted with nothing typed, every row held shown, fewer
  than it starts with too, and never fewer than them with fixed rows, a grid showing a
  table a column gives its rows (those rows whatever it starts with, each value as text,
  no menu or delete, and a value typed writing them all with their values), a Table of
  output columns alone (its starting rows of dashes before a price, no menu or delete,
  then a row for each of the answer's rows, nothing to type), a Table drawn as its options say (no row
  numbers, comfortable rows, every gridline, no stripes, fixed rows with no menu or
  delete, a checkbox's cell as tall as its row, its header as written and no frame), a
  Table's own look over the sheets' colours (its frame's colour and weight and its title's
  rule, its header strip's fill, its labels' font, size and weight, the rule under them in
  its lines' colour and weight, its gridlines in their style, the output's line solid, its
  values' size, and a column's own label fill and font, place and width and its cells'
  fill, font, weight and place, an output column's over its shade, each label cell's own
  weight, an index's numbers in its column's font and a group's name in the header's
  weight), its labels turned up,
  down and stacked, wrapped turned and level, the grid as wide as its columns once each
  has a width, and a column's look along its line transposed, with no width, a
  Collection drawn as its options say (its labels beside the boxes, compact boxes and no
  frame), a Collection's own look (its values' and labels' size, capitals, weight and
  wrap, a label's fill drawn as a cell, its labels' width beside their boxes and its
  boxes' width, a box's width leaving the rest to its label, a narrower box under its
  label placed by its alignment, its labels and its values each placed by the
  Collection's alignment and a field's own over it, its boxes' border style, weight and colour on input and
  output boxes alike, its frame's colour and weight, and a field's own label fill and
  font, box fill over the output shade, font, weight, place and width), the sheets' colours (the components' background and borders, a Chart's too, the
  font on titles, labels, values and a Heading, the input and output cells' fills each on
  its own cells, an unframed component on the sheet), numbers in their column's format (an
  output box's and a grid's, a number's options, a percent typed as a fraction, a cell as
  typed while focused), an empty
  cell blank or showing its column's placeholder, a column's help, an output box's value and its dimming, a
  Table's output columns matched by key and dimmed once typed over, the cells marked once
  anything is typed, and while building a new quote's cells, empty but for their defaults
  and inert, a press on
  one selecting the component;
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` the pricer set and cleared and
  the pricing scheduled on the basis and on the pricing context, the pipeline or the data
  moving, in Preview and never while building, a pricing
  waiting as Preview is left dropped on the store's own timer; and
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` the failure's reason.
- Preview: `frontend/src/utils/__tests__/sheetValues.test.ts` covers filled rows, numbers
  and filled values as the server reads them (every case of
  `frontend/src/utils/__tests__/fixtures/typedText.json`), each rule, a tick or text left
  in a column since retyped marked, a Date's value a
  real day, `quoteProblems` by cell, the basis, `pricedRows` and `formattedNumber`, a new quote's
  values (one-row input tables' defaults alone) and `quoteTyped` (a default not typing, a
  value typed over it typing), the rows a column gives (`presetColumn`, `presetRows` held
  to every case in `fixtures/presetRows.json`, `withPresetRows`) and their values not
  typing for `quoteFilled` or `quoteTyped`, and each read
  through a quoted schema (a hidden cell not filling a row, never checked, counting for
  nothing);
  `frontend/src/stores/__tests__/useWorkbenchPreviewStore.test.ts` the app's quote apart
  from the sample (cells and rows, without history), priced through the quote pricer 300ms
  after the last change whatever its cells hold and not again for the same values, a blank quote
  priced not at all, nor one holding its defaults alone, a locked column taking no typing and not making a quote typed, a saved
  quote opened with its price, read only or not, one that may change and has none priced
  as it opens, a value in a hidden section left out of what is priced and the price's
  basis without it, one more pricing for typing during one and `settle` waiting for both, and for a value
  entered after it began,
  an answer dropped once another quote was opened or the quote pricer went, a failure's
  reason, and nothing without a quote pricer or before the form is read;
  `frontend/src/app/__tests__/UnderwriterApp.test.tsx` the underwriter app's page: the
  release's sheets drawn in Preview with the release and who is signed in and none of the
  builder's controls, no Price or Clear, a quote priced as it is typed through the app's route on the page's form revision
  and release, the sheets that could not be opened said, an app whose quotes are closed
  opening no quote, new or saved, saying they are unavailable and asking for the release
  again every 10 seconds until they open,
  one whose quotes are open opening on the Quotes page and a quote at its address and saying there is no page at
  an unknown one, and the quote pricer dropped once the page goes;
  `frontend/src/app/__tests__/UnderwriterAppClosing.test.tsx` the quotes closing as the
  app's routes answer, through `frontend/src/api/app.ts` with only its requests faked: a
  closed store's 503 closing them, a busy pricing's not, and text typed into a field and
  not yet left kept in the open quote's draft as it closes and offered back once they open;
  `frontend/src/app/__tests__/route.test.ts` each address read and written back, a quote by
  its reference and option, an address naming no quote missing and an unknown list or
  origin read as the default, the page moved and told, `navigate` answering `moved`, `here`
  and `kept`, the leave guard asked and obeyed unless forced, and Back put back while it is
  asked through real traversals, the history's entries kept and Forward leading where it
  did;
  `frontend/src/app/__tests__/quoteValues.test.ts` values one quote however their blanks
  were left, a saved price, one quote by its reference, case included, and option, the
  details the app sets put on the sheets from a quote's details and taken out of values a
  quote is made from, each misfit value and each required field a refusal names, a repeated
  write superseded known, the Quotes page's columns labelled from the sheets' or by their
  names and their values, a date as a day, what a quote was made from in a sentence, drafts
  kept, read, cleared, and none when the browser keeps none or a held write names no
  reference and option, fresh operation keys, the reference's rules, `.` and `..` refused,
  the kept creation and a renewal ticked;
  `frontend/src/app/__tests__/useOpenQuoteStore.test.ts` a quote opened with its price by
  its reference and option, the rows the sheets have no place for named, a failed open, a
  priced and an unpriced save, a moved price, values typed during a save kept unsaved, a
  save waiting for the pricing to settle and keeping its price, a quote priced on another
  release priced again as it opens, a save over a quote moved on read into an offer with
  the values kept, a page of another release, each value a save was refused for named and
  the write let go, an operation key kept after a lost answer or a server failure and let
  go on a refusal, a lost save repeated before saving what was typed since, each written
  once, a lost save let go when refused and when it had landed and the quote moved on
  since, offering the newest and saving nothing typed since over the version it moved on
  from, a held save from an earlier visit let go as done and what is typed saved over the
  version opened, nothing saved untyped, a held save kept through a note and let go once a
  change moves the version on, an alternative's key asked with again after a reload and let
  go once made or refused, held apart from another quote's, no change during a save nor a
  save during a change, what is typed before and during a change that holds the quote read
  only kept and offered back once it is restored, nothing kept or saved while a quote is
  being opened, a save's answer dropped once the quote is left, an alternative made from
  its source's values without the details the app set for the source, a source that could
  not be read, a draft offered back, restored and discarded, kept apart by reference, case
  included, and option, not offered on a quote that may not change or when the same, and
  put back on the sheets' columns alone naming what has no place, a change over the version
  it moved the quote to holding it read only as it leaves it, each required field Mark
  final was refused for named, the details a change set taken onto the sheets from the
  quote it answers with nothing new to save, a change made only on the quote it was asked
  about, a note not moving the version a save names, answers dropped once another quote was
  opened, a conflicting change, the activity read while the history is open, the one asked
  for last shown, its failure said, and read again as the quote is opened, saved and
  changed, the newest open winning, nothing done with nothing open, each notice dismissed,
  and every quote read only for a reader, who changes nothing;
  `frontend/src/api/__tests__/app.test.ts` the price, a creation unpriced with its operation
  key, values, source and reference, a quote and its activity read by its reference encoded
  and its option, and a save over the version it names, each body valid by the generated
  model of the route that reads it;
  `frontend/src/app/__tests__/useQuoteListStore.test.ts` a list loaded and loaded again
  only when asked, its rows kept, an answer to a list since left dropped, the list shown
  loaded again whichever it is, the next page, past the last, and failures;
  `frontend/src/app/__tests__/useAppStore.test.ts` the people, none when unread, one
  question at a time, and the quotes closed by a closed store's refusal and not by a busy
  pricing's;
  `frontend/src/app/__tests__/QuotesPage.test.tsx` the table of the quotes' details under
  the sheets' labels with the lists' and stages' counts, the list, stage, order and assignee
  in the address, the search after a pause and from the address, each empty list's reason,
  a failed read with Retry, Load more, opening and duplicating from a row, New quote and the
  Reference order, archiving with Undo and its failure, Undo reloading the list shown when
  it is pressed, restoring, `/` and `N`, neither from a focused select, and a reader offered
  nothing that changes a quote;
  `frontend/src/app/__tests__/QuoteView.test.tsx` an alternative made from its source's
  values under its reference and taken to its own address, once though its answer is lost
  and the page loaded again, and the Quotes page kept when its answer comes after it was
  left, the line and Ctrl+S, leaving asked and kept on Cancel, the draft kept as typed and
  dropped once typed back to the saved values, Mark final and Amend with a reason, Mark final
  with a required field empty left to the server to name, each value a save was refused for
  named, a question about a quote dropped once the page leaves it, changing nothing, Mark
  final off for a quote unpriced or priced on an earlier release, the conflict's offers and
  a repeated save superseded offering the newest, the release changed, Open the newest
  reading the quote once, a duplicate made from a quote with unsaved values whose leave guard
  keeps the page there saying it was made, the rows that have no place named, archive and
  restore with Undo, Undo restoring the quote archived once another is open, Save unpriced
  once a pricing failed, Save offered for a price made since or of another release, a
  renewal over the quote, Copy link, a draft offered back, assigning and the details it set
  taken onto the sheets with what was typed kept, the name typed in the bar into its
  `quote_details` cell and saved with the quote, the history panel's note, related quotes and
  activity, a quote that could not be opened, and a reader shown the quote read only with
  nothing that changes it;
  `frontend/src/workbench/__tests__/WidgetBody.test.tsx` the underwriter app's quote typed
  apart from the sample with its marked cells and its own price, and a quote held read
  only taking no typing and no rows added or deleted;
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` Build and Preview, the same
  controls in both, Display disabled in Preview, Schema opening and closing the schema's
  pane in both, no Price or Clear, Undo and Redo in
  both, the sample's failure in Preview alone, and Reset disabled while building and
  on a new quote, starting the sample again at its defaults as one undo step; and
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` Preview laid out as building,
  the schema's pane kept open and read only, the palette's components disabled and no
  properties panel.
- The quote's tables: `tests/test_config_io.py` writes a Workbench Input's config file, its
  sample included, to `config/workbench_input/` and reads it back as ports, and refuses one
  with a `path`.
- The Workbench Input: `tests/test_request_inputs.py` runs the checker over `src/haute` after
  its fixtures show each allowed form passing and each other form reported; holds
  `REQUEST_INPUT_KINDS` and the editor's `REQUEST_INPUT_TYPES` equal to
  `REQUEST_INPUT_NODE_TYPES`; holds a Quote Input's labels, a workbench table's names and the
  editor's source handles to `is_frame_label` alike; shows a Workbench Input beside a Constant
  as the request input a Quote Input with the same tables is through codegen and parsing
  (with no `path` in its config file), data points, `Pipeline.score`, deploy scoring and
  trace lineage, while its preview yields nulls and it has no snapshot; previews one typed
  null row per table, with a join downstream admitted, and refuses tables without columns as
  ports; reads its table points directly, in the cache report and through the node-data
  routes' point, run and clear too, the routes giving the browser's graph the project's form;
  derives its request schema and dry-run quote (tables not as the workbench gives them
  refused) and resolves a deploy without a sample file, the request naming the quote in its
  quote_details; runs its generated code on the form folder's tables, its config file holding
  none (`"tables" not in` the parsed config), and parses a submodel's `workbench_input`;
  opens a config file holding copies for its repair, the strict parse refusing it naming the
  keys and recovery dropping them
  (`test_a_saved_workbench_input_holding_copies_opens_for_its_repair`), and gives the
  document's identity no handles while each edge carries its input name;
  previews, runs and leases its sample's rows, a downstream calculation included, with no
  rows for a many-row table the sample leaves out, a null for a column a row leaves out and
  the null quote while there is no sample (`test_a_workbench_input_previews_its_sample`);
  reads a sample as the request it stands for, frame for frame
  (`test_a_sample_prices_as_its_quote_would_when_deployed`, and at the form's level in
  `tests/test_workbench_tables.py`); cuts the sample to a demand's ports and columns; reads each value as its column's type and refuses each that is not, naming it;
  changes a table point's version with the sample; fails each kind of misfit wherever rows
  are read, a join's planning and the preview route's worker (422) included, while the cache
  report still has the points; never reads the sample for a request
  (`test_a_request_never_reads_the_sample`); reads a deployed request into the tables, one
  quote per request, from a frame or from the records of a `QuoteRequest`, a quote leaving a
  table out scored, each misfit refused naming its spot and a `QuoteRequest` refused for a
  Quote Input (`test_a_deployed_pipeline_reads_the_request_into_the_tables`); sizes each
  port's rows for the memory estimate
  (`test_a_deployed_request_is_sized_per_table_for_the_memory_estimate`, with the index in
  `tests/test_ram_estimate.py`); answers a misfit through the generated container's `/quote`
  as the structured 422 (`test_the_containers_quote_route_reads_the_request_as_sent`); and
  refuses a second request input in save and in `resolve_config`.
- The tables given from the form: `tests/test_workbench_nodes.py` runs a browser's graph on
  the project form's tables and sample through the preview route, an edit of the folder
  reaching the next request past every cached result; runs a posted `workbench_form` in
  place of the folder's, nothing written; gives a submodel's Workbench Input the same tables,
  the graph it was given left as it was; reads no form for a graph without a workbench node;
  refuses a Workbench Input or Output in a project whose workbench is not enabled, naming it,
  a preview reporting it at the node; refuses a save holding a node's tables or sample,
  nothing saved, and a connection from a table named like a node; bundles the form's tables
  and no sample in deploy, the config files holding neither; refuses a form deploy cannot
  read (not JSON, gone) before anything is built; and gives `haute run` the form's tables,
  saying why it cannot read one; and holds a Workbench Input `score()` seeds to its form, a
  connection on a table the form dropped and a sidecar holding copies each refused.
  `tests/test_config_io.py` refuses `tables` and `sample`
  written to or read from a workbench node's config file, naming them, and reads an empty one
  back as `{}`; `tests/test_deploy_validators_gaps.py` names a connection to a table the form
  does not have (`connection_errors`); `tests/test_assistant_tools.py` resolves the brief
  again when the form's tables change under the same project revision.
  `tests/test_deploy_batch_scoring.py` keeps a null sample's types in the hard-capped worker,
  for a one-row and a many-row table; `tests/test_deploy_internals.py` builds the nested
  MLflow signature and refuses a `Decimal` inside a table;
  `tests/test_deploy_expected_output_validation.py` walks an expected output into a Workbench
  Output's tables with the tolerance and lets a quote leave a table out of the pre-check. `tests/test_assistant_ops.py` covers the
  assistant's refusals and its wiring to a Workbench Input's frames, and
  `tests/test_codegen_roundtrip_property.py` round-trips one in its corpus.
- In the frontend, `frontend/src/utils/__tests__/requestInputTypes.test.ts` runs the editor's
  guard over `frontend/src` after its fixtures, and covers `isRequestInputType` and the shared
  singleton slot; `frontend/src/utils/__tests__/apiInputPorts.test.ts` a Workbench Input's
  frames (its tables with a column, named by their tables, judged as labels are) and their
  columns, and connections following names; `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts` a palette
  drop reporting its node; and `frontend/src/hooks/__tests__/useNodeDataCache.test.tsx` a point
  that reads directly clearing nothing.
- The Workbench Output: `tests/test_workbench_output.py` covers a cell of a table fed
  straight from the Workbench Input traced from that frame, the tables read from a config
  (no tables refused, tables without columns refused, a `mapping` key refused in parsing and
  in the pipeline file's own run), filling (by
  name, case included, a typed null for a column the frame lacks and for one whose dtype does
  not fit, a connected table of nulls alone keeping its frame's rows (none or two, and a
  one-row one refused for either), a table nothing is connected to, one-row and many-row, a node with nothing
  connected run to its response, deploy answering without them, a one-row table with other
  than one row, the columns cut to the table's in order), the response document and `as_response` for either response node, a pipeline running
  and scoring to it, codegen and parsing of `target_port`, the second response node refused in
  save and deploy, deploy pruning to it and serving its response, the preview's table
  picker, and three tables previewed from one run (`port_labels`), each as its preview
  alone answers, through the executor and the route, a table it does not fill named. In the frontend, `frontend/src/utils/__tests__/workbenchTables.test.ts` covers its
  response tables and their ports from the newest fetch and its connection to a response
  table the fetch no longer has, `frontend/src/nodes/__tests__/PipelineNode.test.tsx` its
  port rows, `frontend/src/utils/__tests__/connectionValidation.test.ts` the connection rules,
  `frontend/src/utils/__tests__/nodeUpdatePlan.test.ts` an update of the node removing none
  of its connections, `frontend/src/hooks/__tests__/useWorkbenchConnections.test.tsx` that
  connection removed and reported as one it filled, and
  `frontend/src/hooks/__tests__/usePipelineAPI.nodeDataEpoch.test.ts` the preview's first table.
