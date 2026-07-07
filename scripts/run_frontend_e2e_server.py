"""Start a deterministic backend + Vite pair for Playwright browser tests."""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import polars as pl

from haute._local_security import SESSION_TOKEN_HEADER, ensure_local_session_token_env
from haute.cli._helpers import _node_env, _npm
from haute.cli._init_cmd import InitConfig, handle_init

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
E2E_PROJECT_DIR = REPO_ROOT / ".tmp-e2e-project"
BACKEND_URL = "http://127.0.0.1:8000/api/pipeline"
FRONTEND_URL = "http://127.0.0.1:5173/"
READINESS_HOST = "127.0.0.1"
READINESS_PORT = 5174
READINESS_URL = f"http://{READINESS_HOST}:{READINESS_PORT}/ready"
_BROWSER_MODEL_BLOCK = """


@pipeline.modelling(config="config/model_training/browser_model.json")
def browser_model(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    \"\"\"Browser E2E training node for async modelling flows.\"\"\"
    return raw_rows
"""
_BROWSER_OPTIMISER_BLOCK = """


@pipeline.data_source(config="config/data_source/browser_optimiser_rows.json")
def browser_optimiser_rows() -> pl.LazyFrame:
    \"\"\"Browser E2E scored rows for optimiser flows.\"\"\"
    df = pl.scan_parquet(Path(__file__).parent.parent / "data" / "optimiser_sample.parquet")
    return df


@pipeline.optimiser(config="config/optimisation/browser_optimiser.json")
def browser_optimiser(browser_optimiser_rows: pl.LazyFrame) -> pl.LazyFrame:
    \"\"\"Browser E2E optimisation node for async optimiser flows.\"\"\"
    return browser_optimiser_rows


@pipeline.optimiser_apply(config="config/apply_optimisation/browser_apply.json")
def browser_apply(browser_optimiser: pl.LazyFrame) -> pl.LazyFrame:
    \"\"\"Browser E2E optimiser-apply node backed by saved optimiser artifacts.\"\"\"
    return browser_optimiser
"""
_BROWSER_MODEL_CONFIG = """{
  "name": "browser_model",
  "target": "value",
  "algorithm": "catboost",
  "task": "regression",
  "params": {
    "iterations": 4,
    "depth": 2
  },
  "split": {
    "strategy": "random",
    "validation_size": 0.2,
    "seed": 42
  },
  "metrics": [
    "gini",
    "rmse"
  ],
  "row_limit": 30,
  "output_dir": ".haute_cache/browser_training"
}
"""
_BROWSER_OPTIMISER_CONFIG = """{
  "mode": "online",
  "objective": "expected_income",
  "constraints": {
    "volume": {
      "min": 0.9
    }
  },
  "quote_id": "quote_id",
  "scenario_index": "scenario_index",
  "scenario_value": "scenario_value",
  "max_iter": 20,
  "tolerance": 0.0001,
  "record_history": true,
  "frontier_enabled": true,
  "frontier_steps": 5,
  "frontier_ranges": {
    "volume": {
      "min": 0.85,
      "max": 0.99
    }
  }
}
"""
_BROWSER_OPTIMISER_APPLY_CONFIG = """{
  "sourceType": "file",
  "artifact_path": "rating/output/optimiser_browser_optimiser.json",
  "version_column": "__optimiser_version__"
}
"""
_QUOTES_API_INPUT_BLOCK = """


@pipeline.api_input(config="config/quote_input/quotes.json", contract="opaque")
def quotes() -> pl.LazyFrame:
    \"\"\"Browser E2E apiInput node for v2-native flow tests.\"\"\"
    from pathlib import Path

    import orjson

    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import (
        is_per_port_cache_valid,
        load_per_port_cache,
    )

    _data_path = Path(__file__).parent.parent / "data/quotes/sample_quote.json"
    _config_path = Path("config/quote_input/quotes.json")
    _v2_config = orjson.loads(_config_path.read_bytes())
    _cache_dir = _json_cache_dir(str(_data_path), "working")
    if not is_per_port_cache_valid(_cache_dir, _v2_config, data_path=str(_data_path)):
        _cache_dir = _json_cache_dir(str(_data_path), "committed")
        if not is_per_port_cache_valid(_cache_dir, _v2_config, data_path=str(_data_path)):
            raise RuntimeError(
                "API Input data hasn't been cached for the current schema. "
                "Click 'Cache as Parquet' on the API Input node to build it."
            )
    _emit_labels = [
        t["label"]
        for t in (_v2_config.get("tables") or [])
        if t.get("emit")
        and any(c.get("selected") for c in (t.get("columns") or []))
    ]
    _bundle = load_per_port_cache(_cache_dir, _v2_config)
    if len(_emit_labels) == 1:
        return _bundle[_emit_labels[0]]
    return {label: _bundle[label] for label in _emit_labels if label in _bundle}
"""
# V2-native starting state: a data path is set (so the Infer Tables
# button is visible) but no schema yet — the editor renders the bare v2
# surface and the test drives Infer Tables to populate tables[]. A
# separate test covers the file-pick → preview-auto-load gap.
_QUOTES_API_INPUT_CONFIG = '{\n  "path": "data/quotes/sample_quote.json"\n}\n'

# Inline sample data for the apiInput e2e fixture.  Earlier versions of
# this harness tried to `shutil.copy2` from a developer-local file at the
# repo root, but `data/` is gitignored on this project — on CI the source
# file doesn't exist and the harness fell over at the subsequent
# `git add -f` step.  Inlining keeps the harness self-contained.  The
# shape exercises the multi-table v2 Infer-Tables path. Under the
# 2026-06-17 object-nesting ruling (commit 6ae967c7), relational depth is
# ARRAY-nesting depth only: a single nested object folds into its parent
# table as dotted-leaf columns (`proposer.first_name`), so the two 1-1
# structs (`proposer`, `vehicle`) add columns to the ROOT table — they do
# NOT mint child tables. A child table is minted only by a nested LIST of
# records, so the fixture carries a `claims` array of objects: that
# descends one relational level and mints the `$[:].claims[:]` child.
# Net: one root table + one child = 2 tables, which is what the spec's
# "≥ 2 tables" assertion checks — and it exercises real child-table
# inference rather than mere dotted-column folding.
_QUOTES_SAMPLE_DATA = """[
  {
    "quote_id": "q_001",
    "quote_version": 1,
    "channel": "direct",
    "premium_amount": 543.21,
    "is_renewal": false,
    "proposer": {
      "first_name": "Ada",
      "date_of_birth": "1985-03-12",
      "licence_held_years": 12
    },
    "vehicle": {
      "make": "Tesla",
      "model": "Model 3",
      "year_of_registration": 2022
    },
    "claims": [
      {
        "claim_date": "2021-06-14",
        "amount": 1240.5,
        "at_fault": true
      },
      {
        "claim_date": "2023-02-02",
        "amount": 305.0,
        "at_fault": false
      }
    ]
  },
  {
    "quote_id": "q_002",
    "quote_version": 1,
    "channel": "aggregator",
    "premium_amount": 712.0,
    "is_renewal": true,
    "proposer": {
      "first_name": "Beatrice",
      "date_of_birth": "1978-07-05",
      "licence_held_years": 20
    },
    "vehicle": {
      "make": "Ford",
      "model": "Focus",
      "year_of_registration": 2018
    },
    "claims": [
      {
        "claim_date": "2022-09-30",
        "amount": 89.99,
        "at_fault": false
      }
    ]
  }
]
"""


def _assert_under_repo(path: Path) -> None:
    repo_root = REPO_ROOT.resolve()
    resolved = path.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise RuntimeError(f"Refusing to operate outside repo root: {resolved}")


def _reset_project_dir() -> None:
    _assert_under_repo(E2E_PROJECT_DIR)
    if E2E_PROJECT_DIR.exists():

        def _retry_remove_readonly(func: object, path: str, _exc_info: object) -> None:
            Path(path).chmod(stat.S_IWRITE)
            func(path)

        shutil.rmtree(E2E_PROJECT_DIR, onerror=_retry_remove_readonly)
    E2E_PROJECT_DIR.mkdir(parents=True)


def _augment_starter_pipeline() -> None:
    main_path = E2E_PROJECT_DIR / "rating" / "main.py"
    source = main_path.read_text(encoding="utf-8")
    source = source.replace(
        'Path(__file__).parent / "../data/sample.parquet"',
        'Path(__file__).parent.parent / "data" / "sample.parquet"',
    )
    if "def browser_model(" not in source:
        source = source.rstrip() + _BROWSER_MODEL_BLOCK
    if "def browser_optimiser(" not in source:
        source = source.rstrip() + _BROWSER_OPTIMISER_BLOCK
    if "def quotes(" not in source:
        source = source.rstrip() + _QUOTES_API_INPUT_BLOCK
    main_path.write_text(source, encoding="utf-8")

    raw_rows_config_path = E2E_PROJECT_DIR / "rating" / "config" / "data_source" / "raw_rows.json"
    raw_rows_config_path.write_text(
        '{\n  "path": "data/sample.parquet",\n  "sourceType": "flat_file"\n}\n',
        encoding="utf-8",
    )
    optimiser_rows_config_path = (
        E2E_PROJECT_DIR / "rating" / "config" / "data_source" / "browser_optimiser_rows.json"
    )
    optimiser_rows_config_path.write_text(
        '{\n  "path": "data/optimiser_sample.parquet",\n  "sourceType": "flat_file"\n}\n',
        encoding="utf-8",
    )

    config_dir = E2E_PROJECT_DIR / "rating" / "config" / "model_training"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "browser_model.json").write_text(_BROWSER_MODEL_CONFIG, encoding="utf-8")

    optimisation_dir = E2E_PROJECT_DIR / "rating" / "config" / "optimisation"
    optimisation_dir.mkdir(parents=True, exist_ok=True)
    (optimisation_dir / "browser_optimiser.json").write_text(
        _BROWSER_OPTIMISER_CONFIG,
        encoding="utf-8",
    )

    apply_dir = E2E_PROJECT_DIR / "rating" / "config" / "apply_optimisation"
    apply_dir.mkdir(parents=True, exist_ok=True)
    (apply_dir / "browser_apply.json").write_text(
        _BROWSER_OPTIMISER_APPLY_CONFIG,
        encoding="utf-8",
    )

    # apiInput (v2-native flow): empty config that the test will populate
    # via Infer Tables, plus the nested-JSON data fixture.
    quote_input_dir = E2E_PROJECT_DIR / "rating" / "config" / "quote_input"
    quote_input_dir.mkdir(parents=True, exist_ok=True)
    (quote_input_dir / "quotes.json").write_text(_QUOTES_API_INPUT_CONFIG, encoding="utf-8")
    quotes_data_dir = E2E_PROJECT_DIR / "data" / "quotes"
    quotes_data_dir.mkdir(parents=True, exist_ok=True)
    (quotes_data_dir / "sample_quote.json").write_text(_QUOTES_SAMPLE_DATA, encoding="utf-8")


def _scaffold_e2e_project() -> None:
    _reset_project_dir()
    os.chdir(E2E_PROJECT_DIR)
    handle_init(InitConfig(target="databricks", ci="none", force=True))
    _augment_starter_pipeline()

    ids = list(range(1, 31))
    values = [float(9 + (i * 4) + (i % 3)) for i in ids]
    values[:3] = [11.0, 23.0, 37.0]
    sample = pl.DataFrame(
        {
            "id": ids,
            "value": values,
        }
    )
    data_dir = E2E_PROJECT_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    sample.write_parquet(data_dir / "sample.parquet")

    scenario_values = [0.8, 0.9, 1.0, 1.1, 1.2]
    optimiser_rows: list[dict[str, object]] = []
    for quote_num in range(1, 9):
        quote_id = f"q_{quote_num:03d}"
        base_income = 100.0 + (quote_num * 25.0)
        base_volume = 1.0 + ((quote_num % 3) * 0.08)
        for scenario_idx, scenario_value in enumerate(scenario_values):
            optimiser_rows.append(
                {
                    "quote_id": quote_id,
                    "scenario_index": scenario_idx,
                    "scenario_value": scenario_value,
                    "expected_income": round(base_income * scenario_value, 4),
                    "volume": round(base_volume * (2.0 - scenario_value), 4),
                }
            )
    optimiser_sample = pl.DataFrame(optimiser_rows).with_columns(
        pl.col("scenario_index").cast(pl.Int32),
        pl.col("scenario_value").cast(pl.Float32),
        pl.col("expected_income").cast(pl.Float32),
        pl.col("volume").cast(pl.Float32),
    )
    optimiser_sample.write_parquet(data_dir / "optimiser_sample.parquet")


def _run_git(*args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(E2E_PROJECT_DIR),
        check=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _init_git_repo() -> None:
    _run_git("init")
    _run_git("branch", "-M", "main")
    _run_git("config", "user.name", "Haute E2E")
    _run_git("config", "user.email", "haute-e2e@example.com")
    _run_git("add", "-A")
    _run_git(
        "add",
        "-f",
        "data/sample.parquet",
        "data/optimiser_sample.parquet",
        "data/quotes/sample_quote.json",
    )
    _run_git("commit", "-m", "Initial scaffold")


# Must match frontend/e2e/projectIsolation.ts, which recreates this exact
# state before every test (resetE2eProject scrubs branches + untracked files).
E2E_WORKING_BRANCH = "pricing/haute-e2e/work"


def _seed_working_branch() -> None:
    """Record a healthy working-branch pair so the app boots modal-free.

    The version-control startup readiness check (S27) opens the
    WorkingBranchModal over the canvas whenever a git repo has no recorded
    working branch ("unset"). The fixture must model a healthy configured
    clone: working branch + its ``-save`` ledger at the scaffold commit, HEAD
    on the ledger (normal operating posture), and ``.haute/state.json``
    recording the association — written via the engine's own writer so the
    shape can't drift from what ``read_working_branch`` expects.
    """
    from haute._git_state import write_working_branch

    _run_git("branch", E2E_WORKING_BRANCH, "main")
    _run_git("branch", f"{E2E_WORKING_BRANCH}-save", "main")
    _run_git("switch", f"{E2E_WORKING_BRANCH}-save")
    write_working_branch(E2E_PROJECT_DIR, E2E_WORKING_BRANCH)


def _start_vite() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    node_env = _node_env()
    if node_env is not None:
        env.update(node_env)
    token = ensure_local_session_token_env()
    if token:
        env["VITE_HAUTE_SESSION_TOKEN"] = token
    return subprocess.Popen(
        [
            _npm(),
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort",
        ],
        cwd=str(FRONTEND_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
    )


def _start_backend() -> subprocess.Popen[bytes]:
    ensure_local_session_token_env()
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "haute.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ],
        cwd=str(E2E_PROJECT_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _local_session_headers() -> dict[str, str]:
    token = ensure_local_session_token_env()
    if not token:
        return {}
    return {SESSION_TOKEN_HEADER: token}


def _url_ready(url: str, *, timeout: float = 1.0) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers=_local_session_headers(), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
        if 200 <= status < 400:
            return True, f"HTTP {status}"
        return False, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 400:
            return True, f"HTTP {exc.code}"
        return False, f"HTTP {exc.code}"
    except OSError as exc:
        return False, str(exc)


def _wait_for_url(
    url: str,
    *,
    label: str,
    processes: list[subprocess.Popen[bytes]],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = "not checked"
    while time.monotonic() < deadline:
        for proc in processes:
            exit_code = proc.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"{label} readiness failed because process {proc.args!r} "
                    f"exited with code {exit_code}."
                )

        ready, last_status = _url_ready(url)
        if ready:
            print(f"[e2e] {label} ready ({last_status})")
            sys.stdout.flush()
            return
        time.sleep(0.5)

    raise TimeoutError(f"{label} was not ready at {url}: {last_status}")


def _readiness_status() -> tuple[bool, dict[str, str]]:
    backend_ready, backend_status = _url_ready(BACKEND_URL, timeout=0.5)
    frontend_ready, frontend_status = _url_ready(FRONTEND_URL, timeout=0.5)
    checks = {
        "backend": backend_status,
        "vite": frontend_status,
    }
    return backend_ready and frontend_ready, checks


class _ReadinessHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/", "/ready"}:
            self.send_error(404)
            return

        ready, checks = _readiness_status()
        payload = json.dumps({"ready": ready, **checks}).encode("utf-8")
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve_readiness() -> None:
    server = ThreadingHTTPServer((READINESS_HOST, READINESS_PORT), _ReadinessHandler)
    print(f"[e2e] Ready    -> {READINESS_URL}")
    sys.stdout.flush()
    server.serve_forever()


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    _scaffold_e2e_project()
    _init_git_repo()
    _seed_working_branch()
    backend_proc = _start_backend()
    vite_proc = _start_vite()
    processes = [backend_proc, vite_proc]

    def _cleanup() -> None:
        for proc in processes:
            _terminate_process(proc)

    def _shutdown(*_: object) -> None:
        _cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("[e2e] Frontend -> http://127.0.0.1:5173")
    print("[e2e] Backend  -> http://127.0.0.1:8000")
    sys.stdout.flush()

    try:
        _wait_for_url(
            BACKEND_URL,
            label="Backend",
            processes=processes,
            timeout_seconds=120,
        )
        _wait_for_url(
            FRONTEND_URL,
            label="Vite",
            processes=processes,
            timeout_seconds=120,
        )
        _serve_readiness()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
