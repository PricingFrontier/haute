"""A tracking client whose runs hold given artifact trees, for model-resolution unit tests."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any


class FakeArtifactClient:
    """Answers ``list_artifacts`` and ``get_run`` from ``{run_id: {path: size}}``.

    A path's parent folders exist implicitly; a size of ``None`` is a store that
    reports none. ``listings`` records each ``(run_id, folder)`` listed, so a test
    can count the store calls a resolution makes.
    """

    def __init__(
        self,
        runs: Mapping[str, Mapping[str, int | None]],
        *,
        artifact_uri: str = "mlflow-artifacts:/0/{run_id}/artifacts",
    ) -> None:
        self.runs = {run_id: dict(files) for run_id, files in runs.items()}
        self.artifact_uri = artifact_uri
        self.listings: list[tuple[str, str]] = []

    def list_artifacts(self, run_id: str, path: str | None = None) -> list[Any]:
        from mlflow.entities import FileInfo

        folder = path or ""
        self.listings.append((run_id, folder))
        prefix = f"{folder}/" if folder else ""
        entries: dict[str, FileInfo] = {}
        for file_path, size in self.runs[run_id].items():
            if not file_path.startswith(prefix):
                continue
            head, _, rest = file_path[len(prefix) :].partition("/")
            child = f"{prefix}{head}"
            entries[child] = FileInfo(child, True, None) if rest else FileInfo(child, False, size)
        return list(entries.values())

    def get_run(self, run_id: str) -> Any:
        return SimpleNamespace(
            info=SimpleNamespace(artifact_uri=self.artifact_uri.format(run_id=run_id))
        )
