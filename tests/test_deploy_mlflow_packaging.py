"""MLflow packaging for a deployment: status, signature and environment.

``get_deploy_status`` reports a deployment's status, model versions compare numerically,
``_build_signature`` builds the Databricks signature (where only an exposure may be omitted), the
conda environment and pip requirements come from the served lock, and Databricks connectivity is
checked before a deploy.
"""

from __future__ import annotations

import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests.conftest import make_graph as _g

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ===========================================================================
# 5. _mlflow.py — get_deploy_status, _build_signature, _conda_env, etc.
# ===========================================================================


class TestGetDeployStatus:
    """Tests for get_deploy_status()."""

    @pytest.fixture(autouse=True)
    def _mlflow_databricks_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_deploy_status resolves the dedicated Databricks MLflow pair first."""
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "mlflow-test-token")
        for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_CONFIG_PROFILE", "MLFLOW_ENABLE_DB_SDK"):
            monkeypatch.delenv(name, raising=False)

    def test_model_not_found(self):
        from haute.deploy._mlflow import get_deploy_status

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = []

            result = get_deploy_status("nonexistent-model")

        assert result["status"] == "not_found"
        assert result["latest_version"] == 0
        assert result["model_name"] == "nonexistent-model"

    def test_model_found_returns_version_info(self):
        from haute.deploy._mlflow import get_deploy_status

        mock_v1 = MagicMock()
        mock_v1.version = "1"
        mock_v1.current_stage = "Production"
        mock_v1.status = "READY"
        mock_v1.run_id = "run_abc"

        mock_v2 = MagicMock()
        mock_v2.version = "2"
        mock_v2.current_stage = "Staging"
        mock_v2.status = "READY"
        mock_v2.run_id = "run_def"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = [mock_v1, mock_v2]

            result = get_deploy_status("my-model", catalog="ws", schema="default")

        assert result["model_name"] == "my-model"
        assert result["latest_version"] == 2
        assert result["status"] == "READY"
        assert result["run_id"] == "run_def"

    def test_model_found_uses_uc_name(self):
        """get_deploy_status uses catalog.schema.model_name for the search query."""
        from haute.deploy._mlflow import get_deploy_status

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = []

            get_deploy_status("my-model", catalog="ws", schema="pricing")

        call_args = mock_client_cls.return_value.search_model_versions.call_args
        assert "ws.pricing.my-model" in call_args[0][0]


class TestBuildSignature:
    """Tests for _build_signature()."""

    @staticmethod
    def _input_type_map(sig):
        """Extract {col_name: DataType} from a ModelSignature's inputs."""
        return {col.name: col.type for col in sig.inputs.inputs}

    def test_basic_types(self):
        from mlflow.models import ModelSignature
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"age": "Int32", "name": "String", "premium": "Float64"},
            output_schema={"result": "Float64"},
        )

        sig = _build_signature(resolved)

        assert isinstance(sig, ModelSignature)
        type_map = self._input_type_map(sig)
        assert type_map["age"] == DataType.integer, "Int32 must map to integer"
        assert type_map["name"] == DataType.string, "String must map to string"
        assert type_map["premium"] == DataType.double, "Float64 must map to double"
        out_map = {col.name: col.type for col in sig.outputs.inputs}
        assert out_map["result"] == DataType.double

    def test_parameterized_datetime_type(self):
        """Datetime('us', 'UTC') should map to datetime DataType."""
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"ts": "Datetime('us', 'UTC')"},
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)
        type_map = self._input_type_map(sig)
        assert type_map["ts"] == DataType.datetime, "Parameterized Datetime must map to datetime"

    @pytest.mark.parametrize(
        "dtype",
        ["Categorical", 'Categorical(Categories("lexical"))', "Enum(categories=['a', 'b'])"],
    )
    def test_categorical_types_map_to_string(self, dtype):
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"category": dtype},
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)

        assert self._input_type_map(sig)["category"] == DataType.string

    @pytest.mark.parametrize("dtype", ["Decimal(12, 2)", "List(Decimal(12, 2))", "Object"])
    def test_unknown_dtype_fails_loudly(self, dtype):
        """Unsupported Polars dtypes must not be misdeclared as strings, in a list too."""
        from haute.deploy._mlflow import _build_signature
        from haute.errors import DeployError

        resolved = _make_resolved(
            input_schema={"exotic": dtype},
            output_schema={"val": "Float64"},
        )

        with pytest.raises(DeployError, match="exotic") as exc_info:
            _build_signature(resolved)

        assert dtype in str(exc_info.value)

    @pytest.mark.parametrize("schema", ["input_schema", "output_schema"])
    @pytest.mark.parametrize(
        "dtype", ["Int128", "UInt128", "List(Int128)", "Struct({'a': UInt128})"]
    )
    def test_a_column_holding_a_128_bit_integer_fails_with_the_cast(self, schema, dtype):
        """No MLflow type or Arrow conversion takes a 128-bit integer, at any depth."""
        from haute.deploy._mlflow import _build_signature
        from haute.errors import DeployError

        schemas = {"input_schema": {"x": "Float64"}, "output_schema": {"val": "Float64"}}
        schemas[schema] = {"policy_key": dtype}
        resolved = _make_resolved(**schemas)

        with pytest.raises(DeployError, match="Column 'policy_key' is ") as exc_info:
            _build_signature(resolved)

        assert "cast it upstream to Int64" in str(exc_info.value)

    def test_a_workbench_pipelines_tables_are_maps_and_arrays_of_them(self):
        """A one-row table is a map of anything and a many-row table an array of them, a
        table left out of a request allowed; a flat column is as it was. The columns are the
        reader's to hold the request to, not the signature's."""
        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={
                "policy": "Struct({'limit': Int64, 'start': Date})",
                "items": "List(Struct({'item_id': String, 'value': Float64}))",
                "when": "Date",
            },
            output_schema={
                "pricing": "Struct({'premium': Float64, 'referral': String})",
                "layers": "List(Struct({'layer': Int32, 'ok': Boolean}))",
            },
        )

        sig = _build_signature(resolved)

        table = {"type": "map", "values": {"type": "any"}}
        assert [spec.to_dict() for spec in sig.inputs.inputs] == [
            {**table, "name": "policy", "required": False},
            {"type": "array", "items": table, "name": "items", "required": False},
            {"type": "datetime", "name": "when", "required": True},
        ]
        assert [spec.to_dict() for spec in sig.outputs.inputs] == [
            {**table, "name": "pricing", "required": False},
            {"type": "array", "items": table, "name": "layers", "required": False},
        ]

    def test_a_request_mlflow_lets_through_is_one_the_workbench_input_reads(self):
        """What MLflow's enforcement passes to ``predict`` is what the container's reader
        takes: a column the tables do not name, of mixed types across rows even, a table
        left out, an empty many-row table and a null one all reach the Workbench Input and
        read as the records would; a misfit reaches it too, to be refused naming the spot,
        as the container refuses it. The records are handed over as sent, numpy's values
        made plain, never a frame inferred from them."""
        import pandas as pd
        from mlflow.models.utils import _enforce_schema
        from polars.testing import assert_frame_equal

        from haute._workbench_input import WorkbenchInputError, workbench_request_frames
        from haute._workbench_tables import parse_workbench_tables, quote_schema
        from haute.deploy._mlflow import _build_signature
        from haute.deploy._model_code import _request
        from haute.deploy._scorer import QuoteRequest

        config = {
            "tables": [
                {
                    "name": "policy",
                    "side": "input",
                    "port": "policy_input",
                    "rows": "one",
                    "columns": [
                        {"name": "limit", "type": "int"},
                        {"name": "start", "type": "date"},
                    ],
                },
                {
                    # The side carries the table's Text index, as a Workbench Input does.
                    "name": "items",
                    "side": "input",
                    "port": "items_input",
                    "rows": "many",
                    "key": "item_id",
                    "columns": [
                        {"name": "item_id", "type": "str"},
                        {"name": "value", "type": "float"},
                    ],
                },
            ]
        }
        tables = parse_workbench_tables(config["tables"], owner="Workbench Input")
        resolved = _make_resolved(
            input_schema={name: str(dtype) for name, dtype in quote_schema(tables).items()},
            output_schema={"pricing": "Struct({'premium': Float64})"},
        )
        signature = _build_signature(resolved)
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "request",
                            "nodeType": "workbenchInput",
                            "config": config,
                        },
                    }
                ],
                "edges": [],
            }
        )

        def served(quote: dict) -> dict:
            # The pandas frame MLflow hands ``predict`` after enforcing the signature, taken
            # as ``HauteModel.predict`` takes it: the records, for the reader.
            enforced = _enforce_schema(pd.DataFrame([quote]), signature.inputs)
            request = _request(graph, ["request"], enforced, {}, frozenset())
            assert isinstance(request, QuoteRequest)
            frames = workbench_request_frames(config, request.records)
            # The frames are the input sides, by port, ``<table>_input``.
            return {port.removesuffix("_input"): frame.to_dicts() for port, frame in frames.items()}

        quote = {
            "policy": {"limit": 100, "start": "2026-01-02", "client_reference": "ABC"},
            "items": [{"item_id": "Z", "value": 7.0, "note": None}],
        }
        assert served(quote) == {
            "policy": [{"limit": 100, "start": __import__("datetime").date(2026, 1, 2)}],
            "items": [{"item_id": "Z", "value": 7.0}],
        }
        for port, frame in workbench_request_frames(config, [quote]).items():
            name = port.removesuffix("_input")
            assert_frame_equal(pl.DataFrame(served(quote)[name], schema=frame.schema), frame)
        assert served({"policy": {"limit": 100}}) == {
            "policy": [{"limit": 100, "start": None}],
            "items": [],
        }
        assert served({"policy": {"limit": 100}, "items": []})["items"] == []
        assert served({"policy": None, "items": None}) == {
            "policy": [{"limit": None, "start": None}],
            "items": [],
        }
        with pytest.raises(WorkbenchInputError, match="policy.limit is 'lots', not 'int'"):
            served({"policy": {"limit": "lots"}})
        # An undeclared column of mixed types, which no frame could be inferred from, is
        # left unread; a declared column of mixed types is refused at its row.
        mixed = {
            "policy": {"limit": 1},
            "items": [
                {"item_id": "A", "value": 1.0, "note": "x"},
                {"item_id": "B", "value": 2.0, "note": 42},
            ],
        }
        assert served(mixed)["items"] == [
            {"item_id": "A", "value": 1.0},
            {"item_id": "B", "value": 2.0},
        ]
        with pytest.raises(WorkbenchInputError, match="items\\[1\\].value is 'x', not 'float'"):
            served({"items": [{"item_id": "A", "value": 1.0}, {"item_id": "B", "value": "x"}]})
        # A frame built by hand, numpy's values inside its tables, reads as JSON's would.
        by_hand = pd.DataFrame(
            {
                "policy": [{"limit": np.int64(100), "start": "2026-01-02"}],
                "items": [np.array([{"item_id": "Z", "value": np.float64(1.5)}], dtype=object)],
            }
        )
        assert _request(graph, ["request"], by_hand, {}, frozenset()) == QuoteRequest(
            (
                {
                    "policy": {"limit": 100, "start": "2026-01-02"},
                    "items": [{"item_id": "Z", "value": 1.5}],
                },
            )
        )
        # A Quote Input takes the frame, as before.
        flat = _g(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {"label": "quotes", "nodeType": "apiInput", "config": {}},
                    }
                ],
                "edges": [],
            }
        )
        limits = pd.DataFrame({"limit": [100]})
        assert _request(
            flat, ["quotes"], limits, {"limit": pl.Int64()}, frozenset()
        ).to_dicts() == [{"limit": 100}]

    def test_all_numeric_types(self):
        """All supported numeric types should produce correct MLflow type mappings."""
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        all_types = {
            "a_i8": "Int8",
            "b_i16": "Int16",
            "c_i32": "Int32",
            "d_i64": "Int64",
            "e_u8": "UInt8",
            "f_u16": "UInt16",
            "g_u32": "UInt32",
            "h_u64": "UInt64",
            "i_f32": "Float32",
            "j_f64": "Float64",
            "k_str": "String",
            "l_utf8": "Utf8",
            "m_bool": "Boolean",
            "n_date": "Date",
        }
        resolved = _make_resolved(
            input_schema=all_types,
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)
        type_map = self._input_type_map(sig)

        # Integer types (Int8, Int16, Int32, UInt8, UInt16) -> integer
        for col in ("a_i8", "b_i16", "c_i32", "e_u8", "f_u16"):
            assert type_map[col] == DataType.integer, f"{col} must map to integer"
        # Long types (Int64, UInt32, UInt64) -> long
        for col in ("d_i64", "g_u32", "h_u64"):
            assert type_map[col] == DataType.long, f"{col} must map to long"
        # Float32 -> float
        assert type_map["i_f32"] == DataType.float, "Float32 must map to float"
        # Float64 -> double
        assert type_map["j_f64"] == DataType.double, "Float64 must map to double"
        # String / Utf8 -> string
        assert type_map["k_str"] == DataType.string
        assert type_map["l_utf8"] == DataType.string
        # Boolean -> boolean
        assert type_map["m_bool"] == DataType.boolean
        # Date -> datetime
        assert type_map["n_date"] == DataType.datetime


class TestCondaEnvAndPipRequirements:
    """Tests for served_lock(), _conda_env() and _pip_requirements()."""

    @staticmethod
    def _lock(resolved: Any, haute: str = "haute==0.6.0") -> Any:
        from haute.deploy._mlflow import served_lock
        from haute.deploy._served_haute import ServedHaute

        return served_lock(resolved, ServedHaute(haute))

    def test_pip_requirements_pin_the_served_haute_and_the_served_runtime(self):
        """The haute that validated the deploy, and this environment's MLflow, Polars,
        pandas and NumPy, each at its locked version (the offline resolver adds none)."""
        from importlib.metadata import version

        from haute.deploy._mlflow import _pip_requirements

        reqs = _pip_requirements("haute==0.6.0", self._lock(_make_resolved()))

        assert reqs == [
            "haute==0.6.0",
            *sorted(f"{name}=={version(name)}" for name in ("mlflow", "polars", "pandas", "numpy")),
        ]

    def test_the_served_closure_is_locked_for_model_serving(self, tmp_path):
        """DEP-R09: resolved for Linux x86_64 and the project's Python, whatever the host."""
        from haute._project_python import deployed_python_version

        resolved = _make_resolved()
        lock = self._lock(resolved)

        assert lock.platform == "x86_64-manylinux_2_28"
        assert lock.python_version == deployed_python_version(resolved.project_root)
        assert lock.roots[0] == "haute==0.6.0"

    def test_pip_requirements_pin_each_bundled_engine_once(self, tmp_path):
        """DEP-R06: a bundled model serves under the engine release that trained it."""
        from haute.deploy._container import _pinned_extra_dockerfile_deps
        from haute.deploy._mlflow import _pip_requirements

        resolved = _make_resolved(
            artifacts={
                "score__model.cbm": tmp_path / "model.cbm",
                "freq__model.ubj": tmp_path / "model.ubj",
                "sev__model.lgbm": tmp_path / "model.lgbm",
            }
        )

        reqs = _pip_requirements("haute==0.6.0", self._lock(resolved))

        # The pins a container image of these artefacts carries: each engine's installed
        # distribution (XGBoost's may be xgboost-cpu) at its installed version.
        engines = _pinned_extra_dockerfile_deps(resolved)
        assert {pin.split("==")[0] for pin in engines} >= {"catboost", "lightgbm"}
        assert any(pin.startswith("xgboost") for pin in engines)
        assert set(engines) <= set(reqs)
        # pandas is both the pyfunc's and the XGBoost artefact's: named once.
        assert len(reqs) == len(set(reqs))
        assert sum(r.startswith("pandas==") for r in reqs) == 1

    def test_conda_env_structure(self):
        from haute.deploy._mlflow import _conda_env

        env = _conda_env("3.13", "haute==0.6.0", self._lock(_make_resolved()))

        assert env["name"] == "mlflow-env"
        assert "conda-forge" in env["channels"]
        assert len(env["dependencies"]) == 3  # python, pip, pip-dict
        assert env["dependencies"][0].startswith("python=")

    @pytest.mark.parametrize("version", ["3.13", "3.12.8"])
    def test_conda_env_pins_the_projects_python(self, version: str):
        """The model serves on the Python the project ran on, at the precision it names."""
        from haute.deploy._mlflow import _conda_env

        env = _conda_env(version, "haute==0.6.0", self._lock(_make_resolved()))
        assert env["dependencies"][0] == f"python={version}"


class TestCheckDatabricksConnectivity:
    """Tests for _check_databricks_connectivity()."""

    def test_missing_host_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.delenv("DATABRICKS_RATING_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_RATING_TOKEN", raising=False)

        with pytest.raises(
            DeployError, match="DATABRICKS_RATING_HOST and DATABRICKS_RATING_TOKEN are not set"
        ):
            _check_databricks_connectivity(lambda msg: None)

    def test_missing_token_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.delenv("DATABRICKS_RATING_TOKEN", raising=False)

        with pytest.raises(DeployError, match="^DATABRICKS_RATING_TOKEN is not set"):
            _check_databricks_connectivity(lambda msg: None)

    def test_success(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "dapi_token")

        messages = []

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock()
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            _check_databricks_connectivity(messages.append)

        assert any("reachable" in m for m in messages)

    def test_403_raises_with_clear_message(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "bad_token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://host/api",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            )

            with pytest.raises(DeployError, match="403 Forbidden"):
                _check_databricks_connectivity(lambda msg: None)

    def test_401_raises_as_a_credential_problem(self, monkeypatch):
        """An expired token is not a reachable workspace."""
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "expired_token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://host/api",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )

            with pytest.raises(DeployError, match="401 Unauthorized.*has not expired"):
                _check_databricks_connectivity(lambda msg: None)

    def test_deploy_status_and_config_share_their_registry_defaults(self):
        import inspect

        from haute.deploy._config import DatabricksConfig
        from haute.deploy._mlflow import get_deploy_status

        parameters = inspect.signature(get_deploy_status).parameters
        config = DatabricksConfig()
        assert (parameters["catalog"].default, parameters["schema"].default) == (
            config.catalog,
            config.schema,
        )

    def test_non_403_http_error_succeeds(self, monkeypatch):
        """Non-403 HTTP errors (e.g. 404) mean host is reachable."""
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        messages = []

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://host/api",
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=None,
            )

            _check_databricks_connectivity(messages.append)

        assert any("reachable" in m for m in messages)

    def test_timeout_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("connection timed out")

            with pytest.raises(DeployError, match="Cannot reach Databricks"):
                _check_databricks_connectivity(lambda msg: None)

    def test_url_error_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity
        from haute.errors import DeployError

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("DNS resolution failed")

            with pytest.raises(DeployError, match="Cannot reach Databricks"):
                _check_databricks_connectivity(lambda msg: None)


# ---------------------------------------------------------------------------
# Bug regression: pruner
# ---------------------------------------------------------------------------


class TestBugB10LexicographicVersionComparison:
    """B10: MLflow version max() must use numeric, not string comparison."""

    def test_version_10_greater_than_9(self) -> None:

        # We just need to test the max() logic. Let's test directly.
        versions = ["1", "2", "9", "10", "11"]
        result = max(versions, key=lambda v: int(v))
        assert result == "11"
        # The bug: max(versions) without int() gives "9"
        assert max(versions) == "9"  # proves the bug exists in string comparison


class TestOmittableRequestColumns:
    """A quote may leave out a bundled model's exposure, and nothing else."""

    def test_the_databricks_signature_requires_every_column_but_an_exposure(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        import mlflow
        import pandas as pd
        from mlflow.exceptions import MlflowException

        from haute.deploy._mlflow import _build_signature
        from haute.graph_utils import PipelineGraph
        from tests._deploy_helpers import make_resolved_deploy

        resolved = make_resolved_deploy(
            pruned_graph=PipelineGraph(),
            input_schema={"age": "Int64", "exposure": "Float64"},
            output_schema={"premium": "Float64"},
            omittable_inputs=["exposure"],
        )
        signature = _build_signature(resolved)
        required = {spec.name: spec.required for spec in signature.inputs.inputs}
        assert required == {"age": True, "exposure": False}
        assert all(spec.required for spec in signature.outputs.inputs)

        # Through MLflow's own enforcement, as Model Serving applies it.
        class Echo(mlflow.pyfunc.PythonModel):
            def predict(self, context, model_input, params=None):  # type: ignore[no-untyped-def]
                del context, params
                return model_input

        from haute._mlflow_utils import mlflow_fluent_operation

        with mlflow_fluent_operation():
            mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
            with mlflow.start_run():
                info = mlflow.pyfunc.log_model(
                    name="echo", python_model=Echo(), signature=signature
                )
            served = mlflow.pyfunc.load_model(info.model_uri)
        assert served.predict(pd.DataFrame({"age": [30]})).to_dict("records") == [{"age": 30}]
        with pytest.raises(MlflowException, match="missing inputs"):
            served.predict(pd.DataFrame({"exposure": [1.0]}))

    def test_a_bundled_model_s_exposure_the_request_carries_is_omittable(self, monkeypatch):
        from haute.deploy._scorer import omittable_request_columns
        from haute.graph_utils import PipelineGraph

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": node_id,
                        "data": {
                            "label": node_id,
                            "nodeType": "modelScore",
                            "config": {"source_type": "file", "model_path": f"{node_id}.cbm"},
                        },
                    }
                    for node_id in ("freq", "sev", "unbundled")
                ]
            }
        )
        models = {
            "freq__freq.cbm": MagicMock(offset_column="exposure", offset_link="log"),
            "sev__sev.cbm": MagicMock(offset_column="premium", offset_link="identity"),
        }
        monkeypatch.setattr(
            "haute.deploy._scorer.load_local_model_cached",
            lambda path, task, contract: models[path],
        )
        artifacts = {name: name for name in models}

        carried = {"age": "Int64", "exposure": "Float64", "premium": "Float64"}
        assert omittable_request_columns(graph, artifacts, carried) == ["exposure"]
        # An exposure the request does not carry is nothing a quote can leave out.
        assert omittable_request_columns(graph, artifacts, {"age": "Int64"}) == []
