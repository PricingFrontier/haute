"""``score_graph`` remapping of optimiser and model artifacts.

Optimiser Apply and Model Score nodes read their bundled artifacts when a deployed graph scores,
bundled models contribute their contract inputs, ``_remap_artifact`` rewrites artifact paths and
the temporary model-score copies are cleaned up.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from tests._deploy_internals_helpers import (
    _single_frame_api_input_config,
)
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ===========================================================================
# 3. _scorer.py — score_graph intercepts
# ===========================================================================


class TestScoreGraphOptimiserApplyRemap:
    """Tests for optimiserApply remapping in score_graph."""

    def test_optimiser_apply_file_remap(self, tmp_path):
        """optimiserApply with remapped artifact path loads from local file."""
        from haute.deploy._scorer import score_graph

        # Write a fake artifact
        artifact_path = tmp_path / "opt_artifact.json"
        artifact_path.write_text(json.dumps({"type": "banding", "data": {}}))

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "source_type": "file",
                                "artifact_path": "artifacts/opt_artifact.json",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "selected_factor", "__opt_v__"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"opt__opt_artifact.json": str(artifact_path)}

        mock_artifact = MagicMock()
        mock_dispatch_result = pl.DataFrame(
            {"x": [1.0], "selected_factor": [1.1], "__opt_v__": ["v1"]}
        ).lazy()

        with (
            patch("haute._optimiser_io.load_optimiser_artifact", return_value=mock_artifact),
            patch(
                "haute._builders._dispatch_apply",
                return_value=mock_dispatch_result,
            ) as mock_dispatch,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert isinstance(result, pl.DataFrame)
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[3] == "selected_factor"

    def test_online_apply_file_remap_plans_from_the_bundled_artifact(self, tmp_path):
        """Projection reads the columns of the artifact the deployed apply serves.

        The original artifact still exists but maximises ``income``; the bundle
        maximises ``margin``. Planning from the original would drop ``margin``
        before the served apply reads it.
        """
        from haute.deploy._scorer import score_graph

        def artifact(objective: str) -> dict[str, object]:
            return {
                "version": f"{objective}_v1",
                "mode": "online",
                "lambdas": {},
                "objective": objective,
                "constraints": {},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
            }

        original = tmp_path / "opt.json"
        original.write_text(json.dumps(artifact("income")), encoding="utf-8")
        bundled = tmp_path / "bundled_opt.json"
        bundled.write_text(json.dumps(artifact("margin")), encoding="utf-8")
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("quote_id", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {"source_type": "file", "artifact_path": str(original)},
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["quote_id", "optimal_scenario_value"]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "opt", "sourceHandle": "src"},
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        result = score_graph(
            graph=graph,
            input_df=pl.DataFrame(
                {
                    "quote_id": ["q1", "q1", "q2", "q2"],
                    "scenario_index": [0, 1, 0, 1],
                    "scenario_value": [0.9, 1.1, 0.9, 1.1],
                    "income": [10.0, 5.0, 10.0, 5.0],
                    "margin": [1.0, 9.0, 1.0, 9.0],
                }
            ),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"opt__opt.json": str(bundled)},
        )

        assert result.sort("quote_id")["optimal_scenario_value"].to_list() == pytest.approx(
            [1.1, 1.1]
        )

    def _ratebook_apply_remap_score(self, tmp_path, *, bundled: bool = True):
        """Score a deployed ratebook-apply graph and return the result frame: its artifact
        bundled, or, with *bundled* False, read from its configured path."""
        from haute.deploy._scorer import score_graph

        artifact_path = tmp_path / "ratebook.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "version": "rb_v1",
                    "mode": "ratebook",
                    "factor_tables": {
                        "region_band": [
                            {
                                "__factor_group__": "South",
                                "optimal_scenario_value": 0.95,
                            },
                            {
                                "__factor_group__": "North",
                                "optimal_scenario_value": 1.10,
                            },
                        ]
                    },
                    # North's 1.10 lies past the collar; the scorer must clip it.
                    "combined_factor_bounds": {"min": 0.9, "max": 1.05},
                    "factor_dtypes": {
                        "region_band": [
                            {
                                "column": "region_band",
                                "dtype": {"kind": "String"},
                            }
                        ]
                    },
                }
            )
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("quote_id", label="src"),
                        },
                    },
                    {
                        "id": "band",
                        "data": {
                            "label": "band",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "banding": "categorical",
                                        "column": "region",
                                        "output_column": "region_band",
                                        "rules": {
                                            "London": "South",
                                            "Manchester": "North",
                                        },
                                    }
                                ]
                            },
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "source_type": "file",
                                "artifact_path": (
                                    "artifacts/ratebook.json" if bundled else str(artifact_path)
                                ),
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                                "ratebook_input": "band",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(
                                ["quote_id", "region_band", "selected_factor", "__opt_v__"]
                            ),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {
                        "id": "e2",
                        "source": "src",
                        "target": "band",
                        "sourceHandle": "src",
                    },
                    {"id": "e3", "source": "band", "target": "opt"},
                    {"id": "e4", "source": "opt", "target": "out"},
                ],
            }
        )

        return score_graph(
            graph=graph,
            input_df=pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                }
            ),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths=(
                {"opt__ratebook.json": str(artifact_path)}
                if bundled
                else {"other__model.cbm": str(tmp_path / "model.cbm")}
            ),
        )

    def test_an_unbundled_file_apply_is_the_builders_reading_its_configured_path(
        self, tmp_path, _widen_sandbox_root
    ):
        """With no bundled copy, the Optimiser Apply builder runs: it reads the configured
        artifact and applies it, never passing the quotes through unpriced."""
        result = self._ratebook_apply_remap_score(tmp_path, bundled=False)
        assert result["selected_factor"].to_list() == [0.95, 1.05]

    def test_ratebook_apply_file_remap_routes_through_configured_input(self, tmp_path):
        """The configured ``ratebook_input`` is used for the factor-table lookup."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["region_band"].to_list() == ["South", "North"]

    def test_ratebook_apply_file_remap_renames_to_configured_value_column(self, tmp_path):
        """The collared optimised value lands in the user-configured column name."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["selected_factor"].to_list() == [0.95, 1.05]

    def test_ratebook_apply_file_remap_emits_artifact_version_column(self, tmp_path):
        """The configured version column carries the artifact version per quote."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["__opt_v__"].to_list() == ["rb_v1", "rb_v1"]

    def test_optimiser_apply_mlflow_source(self):
        """optimiserApply with MLflow source downloads at runtime."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "source_type": "run",
                                "run_id": "run_abc",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "selected_factor", "__opt_v__"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        mock_artifact = MagicMock()
        mock_dispatch_result = pl.DataFrame(
            {"x": [1.0], "selected_factor": [1.1], "__opt_v__": ["v1"]}
        ).lazy()

        with (
            patch("haute._optimiser_io.load_mlflow_optimiser_artifact", return_value=mock_artifact),
            patch(
                "haute._builders._dispatch_apply",
                return_value=mock_dispatch_result,
            ) as mock_dispatch,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert isinstance(result, pl.DataFrame)
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[3] == "selected_factor"

    def test_optimiser_apply_mlflow_forwards_destination(self):
        """The node's ``mlflow_destination`` reaches the request-time loader."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "source_type": "run",
                                "run_id": "run_abc",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                                "mlflow_destination": "server",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "selected_factor", "__opt_v__"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        mock_dispatch_result = pl.DataFrame(
            {"x": [1.0], "selected_factor": [1.1], "__opt_v__": ["v1"]}
        ).lazy()

        with (
            patch(
                "haute._optimiser_io.load_mlflow_optimiser_artifact",
                return_value=MagicMock(),
            ) as mock_load,
            patch("haute._builders._dispatch_apply", return_value=mock_dispatch_result),
        ):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert mock_load.call_args.kwargs["destination"] == "server"


class TestScoreGraphModelScoreRemap:
    """Tests for modelScore remapping in score_graph."""

    def test_model_score_remap_loads_bundled_model(self, tmp_path):
        """modelScore with remap loads from bundled local path."""
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x"]
        mock_model.get_metadata.return_value = {"haute_offset_column": ""}
        mock_model.predict.return_value = np.array([42.0])

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
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

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"ms__model.cbm": str(cbm_path)}

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("bundled scoring must not contact MLflow"),
            ) as remote_loader,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert "pred" in result.columns
        remote_loader.assert_not_called()

    def test_model_score_remap_with_output_fields_uses_bundled_contract(
        self,
        tmp_path,
    ):
        """output_fields projection must not make deployed scoring call MLflow."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
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

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x"]
        mock_model.get_metadata.return_value = {"haute_offset_column": ""}
        mock_model.predict.return_value = np.array([42.0])

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": "remote-run",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                                "code": "df = df",
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

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("MLflow should not be called"),
            ) as load_mlflow,
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "unused": [999.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
                output_fields=["pred"],
            )

        load_mlflow.assert_not_called()
        assert result.columns == ["pred"]
        assert result["pred"].to_list() == [42.0]

    def test_remapped_model_score_scores_a_category_the_model_never_saw(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        mock_model = MagicMock()
        mock_model.feature_names_ = ["region"]
        mock_model.get_metadata.return_value = {"haute_offset_column": ""}
        mock_model.get_cat_feature_indices.return_value = [0]
        mock_model.predict.return_value = np.array([42.0])
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("region", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": "remote-run",
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

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["west"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

        assert result["pred"].to_list() == [42.0]

    def test_contract_only_model_score_accepts_features_in_any_order(
        self,
        tmp_path,
    ):
        """Scoring selects features by name, so a reordered request passes the contract."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["age", "region"],
                feature_types={"age": "Int64", "region": "String"},
                categorical_features=["region"],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                label="src",
                                tables=[
                                    {
                                        "path": "$[:]",
                                        "label": "src",
                                        "emit": True,
                                        "columns": [
                                            {
                                                "name": "region",
                                                "path": "$[:].region",
                                                "type": "str",
                                                "selected": True,
                                            },
                                            {
                                                "name": "age",
                                                "path": "$[:].age",
                                                "type": "int",
                                                "selected": True,
                                            },
                                        ],
                                    }
                                ],
                            ),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
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
                            "config": make_output_config([]),
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

        # The contract matched (region before age is fine); only the missing model stops it.
        with pytest.raises(RuntimeError, match="no bundled model artifact"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["north"], "age": [30]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_fails_for_its_missing_model_not_for_a_category(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        source_config = _single_frame_api_input_config("region", label="src")
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {"label": "src", "nodeType": "apiInput", "config": source_config},
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
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
                            "config": make_output_config([]),
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

        with pytest.raises(RuntimeError, match="no bundled model artifact"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["west"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_multi_row_model_score_scores_in_memory(self, tmp_path):
        """Multi-row deploy modelScore should score eagerly in memory."""
        from haute._mlflow_io import ScoringModel
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        scoring_model = ScoringModel(
            model=MagicMock(),
            feature_names=["x"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
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

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("bundled scoring must not contact MLflow"),
            ) as remote_loader,
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert captured["source"] == "live"
        remote_loader.assert_not_called()

    def test_single_row_model_score_keeps_live_source(self, tmp_path):
        """Single-row deploy modelScore should keep the eager live scorer path."""
        from haute._mlflow_io import ScoringModel
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        scoring_model = ScoringModel(
            model=MagicMock(),
            feature_names=["x"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
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

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("bundled scoring must not contact MLflow"),
            ) as remote_loader,
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0]
        assert captured["source"] == "live"
        remote_loader.assert_not_called()

    def test_model_score_remap_forwards_required_output_columns(self, tmp_path):
        """Bundled deploy scoring should honour projection demand from output_fields."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
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
        scoring_model = MagicMock(offset_column=None, offset_link=None)
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0], "unused": [999.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
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

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "unused": [999.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
                output_fields=["pred"],
            )

        assert result.columns == ["pred"]
        assert captured["required_output_columns"] == {"pred"}
        assert captured["code"] == ""

    def test_model_score_remap_forwards_secondary_inputs(self, tmp_path):
        """Bundled deploy modelScore must preserve extra parent frames for user code."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
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
        scoring_model = MagicMock(offset_column=None, offset_link=None)
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "aux",
                        "data": {
                            "label": "aux",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="aux"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                                "code": "df = src",
                                "contract": {
                                    "inputs": ["x"],
                                    "outputs": ["pred"],
                                },
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
                    {
                        "id": "e2",
                        "source": "aux",
                        "target": "ms",
                        "sourceHandle": "aux",
                    },
                    {"id": "e3", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src", "aux"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

        extra_dfs = captured["extra_dfs"]
        assert isinstance(extra_dfs, tuple)
        assert len(extra_dfs) == 1

    def test_multi_row_unbundled_model_score_scores_in_memory(self):
        """Configured non-bundled deploy modelScore scores eagerly in memory."""
        from haute.deploy._scorer import score_graph

        scoring_model = MagicMock(offset_column=None, offset_link=None, run_contract_path=None)
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": "remote-run",
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

        with (
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert captured["source"] == "live"

    def test_multi_row_model_score_writes_no_temp_parquet(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A multi-row score_graph call never disk-batches modelScore via _sink_to_temp."""
        from haute._mlflow_io import ScoringModel
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        raw_model = MagicMock()
        scoring_model = ScoringModel(
            model=raw_model,
            feature_names=["x"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )

        def fake_run_score_pipeline(*_args, **kwargs):
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        def fail_sink_to_temp(*_args, **_kwargs):
            raise AssertionError("deploy scoring must never disk-batch via _sink_to_temp")

        monkeypatch.setattr("haute._model_scorer._sink_to_temp", fail_sink_to_temp)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
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

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("bundled scoring must not contact MLflow"),
            ) as remote_loader,
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert not list(tmp_path.glob("haute_score_in_*"))
        assert not list(tmp_path.glob("haute_score_out_*"))
        remote_loader.assert_not_called()

    def test_multi_row_score_graph_passes_no_dataframe_cache_request(self, tmp_path):
        """A multi-row score_graph call never materialises through the dataframe cache."""
        from haute.deploy import _scorer
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
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
                    },
                ],
            }
        )

        with patch.object(
            _scorer,
            "execute_lazy_graph",
            wraps=_scorer.execute_lazy_graph,
        ) as execute:
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert result["x"].to_list() == [1.0, 2.0]
        assert execute.call_args.kwargs.get("dataframe_cache_request") is None


class TestBundledModelContractInputs:
    """Tests for deploy-only contract metadata derived from bundled models."""

    @staticmethod
    def _graph():
        return _g(
            {
                "nodes": [
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": "obsolete-run",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    }
                ],
                "edges": [],
            }
        )

    def test_unmatched_remap_leaves_graph_unchanged(self):
        from haute.deploy._scorer import _attach_bundled_model_contract_inputs

        graph = self._graph()

        result = _attach_bundled_model_contract_inputs(
            graph,
            {"other__model.cbm": "unused.cbm"},
            {"ms"},
        )

        assert result is graph

    @pytest.mark.parametrize(
        ("feature_names", "offset_column", "message"),
        [
            ("x", None, "invalid feature or offset metadata"),
            (None, None, "non-iterable feature metadata"),
            (["x", "x"], None, "invalid feature or offset metadata"),
            (["x"], object(), "invalid feature or offset metadata"),
        ],
    )
    def test_invalid_bundled_metadata_fails_loud(
        self,
        feature_names,
        offset_column,
        message,
    ):
        from haute.deploy._scorer import _attach_bundled_model_contract_inputs
        from haute.errors import DeployError

        scoring_model = MagicMock(
            feature_names=feature_names,
            offset_column=offset_column,
        )

        with (
            patch(
                "haute.deploy._scorer.load_local_model_cached",
                return_value=scoring_model,
            ),
            pytest.raises(DeployError, match=message),
        ):
            _attach_bundled_model_contract_inputs(
                self._graph(),
                {"ms__model.cbm": "model.cbm"},
                {"ms"},
            )

    def test_model_offset_is_an_optional_deploy_input(self):
        """A request without the offset is priced for a full policy term."""
        from haute._contracts import (
            _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY,
            _DEPLOY_MODEL_OPTIONAL_INPUT_COLUMNS_CONFIG_KEY,
        )
        from haute.deploy._scorer import _attach_bundled_model_contract_inputs

        def annotated(link: str) -> dict:
            scoring_model = MagicMock(
                feature_names=["x"], offset_column="exposure", offset_link=link
            )
            with patch(
                "haute.deploy._scorer.load_local_model_cached",
                return_value=scoring_model,
            ):
                result = _attach_bundled_model_contract_inputs(
                    self._graph(),
                    {"ms__model.cbm": "model.cbm"},
                    {"ms"},
                )
            return result.node_map["ms"].data.config

        exposure = annotated("log")
        assert exposure[_DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY] == ["x"]
        assert exposure[_DEPLOY_MODEL_OPTIONAL_INPUT_COLUMNS_CONFIG_KEY] == ["exposure"]
        # An additive offset is a base premium, a required input.
        premium = annotated("identity")
        assert premium[_DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY] == ["x", "exposure"]
        assert premium[_DEPLOY_MODEL_OPTIONAL_INPUT_COLUMNS_CONFIG_KEY] == []


class TestRemapArtifact:
    """Tests for the _remap_artifact helper function."""

    def test_remap_found(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"node1__model.pkl": "/local/model.pkl"}
        result = _remap_artifact("node1", {"path": "remote/model.pkl"}, remap, "path")
        assert result == "/local/model.pkl"

    def test_remap_not_found(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"other__model.pkl": "/local/model.pkl"}
        result = _remap_artifact("node1", {"path": "remote/model.pkl"}, remap, "path")
        assert result is None

    def test_remap_empty_path(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"node1__": "/local/empty"}
        result = _remap_artifact("node1", {"path": ""}, remap, "path")
        assert result == "/local/empty"

    def test_remap_missing_key_field(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {}
        result = _remap_artifact("node1", {}, remap, "path")
        assert result is None


class TestDeployModelScoreTempCleanup:
    def test_cleanup_preserves_primary_error_when_unlink_fails(self, monkeypatch):
        from haute.deploy._scorer import _cleanup_model_score_temp_paths

        def fail_cleanup(_paths):
            raise PermissionError("locked")

        monkeypatch.setattr(
            "haute._model_scorer._cleanup_registered_temp_files",
            fail_cleanup,
        )

        _cleanup_model_score_temp_paths(["locked.parquet"], preserve_primary_error=True)

        with pytest.raises(PermissionError, match="locked"):
            _cleanup_model_score_temp_paths(
                ["locked.parquet"],
                preserve_primary_error=False,
            )
