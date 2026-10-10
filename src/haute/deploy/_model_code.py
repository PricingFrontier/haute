"""MLflow models-from-code entrypoint for HauteModel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mlflow.pyfunc
import numpy as np
import pandas as pd
from mlflow.models import set_model
from mlflow.pyfunc import PythonModelContext

from haute._types import PipelineGraph

if TYPE_CHECKING:
    from haute.deploy._scorer import DeployInput


def _plain(value: Any) -> Any:
    """*value* as JSON holds it: numpy's scalars and arrays, which a pandas frame built
    by hand may carry inside a table, as Python's own."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | np.ndarray):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _request(
    graph: PipelineGraph, input_node_ids: list[str], model_input: pd.DataFrame
) -> DeployInput:
    """The request as the pipeline's request input takes it.

    A Workbench Input reads its quotes as their records, each value as it was sent: a
    frame inferred from the request would type a column the tables do not name, and fail
    on one of mixed types, before the reader could leave it unread, where the signature
    lets any value through (specs/workbench). Any other request input takes the frame.
    """
    import polars as pl

    from haute.deploy._scorer import QuoteRequest, reads_one_quote_per_request

    if reads_one_quote_per_request(graph, input_node_ids):
        return QuoteRequest(
            tuple(_plain(record) for record in model_input.to_dict(orient="records"))
        )
    return pl.from_pandas(model_input)


class HauteModel(mlflow.pyfunc.PythonModel):  # type: ignore[name-defined]
    """MLflow PythonModel wrapper for a deployed haute pipeline."""

    def load_context(self, context: PythonModelContext) -> None:
        """Called once when the model is loaded for serving."""
        manifest_path = Path(context.artifacts["deploy_manifest"])
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._graph = PipelineGraph.model_validate(self._manifest["pruned_graph"])
        self._input_node_ids = self._manifest["input_node_ids"]
        self._output_node_id = self._manifest["output_node_id"]
        self._output_fields = self._manifest.get("output_fields")

        manifest_artifacts = set(self._manifest.get("artifacts", {}))
        missing_artifacts = sorted(manifest_artifacts - set(context.artifacts))
        if missing_artifacts:
            raise RuntimeError(
                "MLflow model context is missing deployment artifact(s) declared "
                f"by the manifest: {', '.join(missing_artifacts)}."
            )
        self._artifact_paths = {
            artifact_name: context.artifacts[artifact_name] for artifact_name in manifest_artifacts
        }

    def predict(
        self,
        context: PythonModelContext,
        model_input: pd.DataFrame,
        params: dict | None = None,
    ) -> pd.DataFrame:
        """Score one or more rows through the pipeline."""

        from haute.deploy._scorer import admit_deploy_execution, score_graph

        execution_context = admit_deploy_execution(
            operation="deploy_pyfunc_predict",
            row_count=len(model_input),
        )
        preserve_primary_error = False
        try:
            with execution_context.stage("deploy_from_pandas"):
                input_df = _request(self._graph, self._input_node_ids, model_input)
            execution_context.checkpoint(label="after_deploy_from_pandas")
            result = score_graph(
                graph=self._graph,
                input_df=input_df,
                input_node_ids=self._input_node_ids,
                output_node_id=self._output_node_id,
                artifact_paths=self._artifact_paths,
                output_fields=self._output_fields,
                execution_context=execution_context,
                retain_admission_on_success=True,
            )
            with execution_context.stage("deploy_to_pandas"):
                pandas_result = result.to_pandas()
            execution_context.checkpoint(label="after_deploy_to_pandas")
            return pandas_result
        except BaseException:
            preserve_primary_error = True
            raise
        finally:
            execution_context.release_admission(
                preserve_primary_error=preserve_primary_error,
            )


set_model(HauteModel())
