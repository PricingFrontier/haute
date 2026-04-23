"""Route-level error handling tests (Phase 1 Package 1C).

Covers:
  - #11 Error details leaked to HTTP clients — ``files.py:111``,
        ``submodel.py:45``, ``git.py:67`` raise ``HTTPException`` with
        raw ``str(exc)`` that surfaces internal paths, git output, and
        stack traces to the browser.  After the fix every internal
        failure must produce a sanitized, generic detail message and
        log the real error server-side.
  - #24 ``/schema`` endpoint swallows broad Exception — the catch-all
        ``except Exception`` at ``files.py:114-119`` silently turns every
        internal error into a 500 with a templated message.  Unexpected
        exceptions (KeyError, RuntimeError from polars) must propagate
        to FastAPI's default 500 handler with structured logging, not
        be hidden behind a misleading generic message.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

_SAFE_DETAIL = "Operation failed. Check the server logs for details."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient rooted at a clean temp project directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text('import haute\npipeline = haute.Pipeline("p")\n')
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def parquet_file(tmp_path: Path) -> Path:
    """A parquet file inside the project root for /api/schema tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    target = data_dir / "sample.parquet"
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(target)
    return target


# ---------------------------------------------------------------------------
# #11 — /api/schema (files.py line 111): ValueError detail leakage
# ---------------------------------------------------------------------------


class TestSchemaValueErrorSanitisation:
    """``GET /api/schema`` re-raises the raw ValueError text via
    ``raise HTTPException(status_code=400, detail=str(e))``.

    The fix must either:
      - hide the raw text behind a sanitized message (``_INTERNAL_ERROR_DETAIL``
        or similar), while logging the full error server-side, OR
      - restrict the raw message to a small, allow-listed domain (e.g.
        ``Unsupported file type: .xyz``) that is guaranteed not to embed
        absolute paths, tracebacks, or git output.

    Either way, internal details — absolute paths, stack frames, git
    stderr — must not reach the client.
    """

    @pytest.mark.parametrize(
        ("error_msg", "forbidden"),
        [
            pytest.param(
                "parse error at /home/secretuser/pipelines/main.py:42",
                ["/home/secretuser"],
                id="absolute-path-leak",
            ),
            pytest.param(
                'Traceback (most recent call last):\n  File "/usr/lib/python3.11/',
                ["Traceback", "/usr/lib/python3.11"],
                id="traceback-leak",
            ),
            pytest.param(
                "fatal: unable to access git repo at /home/admin/.ssh/id_rsa",
                ["/home/admin/.ssh", "fatal:"],
                id="git-output-leak",
            ),
        ],
    )
    def test_schema_value_error_does_not_leak_internal_detail(
        self,
        project_client: TestClient,
        parquet_file: Path,
        error_msg: str,
        forbidden: list[str],
    ) -> None:
        """A ValueError bubbling up from read_source must NOT expose
        internal paths, tracebacks, or git output to the HTTP response.
        """
        with patch(
            "haute.graph_utils.read_source",
            side_effect=ValueError(error_msg),
        ):
            resp = project_client.get(
                "/api/schema",
                params={"path": "data/sample.parquet"},
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        for forbidden_token in forbidden:
            assert forbidden_token not in detail, (
                f"#11: /api/schema leaked internal detail: {forbidden_token!r} "
                f"appeared in response body: {detail!r}"
            )


# ---------------------------------------------------------------------------
# #11 — /api/submodel/create (submodel.py line 45): ValueError leakage
# ---------------------------------------------------------------------------


class TestSubmodelCreateValueErrorSanitisation:
    """``POST /api/submodel/create`` catches ``ValueError`` from
    ``create_submodel_graph`` and raises HTTPException with ``str(exc)``.

    The underlying function can include arbitrary graph/node details in
    its ValueError message.  Before the fix, those details leak to the
    client.  After the fix, internal exception text must be hidden.
    """

    @pytest.fixture()
    def _isolated_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def _two_node_graph(self) -> dict:
        return {
            "nodes": [
                {
                    "id": "a",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "a",
                        "nodeType": "dataSource",
                        "config": {"path": "x.parquet"},
                    },
                },
                {
                    "id": "b",
                    "type": "pipelineNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "b",
                        "nodeType": "polars",
                        "config": {"code": "df"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "a", "target": "b"}],
        }

    def test_submodel_create_value_error_does_not_leak_internal_text(
        self,
        _isolated_cwd: Path,
    ) -> None:
        from haute.server import app

        client = TestClient(app, raise_server_exceptions=False)

        sensitive = (
            "internal graph walk failure at node '/root/secret/path.py' "
            "due to KeyError('__internal_field__')"
        )

        with patch(
            "haute.routes._submodel_ops.create_submodel_graph",
            side_effect=ValueError(sensitive),
        ):
            resp = client.post(
                "/api/submodel/create",
                json={
                    "name": "sm",
                    "node_ids": ["a", "b"],
                    "graph": self._two_node_graph(),
                    "source_file": "main.py",
                    "pipeline_name": "main",
                },
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "/root/secret/path.py" not in detail, (
            f"#11: /api/submodel/create leaked absolute path: {detail!r}"
        )
        assert "__internal_field__" not in detail, (
            f"#11: /api/submodel/create leaked internal field name: {detail!r}"
        )


# ---------------------------------------------------------------------------
# #11 — /api/git/* (git.py line 67): GitError raw message leakage
# ---------------------------------------------------------------------------


class TestGitErrorSanitisation:
    """``_handle_git_error`` raises ``HTTPException(detail=str(e))`` for
    every non-guardrail GitError.  ``_run_git`` constructs GitError
    messages from ``result.stderr.strip()`` — raw git subprocess stderr,
    which commonly contains absolute paths, hostnames, SSL errors, and
    credential fragments.

    After the fix, git stderr must never reach the HTTP body.  Domain-
    level guardrail errors (``GitGuardrailError``) remain user-facing so
    genuine blockers like "protected branch" still surface correctly.
    """

    @pytest.mark.parametrize(
        ("method", "url", "body", "patch_target", "raw_stderr", "forbidden"),
        [
            pytest.param(
                "get",
                "/api/git/status",
                None,
                "haute.routes.git.get_status",
                "fatal: unable to access 'https://github.com/org/private-repo.git/': "
                "SSL certificate problem: self signed certificate at "
                "C:/Users/secretuser/.ssh/ca.pem",
                [
                    "C:/Users/secretuser",
                    "private-repo",
                    "SSL certificate problem",
                ],
                id="status-ssl-leak",
            ),
            pytest.param(
                "post",
                "/api/git/save",
                {},
                "haute.routes.git.save_progress",
                "error: pathspec '/etc/shadow' did not match any file(s) known to git",
                ["/etc/shadow", "pathspec"],
                id="save-stderr-path-leak",
            ),
            pytest.param(
                "post",
                "/api/git/pull",
                {},
                "haute.routes.git.pull_latest",
                "fatal: Authentication failed for "
                "https://user:dapi_secret_token@corp.internal.git/repo",
                ["dapi_secret_token", "corp.internal"],
                id="pull-credential-leak",
            ),
            pytest.param(
                "get",
                "/api/git/branches",
                None,
                "haute.routes.git.list_branches",
                "fatal: index file corrupt at /home/admin/.git/index",
                ["/home/admin/.git", "index file corrupt"],
                id="branches-fs-leak",
            ),
        ],
    )
    def test_git_error_stderr_not_leaked(
        self,
        project_client: TestClient,
        method: str,
        url: str,
        body: dict | None,
        patch_target: str,
        raw_stderr: str,
        forbidden: list[str],
    ) -> None:
        from haute._git import GitError

        with patch(patch_target, side_effect=GitError(raw_stderr)):
            if method == "get":
                resp = project_client.get(url)
            else:
                resp = project_client.post(url, json=body or {})

        assert resp.status_code == 400, f"unexpected status: {resp.status_code}"
        detail = resp.json()["detail"]
        for token in forbidden:
            assert token not in detail, (
                f"#11: {url} leaked git stderr token {token!r} in {detail!r}"
            )


# ---------------------------------------------------------------------------
# #24 — /schema endpoint swallows broad Exception
# ---------------------------------------------------------------------------


class TestSchemaBroadExceptionHandling:
    """``GET /api/schema`` has a catch-all ``except Exception`` that rewrites
    every internal failure to ``"Failed to read schema for ...".``

    This masks bugs: a KeyError from a corrupted polars schema, a
    RuntimeError from a native parser crash, or an OSError from file
    permissions all look identical to the client.  The fix requires
    that non-HTTPException failures are either:
      1. re-classified into a meaningful HTTPException with structured
         logging (distinct event name per error kind), OR
      2. allowed to propagate to FastAPI's default 500 handler, where
         middleware formats a generic response AND structured logging
         records the real error.

    The assertions below verify (a) the failure is still a 500, (b) the
    real error is recorded via structured logging with an event name
    that conveys *what* went wrong (not a flat "schema_read_failed"),
    and (c) the raw exception text does not leak to the client.
    """

    def test_keyerror_during_collect_schema_propagates_structured_log(
        self,
        project_client: TestClient,
        parquet_file: Path,
    ) -> None:
        """A KeyError thrown by polars schema inference must surface as
        a 500 with a structured log event whose payload names the error
        class — not a silent generic 500.
        """
        import structlog.testing

        # Make read_source succeed but have collect_schema() blow up
        fake_lf = MagicMock()
        fake_lf.collect_schema.side_effect = KeyError("missing_partition_key")
        fake_lf.head.return_value = MagicMock(collect=MagicMock(return_value=MagicMock()))

        with (
            patch("haute.graph_utils.read_source", return_value=fake_lf),
            structlog.testing.capture_logs() as captured,
        ):
            resp = project_client.get(
                "/api/schema",
                params={"path": "data/sample.parquet"},
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        # Client must not see the raw KeyError text
        assert "missing_partition_key" not in detail, (
            "#24: KeyError message leaked to client despite broad except"
        )

        # A structured log event must record the true error — either the
        # generic "schema_read_failed" event MUST include the exception
        # class name/message in its payload, or a dedicated event must
        # fire for unexpected errors.
        error_events = [e for e in captured if e.get("log_level") == "error"]
        assert error_events, "#24: no error-level log emitted for unexpected KeyError in /schema"
        event_text = " ".join(f"{k}={v}" for e in error_events for k, v in e.items())
        assert "KeyError" in event_text or "missing_partition_key" in event_text, (
            "#24: structured log must record the exception class or message "
            f"(got events: {error_events!r})"
        )

    def test_runtime_error_during_head_collect_is_not_silently_swallowed(
        self,
        project_client: TestClient,
        parquet_file: Path,
    ) -> None:
        """A RuntimeError thrown by ``preview_df.head(5).collect()`` must
        not produce a generic "Failed to read schema" with no further
        diagnostic.  The fix logs the real error at ERROR level and
        either re-raises or returns a sanitized 500.
        """
        import structlog.testing

        fake_lf = MagicMock()
        fake_schema = MagicMock()
        fake_schema.items.return_value = [("x", pl.Int64)]
        fake_lf.collect_schema.return_value = fake_schema
        # head(...).collect() raises
        fake_head = MagicMock()
        fake_head.collect.side_effect = RuntimeError("native polars decoder crash")
        fake_lf.head.return_value = fake_head

        with (
            patch("haute.graph_utils.read_source", return_value=fake_lf),
            structlog.testing.capture_logs() as captured,
        ):
            resp = project_client.get(
                "/api/schema",
                params={"path": "data/sample.parquet"},
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "native polars decoder" not in detail
        error_events = [e for e in captured if e.get("log_level") == "error"]
        assert error_events, "#24: unexpected RuntimeError produced no error-level log"
        joined = " ".join(f"{k}={v}" for e in error_events for k, v in e.items())
        assert "RuntimeError" in joined or "native polars decoder crash" in joined, (
            f"#24: structured log must include the real exception (got: {error_events!r})"
        )

    def test_http_exception_still_passes_through_unmodified(
        self,
        project_client: TestClient,
        parquet_file: Path,
    ) -> None:
        """HTTPException raised by inner helpers (e.g. 404 from a missing
        nested file) must not be caught by the broad ``except Exception``.

        This is the only path that the current broad catch gets right;
        the fix must preserve that behaviour.
        """
        from fastapi import HTTPException

        def raise_http(*a, **kw):
            raise HTTPException(status_code=404, detail="inner-404")

        with patch("haute.graph_utils.read_source", side_effect=raise_http):
            resp = project_client.get(
                "/api/schema",
                params={"path": "data/sample.parquet"},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "inner-404"


class TestSchemaBroadExceptionStructuralFix:
    """Structural test: either ``get_schema`` no longer has a blanket
    ``except Exception`` AND ``except Exception as e`` tied to a generic
    rewrite, or the blanket catch distinguishes *expected* user errors
    (ValueError for unsupported types) from *unexpected* failures so the
    latter are explicitly classified.

    After the fix we expect at least one of:
      - No bare ``except Exception`` in ``get_schema`` (exception is
        reclassified or allowed to bubble).
      - A distinct ``logger.exception`` or ``logger.error`` with
        ``exc_info=True`` so the full stack trace is captured.
    """

    def test_schema_handler_logs_exc_info_on_unexpected_failure(
        self,
        project_client: TestClient,
        parquet_file: Path,
    ) -> None:
        """The error log must carry ``exc_info`` for server-side diagnosis."""
        import structlog.testing

        def boom(*a, **kw):
            raise RuntimeError("native parquet decoder exploded")

        with (
            patch("haute.graph_utils.read_source", side_effect=boom),
            structlog.testing.capture_logs() as captured,
        ):
            resp = project_client.get(
                "/api/schema",
                params={"path": "data/sample.parquet"},
            )

        assert resp.status_code == 500
        assert resp.json()["detail"] == _SAFE_DETAIL
        error_events = [e for e in captured if e.get("event") == "schema_read_failed"]
        assert error_events, "#24: unexpected schema failure produced no structured error log"
        assert error_events[-1].get("exc_info") is True
        assert error_events[-1].get("error_class") == "RuntimeError"

    def _deprecated_schema_handler_logs_full_stack_trace_source_check(self) -> None:
        import inspect

        from haute.routes import files

        src = inspect.getsource(files.get_schema)
        # After the fix: the broad catch must hand the exception to
        # structured logging with exc_info or use logger.exception so the
        # stack trace is preserved server-side.
        has_exc_info = "exc_info=True" in src or "logger.exception" in src
        assert has_exc_info, (
            "#24: get_schema's broad except must capture the full stack "
            "trace via exc_info=True or logger.exception — currently the "
            "real error is silenced."
        )
