"""Workbench test projects (specs/workbench): a pipeline file whose folder holds the form
folder, the ``[workbench]`` table, and a form written as its folder's files, as the editor
writes them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from haute._workbench_folder import folder_files
from haute._workbench_form import FormSpec, with_quote_details

#: Where a test project's pipeline is, and so its form folder.
PIPELINE = "rating/main.py"
FORM = "rating/workbench"
PIPELINE_SOURCE = 'import haute\n\npipeline = haute.Pipeline("x")\n'


def write_pipeline(project: Path) -> None:
    """The project's pipeline file, whose folder holds the form folder."""
    path = project / PIPELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PIPELINE_SOURCE, encoding="utf-8")


def enable(project: Path, table: str = "[workbench]\nenabled = true\n") -> None:
    """A ``haute.toml`` naming the project's pipeline, with *table*, and the pipeline file."""
    write_pipeline(project)
    (project / "haute.toml").write_text(
        f'[project]\nname = "x"\npipeline = "{PIPELINE}"\n\n{table}', encoding="utf-8"
    )


def form_folder(project: Path) -> Path:
    return project / FORM


def form_files(spec: FormSpec | dict[str, Any], folder: Path) -> dict[str, bytes]:
    """The files of *spec*'s folder at *folder*, as a save writes them."""
    form = spec if isinstance(spec, FormSpec) else FormSpec.model_validate(spec)
    return folder_files(with_quote_details(form), folder)


def write_files(folder: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_form(project: Path, spec: FormSpec | dict[str, Any]) -> dict[str, bytes]:
    """*spec* written as the project's form folder; its files, as written."""
    folder = form_folder(project)
    files = form_files(spec, folder)
    write_files(folder, files)
    return files
