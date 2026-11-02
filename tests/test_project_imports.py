"""The project's ``utility`` package is imported through a finder, never ``sys.path``."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest

from haute._cache import _resolve_utility_locations
from haute._project_imports import evict_utility_modules
from haute.deploy._project_modules import resolve_project_modules
from haute.errors import PreambleError
from haute.executor import _compile_preamble


@pytest.fixture(autouse=True)
def _fresh_utility() -> Iterator[None]:
    """No test sees another's ``utility``, and no compile is reused across tests."""
    evict_utility_modules()
    _compile_preamble.cache_clear()
    yield
    evict_utility_modules()
    _compile_preamble.cache_clear()


def _project(root: Path, files: dict[str, str]) -> Path:
    """A project at *root*: its pipeline folder ``rating/`` and the files given."""
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    (root / "rating").mkdir(exist_ok=True)
    return root / "rating"


def test_compiling_and_running_leave_sys_path_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_dir = _project(tmp_path, {"rating/utility/__init__.py": "VALUE = 3\n"})
    monkeypatch.chdir(tmp_path)
    before = list(sys.path)

    ns = _compile_preamble("from utility import VALUE\n", pipeline_dir=str(pipeline_dir))
    assert ns["VALUE"] == 3
    assert sys.path == before

    with pytest.raises(PreambleError):
        _compile_preamble("from utility import ABSENT\n", pipeline_dir=str(pipeline_dir))
    assert sys.path == before

    failing = _compile_preamble(
        "def fail(frame):\n    raise ValueError('node failed')\n", pipeline_dir=str(pipeline_dir)
    )
    with pytest.raises(Exception, match="node failed"):
        pl.LazyFrame({"x": [1]}).map_batches(failing["fail"]).collect()
    assert sys.path == before


def test_a_helper_importing_utility_at_collect_time_resolves_through_the_finder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A helper imports ``utility.sub`` only when Polars calls it, after the compile."""
    pipeline_dir = _project(
        tmp_path,
        {"rating/utility/__init__.py": "", "rating/utility/sub.py": "VALUE = 7\n"},
    )
    monkeypatch.chdir(tmp_path)
    ns = _compile_preamble(
        """
def add_value(frame):
    import utility.sub
    return frame.with_columns(v=pl.lit(utility.sub.VALUE))
""",
        pipeline_dir=str(pipeline_dir),
    )
    # The project's directories are on no import path the collect could use.
    assert str(pipeline_dir.resolve()) not in sys.path
    assert "utility" not in sys.modules

    collected = (
        pl.LazyFrame({"x": [1]})
        .map_batches(ns["add_value"], schema={"x": pl.Int64, "v": pl.Int32})
        .collect()
    )

    assert collected["v"].to_list() == [7]


def test_a_deferred_utility_import_resolves_the_project_compiled_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finder is bound to the project whose preamble was compiled last: a helper
    project A's preamble defined imports project B's ``utility`` once B compiled after
    it. One process serves one project, so the binding is that project's."""
    monkeypatch.chdir(tmp_path)
    helper = """
def add_value(frame):
    import utility.sub
    return frame.with_columns(v=pl.lit(utility.sub.VALUE))
"""
    first = _project(
        tmp_path / "a", {"rating/utility/__init__.py": "", "rating/utility/sub.py": "VALUE = 1\n"}
    )
    second = _project(
        tmp_path / "b", {"rating/utility/__init__.py": "", "rating/utility/sub.py": "VALUE = 2\n"}
    )
    ns_first = _compile_preamble(helper, pipeline_dir=str(first))
    _compile_preamble("from utility.sub import VALUE\n", pipeline_dir=str(second))

    collected = (
        pl.LazyFrame({"x": [1]})
        .map_batches(ns_first["add_value"], schema={"x": pl.Int64, "v": pl.Int32})
        .collect()
    )

    assert collected["v"].to_list() == [2]


@pytest.mark.parametrize(
    "module_path", ["rating/utility.py", "rating/utility/rates.py"], ids=["file", "package"]
)
def test_a_same_size_same_mtime_edit_reaches_the_next_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module_path: str
) -> None:
    files = {module_path: "VALUE = 1\n"}
    if module_path.endswith("rates.py"):
        files["rating/utility/__init__.py"] = ""
    pipeline_dir = _project(tmp_path, files)
    monkeypatch.chdir(tmp_path)
    module = tmp_path / module_path
    stat = module.stat()
    statement = (
        "from utility import VALUE\n"
        if module_path.endswith("utility.py")
        else "from utility.rates import VALUE\n"
    )

    first = _compile_preamble(statement, pipeline_dir=str(pipeline_dir))
    module.write_text("VALUE = 2\n", encoding="utf-8")
    os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = _compile_preamble(statement, pipeline_dir=str(pipeline_dir))

    assert (first["VALUE"], second["VALUE"]) == (1, 2)
    # The loader compiles from source and writes no bytecode a later edit could hide behind.
    assert not list(tmp_path.rglob("*.pyc"))


def test_utility_beside_the_pipeline_wins_over_one_at_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_dir = _project(
        tmp_path,
        {"utility.py": "WHERE = 'root'\n", "rating/utility.py": "WHERE = 'pipeline'\n"},
    )
    monkeypatch.chdir(tmp_path)

    ns = _compile_preamble("from utility import WHERE\n", pipeline_dir=str(pipeline_dir))

    assert ns["WHERE"] == "pipeline"


def test_a_local_module_outside_utility_is_not_importable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Haute puts no project directory on the import path, so ``utility`` is the only
    project code it imports, as only it is deployed. (The interpreter's own path is its
    own: ``python -m`` adds the working directory, so the test leaves that entry out.)"""
    pipeline_dir = _project(tmp_path, {"rating/helpers.py": "VALUE = 1\n"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry not in ("", ".")])

    with pytest.raises(PreambleError, match="No module named 'helpers'"):
        _compile_preamble("from helpers import VALUE\n", pipeline_dir=str(pipeline_dir))


def test_a_file_named_like_an_installed_package_does_not_shadow_it(tmp_path: Path) -> None:
    """``six`` is installed and not imported by the compile path, so a fresh interpreter
    imports it for the first time from the preamble."""
    pipeline_dir = _project(tmp_path, {"rating/six.py": "SHADOW = True\n"})
    script = textwrap.dedent(
        f"""
        import os
        os.chdir({str(tmp_path)!r})
        from haute.executor import _compile_preamble
        ns = _compile_preamble(
            "import six\\nSHADOWED = hasattr(six, 'SHADOW')\\n",
            pipeline_dir={str(pipeline_dir)!r},
        )
        print(ns["SHADOWED"])
        """
    )

    shown = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=120
    )

    assert shown.stdout.strip().splitlines()[-1] == "False"


def test_the_fingerprint_and_the_deploy_bundle_read_the_utility_the_finder_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_dir = _project(
        tmp_path,
        {"utility/__init__.py": "WHERE = 'root'\n", "rating/utility/__init__.py": "WHERE = 'x'\n"},
    )
    monkeypatch.chdir(tmp_path)
    _compile_preamble("import utility\n", pipeline_dir=str(pipeline_dir))
    loaded = Path(sys.modules["utility"].__file__ or "").resolve().parent

    assert _resolve_utility_locations(pipeline_dir) == [loaded]
    assert resolve_project_modules("import utility\n", pipeline_dir).utility == loaded


def test_a_project_without_utility_leaves_the_name_to_the_import_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deploy bundle's own copy is found where its runtime puts it (an MLflow code path)."""
    pipeline_dir = _project(tmp_path / "project", {"rating/main.py": ""})
    code_path = tmp_path / "code"
    (code_path / "utility").mkdir(parents=True)
    (code_path / "utility" / "__init__.py").write_text("WHERE = 'code path'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "project")
    monkeypatch.syspath_prepend(str(code_path))

    ns = _compile_preamble("from utility import WHERE\n", pipeline_dir=str(pipeline_dir))

    assert ns["WHERE"] == "code path"
