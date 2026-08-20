"""MLflow models-from-code entrypoint for HauteModel."""

from __future__ import annotations

import json
from pathlib import Path

import mlflow.pyfunc
import pandas as pd
from mlflow.models import set_model
from mlflow.pyfunc import PythonModelContext

from haute._types import PipelineGraph


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
        import polars as pl

        from haute.deploy._scorer import admit_deploy_execution, score_graph

        execution_context = admit_deploy_execution(
            operation="deploy_pyfunc_predict",
            row_count=len(model_input),
        )
        preserve_primary_error = False
        try:
            with execution_context.stage("deploy_from_pandas"):
                input_df = pl.from_pandas(model_input)
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
