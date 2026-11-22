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
    from haute.deploy._request_frame import RequestReader
    from haute.deploy._scorer import DeployInput


def _plain(value: Any) -> Any:
    """*value* as JSON holds it: numpy's scalars and arrays, which a pandas frame built
    by hand may carry inside a table, as Python's own, and a ``datetime64`` as its ISO
    text at its own precision (``item()`` would give a nanosecond one as a bare integer,
    and a Python ``datetime`` holds only microseconds), a missing one as null."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | np.ndarray):
        return [_plain(item) for item in value]
    if isinstance(value, np.datetime64):
        return None if np.isnat(value) else str(np.datetime_as_string(value))
    if isinstance(value, np.generic):
        return value.item()
    return value


def _request(reader: RequestReader, model_input: pd.DataFrame) -> DeployInput:
    """The request as the pipeline's request input takes it.

    A Workbench Input, and a Quote Input with tables, read their quotes as their records,
    each value as it was sent: a frame inferred from the request would type a column the
    tables do not name, and fail on one of mixed types, before the reader could leave it
    unread, where the signature lets any value through (specs/workbench). Any other
    request input takes a frame typed by its declared *input_schema*, as a container's
    request is, leaving out only the *omittable* columns: pandas has no typed null, so a
    missing value (``NaN``, ``NaT``, ``NA``) is a null first. *reader* reads either
    (:class:`~haute.deploy._request_frame.RequestReader`).
    """
    records = [_plain(record) for record in model_input.to_dict(orient="records")]
    if reader.records:
        return reader.read(records)
    present = [
        {key: None if _missing(value) else value for key, value in record.items()}
        for record in records
    ]
    return reader.read(present)


def _missing(value: object) -> bool:
    """Whether *value* is pandas' missing value (``NaN``, ``NaT``, ``NA``)."""
    import pandas as pd

    return not isinstance(value, dict | list | tuple) and bool(pd.isna(value))


def _require_deployed_polars(deployed: str) -> None:
    """Refuse to serve under a Polars other than the one the pipeline was deployed with.

    A Polars release can change what a pipeline computes and the memory it takes,
    and the deploy's checks measured this one. The logged requirements pin it, so
    another means the serving environment did not install them.
    """
    import polars

    if polars.__version__ != deployed:
        raise RuntimeError(
            f"This model was deployed with Polars {deployed} and is loading under Polars "
            f"{polars.__version__}. A Polars release can change a pipeline's results, so the "
            f"model serves only under the Polars it was deployed with: serve it where "
            f"polars=={deployed} is installed, or deploy it again from this environment."
        )


class HauteModel(mlflow.pyfunc.PythonModel):  # type: ignore[name-defined]
    """MLflow PythonModel wrapper for a deployed haute pipeline."""

    def load_context(self, context: PythonModelContext) -> None:
        """Called once when the model is loaded for serving."""
        manifest_path = Path(context.artifacts["deploy_manifest"])
        # The model scores in its own project, the folder its manifest ships in,
        # whatever the serving process's working directory.
        from haute._project_context import ProjectContext

        # MLflow copies the bundled ``utility`` (a code path) into the model's ``code``
        # folder and puts that on the import path: it is the project's code, so the
        # finder and the cache identities search it as a project folder.
        self._project = ProjectContext.at(
            manifest_path.parent, utility_root=manifest_path.parent.parent / "code"
        )
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _require_deployed_polars(self._manifest["polars_version"])
        self._graph = PipelineGraph.model_validate(self._manifest["pruned_graph"])
        self._input_node_ids = self._manifest["input_node_ids"]
        self._output_node_id = self._manifest["output_node_id"]
        self._output_fields = self._manifest.get("output_fields")
        # Whether a request of several quotes is answered in request order, or refused.
        from haute.deploy._request_rows import RequestRows

        self._request_rows = RequestRows.from_manifest(self._manifest["request_rows"])
        from haute.deploy._request_frame import RequestReader

        # The request read as a container's is: a flat Quote Input's framed by the schema
        # its sample recorded, leaving out only its exposures, priced for a full term.
        self._reader = RequestReader.of(self._graph, self._input_node_ids, self._manifest)

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
        """Score one or more rows through the pipeline, in the model's project."""

        from haute._project_context import project_scope

        with project_scope(self._project):
            return self._predict(model_input)

    def _predict(self, model_input: pd.DataFrame) -> pd.DataFrame:
        from haute.deploy._response_json import refuse_non_finite
        from haute.deploy._scorer import admit_deploy_execution, score_graph

        execution_context = admit_deploy_execution(
            operation="deploy_pyfunc_predict",
            row_count=len(model_input),
        )
        preserve_primary_error = False
        try:
            with execution_context.stage("deploy_from_pandas"):
                input_df = _request(self._reader, model_input)
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
                request_rows=self._request_rows,
            )
            # Serving encodes the answer as JSON, which holds no NaN or infinity: one is
            # refused naming its column and quote, as a container's /quote refuses it.
            refuse_non_finite(result, first_quote=None if len(model_input) == 1 else 0)
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
