"""Workbench test projects (specs/workbench): a pipeline file whose folder holds the form
folder, the ``[workbench]`` table, and a form written as its folder's files, as the editor
writes them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from haute._config_validation import FORM_GIVEN_KEYS
from haute._types import NodeType, PipelineGraph
from haute._workbench_folder import folder_files
from haute._workbench_form import FormSpec, with_quote_details
from haute._workbench_tables import WorkbenchTables, parse_workbench_tables

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


def without_form_tables(graph: PipelineGraph) -> PipelineGraph:
    """*graph* as the editor holds it and save writes it: its workbench nodes, its submodels'
    too, holding none of what they are given from the form where the pipeline runs
    (``FORM_GIVEN_KEYS``)."""
    nodes = [
        node.model_copy(
            update={
                "data": node.data.model_copy(
                    update={
                        "config": {
                            key: value
                            for key, value in node.data.config.items()
                            if key not in FORM_GIVEN_KEYS.get(node.data.nodeType, frozenset())
                        }
                    }
                )
            }
        )
        for node in graph.nodes
    ]
    submodels = graph.submodels and {
        name: definition.model_copy(update={"graph": without_form_tables(definition.graph)})
        for name, definition in graph.submodels.items()
    }
    return graph.model_copy(update={"nodes": nodes, "submodels": submodels})


def held_tables(graph: PipelineGraph) -> WorkbenchTables:
    """The tables, sample and response tables *graph*'s workbench nodes hold, as a run gives
    them from a form defining them: for a graph a test builds holding them, written as save
    writes it (:func:`without_form_tables`) and read back."""
    inputs = next(
        (n.data.config for n in graph.nodes if n.data.nodeType is NodeType.WORKBENCH_INPUT), {}
    )
    outputs = next(
        (n.data.config for n in graph.nodes if n.data.nodeType is NodeType.WORKBENCH_OUTPUT), {}
    )
    return WorkbenchTables(
        parse_workbench_tables(inputs.get("tables", []), owner="Workbench Input"),
        inputs.get("sample", {}),
        parse_workbench_tables(outputs.get("tables", []), owner="Workbench Output"),
    )


def form_table(
    tid: str, name: str, role: str, *columns: tuple[str, str], many: bool = False
) -> dict[str, Any]:
    """A schema table of a form, *tid* its id and each column's id ``c_`` and its name."""
    return {
        "id": tid,
        "name": name,
        "role": role,
        "rows": "many" if many else "one",
        "columns": [
            {"id": f"c_{column}", "name": column, "type": kind} for column, kind in columns
        ],
    }


def tables_form(
    tables: list[dict[str, Any]], sample: dict[str, Any] | None = None, *, name: str = "x"
) -> FormSpec:
    """A form of one blank sheet whose schema holds *tables* (:func:`form_table`), with
    *sample* by table and column id, each value as typed, and Haute's ``quote_details``."""
    return with_quote_details(
        FormSpec.model_validate(
            {
                "version": 1,
                "name": name,
                "schema": {"tables": tables},
                "pages": [
                    {
                        "id": "p",
                        "title": "Sheet 1",
                        "width": 1280,
                        "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                    }
                ],
                "sample": sample or {},
            }
        )
    )


def workbench_project(
    project: Path,
    tables: list[dict[str, Any]],
    sample: dict[str, Any] | None = None,
    *,
    pipeline: str = "main.py",
) -> FormSpec:
    """*project* made a workbench project: a ``haute.toml`` naming *pipeline*, its workbench
    enabled, a repository, the pipeline file when it has none yet, and the form folder beside
    it holding :func:`tables_form` of *tables* and *sample*, which it answers."""
    (project / ".git").mkdir(exist_ok=True)
    (project / "haute.toml").write_text(
        f'[project]\nname = "x"\npipeline = "{pipeline}"\n\n[workbench]\nenabled = true\n',
        encoding="utf-8",
    )
    pipeline_file = project / pipeline
    if not pipeline_file.exists():
        pipeline_file.parent.mkdir(parents=True, exist_ok=True)
        pipeline_file.write_text(PIPELINE_SOURCE, encoding="utf-8")
    spec = tables_form(tables, sample)
    folder = pipeline_file.parent / "workbench"
    if folder.exists():
        import shutil

        shutil.rmtree(folder)
    write_files(folder, folder_files(spec, folder))
    return spec
