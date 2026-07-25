"""Served-price integrity: validate-time scoring must match serve-time scoring.

Regression tests for the deploy identity cluster (C0.3):

* **F055** — the test-before-live gate (``score_test_quotes``) and the
  output-schema dry-run (``infer_output_schema``) must score with the exact
  bundled ``artifact_paths`` the container serves, not a divergent live lookup.
* **F056** — ``infer_output_schema``'s cache key must fold in the served
  artifact identity so a model retrained in place (byte-identical graph config)
  busts the stale schema instead of baking it into the manifest.
* **F138** — a misconfigured ``modelScore`` node must NOT deploy as a silent
  identity passthrough; the deploy scorer fails loud instead.
* **F564** — parity: the artifacts loaded at validate time are byte-identical
  to the bundled container artifacts, and a ``modelScore`` with no bundled
  model raises at validate time rather than serving unscored inputs.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import polars as pl
import pytest

from haute.deploy._config import DeployConfig
from haute.errors import DeployError
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    build_contract,
    save_contract,
)
from tests._deploy_helpers import FIXTURE_DIR
from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _model_score_graph(config: dict, *, output_column: str = "x"):
    """apiInput ``src`` → modelScore ``ms`` → output ``out`` (maps ``ms.<col>``)."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "ms",
                    "data": {"label": "ms", "nodeType": "modelScore", "config": config},
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config([output_column], source_port="ms"),
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


def _passthrough_schema_graph(parquet_path):
    """apiInput ``src`` (reads ``parquet_path``) → output ``out``."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": {"path": str(parquet_path)},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config([]),
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


# ---------------------------------------------------------------------------
# F056 — artifact identity fingerprint helper + cache key
# ---------------------------------------------------------------------------


class TestArtifactIdentityFingerprint:
    def test_empty_returns_blank(self):
        from haute.deploy._scorer import artifact_identity_fingerprint

        assert artifact_identity_fingerprint(None) == ""
        assert artifact_identity_fingerprint({}) == ""

    def test_changes_when_artifact_bytes_change(self, tmp_path):
        from haute.deploy._scorer import artifact_identity_fingerprint

        art = tmp_path / "model.cbm"
        art.write_bytes(b"v1")
        fp1 = artifact_identity_fingerprint({"ms__model.cbm": str(art)})

        art.write_bytes(b"a much longer set of model bytes")
        fp2 = artifact_identity_fingerprint({"ms__model.cbm": str(art)})

        assert fp1 and fp2
        assert fp1 != fp2

    def test_stable_when_unchanged(self, tmp_path):
        from haute.deploy._scorer import artifact_identity_fingerprint

        art = tmp_path / "model.cbm"
        art.write_bytes(b"stable")
        paths = {"ms__model.cbm": str(art)}
        assert artifact_identity_fingerprint(paths) == artifact_identity_fingerprint(paths)


class TestOutputSchemaCacheFoldsArtifactIdentity:
    """F056: retraining a model in place must bust the output-schema cache."""

    def test_cache_busts_on_artifact_change(self, tmp_path, monkeypatch):
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)
        pq = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq)
        graph = _passthrough_schema_graph(pq)

        art = tmp_path / "model.cbm"
        art.write_bytes(b"regression-v1")
        artifact_paths = {"ms__model.cbm": str(art)}

        # First run: an old (regression) model produces a Float64 prediction.
        with patch(
            "haute.deploy._scorer.score_graph",
            return_value=pl.DataFrame({"prediction": [1.0]}),
        ) as first:
            r1 = infer_output_schema(graph, "out", ["src"], artifact_paths=artifact_paths)
        first.assert_called_once()
        assert r1 == {"prediction": "Float64"}

        # Model retrained in place under the SAME config — new classification
        # model emits an Int64 label.  The graph fingerprint is byte-identical;
        # only the artifact bytes changed.
        art.write_bytes(b"classification-v2 with different bytes")
        with patch(
            "haute.deploy._scorer.score_graph",
            return_value=pl.DataFrame({"prediction": [1]}),
        ) as second:
            r2 = infer_output_schema(graph, "out", ["src"], artifact_paths=artifact_paths)

        # The dry-run MUST re-run (cache busted) and observe the new dtype.
        second.assert_called_once()
        assert r2 == {"prediction": "Int64"}

    def test_no_artifacts_keeps_cache_key_stable(self, tmp_path, monkeypatch):
        """No bundled artifacts → key unchanged from the bare graph fingerprint."""
        from haute._cache import graph_fingerprint
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)
        pq = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq)
        graph = _passthrough_schema_graph(pq)

        # Pre-seed the cache under the plain graph fingerprint (no artifacts).
        fp = graph_fingerprint(graph, "out", "src")
        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        (cache_dir / "output_schema.json").write_text(
            json.dumps({"fingerprint": fp, "schema": {"premium": "Float64"}})
        )

        with patch("haute.deploy._scorer.score_graph") as score:
            result = infer_output_schema(graph, "out", ["src"], artifact_paths={})

        score.assert_not_called()  # cache hit — key unchanged
        assert result == {"premium": "Float64"}


# ---------------------------------------------------------------------------
# F138 — misconfigured modelScore must not serve as a silent passthrough
# ---------------------------------------------------------------------------


class TestModelScorePassthroughRejectedAtDeploy:
    @pytest.mark.parametrize(
        "config",
        [
            {"sourceType": "", "output_column": "pred"},
            {"output_column": "pred"},
            {"sourceType": "run", "run_id": "", "output_column": "pred"},
            {"sourceType": "registered", "registered_model": "", "output_column": "pred"},
        ],
    )
    def test_unconfigured_model_score_raises(self, config):
        from haute.deploy._scorer import score_graph

        graph = _model_score_graph(config)
        with pytest.raises(DeployError, match="passthrough"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )

    def test_configured_run_source_does_not_trip_guard(self):
        """A node with a valid run source is NOT a passthrough — guard stays off.

        It falls through to the base builder (live MLflow load).  We only need
        to prove the deploy guard does not fire; the MLflow load itself is
        expected to fail loudly for a bogus run, and that error is emphatically
        NOT the passthrough DeployError.
        """
        from haute.deploy._scorer import score_graph

        graph = _model_score_graph(
            {
                "sourceType": "run",
                "run_id": "does-not-exist",
                "artifact_path": "model.cbm",
                "output_column": "pred",
            }
        )
        with pytest.raises(Exception) as exc_info:  # noqa: PT011 - asserting NOT-a-passthrough
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )
        assert "silent identity passthrough" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# F055 / F564 — validate-time scoring uses the pinned bundle, byte-identically
# ---------------------------------------------------------------------------


def _resolved_with(graph, quotes_dir, artifacts):
    config = DeployConfig(
        pipeline_file=PIPELINE_FILE,
        model_name="test-model",
        test_quotes_dir=quotes_dir,
    )
    return _make_resolved(
        config=config,
        pruned_graph=graph,
        input_node_ids=["src"],
        output_node_id="out",
        artifacts=artifacts,
    )


class TestScoreTestQuotesThreadsArtifacts:
    def test_forwards_bundled_artifacts_byte_identical(self, tmp_path):
        """F055/F564: score_test_quotes passes the exact bundled artifact paths.

        The forwarded mapping must equal ``{name: str(path)}`` of
        ``resolved.artifacts`` — the same source ``_container.py`` builds
        ``_artifact_paths`` from — so validate and serve load identical bytes.
        """
        from haute.deploy._validators import score_test_quotes

        quotes = tmp_path / "quotes"
        quotes.mkdir()
        (quotes / "a.json").write_text(json.dumps([{"x": 1.0}]))

        model = tmp_path / "model.cbm"
        model.write_bytes(b"model-bytes")
        contract = tmp_path / CONTRACT_FILENAME
        contract.write_bytes(b"{}")
        artifacts = {"ms__model.cbm": model, f"ms__{CONTRACT_FILENAME}": contract}

        graph = _model_score_graph(
            {
                "sourceType": "run",
                "run_id": "r1",
                "artifact_path": "model.cbm",
                "output_column": "x",
            }
        )
        resolved = _resolved_with(graph, quotes, artifacts)

        captured: dict[str, object] = {}

        def fake_score_graph(**kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"x": [1.0]})

        with patch("haute.deploy._validators.score_graph", side_effect=fake_score_graph):
            score_test_quotes(resolved)

        assert captured["artifact_paths"] == {
            "ms__model.cbm": str(model),
            f"ms__{CONTRACT_FILENAME}": str(contract),
        }

    def test_validate_gate_rejects_contract_only_model_score(self, tmp_path):
        """F564: a bundled contract but NO bundled model must fail at validate.

        Before the artifacts were threaded, the misconfigured node scored as a
        silent passthrough (``status == 'ok'``).  Now the pinned contract is
        exercised and the missing-model failure surfaces at the gate.
        """
        from haute.deploy._validators import score_test_quotes

        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            ),
            tmp_path / CONTRACT_FILENAME,
        )
        quotes = tmp_path / "quotes"
        quotes.mkdir()
        (quotes / "a.json").write_text(json.dumps([{"x": 1.0}]))

        graph = _model_score_graph({"sourceType": "", "output_column": "x"})
        resolved = _resolved_with(
            graph,
            quotes,
            {f"ms__{CONTRACT_FILENAME}": tmp_path / CONTRACT_FILENAME},
        )

        results = score_test_quotes(resolved)

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "no bundled model artifact" in results[0]["error"]

    def test_validate_gate_enforces_bundled_contract_drift(self, tmp_path):
        """F055/F564: a drifting quote is blocked at validate, as at serve.

        The bundled contract declares ``x: Float64``; the quote supplies an
        Int64 ``x``.  With the pinned contract threaded in, the gate raises
        FeatureMismatch — the same error the container would raise on drift.
        """
        from haute.deploy._validators import score_test_quotes

        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            ),
            tmp_path / CONTRACT_FILENAME,
        )
        quotes = tmp_path / "quotes"
        quotes.mkdir()
        # Int64 x — drifts from the contract's Float64.
        (quotes / "a.json").write_text(json.dumps([{"x": 1}]))

        graph = _model_score_graph({"sourceType": "", "output_column": "x"})
        resolved = _resolved_with(
            graph,
            quotes,
            {f"ms__{CONTRACT_FILENAME}": tmp_path / CONTRACT_FILENAME},
        )

        results = score_test_quotes(resolved)

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "contract mismatch" in results[0]["error"].lower()
