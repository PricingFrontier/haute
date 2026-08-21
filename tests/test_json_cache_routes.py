"""API integration tests for per-port JSON cache endpoints.

Covers:
  - POST /api/json-cache/build: 422 without schema source, 404 missing file,
    missing-path 422, timeout 504
  - GET /api/json-cache/progress: inactive with no build, active while
    worker builds, missing-path 422
  - GET /api/json-cache/status: missing-path 422
  - POST /api/json-cache/status: 422 without schema source
  - DELETE /api/json-cache: success (clear_json_cache called), missing-path 422
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from haute.routes._isolated_worker_async import WorkerCancellationGate


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd set to a temp directory."""
    monkeypatch.chdir(tmp_path)
    from haute.server import app

    return TestClient(app)


def _minimal_root_schema() -> dict[str, Any]:
    return {
        "tables": [
            {
                "label": "root",
                "path": "$[:]",
                "emit": True,
                "columns": [
                    {
                        "name": "a",
                        "path": "$[:].a",
                        "type": "int",
                        "status": "Inferred",
                        "selected": True,
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/json-cache/build
# ---------------------------------------------------------------------------


class TestBuildJsonCache:
    @pytest.mark.parametrize(
        ("raised", "kind"),
        [
            (FileNotFoundError("gone"), "file_not_found"),
            (__import__("orjson").JSONDecodeError("bad", "x", 0), "invalid_json"),
        ],
    )
    def test_prepare_worker_classifies_expected_source_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raised: BaseException,
        kind: str,
    ) -> None:
        from haute.routes import json_cache

        context = Mock()
        context.stage.return_value.__enter__ = Mock()
        context.stage.return_value.__exit__ = Mock(return_value=False)
        monkeypatch.setattr(
            json_cache, "create_isolated_execution_context", lambda _budget: context
        )
        monkeypatch.setattr(
            "haute._json_shred.prepare_per_port_cache",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(raised),
        )

        outcome = json_cache._prepare_json_cache_worker("data.json", {}, "cache", "staging", Mock())

        assert outcome.failure_kind == kind
        context.release_admission.assert_called_once_with(preserve_primary_error=True)

    @pytest.mark.parametrize("kind", ["schema", "source_changed", "memory"])
    def test_prepare_worker_classifies_contract_and_memory_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
    ) -> None:
        from haute._api_input_schema import ApiInputSchemaError
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute._json_shred import SourceChangedDuringCacheBuildError
        from haute.routes import json_cache

        failures: dict[str, BaseException] = {
            "schema": ApiInputSchemaError("schema mismatch"),
            "source_changed": SourceChangedDuringCacheBuildError("source changed"),
            "memory": ExecutionMemoryLimitExceededError(
                "json_cache_build_v2",
                rss_bytes=2,
                limit_bytes=1,
            ),
        }
        context = Mock()
        context.stage.return_value.__enter__ = Mock()
        context.stage.return_value.__exit__ = Mock(return_value=False)
        monkeypatch.setattr(
            json_cache, "create_isolated_execution_context", lambda _budget: context
        )
        monkeypatch.setattr(
            "haute._json_shred.prepare_per_port_cache",
            Mock(side_effect=failures[kind]),
        )

        outcome = json_cache._prepare_json_cache_worker("data.json", {}, "cache", "staging", Mock())

        assert outcome.failure_kind == kind
        assert outcome.detail == str(failures[kind])
        assert (outcome.payload is not None) is (kind == "memory")
        context.release_admission.assert_called_once_with(preserve_primary_error=True)

    def test_prepare_worker_returns_success_and_releases_admission(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.routes import json_cache

        context = Mock()
        context.stage.return_value.__enter__ = Mock()
        context.stage.return_value.__exit__ = Mock(return_value=False)
        prepared = object()
        monkeypatch.setattr(
            json_cache, "create_isolated_execution_context", lambda _budget: context
        )
        monkeypatch.setattr(
            "haute._json_shred.prepare_per_port_cache",
            Mock(return_value=prepared),
        )

        outcome = json_cache._prepare_json_cache_worker("data.json", {}, "cache", "staging", Mock())

        assert outcome.prepared is prepared
        assert outcome.failure_kind is None
        context.release_admission.assert_called_once_with(preserve_primary_error=True)

    def test_transaction_preserves_primary_error_when_staging_cleanup_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute.routes import json_cache

        cache_dir = tmp_path / "cache"
        staging = tmp_path / "staging"
        budget = IsolatedExecutionBudget("x", ExecutionProfile.LAZY_SINK, 1, "x", "x")
        monkeypatch.setattr(
            "haute._json_shred.new_per_port_cache_staging_dir", lambda _path: staging
        )
        monkeypatch.setattr(
            json_cache,
            "run_isolated_worker",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("primary")),
        )
        monkeypatch.setattr(
            "haute._json_shred.discard_per_port_cache_staging",
            lambda *_args: (_ for _ in ()).throw(OSError("cleanup")),
        )

        with pytest.raises(ValueError, match="primary") as exc_info:
            json_cache._json_cache_build_transaction(
                "data.json", {}, cache_dir, budget, WorkerCancellationGate()
            )
        assert "staging cleanup failed" in " ".join(exc_info.value.__notes__)

    def test_isolated_memory_detail_covers_memory_failure_classes(self) -> None:
        from haute._worker_isolation import (
            IsolatedWorkerCrashedError,
            IsolatedWorkerMemoryLimitExceededError,
            IsolatedWorkerMemoryLimitUnsupportedError,
        )
        from haute.routes.json_cache import _isolated_memory_detail

        exceeded = IsolatedWorkerMemoryLimitExceededError(rss_bytes=8, rss_limit_bytes=7)
        assert (
            _isolated_memory_detail(exceeded, memory_limit_bytes=5)["reason"]
            == "worker_rss_limit_exceeded"
        )
        unsupported = IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=5)
        assert (
            _isolated_memory_detail(unsupported, memory_limit_bytes=5)["reason"]
            == "native_memory_cap_unavailable"
        )
        crashed = IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=5)
        assert (
            _isolated_memory_detail(crashed, memory_limit_bytes=5)["reason"]
            == "worker_may_have_exceeded_memory_limit"
        )
        generic = _isolated_memory_detail(RuntimeError("failure"), memory_limit_bytes=None)
        assert generic == {
            "error_code": "memory_limit",
            "operation": "json_cache_build_v2",
            "reason": "worker_memory_limit",
        }

    @pytest.mark.parametrize(
        ("mutate", "error"),
        [
            (lambda prepared, _staging: object(), TypeError),
            (
                lambda prepared, _staging: replace(prepared, data_path="C:/attacker/data.json"),
                ValueError,
            ),
            (
                lambda prepared, _staging: replace(prepared, cache_dir="C:/attacker/cache"),
                ValueError,
            ),
            (lambda prepared, _staging: replace(prepared, no_op=1), TypeError),
            (
                lambda prepared, _staging: replace(prepared, staging_dir="C:/attacker/staging"),
                ValueError,
            ),
            (
                lambda prepared, _staging: replace(prepared, no_op=True, staging_dir="C:/attacker"),
                ValueError,
            ),
        ],
    )
    def test_worker_manifest_must_match_parent_owned_paths(
        self,
        tmp_path: Path,
        mutate: Any,
        error: type[Exception],
    ) -> None:
        from haute._json_shred import PreparedPerPortCacheBuild
        from haute.routes.json_cache import _validate_worker_prepared_manifest

        data_path = str((tmp_path / "data.json").resolve())
        cache_dir = (tmp_path / "cache").resolve()
        staging = cache_dir.with_name(f"{cache_dir.name}.build-tmp-{'a' * 32}")
        prepared = PreparedPerPortCacheBuild(
            data_path=data_path,
            cache_dir=str(cache_dir),
            staging_dir=str(staging),
            schema_fingerprint="fingerprint",
            data_file_signature={},
            summary={},
        )

        with pytest.raises(error):
            _validate_worker_prepared_manifest(
                mutate(prepared, staging),
                data_path=data_path,
                cache_dir=cache_dir,
                staging_dir=staging,
            )

        no_op = replace(prepared, no_op=True, staging_dir=None)
        assert (
            _validate_worker_prepared_manifest(
                no_op,
                data_path=data_path,
                cache_dir=cache_dir,
                staging_dir=staging,
            )
            is no_op
        )

    def test_transaction_rejects_malformed_manifest_before_publication_or_cleanup_redirect(
        self, tmp_path: Path
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute._json_shred import PreparedPerPortCacheBuild
        from haute.routes import json_cache

        data_path = str((tmp_path / "data.json").resolve())
        cache_dir = (tmp_path / "cache").resolve()
        parent_staging = cache_dir.with_name(f"{cache_dir.name}.build-tmp-{'b' * 32}")
        attacker_staging = tmp_path / "attacker-staging"
        malformed = PreparedPerPortCacheBuild(
            data_path=data_path,
            cache_dir=str(cache_dir),
            staging_dir=str(attacker_staging),
            schema_fingerprint="fingerprint",
            data_file_signature={},
            summary={},
        )
        discard = Mock()
        commit = Mock()
        budget = IsolatedExecutionBudget(
            operation="test",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1,
            config_key="test",
            budget_policy="test",
        )

        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=parent_staging,
            ),
            patch.object(
                json_cache,
                "run_isolated_worker",
                return_value=json_cache._JsonCacheWorkerOutcome(prepared=malformed),
            ),
            patch("haute._json_shred.commit_prepared_per_port_cache", commit),
            patch("haute._json_shred.discard_per_port_cache_staging", discard),
        ):
            with pytest.raises(ValueError, match="staging directory"):
                json_cache._json_cache_build_transaction(
                    data_path,
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    WorkerCancellationGate(),
                )

        commit.assert_not_called()
        discard.assert_called_once_with(cache_dir, parent_staging)

    def test_transaction_does_not_publish_after_request_cancellation(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute._json_shred import PreparedPerPortCacheBuild
        from haute._worker_isolation import IsolatedWorkerStoppedError
        from haute.routes import json_cache

        data_path = str((tmp_path / "data.json").resolve())
        cache_dir = (tmp_path / "cache").resolve()
        parent_staging = cache_dir.with_name(f"{cache_dir.name}.build-tmp-{'c' * 32}")
        prepared = PreparedPerPortCacheBuild(
            data_path=data_path,
            cache_dir=str(cache_dir),
            staging_dir=str(parent_staging),
            schema_fingerprint="fingerprint",
            data_file_signature={},
            summary={},
        )
        cancellation_requested = WorkerCancellationGate()

        def reject_late_publication(
            *_args: Any,
            publication_guard: Any,
            **_kwargs: Any,
        ) -> None:
            with publication_guard:
                raise AssertionError("cancelled cache generation was published")

        commit = Mock(side_effect=reject_late_publication)
        discard = Mock()
        budget = IsolatedExecutionBudget(
            operation="test",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1,
            config_key="test",
            budget_policy="test",
        )

        def return_after_cancellation(*_args: Any, **_kwargs: Any):
            cancellation_requested.request()
            return json_cache._JsonCacheWorkerOutcome(prepared=prepared)

        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=parent_staging,
            ),
            patch.object(
                json_cache,
                "run_isolated_worker",
                side_effect=return_after_cancellation,
            ),
            patch("haute._json_shred.commit_prepared_per_port_cache", commit),
            patch("haute._json_shred.discard_per_port_cache_staging", discard),
        ):
            with pytest.raises(IsolatedWorkerStoppedError):
                json_cache._json_cache_build_transaction(
                    data_path,
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    cancellation_requested,
                )

        commit.assert_called_once()
        discard.assert_called_once_with(cache_dir, parent_staging)

    @pytest.mark.parametrize(
        ("outcome", "message"),
        [
            (object(), "invalid outcome"),
            (None, "omitted its prepared generation"),
        ],
    )
    def test_transaction_rejects_invalid_or_incomplete_worker_outcomes(
        self,
        tmp_path: Path,
        outcome: object,
        message: str,
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute.routes import json_cache

        cache_dir = tmp_path / "cache"
        staging = tmp_path / "cache.build-tmp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        returned = json_cache._JsonCacheWorkerOutcome() if outcome is None else outcome
        budget = IsolatedExecutionBudget(
            "json_cache_build_v2",
            ExecutionProfile.LAZY_SINK,
            1,
            "test",
            "fixed_default",
        )

        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=staging,
            ),
            patch.object(json_cache, "run_isolated_worker", return_value=returned),
            patch("haute._json_shred.discard_per_port_cache_staging"),
        ):
            with pytest.raises(RuntimeError, match=message):
                json_cache._json_cache_build_transaction(
                    "data.json",
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    WorkerCancellationGate(),
                )

    def test_transaction_propagates_typed_worker_failure_envelope(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute.routes import json_cache

        cache_dir = tmp_path / "cache"
        staging = tmp_path / "cache.build-tmp-cccccccccccccccccccccccccccccccc"
        budget = IsolatedExecutionBudget(
            "json_cache_build_v2",
            ExecutionProfile.LAZY_SINK,
            1,
            "test",
            "fixed_default",
        )
        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=staging,
            ),
            patch.object(
                json_cache,
                "run_isolated_worker",
                return_value=json_cache._JsonCacheWorkerOutcome(
                    failure_kind="schema",
                    detail="schema failed",
                ),
            ),
            patch("haute._json_shred.discard_per_port_cache_staging"),
        ):
            with pytest.raises(json_cache._JsonCacheBuildError, match="schema failed"):
                json_cache._json_cache_build_transaction(
                    "data.json",
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    WorkerCancellationGate(),
                )

    def test_transaction_rejects_pre_dispatch_cancellation_and_surfaces_cleanup_failure(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute._json_shred import PreparedPerPortCacheBuild
        from haute._worker_isolation import IsolatedWorkerStoppedError
        from haute.routes import json_cache

        cache_dir = tmp_path / "cache"
        staging = tmp_path / "cache.build-tmp-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        budget = IsolatedExecutionBudget(
            "json_cache_build_v2",
            ExecutionProfile.LAZY_SINK,
            1,
            "test",
            "fixed_default",
        )
        cancelled = WorkerCancellationGate()
        cancelled.request()
        worker = Mock()

        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=staging,
            ),
            patch.object(json_cache, "run_isolated_worker", worker),
            patch("haute._json_shred.discard_per_port_cache_staging"),
        ):
            with pytest.raises(IsolatedWorkerStoppedError):
                json_cache._json_cache_build_transaction(
                    "data.json",
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    cancelled,
                )
        worker.assert_not_called()

        prepared = PreparedPerPortCacheBuild(
            data_path=str((tmp_path / "data.json").resolve()),
            cache_dir=str(cache_dir.resolve()),
            staging_dir=str(staging.resolve()),
            schema_fingerprint="fingerprint",
            data_file_signature={},
            summary={},
        )
        with (
            patch(
                "haute._json_shred.new_per_port_cache_staging_dir",
                return_value=staging,
            ),
            patch.object(
                json_cache,
                "run_isolated_worker",
                return_value=json_cache._JsonCacheWorkerOutcome(prepared=prepared),
            ),
            patch(
                "haute._json_shred.commit_prepared_per_port_cache",
                return_value={"schema_mode": "v2"},
            ),
            patch(
                "haute._json_shred.discard_per_port_cache_staging",
                side_effect=OSError("cleanup failed"),
            ),
        ):
            with pytest.raises(OSError, match="cleanup failed"):
                json_cache._json_cache_build_transaction(
                    prepared.data_path,
                    _minimal_root_schema(),
                    cache_dir,
                    budget,
                    WorkerCancellationGate(),
                )

    def test_build_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' field returns 422."""
        resp = client.post("/api/json-cache/build", json={})
        assert resp.status_code == 422

    def test_build_no_schema_source_returns_422(self, client: TestClient) -> None:
        """Path without volatile_schema or config_path returns 422 (no v2 schema source)."""
        resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("type") == "ApiInputSchemaError"

    def test_build_timeout_returns_504(self, client: TestClient, tmp_path: Path) -> None:
        """Build exceeding timeout returns 504."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a":1}\n', encoding="utf-8")
        minimal_schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[:]",
                    "emit": True,
                    "columns": [
                        {
                            "name": "a",
                            "path": "$[:].a",
                            "type": "int",
                            "selected": True,
                        }
                    ],
                }
            ]
        }
        started = threading.Event()

        def _timed_out_transaction(*_args: Any) -> dict[str, Any]:
            from haute._worker_isolation import IsolatedWorkerTimeoutError

            started.set()
            raise IsolatedWorkerTimeoutError(timeout_seconds=0.001)

        with (
            patch(
                "haute.routes.json_cache._json_cache_build_transaction",
                _timed_out_transaction,
            ),
            patch.dict(os.environ, {"HAUTE_BUILD_TIMEOUT": "0.001"}),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={"path": str(data_file), "volatile_schema": minimal_schema},
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]
        assert started.wait(timeout=5), "build worker did not start"

    def test_build_missing_file_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """Non-existent data file returns 404 (requires a valid haute.toml project)."""
        # Route path resolution requires a project root (haute.toml).
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "test"\npipeline = "pipeline.py"\n',
            encoding="utf-8",
        )
        minimal_schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[:]",
                    "emit": True,
                    "columns": [
                        {
                            "name": "a",
                            "path": "$[:].a",
                            "type": "int",
                            "selected": True,
                        }
                    ],
                }
            ]
        }
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "does_not_exist.jsonl", "volatile_schema": minimal_schema},
        )
        assert resp.status_code == 404

    def test_build_file_removed_during_worker_returns_404(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """FileNotFoundError raised after dispatch still maps to a user-visible 404."""
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        from haute.routes.json_cache import _JsonCacheBuildError

        with patch(
            "haute.routes.json_cache._json_cache_build_transaction",
            side_effect=_JsonCacheBuildError("file_not_found", "data disappeared"),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Data file not found"

    @pytest.mark.parametrize(
        ("failure_kind", "expected_status"),
        [
            ("invalid_json", 422),
            ("schema", 422),
            ("source_changed", 409),
            ("memory", 507),
            ("unknown_envelope", 500),
            ("admission", 507),
            ("native_rss", 507),
            ("native_unsupported", 507),
            ("crashed_memory", 507),
            ("crashed", 500),
            ("stopped", 409),
            ("remote_memory", 507),
            ("remote", 500),
            ("generic", 500),
        ],
    )
    def test_build_maps_isolated_failures_to_stable_http_contracts(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure_kind: str,
        expected_status: int,
    ) -> None:
        from types import SimpleNamespace

        from haute._execution_admission import ExecutionAdmissionError
        from haute._execution_context import ExecutionProfile
        from haute._worker_isolation import (
            IsolatedWorkerCrashedError,
            IsolatedWorkerMemoryLimitExceededError,
            IsolatedWorkerMemoryLimitUnsupportedError,
            IsolatedWorkerRemoteError,
            IsolatedWorkerStoppedError,
        )
        from haute.routes import json_cache
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        context = Mock()
        budget = SimpleNamespace(memory_limit_bytes=100)
        if failure_kind == "admission":
            failure: BaseException = ExecutionAdmissionError(
                "json_cache_build_v2",
                profile=ExecutionProfile.LAZY_SINK,
                memory_limit_bytes=1,
                rss_at_admission_bytes=2,
                reason="forced admission failure",
            )
            monkeypatch.setattr(
                json_cache,
                "create_admitted_execution_context",
                Mock(side_effect=failure),
            )
        else:
            monkeypatch.setattr(
                json_cache,
                "create_admitted_execution_context",
                Mock(return_value=context),
            )
            monkeypatch.setattr(
                json_cache,
                "isolated_execution_budget",
                Mock(return_value=budget),
            )
            if failure_kind in {
                "invalid_json",
                "schema",
                "source_changed",
                "memory",
                "unknown_envelope",
            }:
                envelope_kind = {
                    "invalid_json": "invalid_json",
                    "schema": "schema",
                    "source_changed": "source_changed",
                    "memory": "memory",
                    "unknown_envelope": "unknown",
                }[failure_kind]
                payload = {"error_code": "memory_limit"} if failure_kind == "memory" else None
                failure = json_cache._JsonCacheBuildError(
                    envelope_kind,
                    "private worker failure",
                    payload,
                )
            elif failure_kind == "native_rss":
                failure = IsolatedWorkerMemoryLimitExceededError(
                    rss_bytes=200,
                    rss_limit_bytes=100,
                )
            elif failure_kind == "native_unsupported":
                failure = IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=100)
            elif failure_kind == "crashed_memory":
                failure = IsolatedWorkerCrashedError(
                    exitcode=-9,
                    memory_limit_bytes=100,
                )
            elif failure_kind == "crashed":
                failure = IsolatedWorkerCrashedError(
                    exitcode=1,
                    memory_limit_bytes=100,
                )
            elif failure_kind == "stopped":
                failure = IsolatedWorkerStoppedError(terminal_reason="cancelled")
            elif failure_kind == "remote_memory":
                failure = IsolatedWorkerRemoteError(
                    remote_type="MemoryError",
                    remote_message="private memory detail",
                    remote_traceback="private traceback",
                )
            elif failure_kind == "remote":
                failure = IsolatedWorkerRemoteError(
                    remote_type="RuntimeError",
                    remote_message="private child detail",
                    remote_traceback="private traceback",
                )
            else:
                failure = RuntimeError("private generic failure")
            monkeypatch.setattr(
                json_cache,
                "_json_cache_build_transaction",
                Mock(side_effect=failure),
            )

        response = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
        )

        assert response.status_code == expected_status
        if expected_status == 500:
            expected_detail = (
                "Internal server error"
                if failure_kind == "unknown_envelope"
                else _INTERNAL_ERROR_DETAIL
            )
            assert response.json()["detail"] == expected_detail
            assert "private" not in response.text

    def test_success_without_an_admission_handle_skips_release(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from haute.routes import json_cache

        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        monkeypatch.setattr(
            json_cache,
            "create_admitted_execution_context",
            Mock(return_value=None),
        )
        monkeypatch.setattr(
            json_cache,
            "isolated_execution_budget",
            Mock(return_value=SimpleNamespace(memory_limit_bytes=100)),
        )
        monkeypatch.setattr(
            json_cache,
            "_json_cache_build_transaction",
            Mock(
                return_value={
                    "schema_mode": "v2",
                    "schema_fingerprint": "fingerprint",
                    "tables": [],
                    "data_file": {},
                    "skipped": {"records": 0, "rows_by_table": {}},
                }
            ),
        )
        monkeypatch.setattr("haute._json_flatten._mark_working_consulted", Mock())

        response = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
        )

        assert response.status_code == 200
        assert response.json()["row_count"] == 0


# ---------------------------------------------------------------------------
# GET /api/json-cache/progress
# ---------------------------------------------------------------------------


class TestJsonCacheProgress:
    def test_progress_always_inactive(self, client: TestClient) -> None:
        """With no active build, progress reports inactive."""
        resp = client.get("/api/json-cache/progress", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_progress_reports_active_while_v2_build_running(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[:]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[:].a", "type": "int", "selected": True}],
                }
            ]
        }
        started = threading.Event()
        release = threading.Event()
        response_by_thread: dict[str, Response] = {}

        def _slow_build(*args: Any) -> dict[str, Any]:
            started.set()
            assert release.wait(timeout=5), "test build was not released"
            cache_dir = args[2]
            return {
                "schema_mode": "v2",
                "schema_fingerprint": "fake",
                "tables": [],
                "data_file": {},
                "skipped": {"records": 0, "rows_by_table": {}},
                "cache_dir": str(cache_dir),
            }

        def _post_build() -> None:
            response_by_thread["response"] = client.post(
                "/api/json-cache/build",
                json={"path": "data.json", "volatile_schema": schema},
            )

        with patch(
            "haute.routes.json_cache._json_cache_build_transaction",
            _slow_build,
        ):
            worker = threading.Thread(target=_post_build)
            worker.start()
            try:
                assert started.wait(timeout=5), "build did not start"
                resp = client.get("/api/json-cache/progress", params={"path": "data.json"})
            finally:
                release.set()
                worker.join(timeout=5)

        assert not worker.is_alive()
        build_resp = response_by_thread["response"]
        assert build_resp.status_code == 200
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["phase"] == "building"
        assert data["elapsed"] >= 0
        # F440: no producer ever updates `rows`; the response defaults it to 0.
        assert data["rows"] == 0

    @pytest.mark.asyncio
    async def test_progress_is_inactive_when_504_returns_after_worker_join(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        from haute.routes.json_cache import build_json_cache, get_json_cache_progress
        from haute.schemas import JsonCacheBuildRequest

        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[:]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[:].a", "type": "int", "selected": True}],
                }
            ]
        }

        def _timed_out_transaction(*_args: Any) -> dict[str, Any]:
            from haute._worker_isolation import IsolatedWorkerTimeoutError

            raise IsolatedWorkerTimeoutError(timeout_seconds=0.001)

        with (
            patch(
                "haute.routes.json_cache._json_cache_build_transaction",
                _timed_out_transaction,
            ),
            patch.dict(os.environ, {"HAUTE_BUILD_TIMEOUT": "0.001"}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await build_json_cache(
                    JsonCacheBuildRequest(path="data.json", volatile_schema=schema)
                )
            assert exc_info.value.status_code == 504

            progress = await get_json_cache_progress("data.json")
            assert progress.active is False

    def test_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' query param returns 422."""
        resp = client.get("/api/json-cache/progress")
        assert resp.status_code == 422

    def test_progress_counter_tracks_overlapping_builds_and_unknown_finish(
        self, tmp_path: Path
    ) -> None:
        """Progress bookkeeping is reference-counted across overlapping builds."""
        from haute.routes import json_cache

        data_file = tmp_path / "data.json"
        data_file.write_text("[]", encoding="utf-8")
        data_path = str(data_file)

        with json_cache._build_progress_lock:
            json_cache._build_progress.clear()
        try:
            json_cache._finish_build_progress(data_path)
            assert json_cache._get_build_progress(data_path).active is False

            json_cache._start_build_progress(data_path)
            json_cache._start_build_progress(data_path)

            with json_cache._build_progress_lock:
                key = json_cache._progress_key(data_path)
                assert json_cache._build_progress[key]["active_count"] == 2

            json_cache._finish_build_progress(data_path)

            with json_cache._build_progress_lock:
                assert json_cache._build_progress[key]["active_count"] == 1
            assert json_cache._get_build_progress(data_path).active is True

            json_cache._finish_build_progress(data_path)
            assert json_cache._get_build_progress(data_path).active is False
        finally:
            with json_cache._build_progress_lock:
                json_cache._build_progress.clear()


# ---------------------------------------------------------------------------
# GET /api/json-cache/status
# ---------------------------------------------------------------------------


class TestJsonCacheStatus:
    def test_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/json-cache/status")
        assert resp.status_code == 422

    def test_no_schema_source_post_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        """POST status without volatile_schema or config_path returns 422."""
        resp = client.post(
            "/api/json-cache/status",
            json={"path": "data.jsonl"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("type") == "ApiInputSchemaError"

    def test_get_status_missing_config_returns_uncached(self, client: TestClient) -> None:
        """GET status for a file with no on-disk v2 config returns cached=False."""
        resp = client.get("/api/json-cache/status", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.jsonl"

    def test_post_status_valid_cache_without_meta_returns_uncached(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A cache directory that loses meta during status polling is treated as uncached."""
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        with (
            patch("haute._json_shred.is_per_port_cache_valid", return_value=True),
            patch("haute._json_shred.read_per_port_cache_meta", return_value=None),
        ):
            resp = client.post(
                "/api/json-cache/status",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.json"

    def test_post_status_schema_error_returns_structured_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Status preserves the ApiInputSchemaError discriminator from cache validation."""
        from haute._api_input_schema import ApiInputSchemaError

        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        with patch(
            "haute._json_shred.is_per_port_cache_valid",
            side_effect=ApiInputSchemaError("status schema mismatch"),
        ):
            resp = client.post(
                "/api/json-cache/status",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "ApiInputSchemaError"
        assert "status schema mismatch" in body["detail"]


# ---------------------------------------------------------------------------
# DELETE /api/json-cache
# ---------------------------------------------------------------------------


class TestDeleteJsonCache:
    def test_delete_success(self, client: TestClient) -> None:
        """Deleting an existing cache returns cached=False."""
        with patch("haute._json_flatten.clear_json_cache"):
            resp = client.delete("/api/json-cache", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.jsonl"

    def test_delete_already_missing(self, client: TestClient) -> None:
        """Deleting a nonexistent cache still returns 200 with cached=False."""
        with patch("haute._json_flatten.clear_json_cache"):
            resp = client.delete("/api/json-cache", params={"path": "no_such.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False

    def test_delete_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.delete("/api/json-cache")
        assert resp.status_code == 422


class TestStatusPathValidatesSchema:
    """F053: the status/validity path must enforce the SAME v2 invariants
    as ``build_per_port_cache`` (which runs ``validate_v2_schema`` first).

    Without validation the status path evaluates an invalid schema (e.g.
    duplicate table labels, a B1 illegal column type) and reports
    ``cached=False`` — silently accepting a schema the build would loudly
    reject. POST /status must surface the structured 422; GET /status (a
    read-only poll) reports ``cached=False`` truthfully, mirroring how it
    already treats a corrupt on-disk config.
    """

    @staticmethod
    def _duplicate_label_schema() -> dict[str, Any]:
        # v2-shaped (has `tables`) so it reaches the status/validity path,
        # but validate_v2_schema rejects the repeated label.
        return {
            "tables": [
                {"label": "root", "path": "$[:]", "emit": True, "columns": []},
                {"label": "root", "path": "$[:]", "emit": True, "columns": []},
            ]
        }

    def test_post_status_invalid_schema_returns_structured_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        resp = client.post(
            "/api/json-cache/status",
            json={"path": "data.json", "volatile_schema": self._duplicate_label_schema()},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "ApiInputSchemaError"
        assert "root" in body["detail"]

    def test_get_status_invalid_config_returns_uncached(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        config_file = tmp_path / "config.json"
        import json as _json

        config_file.write_text(_json.dumps(self._duplicate_label_schema()), encoding="utf-8")

        resp = client.get(
            "/api/json-cache/status",
            params={"path": "data.json", "config_path": "config.json"},
        )

        # Read-only poll: an invalid schema means no valid cache can exist.
        assert resp.status_code == 200
        assert resp.json()["cached"] is False


class TestReadV2ConfigRejectsDuplicateKeys:
    """F009: the cache-build read funnel (``_read_v2_config``) must reject
    duplicate JSON keys, exactly as the parser load funnel
    (``_config_io._load_json_object``) does — otherwise the two funnels
    disagree on the same file (one silently keeps the last value, the
    other fails loud).
    """

    def test_read_v2_config_raises_on_duplicate_key(self, tmp_path: Path) -> None:
        from haute._api_input_schema import ApiInputSchemaError
        from haute.routes.json_cache import _read_v2_config

        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"tables": [{"label": "root", "path": "$", "emit": true, "columns": []}],'
            ' "foo": 1, "foo": 2}',
            encoding="utf-8",
        )

        with pytest.raises(ApiInputSchemaError):
            _read_v2_config(str(config_file))


class TestResolveConfigPathNullByte:
    """F439: an embedded-null-byte path must map to HTTP 400 on the config
    funnel just as it does on the data funnel (``_resolve_data_path``) —
    it's a malformed request, not a forbidden traversal (403).
    """

    def test_config_path_null_byte_returns_400(self, tmp_path: Path) -> None:
        from fastapi import HTTPException

        from haute.routes.json_cache import _resolve_config_path

        with pytest.raises(HTTPException) as exc_info:
            _resolve_config_path("bad\x00config.json")
        assert exc_info.value.status_code == 400


class TestBuildStatusAggregateEquality:
    """Witness: build (POST) and status (GET/POST) collapse the SAME
    per-port ``tables[]`` into EQUAL per-port aggregates.

    ``_aggregate_v2_build_response`` (json_cache.py:255) and
    ``_aggregate_v2_status_response`` (json_cache.py:300) are
    copy-paste-near-identical helpers. For the same inputs (same
    ``tables[]``, same on-disk parquets) every per-port aggregate they
    compute — row_count, column_count, the ``{label}.colN`` columns map,
    size_bytes, cached_at, skipped_records, skipped_rows — must match.
    The two helpers will drift (a fix in one forgotten in the other); the
    equality is the actual contract and is otherwise untested (every
    existing status assertion is on the cached=False / error path).

    JsonCacheBuildResponse and JsonCacheStatusResponse are *different*
    Pydantic models (schemas.py:703 vs :733), so this asserts the shared
    per-port FIELDS equal, not whole objects. The intentional
    non-aggregate differences — ``cache_seconds`` (build-only) and
    ``cached=True`` (status-only) — are deliberately NOT asserted equal.
    """

    def test_build_and_status_per_port_aggregates_match(self, tmp_path: Path) -> None:
        from haute.routes.json_cache import (
            _aggregate_v2_build_response,
            _aggregate_v2_status_response,
        )

        cache_dir = tmp_path
        # Two emit-true tables: 10+4 rows, 3+2 columns.
        tables = [
            {
                "label": "policies",
                "parquet": "policies.parquet",
                "row_count": 10,
                "column_count": 3,
                "columns": {"id": "Int64", "name": "String", "premium": "Float64"},
            },
            {
                "label": "drivers",
                "parquet": "drivers.parquet",
                "row_count": 4,
                "column_count": 2,
                "columns": {"id": "Int64", "age": "Int64"},
            },
        ]
        # Create identical stub parquet files so size_bytes > 0 and
        # cached_at > 0 are asserted equal NON-trivially (not both 0).
        (cache_dir / "policies.parquet").write_bytes(b"PAR1" * 8)
        (cache_dir / "drivers.parquet").write_bytes(b"PAR1" * 8)

        skipped = {"records": 1, "rows_by_table": {"drivers": 2}}
        summary = {"tables": tables, "skipped": skipped}
        meta = {"tables": tables, "skipped": skipped}

        build_resp = _aggregate_v2_build_response(
            summary, cache_dir, "data.json", elapsed_seconds=0.1
        )
        status_resp = _aggregate_v2_status_response(cache_dir, "data.json", meta)

        # Per-port aggregates must be equal across both responses.
        assert build_resp.row_count == status_resp.row_count == 14
        assert build_resp.column_count == status_resp.column_count == 5
        assert build_resp.columns == status_resp.columns
        assert len(build_resp.columns) == 5
        assert build_resp.size_bytes == status_resp.size_bytes
        assert build_resp.size_bytes > 0
        assert build_resp.cached_at == status_resp.cached_at
        assert build_resp.cached_at > 0
        assert build_resp.skipped_records == status_resp.skipped_records == 1
        assert build_resp.skipped_rows == status_resp.skipped_rows == {"drivers": 2}

        # Intentional non-aggregate differences (NOT asserted equal):
        #   build carries cache_seconds; status carries cached=True.
        assert build_resp.cache_seconds == 0.1
        assert status_resp.cached is True

    def test_aggregate_uses_only_canonical_metadata_columns(self, tmp_path: Path) -> None:
        """Aggregate responses derive columns only from canonical metadata."""
        from haute.routes.json_cache import _aggregate_v2_tables

        (tmp_path / "root.parquet").write_bytes(b"not a parquet file")
        _rows, _count, columns, _size, _cached_at = _aggregate_v2_tables(
            tmp_path,
            [
                {
                    "label": "root",
                    "parquet": "root.parquet",
                    "row_count": 1,
                    "column_count": 1,
                }
            ],
        )

        assert columns == {}
