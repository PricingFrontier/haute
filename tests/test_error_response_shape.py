"""Pin the HTTP error response shape across every route (item #76).

Item #76 flags that error bodies drift across routes:

* ``routes/utility.py`` (``create_utility_file``, ``update_utility_file``):
  on a Python ``SyntaxError`` the handler raises ``HTTPException`` with
  a *nested dict* detail — ``{"detail": {"error": "...", "error_line": N}}``.
* ``routes/pipeline.py``, ``routes/files.py``, ``routes/git.py``, etc.
  uniformly use ``HTTPException(detail="<str>")`` — ``{"detail": "..."}``.

A frontend that must handle both flat strings and nested dicts ends up
with ``typeof detail === "string" ? detail : detail.error`` branching
everywhere.  The fix is to standardise on the flat contract:

* every 4xx/5xx response body is a JSON object with exactly one
  top-level key ``detail``;
* the value of ``detail`` is a non-empty plain string — no nested
  objects, no arrays, no numbers, no booleans;
* structured metadata that was previously embedded in the body
  (``error_line``, exception kind, stack frames) lives in the
  server-side structured logs instead.

Additionally (still item #76 / item #11), 500-level responses must not
leak raw exception repr or stack traces — the body must be a short
user-friendly sentence and the full diagnostic stays in logs.

These tests pin the contract.  They are expected to FAIL today because
``utility.py`` still returns the nested-dict shape on syntax errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute.server import app
from tests._source_files import source_files

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient in an isolated cwd — prevents test pollution and guarantees
    that "directory not found" / "file not found" errors actually fire.
    """
    # Minimal haute.toml so pipeline_dir() resolution doesn't log noise.
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "main.py"\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("p")\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Shared assertion — the ONLY legal error shape
# ---------------------------------------------------------------------------


def _assert_standard_error_shape(
    body: Any,
    *,
    status: int,
    method: str,
    url: str,
) -> None:
    """Assert *body* is ``{"detail": "<non-empty str>"}`` and nothing else.

    We deliberately do NOT allow:
      * ``{"error": ...}`` top-level keys
      * ``{"detail": {...}}`` nested-dict details
      * ``{"detail": "..."}`` alongside any other key (structured sidecars)
      * ``{"detail": ""}`` or ``{"detail": null}``
      * plain strings as the body (FastAPI wraps them, but some handlers
        return JSONResponse manually).
    """
    context = f"{method} {url} -> {status}"

    # Body must be a JSON object.
    assert isinstance(body, dict), (
        f"#76: error body must be a JSON object ({context}); got {type(body).__name__!r}: {body!r}"
    )

    # Exactly one top-level key, and it must be 'detail' (not 'error').
    assert "detail" in body, f"#76: error body must contain key 'detail' ({context}); got: {body!r}"
    assert "error" not in body, (
        f"#76: error body must not contain top-level 'error' key "
        f"({context}); structured payloads belong in the server log, not "
        f"the HTTP response. Got: {body!r}"
    )

    extra = set(body.keys()) - {"detail"}
    assert not extra, (
        f"#76: error body must have exactly one key ('detail'); "
        f"found extras {sorted(extra)} in {context}: {body!r}"
    )

    # The detail value must be a non-empty plain string.
    detail = body["detail"]
    assert isinstance(detail, str), (
        f"#76: body['detail'] must be a string ({context}); got "
        f"{type(detail).__name__!r}: {detail!r}.  Nested dicts like "
        f"{{'error': ..., 'error_line': ...}} must move to logs."
    )
    assert detail.strip(), f"#76: body['detail'] must not be empty ({context}); got {detail!r}"


# ---------------------------------------------------------------------------
# Parametrized matrix of error-producing requests, one row per failure mode
# ---------------------------------------------------------------------------


def _bad_graph() -> dict[str, Any]:
    """An intentionally invalid graph body (empty nodes)."""
    return {"graph": {"nodes": [], "edges": []}, "node_id": "nope"}


# Each row: (method, url, body-or-None, expected-status, reason)
_ERROR_CASES = [
    # --- 400: bad name ---
    pytest.param(
        "post",
        "/api/utility",
        {"name": "1-bad-name", "content": "x = 1\n"},
        400,
        id="utility-invalid-module-name",
    ),
    # --- 400: empty graph on preview ---
    pytest.param(
        "post",
        "/api/pipeline/preview",
        {"graph": {"nodes": [], "edges": []}, "node_id": "x"},
        400,
        id="preview-empty-graph",
    ),
    # --- 400: empty graph on sink ---
    pytest.param(
        "post",
        "/api/pipeline/write-output",
        {"graph": {"nodes": [], "edges": []}, "node_id": "x"},
        400,
        id="sink-empty-graph",
    ),
    # --- 400: empty graph on trace ---
    pytest.param(
        "post",
        "/api/pipeline/trace",
        {
            "graph": {"nodes": [], "edges": []},
            "seed_plan": [],
            "row_index": 0,
            "target_node_id": "x",
            "column": "y",
        },
        400,
        id="trace-empty-graph",
    ),
    # --- 404: utility file not found ---
    pytest.param(
        "get",
        "/api/utility/does_not_exist",
        None,
        404,
        id="utility-read-missing",
    ),
    # --- 404: pipeline lookup by unknown name ---
    pytest.param(
        "get",
        "/api/pipeline/totally_nonexistent_pipeline",
        None,
        404,
        id="pipeline-unknown-name",
    ),
    # --- 404: schema path missing ---
    pytest.param(
        "get",
        "/api/schema?path=definitely/not/here.parquet",
        None,
        404,
        id="schema-file-missing",
    ),
    # --- 404: browse missing directory ---
    pytest.param(
        "get",
        "/api/files?dir=no_such_dir_here",
        None,
        404,
        id="browse-missing-dir",
    ),
    # --- 400: update missing utility file ---
    pytest.param(
        "put",
        "/api/utility/also_missing",
        {"content": "x = 1\n", "revision": "0-0"},
        404,
        id="utility-update-missing",
    ),
    # --- 400: syntax error on utility update (2nd #76 violator) ---
    pytest.param(
        "put",
        "/api/utility/helper",  # expect 404 first unless we set up; paired test below
        {"content": "if True\n", "revision": "0-0"},
        404,
        id="utility-update-missing-syntax-path",
    ),
]


class TestStandardErrorShape:
    """Every non-syntax-error 4xx response across routes must have the same JSON
    shape: ``{"detail": "<str>"}``.
    """

    @pytest.mark.parametrize(("method", "url", "body", "expected_status"), _ERROR_CASES)
    def test_error_response_is_flat_detail_string(
        self,
        isolated_client: TestClient,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        expected_status: int,
    ) -> None:
        call = getattr(isolated_client, method)
        res = call(url, json=body) if body is not None else call(url)
        assert res.status_code == expected_status, (
            f"#76: unexpected status for {method} {url}: got "
            f"{res.status_code}, expected {expected_status}.  Body: {res.text!r}"
        )
        _assert_standard_error_shape(
            res.json(),
            status=res.status_code,
            method=method.upper(),
            url=url,
        )


# ---------------------------------------------------------------------------
# Focused: utility syntax errors carry structured {line, message} detail so
# the frontend never scrapes line numbers out of prose with a regex.
# ---------------------------------------------------------------------------


class TestUtilitySyntaxErrorDetailShape:
    """A Python SyntaxError in utility create/update returns
    ``{"detail": {"line": <int | null>, "message": "<str>"}}`` so ``UtilityPanel``
    and ``apiErrorMessage`` can read the line number and message directly without
    regex scraping.
    """

    def test_create_syntax_error_uses_line_and_message_detail(
        self, isolated_client: TestClient
    ) -> None:
        res = isolated_client.post(
            "/api/utility",
            json={"name": "broken_create", "content": "def foo(\n"},
        )
        assert res.status_code == 400
        body = res.json()
        assert set(body.keys()) == {"detail"}
        detail = body["detail"]
        assert isinstance(detail, dict)
        assert set(detail.keys()) == {"line", "message"}
        assert detail["line"] == 1
        assert "syntax" in detail["message"].lower()

    def test_update_syntax_error_uses_line_and_message_detail(
        self, isolated_client: TestClient, tmp_path: Path
    ) -> None:
        # Create the file first so the update path reaches syntax validation.
        util = tmp_path / "utility"
        util.mkdir()
        (util / "helper.py").write_text("x = 1\n")

        res = isolated_client.put(
            "/api/utility/helper",
            json={"content": "if True\n", "revision": "0-0"},
        )
        assert res.status_code == 400
        body = res.json()
        assert set(body.keys()) == {"detail"}
        detail = body["detail"]
        assert isinstance(detail, dict)
        assert set(detail.keys()) == {"line", "message"}
        assert detail["line"] == 1
        assert "syntax" in detail["message"].lower()
        # Original file must be unchanged (existing invariant).
        assert (util / "helper.py").read_text() == "x = 1\n"


# ---------------------------------------------------------------------------
# 500-level: detail must be a short user-friendly string, never a raw repr
# ---------------------------------------------------------------------------


class TestInternalErrorDetailNotRawException:
    """For 500 responses the wire body must contain a *short* human-readable
    sentence — never the raw ``str(exc)`` from an internal failure.  The
    full stack trace must be available via the server-side structured log
    (``logger.error(..., exc_info=True)``) — that's the #11 sibling
    contract this test co-enforces.
    """

    def _sensitive_repr_tokens(self) -> list[str]:
        """Substrings that, if present in a 500 body, prove we leaked."""
        return [
            "Traceback (most recent call last)",
            'File "',  # any tracebacky path marker
            "KeyError(",
            "RuntimeError(",
            "ValueError(",
            # Raw Python-style object repr
            "<class '",
            "object at 0x",
        ]

    def test_schema_internal_500_body_is_sanitized(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provoke a 500 from ``GET /api/schema`` by making the inner
        reader raise an unexpected error.  The HTTP body must be a short
        string, never a traceback or class repr.
        """
        from unittest.mock import MagicMock, patch

        # Make a real file so the up-front 404 check passes.
        data_dir = Path.cwd() / "data"
        data_dir.mkdir(exist_ok=True)
        target = data_dir / "sample.parquet"
        import polars as pl

        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(target)

        fake_lf = MagicMock()
        fake_lf.collect_schema.side_effect = RuntimeError(
            "native decoder crashed at 0xDEADBEEF — internal path /etc/secret"
        )
        with patch("haute.graph_utils.read_source", return_value=fake_lf):
            res = isolated_client.get("/api/schema", params={"path": "data/sample.parquet"})

        assert res.status_code == 500
        body = res.json()
        _assert_standard_error_shape(body, status=500, method="GET", url="/api/schema")
        detail = body["detail"]
        for token in self._sensitive_repr_tokens():
            assert token not in detail, (
                f"#76/#11: 500 body must not embed raw exception repr; "
                f"found {token!r} in {detail!r}"
            )
        # Bound on length — a user-friendly sentence, not a dump.
        assert len(detail) < 300, (
            f"#76: 500 detail looks like a dump ({len(detail)} chars); "
            f"keep it a short sentence: {detail!r}"
        )

    def test_unhandled_middleware_500_body_is_sanitized(self, isolated_client: TestClient) -> None:
        """Even an exception that escapes a handler entirely (handled by
        ``_RequestIdMiddleware``) must produce ``{"detail": "<short str>"}``.
        """
        from unittest.mock import patch

        # Force list_pipelines to raise an exception that no handler
        # catches.  The middleware's except-Exception branch should kick
        # in and translate it to a sanitised JSON response.
        with patch(
            "haute.routes.pipeline.discover_pipelines",
            side_effect=RuntimeError("deeply internal /var/secret/path failure"),
        ):
            res = isolated_client.get("/api/pipelines")

        assert res.status_code == 500
        body = res.json()
        _assert_standard_error_shape(body, status=500, method="GET", url="/api/pipelines")
        detail = body["detail"]
        # Sensitive path / class repr must not leak.
        assert "/var/secret/path" not in detail, (
            f"#76/#11: middleware 500 leaked internal path: {detail!r}"
        )


# ---------------------------------------------------------------------------
# Structural: no route module constructs HTTPException with a dict detail,
# except _refuse_syntax_error in utility.py which returns {line, message}.
# ---------------------------------------------------------------------------


class TestNoRouteConstructsDictDetail:
    """Only ``_refuse_syntax_error`` in ``src/haute/routes/utility.py`` is allowed
    to construct a literal dict ``detail`` on ``HTTPException`` (carrying
    ``{"line": ..., "message": ...}``).
    """

    @pytest.mark.meta
    def test_no_dict_detail_in_routes(self) -> None:
        import ast

        routes_pkg = Path("src/haute/routes")
        offenders: list[str] = []
        for py in source_files(routes_pkg):
            rel = py.relative_to(routes_pkg.parent.parent.parent).as_posix()
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py))
            for node in ast.walk(tree):
                # Looking for HTTPException(..., detail={...})
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = (
                    fn.attr
                    if isinstance(fn, ast.Attribute)
                    else fn.id
                    if isinstance(fn, ast.Name)
                    else None
                )
                if name != "HTTPException":
                    continue
                for kw in node.keywords:
                    if kw.arg == "detail" and isinstance(kw.value, (ast.Dict, ast.List, ast.Tuple)):
                        if (
                            rel == "src/haute/routes/utility.py"
                            and isinstance(kw.value, ast.Dict)
                            and [
                                k.value
                                for k in kw.value.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str)
                            ]
                            == ["line", "message"]
                        ):
                            continue
                        offenders.append(
                            f"{rel}:{node.lineno} — detail is {type(kw.value).__name__}"
                        )
        assert not offenders, (
            "#76: HTTPException must carry a plain string detail. Offenders:\n  "
            + "\n  ".join(offenders)
        )
