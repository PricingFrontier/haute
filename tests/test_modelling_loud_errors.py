"""Phase 1 Package 1E — loud-error tests for modelling config & artifact load.

TDD suite covering:

* #25 — ``_model_score_columns`` must not swallow configuration errors
* #27 — Artifact download corrupt retry must be bounded and diagnostic,
        not a silent delete-and-retry loop that hides corruption.

The project convention is fail loudly: callers prefer a clear error over a
debug log line that silently drops information.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from haute._mlflow_io import ScoringModel, load_mlflow_model
from haute.errors import ConfigError

# ===========================================================================
# Item #25 — model-score column detection must not swallow config errors
# ===========================================================================


class TestModelScoreColumnDetectionLoud:
    """``_builders._model_score_columns`` is invoked during executor graph
    construction.  Currently it catches every exception and logs a debug
    line, so a typo in ``run_id`` silently degrades column inference to
    "opaque", downstream optimiser/rating nodes then see missing columns
    and produce confusing errors several steps later.

    Rule: a misconfigured model-score node (sourceType set, but mandatory
    reference missing) must raise ``ConfigError`` on the spot.
    """

    def test_missing_run_id_raises_config_error(self) -> None:
        from haute._builders import _model_score_columns

        bad_config = {
            "sourceType": "run",
            "run_id": "",
            "artifact_path": "model.cbm",
            "output_column": "pred",
        }
        with pytest.raises(ConfigError) as exc_info:
            _model_score_columns(bad_config)
        # The error must hint at the missing field so the operator can fix
        # it without tracing through MLflow internals.
        err_text = str(exc_info.value)
        assert "run_id" in err_text.lower() or "sourceType" in err_text

    def test_registered_source_without_model_name_raises(self) -> None:
        from haute._builders import _model_score_columns

        bad_config = {
            "sourceType": "registered",
            "registered_model": "",
            "version": "latest",
            "output_column": "pred",
        }
        with pytest.raises(ConfigError):
            _model_score_columns(bad_config)

    def test_mlflow_load_error_propagates(self) -> None:
        """When MLflow is configured correctly but the load itself fails
        (e.g. run doesn't exist on the tracking server) the exception must
        propagate — the current behaviour of silently absorbing it into a
        debug-level log line is a documented fail-loud violation.
        """
        from haute._builders import _model_score_columns

        config = {
            "sourceType": "run",
            "run_id": "definitely-not-a-real-run",
            "artifact_path": "model.cbm",
            "output_column": "pred",
        }
        with patch(
            "haute._mlflow_io.load_mlflow_model",
            side_effect=FileNotFoundError("run not found on tracking server"),
        ):
            with pytest.raises(Exception) as exc_info:  # noqa: PT011 - intentionally broad: testing error propagation from patched failure
                _model_score_columns(config)
            # Must NOT be swallowed — the caller needs to see something
            # mentioning the configuration or the failure origin.
            assert "run" in str(exc_info.value).lower() or "file" in str(exc_info.value).lower()

    def test_empty_source_type_is_passthrough(self) -> None:
        """An *unconfigured* node (blank sourceType) must still be OK —
        this is the dev-UX case of dragging an empty model-score node
        onto the canvas.  Silent passthrough here is correct.

        Post column-contract adoption the referenced side is ``set()``
        (not ``None``) when ``output_column`` is set: the runtime path
        is a passthrough that reads nothing, so ``set()`` is more
        honest than opaque.  The fully-blank config stays opaque on
        referenced — tested in the ``test_column_contracts_adoption``
        suite as ``test_model_score_unconfigured_referenced_is_opaque``.
        """
        from haute._builders import _model_score_columns

        config: dict[str, Any] = {"sourceType": "", "output_column": "pred"}
        produced, referenced = _model_score_columns(config)
        assert produced == {"pred"}
        assert referenced == set()

    def test_post_processing_code_keeps_referenced_opaque(self) -> None:
        """When the node has user post-processing code, ``referenced`` is
        opaque by design — the code may touch arbitrary columns.  Must not
        raise for this case either.
        """
        from haute._builders import _model_score_columns

        config = {
            "sourceType": "run",
            "run_id": "real_run",
            "artifact_path": "model.cbm",
            "output_column": "pred",
            "code": "df = df.with_columns(extra=pl.col('pred') * 2)",
        }
        # With code set, the early return path is taken — no MLflow load
        # attempted, no raise.
        produced, referenced = _model_score_columns(config)
        assert produced == {"pred"}
        assert referenced is None

    def test_empty_deploy_model_inputs_stay_opaque_without_mlflow(self) -> None:
        """A resolved bundled model with no feature metadata is opaque.

        The explicit deploy marker must prevent a fallback to the graph's
        obsolete external source even when it cannot provide concrete inputs.
        """
        from haute._builders import _model_score_columns
        from haute._contracts import _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY

        config = {
            "sourceType": "run",
            "run_id": "obsolete-remote-run",
            "artifact_path": "model.cbm",
            "output_column": "pred",
            _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: [],
        }
        with patch(
            "haute._mlflow_io.load_mlflow_model",
            side_effect=AssertionError("deploy contract must not contact MLflow"),
        ) as remote_loader:
            produced, referenced = _model_score_columns(config)

        assert produced == {"pred"}
        assert referenced is None
        remote_loader.assert_not_called()

    @pytest.mark.parametrize(
        "deploy_inputs",
        [
            "x",
            ["x", "x"],
            [""],
        ],
    )
    def test_invalid_internal_deploy_model_inputs_raise(
        self,
        deploy_inputs: object,
    ) -> None:
        """Malformed scorer annotations fail instead of reaching MLflow."""
        from haute._builders import _model_score_columns
        from haute._contracts import _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY

        config = {
            "sourceType": "run",
            "run_id": "obsolete-remote-run",
            "artifact_path": "model.cbm",
            "output_column": "pred",
            _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: deploy_inputs,
        }
        with pytest.raises(ConfigError, match="internal deploy model inputs"):
            _model_score_columns(config)


# ===========================================================================
# Item #27 — Artifact download corruption must not loop silently
# ===========================================================================


class TestArtifactLoadCorruptionRaises:
    """``load_mlflow_model`` deletes a cached file after a first failure
    and re-downloads.  If the re-downloaded file is ALSO corrupt the
    second exception must bubble up with diagnostic context — the current
    behaviour (which in the past silently looped) is a loud-error
    violation.
    """

    def test_second_corruption_raises_with_diagnostic(self, tmp_path: Path) -> None:
        """If both the first load AND the retry load fail, the second
        exception must propagate — no third attempt, no silent fallback.
        """

        corrupt_file = tmp_path / "corrupt.cbm"
        corrupt_file.write_bytes(b"garbage bytes")

        call_count = 0

        def _always_fails(path: str, task: str) -> Any:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"corrupt read attempt {call_count}")

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run_x", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=_always_fails,
            ),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                load_mlflow_model(
                    source_type="run",
                    run_id="run_x",
                    artifact_path="corrupt.cbm",
                    task="regression",
                )

        # Bounded retry: one retry only, total two attempts, then raise.
        assert call_count == 2, (
            f"Expected exactly two load attempts (1 fresh + 1 retry); saw {call_count}. "
            f"Unbounded retry masks persistent corruption."
        )
        # Diagnostic: the surfaced error must include SOMETHING pointing at
        # the cache path or the retry having been attempted so an operator
        # can act on it.
        err_text = str(exc_info.value)
        assert "corrupt" in err_text.lower()

    def test_retry_on_first_failure_succeeds_on_second(self, tmp_path: Path) -> None:
        """The bounded retry path still works for the single-corruption
        case — a fresh download on the retry recovers.  (This test pins the
        existing behaviour so the loud-error fix doesn't accidentally
        remove the retry altogether.)
        """
        corrupt_file = tmp_path / "corrupt.cbm"
        corrupt_file.write_bytes(b"first bad bytes")

        fake_model = MagicMock()
        fake_model.feature_names_ = ["a"]
        fake_model.get_cat_feature_indices.return_value = []

        call_count = 0

        def _fails_then_succeeds(path: str, task: str) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first load bad")
            return fake_model

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run_y", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=_fails_then_succeeds,
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run_y",
                artifact_path="corrupt.cbm",
                task="regression",
            )

        assert call_count == 2
        assert isinstance(result, ScoringModel)
        assert result.flavor == "catboost"

    def test_rustystats_corruption_also_bounded(self, tmp_path: Path) -> None:
        """Same rule for RustyStats: persistent corruption must raise after
        the first retry, not loop.
        """
        corrupt_file = tmp_path / "corrupt.rsglm"
        corrupt_file.write_bytes(b"garbage bytes")

        call_count = 0

        def _always_fails(path: str) -> Any:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"corrupt rustystats attempt {call_count}")

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run_rs", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_rustystats_model",
                side_effect=_always_fails,
            ),
        ):
            with pytest.raises(RuntimeError):
                load_mlflow_model(
                    source_type="run",
                    run_id="run_rs",
                    artifact_path="corrupt.rsglm",
                    task="regression",
                )

        assert call_count == 2, (
            f"RustyStats corruption must be bounded to two attempts; got {call_count}"
        )

    def test_persistently_corrupt_file_not_unlinked_forever(self, tmp_path: Path) -> None:
        """After the final failure the retry must surface a diagnostic so
        the operator can act.  Absence of such a diagnostic is itself a
        silent-failure regression.
        """
        corrupt_file = tmp_path / "corrupt.cbm"
        corrupt_file.write_bytes(b"garbage")

        def _always_fails(path: str, task: str) -> Any:
            raise RuntimeError("persistent corruption")

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run_p", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=_always_fails,
            ),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                load_mlflow_model(
                    source_type="run",
                    run_id="run_p",
                    artifact_path="corrupt.cbm",
                    task="regression",
                )

        # The propagated error must clearly mention corruption / retry so
        # an on-call engineer doesn't need to spelunk debug logs.
        err_msg = str(exc_info.value).lower()
        assert "corrupt" in err_msg or "retry" in err_msg or "persistent" in err_msg
