"""Multi-frame end-to-end integration test (MULTI_FRAME_PLAN.md, "Commit 9").

Source plan: ``notes-haute/data-model/MULTI_FRAME_PLAN.md`` §"Commit 9 — End-to-end
integration test". This drives the full multi-port HTTP chain a user exercises in
the canvas — save the graph, build the per-port JSON cache, preview the OUTPUT
node — and asserts the reassembled nested document structurally equals a
checked-in fixture (``tests/fixtures/multi_frame_expected_output.json``).

The canonical multi-port witness is the data-model example
(``tests/fixtures/output_assembler/data_model_example.json``): a single nested
document shredded by a v2 apiInput into four ports (policies / drivers / licenses
/ vehicles, with W1 ancestor keys distributed into the child tables), then
reassembled by the OUTPUT node's ``outputMapping`` back into the *same* nested
document. Under that identity round-trip the input document IS the expected
output, so the fixture is a copy of ``data_model_example.json``. The test reuses
the apiInput/OUTPUT config + expected-document helpers already imported by
``test_output_assemble_routes.py`` rather than coupling to the mutable live
``rating/`` tree (whose ``Quote_Response_*`` filename has drifted from the plan
text).

HARNESS CHOICE — in-process ``TestClient``, NOT a uvicorn subprocess.
The plan (step 1) explicitly authorises following whichever boot pattern is
already canonical in this repo's tests. Every HTTP-route test here
(``test_json_cache_integrity``, ``test_output_assemble_routes``, ...) uses
``fastapi.testclient.TestClient(app)`` in-process; ``tests/test_e2e.py`` is
entirely in-process (no HTTP at all). A real uvicorn subprocess is the heavyweight
Playwright harness (``scripts/run_frontend_e2e_server.py``) and is wrong for a
pytest function. The four "stability protections" the plan names are all
satisfied by TestClient:

- Ephemeral port / no port-8000 collision: TestClient has no real socket, so the
  reviewers' flagged port race cannot occur.
- Readiness probe: TestClient(app) is synchronously ready; no poll needed.
- Temp cache directory / no leakage: the build route and executor both resolve
  the cache dir via ``_json_cache_dir`` which is rooted at ``Path.cwd()``
  (``haute._json_flatten``). We ``monkeypatch.chdir(tmp_path)`` + ``set_project_root``
  so ``.haute_cache`` lands under the per-test tmp dir — the established
  ``test_output_assemble_routes`` pattern.
- File-watcher disabled: the watcher only starts in the server lifespan, which
  runs only when TestClient is entered as a context manager. We instantiate it
  BARE (no ``with``), so the lifespan/watcher never start — the repo convention.

Auth: ``tests/conftest.py`` autouse-patches ``TestClient.__init__`` to inject the
local-session cookie, so any in-process TestClient is transparently
authenticated. (A subprocess would NOT inherit this — another reason to stay
in-process.)

Curl recipe (the HTTP sequence this test drives; ``$T`` is the local session
token, ``$D`` the data path under the project root):

    # 1. SAVE the multi-port apiInput -> OUTPUT graph
    curl -sX POST localhost:8000/api/pipeline/save -H "X-Haute-Session: $T" \
      -H 'content-type: application/json' \
      -d '{"name":"multi_frame_e2e","description":"","graph":<GRAPH>,
           "source_file":"multi_frame_e2e.py"}'

    # 2. BUILD the per-port JSON cache (volatile_schema is the apiInput config)
    curl -sX POST localhost:8000/api/json-cache/build -H "X-Haute-Session: $T" \
      -H 'content-type: application/json' \
      -d '{"path":"data/data_model_example.json","volatile_schema":<API_CONFIG>}'

    # 3. PREVIEW the OUTPUT node -> the assembled nested document
    curl -sX POST localhost:8000/api/pipeline/preview -H "X-Haute-Session: $T" \
      -H 'content-type: application/json' \
      -d '{"graph":<GRAPH>,"node_id":"out"}'
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import _json_cache_dir
from haute._sandbox import _get_project_root, set_project_root
from haute._types import NodeType
from haute.executor import _preview_cache

# Reuse the canonical multi-port helpers (same imports test_output_assemble_routes
# uses) — single source of truth for the apiInput shred config, the OUTPUT
# reassembly mapping, and the expected round-trip document.
from tests.test_output_nested_roundtrip import (
    _FIXTURE,
    _api_input_config,
    _expected_document,
    _output_mapping,
)

_PORTS = ["policies", "drivers", "licenses", "vehicles"]
_EXPECTED_FIXTURE = Path(__file__).parent / "fixtures" / "multi_frame_expected_output.json"


def _graph_json(api_config: dict[str, Any]) -> dict[str, Any]:
    """React-Flow ``Graph`` shape: a four-port apiInput → OUTPUT.

    Unlike the dry-run route (which supplies a *volatile* mapping), this graph
    carries the real ``outputMapping`` on the OUTPUT node so the SAME graph dict
    drives both ``/save`` and ``/preview`` — the end-to-end chain the plan wants.
    """
    return {
        "nodes": [
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": NodeType.API_INPUT.value,
                    "config": api_config,
                },
            },
            {
                "id": "out",
                "data": {
                    "label": "out",
                    "nodeType": NodeType.OUTPUT.value,
                    "config": {"outputMapping": _output_mapping(), "outputFormat": "json"},
                },
            },
        ],
        "edges": [
            {"id": f"e_{p}", "source": "api", "target": "out", "sourceHandle": p} for p in _PORTS
        ],
    }


def _canonical(node: Any) -> Any:
    """Recursive structural canonicaliser (plan §Commit 9, step 5d).

    - list-of-objects → sorted by a total order over the element's canonical JSON
      (``json.dumps(..., sort_keys=True)``), recursing into each element. This is
      order-insensitive without hard-coding a per-list sort column.
    - other lists → element-wise canonicalised, order preserved.
    - dict → keys sorted, values canonicalised (key-insensitive, value-exact).
    - primitives → returned as-is (value-exact).

    Forbids byte-level comparison: equality is on the canonical structure, not the
    serialized text.
    """
    if isinstance(node, list):
        items = [_canonical(x) for x in node]
        if node and all(isinstance(x, dict) for x in node):
            return sorted(items, key=lambda e: json.dumps(e, sort_keys=True, default=str))
        return items
    if isinstance(node, dict):
        return {k: _canonical(v) for k, v in sorted(node.items())}
    return node


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path]]:
    """In-process app rooted at an isolated tmp project (see module docstring,
    HARNESS CHOICE). TestClient is instantiated BARE so the lifespan/file-watcher
    never starts; chdir + set_project_root keep the cache dir under tmp_path."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.clear()

    from haute.server import app

    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    yield TestClient(app), data_path
    set_project_root(original)
    _preview_cache.clear()


def test_multi_frame_save_build_preview_round_trips(project) -> None:
    """The full multi-port HTTP chain: save → build → on-disk parquets → preview →
    structural equality. Sequential per-stage asserts (plan §4f) — each message
    names the upstream commit/seam it guards so a regression localises.
    """
    client, data_path = project
    api_config = _api_input_config(data_path)
    graph = _graph_json(api_config)

    # STAGE 1 — SAVE the multi-port graph (guards graph + sourceHandle
    # persistence / codegen, commit 6).
    save = client.post(
        "/api/pipeline/save",
        json={
            "name": "multi_frame_e2e",
            "description": "",
            "graph": graph,
            "source_file": "multi_frame_e2e.py",
        },
    )
    assert save.status_code == 200, f"STAGE 1 SAVE failed (commit 6 graph/codegen): {save.text}"

    # STAGE 2 — BUILD the per-port JSON cache (guards the shred/build route,
    # commit 3). NOTE: the build response ``row_count`` is the SUM across all
    # ports (haute.routes.json_cache sums per-table counts), NOT per-port — so we
    # assert the aggregate is > 0 here and verify each port's parquet on disk in
    # STAGE 3.
    build = client.post(
        "/api/json-cache/build",
        json={"path": "data/data_model_example.json", "volatile_schema": api_config},
    )
    assert build.status_code == 200, f"STAGE 2 BUILD failed (commit 3 shred/build): {build.text}"
    body = build.json()
    assert body["row_count"] > 0, (
        f"STAGE 2: aggregate per-port row_count was {body['row_count']} (commit 3 shred)"
    )
    assert body["skipped_records"] == 0, (
        f"STAGE 2: clean fixture dropped records (commit 3 shred): {body['skipped_records']}"
    )

    # STAGE 3 — ON-DISK PER-PORT PARQUETS (guards the per-port writer; the plan's
    # "build returned 200 but parquets are missing" diagnostic). Filenames are the
    # table LABEL + ".parquet" — confirmed in test_json_cache_integrity.py
    # (root.parquet / drivers.parquet asserted by label).
    cache_dir = _json_cache_dir(data_path, "working")
    assert cache_dir.exists(), f"STAGE 3: cache dir missing after 200 build (commit 3): {cache_dir}"
    for port in _PORTS:
        parquet = cache_dir / f"{port}.parquet"
        assert parquet.exists(), (
            f"STAGE 3: missing {port}.parquet — per-port shred writer regressed (commit 3)"
        )

    # STAGE 4 — PREVIEW the OUTPUT node (guards the OUTPUT assembler wiring,
    # commit 7). The OUTPUT node's ``preview`` field IS the rendered nested
    # document (proven by test_output_nested_roundtrip.py).
    preview = client.post(
        "/api/pipeline/preview",
        json={"graph": graph, "node_id": "out"},
    )
    assert preview.status_code == 200, (
        f"STAGE 4 PREVIEW failed (commit 7 OUTPUT wiring): {preview.text}"
    )
    payload = preview.json()
    assert payload["status"] == "ok", (
        f"STAGE 4: OUTPUT node errored (commit 7 assembler): {payload.get('error')}"
    )
    assert payload["row_count"] == 2, (
        f"STAGE 4: expected 2 root policies, got {payload['row_count']} (commit 7 assembler)"
    )

    # STAGE 5 — STRUCTURAL EQUALITY vs the checked-in fixture (guards the
    # assembler/render round-trip, commit 7/4b). Order-insensitive for
    # list-of-objects, key-insensitive for dicts, value-exact for primitives;
    # no byte comparison (plan §Commit 9 step 5d).
    expected = json.loads(_EXPECTED_FIXTURE.read_text())
    # Sanity: the checked-in fixture matches the helper's expected document, so a
    # drift in either is caught.
    assert _canonical(expected) == _canonical(_expected_document()), (
        "STAGE 5: multi_frame_expected_output.json drifted from data_model_example.json"
    )
    actual = payload["preview"]
    assert _canonical(actual) == _canonical(expected), (
        "STAGE 5: assembled document differs from expected (assembler/render regressed, commit 7)"
    )
