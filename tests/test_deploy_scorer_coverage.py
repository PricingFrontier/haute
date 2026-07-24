"""Coverage tests for haute.deploy._scorer internals.

Fills the gaps left by tests/test_deploy_internals.py and
tests/test_deploy_contract_integrity.py:

* ``_canonical_dtype`` — every dtype branch (Boolean / String / Int /
  Float / fallback). A wrong mapping here would silently pass or fail
  contract-drift detection in ``_assert_runtime_contract_matches``.
* ``_assert_runtime_contract_matches`` — the matching path for each
  canonical dtype, plus the MISSING-feature placeholder branch.
* score_graph builders: the modelScore contract-only short-circuit
  (no model bundled, just a contract) and the static dataInput remap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute.errors import FeatureMismatchError
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    build_contract,
    save_contract,
)
from tests.conftest import make_graph as _g

# ===========================================================================
# _canonical_dtype — every branch
# ===========================================================================


class TestCanonicalDtype:
    """Map polars dtypes to the canonical contract dtype string."""

    def test_boolean(self):
        from haute.deploy._scorer import _canonical_dtype

        assert _canonical_dtype(pl.Boolean) == "Boolean"

    @pytest.mark.parametrize("dtype", [pl.Utf8, pl.String, pl.Categorical])
    def test_string_family(self, dtype):
        from haute.deploy._scorer import _canonical_dtype

        assert _canonical_dtype(dtype) == "String"

    @pytest.mark.parametrize("dtype", [pl.Int8, pl.Int32, pl.Int64, pl.UInt16, pl.UInt64])
    def test_integer_family(self, dtype):
        from haute.deploy._scorer import _canonical_dtype

        assert _canonical_dtype(dtype) == "Int64"

    @pytest.mark.parametrize("dtype", [pl.Float32, pl.Float64])
    def test_float_family(self, dtype):
        from haute.deploy._scorer import _canonical_dtype

        assert _canonical_dtype(dtype) == "Float64"

    def test_fallback_returns_str_repr(self):
        """Unrecognised dtypes fall through to ``str(dtype)``."""
        from haute.deploy._scorer import _canonical_dtype

        result = _canonical_dtype(pl.Datetime)
        assert result == str(pl.Datetime)
        assert result not in ("Boolean", "String", "Int64", "Float64")


# ===========================================================================
# _assert_runtime_contract_matches — matching path per dtype + MISSING
# ===========================================================================


class TestAssertRuntimeContractMatches:
    """Drive each canonical-dtype branch through a *matching* contract so a
    wrong mapping would surface as a spurious FeatureMismatchError.
    """

    @staticmethod
    def _save(tmp_path, *, feature_types, categorical_features):
        contract = build_contract(
            features=list(feature_types),
            feature_types=feature_types,
            categorical_features=categorical_features,
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        path = tmp_path / CONTRACT_FILENAME
        save_contract(contract, path)
        return str(path)

    def test_boolean_int_float_string_all_match(self, tmp_path):
        """A live frame whose dtypes canonicalise to the contract types must
        pass — exercises the Boolean, Int, Float and String branches at once.
        """
        from haute.deploy._scorer import _assert_runtime_contract_matches

        path = self._save(
            tmp_path,
            feature_types={
                "flag": "Boolean",
                "count": "Int64",
                "amount": "Float64",
                "region": "String",
            },
            categorical_features=["region"],
        )

        lf = pl.DataFrame(
            {
                "flag": [True, False],
                "count": pl.Series([1, 2], dtype=pl.Int32),
                "amount": pl.Series([1.0, 2.0], dtype=pl.Float32),
                "region": ["north", "south"],
            }
        ).lazy()

        # Must not raise — the canonical mapping agrees with the contract.
        _assert_runtime_contract_matches(lf, path, "regression")

    def test_missing_feature_raises_with_placeholder(self, tmp_path):
        """A contract feature absent from the live schema is recorded as
        ``MISSING`` so the contract diff names the missing column and raises.
        """
        from haute.deploy._scorer import _assert_runtime_contract_matches

        path = self._save(
            tmp_path,
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
        )

        # ``region`` is absent at runtime → MISSING placeholder → mismatch.
        lf = pl.DataFrame({"age": [25, 30]}).lazy()

        with pytest.raises(FeatureMismatchError):
            _assert_runtime_contract_matches(lf, path, "regression")

    def test_dtype_drift_raises(self, tmp_path):
        """A live dtype that canonicalises differently from the contract
        (Boolean where Float was trained) raises FeatureMismatchError.
        """
        from haute.deploy._scorer import _assert_runtime_contract_matches

        path = self._save(
            tmp_path,
            feature_types={"amount": "Float64"},
            categorical_features=[],
        )

        lf = pl.DataFrame({"amount": [True, False]}).lazy()

        with pytest.raises(FeatureMismatchError):
            _assert_runtime_contract_matches(lf, path, "regression")


# ===========================================================================
# score_graph — modelScore contract-only short-circuit
# ===========================================================================


class TestScoreGraphModelScoreContractOnly:
    """When the model artifact is absent from the remap but a feature
    contract WAS bundled, score_graph validates the live schema against the
    contract (so drift errors stay precise) and then fails loudly: deploy
    scoring must never manufacture predictions without a real model artifact.
    """

    @staticmethod
    def _graph(output_columns=("age", "region")):
        return _g(
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
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": {
                                "outputMapping": [
                                    {
                                        "source_port": "ms",
                                        "source_column": col,
                                        "output_path": f"$[:].{col}",
                                        "enabled": True,
                                    }
                                    for col in output_columns
                                ],
                                "outputFormat": "json",
                            },
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

    def test_contract_only_match_raises_missing_model(self, tmp_path):
        """Contract bundled, model NOT bundled, contract matches → the schema
        check passes but scoring fails loudly because there is no model
        artifact to produce honest predictions.
        """
        from haute.deploy._scorer import score_graph

        contract = build_contract(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(contract, contract_path)

        live = pl.DataFrame({"age": [25, 30], "region": ["north", "south"]})

        # Only the contract is in the remap — no ``ms__model.cbm`` entry.
        with pytest.raises(RuntimeError, match="no bundled model artifact"):
            score_graph(
                graph=self._graph(),
                input_df=live,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_drift_raises(self, tmp_path):
        """Contract bundled, model NOT bundled, contract drifts → raise."""
        from haute.deploy._scorer import score_graph

        contract = build_contract(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(contract, contract_path)

        # region is Int64 at runtime instead of String.
        live = pl.DataFrame({"age": [25, 30], "region": [1, 2]})

        with pytest.raises(FeatureMismatchError):
            score_graph(
                graph=self._graph(),
                input_df=live,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_model_present_and_contract_match(self, tmp_path):
        """Both model and contract bundled, contract matches → the model
        score path runs the contract check (line 352->353) then loads.
        """
        import numpy as np

        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")

        contract = build_contract(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(contract, contract_path)

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x"]
        mock_model.predict.return_value = np.array([42.0])

        live = pl.DataFrame({"x": [1.0]})
        remap = {
            "ms__model.cbm": str(cbm_path),
            f"ms__{CONTRACT_FILENAME}": str(contract_path),
        }

        with patch("haute._mlflow_io._load_catboost_model", return_value=mock_model):
            result = score_graph(
                graph=self._graph(output_columns=("x", "prediction")),
                input_df=live,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert isinstance(result, pl.DataFrame)


# ===========================================================================
# score_graph — static dataInput remap
# ===========================================================================


class TestScoreGraphStaticDataSourceRemap:
    """A static dataInput (non-apiInput) node with a remapped artifact path
    reads from the local bundled file at runtime.
    """

    def test_static_source_reads_remapped_path(self, tmp_path):
        from haute.deploy._scorer import score_graph

        # Bundled static parquet at a local path.
        ds_path = tmp_path / "factors.parquet"
        pl.DataFrame({"area": ["A", "B"], "factor": [1.1, 1.2]}).write_parquet(ds_path)

        graph = _g(
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
                        "id": "static_ds",
                        "data": {
                            "label": "static_ds",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "cacheMode": "direct",
                                "path": "original/factors.parquet",
                                "arguments": {},
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": {
                                "outputMapping": [
                                    {
                                        "source_port": "static_ds",
                                        "source_column": col,
                                        "output_path": f"$[:].{col}",
                                        "enabled": True,
                                    }
                                    for col in ("area", "factor")
                                ],
                                "outputFormat": "json",
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "static_ds",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "static_ds", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"static_ds__factors.parquet": str(ds_path)}

        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths=remap,
        )

        # The output node sees the static source's rows (the dataInput
        # intercept replaced the file read with the remapped local path).
        assert isinstance(result, pl.DataFrame)
        assert set(result.columns) >= {"area", "factor"}
        assert sorted(result["area"].to_list()) == ["A", "B"]
