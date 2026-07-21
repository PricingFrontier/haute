"""Deployed scorer artifact caching (W2 4a.3).

Pre-fix, every ``/quote`` reloaded the model from disk
(``load_local_model`` at ``deploy/_scorer.py``) and re-read + re-hashed
the bundled feature contract — disk parsing as per-request latency.

The fix caches both by ``(resolved path, task)`` (models) / resolved
path (contracts), gated on ``(st_mtime_ns, st_size)`` — the same
invalidation discipline as
:func:`haute.execution._stat_gated_runtime_path_fingerprint`:

* N quotes against an unchanged artifact → exactly one disk load;
* any mtime/size change → exactly one reload;
* concurrent quotes during the first load → single flight (one load,
  the rest wait and reuse it);
* a load that observes the file changing under it fails loudly and
  caches nothing;
* responses are byte-identical with a cold or warm cache — caching is
  invisible apart from the load counts.

Contract MATCHING (``assert_contracts_match`` against the live request
schema) still runs per request — only the disk read/hash is cached.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import ScoringModel
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    build_contract,
    save_contract,
)
from tests.conftest import make_graph, make_output_config

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_artifact_caches():
    """Isolate every test from process-wide deploy artifact caches.

    ``getattr`` keeps the module importable pre-fix so the RED runs fail
    on load counts, not on a missing helper.
    """
    from haute.deploy import _scorer

    clear = getattr(_scorer, "_clear_deploy_artifact_caches", lambda: None)
    clear()
    yield
    clear()


def _model_score_graph() -> Any:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": {"path": ""},
                    },
                },
                {
                    "id": "ms",
                    "data": {
                        "label": "ms",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": "r1",
                            "artifact_path": "model.cbm",
                            "task": "regression",
                            "output_column": "pred",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["pred"]),
                    },
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "src",
                    "target": "ms",
                    "sourceHandle": "src",
                },
                {"id": "e2", "source": "ms", "target": "out"},
            ],
        }
    )


def _write_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """Write a fake .cbm artifact plus a matching feature contract."""
    cbm_path = tmp_path / "model.cbm"
    cbm_path.write_bytes(b"fake model bytes")
    contract_path = tmp_path / CONTRACT_FILENAME
    save_contract(
        build_contract(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    return cbm_path, contract_path


def _remap(cbm_path: Path, contract_path: Path | None = None) -> dict[str, str]:
    remap = {"ms__model.cbm": str(cbm_path)}
    if contract_path is not None:
        remap[f"ms__{CONTRACT_FILENAME}"] = str(contract_path)
    return remap


def _doubling_scoring_model() -> ScoringModel:
    """Real ScoringModel around a deterministic mock: pred == 2 * x."""
    model = MagicMock()
    model.predict.side_effect = lambda x: np.asarray(x, dtype=np.float64).flatten() * 2.0
    del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=["x"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )


class _LoadSpy:
    """Counting stand-in for ``haute._mlflow_io.load_local_model``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, path: str, task: str = "regression") -> ScoringModel:
        self.calls.append((path, task))
        return _doubling_scoring_model()

    @property
    def count(self) -> int:
        return len(self.calls)


def _score_once(graph: Any, remap: dict[str, str]) -> pl.DataFrame:
    from haute.deploy._scorer import score_graph

    # Single-row input → DEPLOY_LIVE, which bypasses the dataframe
    # execution cache entirely; the only caching under test here is the
    # artifact cache.
    return score_graph(
        graph=graph,
        input_df=pl.DataFrame({"x": [3.0]}),
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths=remap,
    )


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


# ---------------------------------------------------------------------------
# Load-count mechanism: N quotes, 1 disk load
# ---------------------------------------------------------------------------


class TestModelLoadedOncePerArtifact:
    def test_repeated_quotes_load_model_once(self, tmp_path: Path) -> None:
        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            results = [_score_once(graph, _remap(cbm_path, contract_path)) for _ in range(3)]

        assert spy.count == 1, (
            f"3 quotes performed {spy.count} model loads from disk; an "
            "unchanged artifact must be loaded exactly once."
        )
        assert results[0]["pred"].to_list() == [6.0]
        assert results[0].equals(results[1])
        assert results[1].equals(results[2])

    def test_repeated_quotes_read_contract_once(self, tmp_path: Path) -> None:
        import haute.modelling._feature_contract as fc_mod

        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        real_load_contract = fc_mod.load_contract
        contract_reads: list[str] = []

        def counting_load_contract(path: Any, **kwargs: Any) -> Any:
            contract_reads.append(str(path))
            return real_load_contract(path, **kwargs)

        with (
            patch("haute._mlflow_io.load_local_model", side_effect=spy),
            patch(
                "haute.modelling._feature_contract.load_contract",
                side_effect=counting_load_contract,
            ),
        ):
            for _ in range(3):
                _score_once(graph, _remap(cbm_path, contract_path))

        assert len(contract_reads) == 1, (
            f"3 quotes re-read/re-hashed the feature contract "
            f"{len(contract_reads)} times; an unchanged contract must be "
            "read exactly once."
        )

    def test_contract_only_branch_fails_loudly_and_reads_contract_once(
        self, tmp_path: Path
    ) -> None:
        """A bundled contract without a model validates, then fails loudly."""
        import haute.modelling._feature_contract as fc_mod

        _, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()

        real_load_contract = fc_mod.load_contract
        contract_reads: list[str] = []

        def counting_load_contract(path: Any, **kwargs: Any) -> Any:
            contract_reads.append(str(path))
            return real_load_contract(path, **kwargs)

        with patch(
            "haute.modelling._feature_contract.load_contract",
            side_effect=counting_load_contract,
        ):
            for _ in range(3):
                with pytest.raises(RuntimeError, match="model artifact"):
                    _score_once(
                        graph,
                        {f"ms__{CONTRACT_FILENAME}": str(contract_path)},
                    )

        assert len(contract_reads) == 1


# ---------------------------------------------------------------------------
# Invalidation: (mtime_ns, size) stat gate
# ---------------------------------------------------------------------------


class TestStatGateInvalidation:
    def test_model_mtime_bump_triggers_reload(self, tmp_path: Path) -> None:
        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            _score_once(graph, _remap(cbm_path, contract_path))
            _score_once(graph, _remap(cbm_path, contract_path))
            assert spy.count == 1

            _bump_mtime(cbm_path)
            _score_once(graph, _remap(cbm_path, contract_path))
            assert spy.count == 2, "mtime bump must invalidate the cached model"

            _score_once(graph, _remap(cbm_path, contract_path))
            assert spy.count == 2, "reloaded artifact must be cached again"

    def test_model_content_rewrite_triggers_reload(self, tmp_path: Path) -> None:
        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            _score_once(graph, _remap(cbm_path, contract_path))
            cbm_path.write_bytes(b"different fake model bytes entirely")
            _score_once(graph, _remap(cbm_path, contract_path))

        assert spy.count == 2

    def test_contract_rewrite_triggers_reread(self, tmp_path: Path) -> None:
        import haute.modelling._feature_contract as fc_mod

        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        real_load_contract = fc_mod.load_contract
        contract_reads: list[str] = []

        def counting_load_contract(path: Any, **kwargs: Any) -> Any:
            contract_reads.append(str(path))
            return real_load_contract(path, **kwargs)

        with (
            patch("haute._mlflow_io.load_local_model", side_effect=spy),
            patch(
                "haute.modelling._feature_contract.load_contract",
                side_effect=counting_load_contract,
            ),
        ):
            _score_once(graph, _remap(cbm_path, contract_path))
            assert len(contract_reads) == 1

            _bump_mtime(contract_path)
            _score_once(graph, _remap(cbm_path, contract_path))
            assert len(contract_reads) == 2


class TestDeployArtifactPathFingerprints:
    def test_batch_cache_request_uses_stat_gated_artifact_fingerprint(self, tmp_path: Path) -> None:
        import haute.execution as execution_mod
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.deploy import _scorer

        artifact_path = tmp_path / "artifact.parquet"
        artifact_path.write_bytes(b"stable artifact bytes")
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": ""},
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )
        real_content_hash = execution_mod.content_hash
        hash_calls: list[Path] = []

        def counting_content_hash(path: Path) -> str:
            hash_calls.append(Path(path))
            return real_content_hash(path)

        with (
            patch.object(execution_mod, "content_hash", side_effect=counting_content_hash),
            patch.object(
                _scorer,
                "execute_lazy_graph",
                return_value=(
                    {"out": pl.DataFrame({"x": [1.0, 2.0]}).lazy()},
                    ["src", "out"],
                    {},
                    {},
                ),
            ),
        ):
            for _ in range(2):
                plan = _scorer.score_graph_lazy(
                    graph=graph,
                    input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                    input_node_ids=["src"],
                    output_node_id="out",
                    artifact_paths={"artifact": str(artifact_path)},
                    execution_context=ExecutionContext(
                        operation="deploy_score_graph",
                        profile=ExecutionProfile.DEPLOY_BATCH,
                    ),
                )
                plan.cleanup(preserve_primary_error=False)

        assert hash_calls == [artifact_path.resolve()]


# ---------------------------------------------------------------------------
# Cache unit surface: keying, single flight, fail-loud
# ---------------------------------------------------------------------------


class TestArtifactCacheUnit:
    def test_cache_keyed_by_resolved_path_and_task(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            regression = _load_local_model_cached(str(cbm_path), "regression")
            classification = _load_local_model_cached(str(cbm_path), "classification")
            again = _load_local_model_cached(str(cbm_path), "regression")

        assert spy.count == 2, "distinct tasks must load distinct entries"
        assert {task for _, task in spy.calls} == {"regression", "classification"}
        assert again is regression
        assert classification is not regression

    def test_distinct_paths_cached_independently(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import _load_local_model_cached

        path_a = tmp_path / "a.cbm"
        path_a.write_bytes(b"model a")
        path_b = tmp_path / "b.cbm"
        path_b.write_bytes(b"model b")
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            first_a = _load_local_model_cached(str(path_a), "regression")
            first_b = _load_local_model_cached(str(path_b), "regression")
            assert _load_local_model_cached(str(path_a), "regression") is first_a
            assert _load_local_model_cached(str(path_b), "regression") is first_b

        assert spy.count == 2

    def test_concurrent_first_load_is_single_flight(self, tmp_path: Path) -> None:
        """Concurrent quotes during the first load: one disk load, the rest
        wait for it and reuse the same object."""
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        loaded_model = _doubling_scoring_model()

        def gated_load(path: str, task: str = "regression") -> ScoringModel:
            calls.append(path)
            started.set()
            assert release.wait(timeout=10), "test deadlock: release never set"
            return loaded_model

        results: list[ScoringModel] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                results.append(_load_local_model_cached(str(cbm_path), "regression"))
            except BaseException as exc:  # pragma: no cover - failure diagnostics
                errors.append(exc)

        with patch("haute._mlflow_io.load_local_model", side_effect=gated_load):
            threads = [threading.Thread(target=worker) for _ in range(6)]
            for thread in threads:
                thread.start()
            assert started.wait(timeout=10), "first loader never started"
            # While the first load is in flight nothing else may load.
            threads[0].join(timeout=0.2)
            assert len(calls) == 1, "waiters must block on the in-flight load"
            release.set()
            for thread in threads:
                thread.join(timeout=10)

        assert not errors, f"concurrent loads raised: {errors!r}"
        assert len(calls) == 1, f"expected single-flight load, got {len(calls)}"
        assert len(results) == 6
        assert all(result is loaded_model for result in results)

    def test_missing_model_file_fails_loud_and_is_not_cached(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import _load_local_model_cached

        missing = tmp_path / "missing.cbm"
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            with pytest.raises(FileNotFoundError):
                _load_local_model_cached(str(missing), "regression")
            assert spy.count == 0, "stat must fail before any loader call"

            missing.write_bytes(b"now present")
            _load_local_model_cached(str(missing), "regression")

        assert spy.count == 1

    def test_loader_failure_is_not_cached(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        attempts: list[int] = []
        good_model = _doubling_scoring_model()

        def flaky_load(path: str, task: str = "regression") -> ScoringModel:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("corrupt artifact")
            return good_model

        with patch("haute._mlflow_io.load_local_model", side_effect=flaky_load):
            with pytest.raises(RuntimeError, match="corrupt artifact"):
                _load_local_model_cached(str(cbm_path), "regression")
            assert _load_local_model_cached(str(cbm_path), "regression") is good_model

        assert len(attempts) == 2

    def test_artifact_changed_while_loading_fails_loud(self, tmp_path: Path) -> None:
        """A file whose stat gate keeps moving during the load is a torn
        read — never cached, never served."""
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        calls: list[int] = []

        def mutating_load(path: str, task: str = "regression") -> ScoringModel:
            calls.append(1)
            _bump_mtime(cbm_path)
            return _doubling_scoring_model()

        with patch("haute._mlflow_io.load_local_model", side_effect=mutating_load):
            with pytest.raises(RuntimeError, match="changed on disk while loading"):
                _load_local_model_cached(str(cbm_path), "regression")

        assert len(calls) == 2, "one retry, then fail loud"

    def test_artifact_changed_once_while_loading_retries_and_caches(self, tmp_path: Path) -> None:
        """A gate that moves during the first load but holds on the retry
        means the second read was clean — cached under the fresh gate."""
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        calls: list[int] = []
        stable_model = _doubling_scoring_model()

        def mutating_once_load(path: str, task: str = "regression") -> ScoringModel:
            calls.append(1)
            if len(calls) == 1:
                _bump_mtime(cbm_path)
                return _doubling_scoring_model()
            return stable_model

        with patch("haute._mlflow_io.load_local_model", side_effect=mutating_once_load):
            first = _load_local_model_cached(str(cbm_path), "regression")
            second = _load_local_model_cached(str(cbm_path), "regression")

        assert len(calls) == 2, "torn first read retried exactly once"
        assert first is stable_model, "the clean retry result is served"
        assert second is stable_model, "the clean retry result is cached"

    def test_clear_resets_both_caches(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import (
            _clear_deploy_artifact_caches,
            _load_feature_contract_cached,
            _load_local_model_cached,
        )

        cbm_path, contract_path = _write_bundle(tmp_path)
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            _load_local_model_cached(str(cbm_path), "regression")
            first_contract = _load_feature_contract_cached(str(contract_path))
            _clear_deploy_artifact_caches()
            _load_local_model_cached(str(cbm_path), "regression")
            second_contract = _load_feature_contract_cached(str(contract_path))

        assert spy.count == 2
        assert first_contract == second_contract

    def test_contract_cache_returns_loaded_contract(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import _load_feature_contract_cached

        _, contract_path = _write_bundle(tmp_path)

        contract = _load_feature_contract_cached(str(contract_path))
        assert contract.features == ["x"]
        assert _load_feature_contract_cached(str(contract_path)) is contract


# ---------------------------------------------------------------------------
# Cache key canonicalisation: normcase(expanduser(resolve()))
# ---------------------------------------------------------------------------


class TestArtifactCacheKeyCanonicalisation:
    """Cache keys mirror :func:`haute._json_flatten._path_hash`:
    ``os.path.normcase(str(Path(p).expanduser().resolve()))``.

    ``normcase`` is a no-op on POSIX, so the case-folding tests pin the
    convention by patching ``os.path.normcase`` (Windows semantics) rather
    than relying on the host filesystem — pre-fix the call sites keyed on
    bare ``resolve()`` and never consulted ``normcase`` at all.
    """

    def test_model_cache_key_folds_case_via_normcase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.deploy import _scorer

        model_dir = tmp_path / "Models"
        model_dir.mkdir()
        cbm_path, _ = _write_bundle(model_dir)
        monkeypatch.setattr(os.path, "normcase", lambda s: str(s).lower())
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            _scorer._load_local_model_cached(str(cbm_path), "regression")

        expected_key = str(cbm_path.resolve()).lower()
        assert list(_scorer._local_model_cache._entries) == [(expected_key, "regression")]

    def test_contract_cache_key_folds_case_via_normcase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.modelling import _feature_contract

        bundle_dir = tmp_path / "Bundle"
        bundle_dir.mkdir()
        _, contract_path = _write_bundle(bundle_dir)
        monkeypatch.setattr(os.path, "normcase", lambda s: str(s).lower())

        _feature_contract.load_contract_cached(str(contract_path))

        expected_key = str(contract_path.resolve()).lower()
        assert list(_feature_contract._contract_cache._entries) == [expected_key]

    def test_relative_and_absolute_spellings_share_one_model_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.deploy._scorer import _load_local_model_cached

        cbm_path, _ = _write_bundle(tmp_path)
        monkeypatch.chdir(tmp_path)
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            first = _load_local_model_cached("./model.cbm", "regression")
            assert _load_local_model_cached(str(cbm_path), "regression") is first

        assert spy.count == 1

    def test_relative_and_absolute_spellings_share_one_contract_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.deploy._scorer import _load_feature_contract_cached

        _, contract_path = _write_bundle(tmp_path)
        monkeypatch.chdir(tmp_path)

        first = _load_feature_contract_cached("./" + CONTRACT_FILENAME)
        assert _load_feature_contract_cached(str(contract_path)) is first


# ---------------------------------------------------------------------------
# Behaviour identical apart from caching
# ---------------------------------------------------------------------------


class TestResponsesByteEqual:
    def test_cold_and_warm_responses_byte_equal(self, tmp_path: Path) -> None:
        from haute.deploy import _scorer

        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            cold = _score_once(graph, _remap(cbm_path, contract_path))
            warm = _score_once(graph, _remap(cbm_path, contract_path))
            getattr(_scorer, "_clear_deploy_artifact_caches", lambda: None)()
            cold_again = _score_once(graph, _remap(cbm_path, contract_path))

        assert cold.equals(warm)
        assert cold.equals(cold_again)
        assert cold.write_json() == warm.write_json()
        assert cold.write_json() == cold_again.write_json()

    def test_contract_drift_still_raises_per_request(self, tmp_path: Path) -> None:
        """Caching the contract READ must not cache the contract CHECK —
        a drifting request schema fails on the very request that drifts."""
        from haute.errors import FeatureMismatchError

        cbm_path, contract_path = _write_bundle(tmp_path)
        graph = _model_score_graph()
        spy = _LoadSpy()

        with patch("haute._mlflow_io.load_local_model", side_effect=spy):
            _score_once(graph, _remap(cbm_path, contract_path))

            from haute.deploy._scorer import score_graph

            with pytest.raises(FeatureMismatchError):
                score_graph(
                    graph=graph,
                    input_df=pl.DataFrame({"x": ["not a float"]}),
                    input_node_ids=["src"],
                    output_node_id="out",
                    artifact_paths=_remap(cbm_path, contract_path),
                )
