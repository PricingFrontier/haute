# Frequency modelling workflow audit

These scripts replay the browser workflow against the real backend, preserve timings,
HTTP response sizes, console errors/warnings, screenshots and optional cProfile data.
They use the saved frequency node configuration and the full input dataset. No mock
training or reduced tree count is used.

Run from the Haute repository with its Python environment and frontend dependencies
installed. Build the frontend first (`npm --prefix frontend run build`). Use a **new,
disposable project**: the browser runner trains and applies models there.

```sh
.venv/bin/python scripts/modelling_audit/prepare.py /path/to/demo /tmp/frequency-run
.venv/bin/python -m scripts.modelling_audit.audit_server \
  --project /tmp/frequency-run --output /tmp/frequency-evidence --port 8012
```

In another terminal:

```sh
node scripts/modelling_audit/workflow-interactions.mjs \
  --project /tmp/frequency-run --output /tmp/frequency-evidence \
  --url http://127.0.0.1:8012 --node freq \
  --table-budget 42 --tolerance 0.75 --strength 4
```

The runner uses Playwright's installed Chromium, or `--chromium /path/to/chromium`.
Install Chromium and its system libraries through the usual Playwright setup if needed.
Pass `--source-file` if the project pipeline differs from `rating/main.py`.
The default run refuses an existing applied model (`--resume` explicitly allows a
retained full fit with a pending pruning path). It fails if the requested path point is absent,
a browser assertion fails, or an API request returns an error. It does not silently
select a different recipe. Its disk polling reads compact stage views so measurement
polling does not inflate the server's HTTP workload. Final evidence includes the
selected stage, metrics, candidate inspection and Apply timings, and browser long tasks.

The budgets offered depend on the configuration (42 is one of the ten points for the
current frequency node). If the requested point is absent, the runner fails and lists the
available candidates.

Add `--phase-timing` to the server to record wall-clock spans of the parent's job handoff
(artifact validation, stage preparation and publication, follow-up launch) and of each
worker job in `phase-timing.jsonl`. It adds negligible overhead and cannot be combined
with `--profile`.

Add `--profile` to the server to collect worker/request cProfile files. Compare profiled
runs only with profiled runs; instrumentation adds overhead. To measure ordinary wall
time, omit that flag. Keep each run's output directory separate. Stop the audit server
with Ctrl-C when done.

The browser writes `request.json`, which supports targeted repeat measurements:

```sh
.venv/bin/python scripts/modelling_audit/read_bench.py \
  --project /tmp/frequency-run --output /tmp/frequency-evidence \
  --request /tmp/frequency-evidence/request.json
.venv/bin/python scripts/modelling_audit/path_bench.py \
  --project /tmp/frequency-run --output /tmp/frequency-evidence \
  --request /tmp/frequency-evidence/request.json --stage smoothed
```

Use `--full` to measure dense table retrieval. `path_bench.py --apply` also measures
re-applying the selected point and therefore changes the disposable project's current
revision. `serialization_bench.py --checkpoint FILE.hstage --data FILE.parquet --output DIR`
compares JSON/binary model loading and asserts identical predictions for 1,000 rows.

Use `--full-only` to stop after the GBM and automatic pruning path. This also
checks live and completed bag curves, their visibility controls, and that the
model progress bar neither retreats nor disappears during the handoff. Progress
samples and Training/Validation screenshots are saved with the run evidence.
