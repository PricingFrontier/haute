"""Isolated reproduction for V049.

Claim: ``deploy_to_mlflow`` discards the version returned by
``mlflow.pyfunc.log_model(...)`` (``ModelInfo.registered_model_version``) and
instead re-derives the version by re-querying ALL versions of the UC model via
``search_versions`` + ``max(...)``, falling back to the literal string ``"1"``
when the search is empty.

Two demonstrably-wrong-VALUE defects follow:

  Defect 1 (race to wrong version): if a *concurrent* deploy of the same UC
  model registers a HIGHER version between this ``log_model`` call and the
  ``search_versions`` call, ``max()`` returns that OTHER deploy's version. The
  live serving endpoint is then bound to the wrong version and the wrong
  ``v{n}`` is reported to the user.

  Defect 2 (silent bogus fallback): after a SUCCESSFUL ``log_model`` there is
  always >= 1 version. An empty ``search_versions`` indicates a genuine
  registry problem, yet the ``else: latest_version = "1"`` branch fabricates
  version 1 and proceeds to serve/return it, masking the failure.

ISOLATION: all disk I/O is confined to a Python tempfile dir (the code writes
``<pipeline_dir>/.haute_build/deploy_manifest.json``). mlflow + the Databricks
serving call + connectivity probe are fully mocked. No rating/, src/, tests/,
or real project files are read or written.

The repro ASSERTS on the specific wrong VALUE returned by ``deploy_to_mlflow``
(``DeployResult.model_version`` and the ``model_version`` passed to the serving
endpoint), not merely that "something raised".
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch


@contextmanager
def _mock_mlflow_deploy(*, search_versions_return, log_model_registered_version):
    """Patch every external dependency of ``deploy_to_mlflow``.

    Mirrors the production code's seams:
      * ``mlflow.pyfunc.log_model`` returns a ``ModelInfo``-like object whose
        ``registered_model_version`` is the version JUST created.
      * ``mlflow.tracking.MlflowClient().search_model_versions(...)`` returns
        whatever the registry reports at re-query time (possibly a racing,
        higher version, or an empty list).
    """
    with (
        patch("mlflow.set_tracking_uri"),
        patch("mlflow.set_registry_uri"),
        patch("mlflow.set_experiment"),
        patch("mlflow.start_run") as m_run,
        patch("mlflow.log_dict"),
        patch("mlflow.pyfunc.log_model") as m_log_model,
        patch("mlflow.tracking.MlflowClient") as m_client,
        patch("haute.deploy._mlflow._check_databricks_connectivity"),
        patch("haute.deploy._mlflow._build_signature"),
        patch("haute.deploy._mlflow._create_or_update_serving_endpoint") as m_ep,
    ):
        # start_run() is used as a context manager.
        m_run.return_value.__enter__ = MagicMock()
        m_run.return_value.__exit__ = MagicMock(return_value=False)

        # log_model returns a ModelInfo-like object carrying the version that
        # was actually registered by THIS call. The production code ignores it.
        model_info = MagicMock()
        model_info.registered_model_version = log_model_registered_version
        m_log_model.return_value = model_info

        # The registry re-query returns whatever versions exist at that moment.
        m_client.return_value.search_model_versions.return_value = search_versions_return

        # Endpoint creation is a no-op; we capture the version it was asked to bind.
        m_ep.return_value = "https://host/serving-endpoints/ep/invocations"

        yield m_ep


def _make_version(v: str) -> MagicMock:
    """A minimal stand-in for ``mlflow.entities.model_registry.ModelVersion``."""
    mv = MagicMock()
    mv.version = v
    return mv


def _build_resolved(pipeline_dir: Path):
    """Build a minimal ResolvedDeploy whose build dir lives under *pipeline_dir*."""
    from haute.deploy._config import DatabricksConfig, DeployConfig, ResolvedDeploy
    from haute.graph_utils import PipelineGraph

    pipeline_file = pipeline_dir / "pipeline.py"
    pipeline_file.write_text("# repro pipeline\n", encoding="utf-8")

    config = DeployConfig(
        pipeline_file=pipeline_file,
        model_name="my-model",
        endpoint_name="my-endpoint",  # ensures the serving endpoint path runs
        databricks=DatabricksConfig(catalog="ws", schema="default"),
    )
    return ResolvedDeploy(
        config=config,
        full_graph=PipelineGraph(),
        pruned_graph=PipelineGraph(),
        input_node_ids=["policies"],
        output_node_id="output",
        artifacts={},
        input_schema={"col": "Int64"},
        output_schema={"col": "Int64"},
    )


def main() -> int:
    from haute.deploy._mlflow import deploy_to_mlflow

    failures: list[str] = []

    # ---------------------------------------------------------------------
    # Defect 1: race to the WRONG version.
    #
    # log_model just created version 7. But between log_model and the
    # search_versions re-query, a concurrent deploy registered version 42.
    # Correct behaviour: bind/report version 7 (the one we just created).
    # Buggy behaviour:  max(search_versions) -> 42.
    # ---------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        resolved = _build_resolved(Path(td))
        with _mock_mlflow_deploy(
            search_versions_return=[_make_version("42"), _make_version("41")],
            log_model_registered_version="7",
        ) as m_ep:
            result = deploy_to_mlflow(resolved)

        served_version = m_ep.call_args.kwargs["model_version"]
        print(
            f"[defect1] log_model registered v7; concurrent deploy registered v42 -> "
            f"DeployResult.model_version={result.model_version!r}, "
            f"served model_version={served_version!r}, "
            f"model_uri={result.model_uri!r}"
        )
        if result.model_version != 7:
            failures.append(
                f"DEFECT 1 CONFIRMED: expected version 7 (the version log_model just "
                f"created), but deploy reported {result.model_version!r} "
                f"(the racing concurrent version)."
            )
        if served_version != 7:
            failures.append(
                f"DEFECT 1 CONFIRMED: serving endpoint bound to model_version="
                f"{served_version!r} instead of the just-created version 7."
            )
        if "/42" in result.model_uri:
            failures.append(
                f"DEFECT 1 CONFIRMED: model_uri points at the racing version: "
                f"{result.model_uri!r}."
            )

    # ---------------------------------------------------------------------
    # Defect 2: silent bogus "1" fallback after a SUCCESSFUL log_model.
    #
    # log_model returned registered_model_version="5", proving a version was
    # created. The registry re-query comes back EMPTY (a genuine problem).
    # Correct behaviour: use 5 (or fail loudly). Buggy behaviour: fabricate "1".
    # ---------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        resolved = _build_resolved(Path(td))
        with _mock_mlflow_deploy(
            search_versions_return=[],
            log_model_registered_version="5",
        ) as m_ep:
            result = deploy_to_mlflow(resolved)

        served_version = m_ep.call_args.kwargs["model_version"]
        print(
            f"[defect2] log_model registered v5; search_versions empty -> "
            f"DeployResult.model_version={result.model_version!r}, "
            f"served model_version={served_version!r}, "
            f"model_uri={result.model_uri!r}"
        )
        if result.model_version != 5:
            failures.append(
                f"DEFECT 2 CONFIRMED: expected version 5 (returned by log_model), but "
                f"deploy fabricated {result.model_version!r} via the silent "
                f"`else: latest_version = \"1\"` fallback."
            )

    print("-" * 72)
    if failures:
        print("V049 REPRODUCED -- demonstrably wrong values:")
        for f in failures:
            print("  * " + f)
        return 1

    print("V049 NOT reproduced: deploy used the log_model version in every case.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
