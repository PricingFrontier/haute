"""Node code is trusted project code; the exec guard is an accident guard (ENG-T04, F1).

The 5 September 2026 review showed node text reaching the operating system
through the injected Polars module (``pl.io.csv.functions.os``). The product
decision of 6 September 2026 is that project code (node text, preambles,
utility modules, training scripts) is trusted first-party code: it runs with
the privileges of the process that runs haute, and haute does not contain it.
These witnesses run through the real node entry point ``_exec_user_code`` and
pin both halves of that decision: the guard still rejects the documented
accident shapes before any code runs while permitted transforms execute, and
the reachability of the process environment and of paths outside the project
is stated rather than silently assumed away.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._sandbox import UnsafeCodeError
from haute._user_exec import _exec_user_code

MARKER = "HAUTE_TEST_TRUST_BOUNDARY_MARKER"


def _run(code: str, frame: pl.LazyFrame) -> pl.LazyFrame:
    """Execute *code* exactly as a Polars node with one input called ``rows``."""
    return _exec_user_code(code, ["rows"], (frame,))


class TestAccidentGuardAtTheNodeEntryPoint:
    @pytest.mark.parametrize(
        "code",
        [
            'df = rows\nopen("sentinel.txt", "w").write("clobbered")',
            'df = rows\n__import__("os").remove("sentinel.txt")',
            "import os\ndf = rows",
            'df = rows\ngetattr(rows, "__class__")',
            'df = rows\neval("1 + 1")',
        ],
        ids=["open", "dunder-import", "import", "reflection", "eval"],
    )
    def test_documented_accident_shapes_are_rejected_before_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
    ) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        sentinel = workdir / "sentinel.txt"
        sentinel.write_text("intact", encoding="utf-8")
        monkeypatch.chdir(workdir)

        with pytest.raises(UnsafeCodeError):
            _run(code, pl.LazyFrame({"x": [1]}))

        assert sentinel.read_text(encoding="utf-8") == "intact"

    def test_permitted_transform_runs_through_the_same_entry_point(self) -> None:
        out = _run(
            'df = rows.with_columns((pl.col("x") * 2).alias("doubled"))',
            pl.LazyFrame({"x": [1, 2, 3]}),
        )
        assert out.collect().to_dict(as_series=False) == {"x": [1, 2, 3], "doubled": [2, 4, 6]}


class TestNodeCodeIsTrustedProjectCode:
    """The guard is not a containment boundary, and the specification says so.

    This is the accepted posture, not a defect: the injected Polars module
    carries the whole Python object graph, so node text can read the process
    environment and write outside the project exactly as a preamble can. The
    test keeps that fact visible so nobody mistakes the accident guard for
    isolation; if the product ever adopts real containment, this test is the
    one that must flip.
    """

    def test_environment_and_outside_writes_remain_reachable_through_polars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MARKER, "visible-to-project-code")
        project = tmp_path / "project"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        monkeypatch.chdir(project)
        target = outside / "written_by_node_code.csv"

        code = (
            "df = rows.with_columns(\n"
            f'    pl.lit(pl.io.csv.functions.os.environ["{MARKER}"]).alias("marker")\n'
            ")\n"
            f"rows.collect().write_csv({str(target)!r})\n"
        )
        out = _run(code, pl.LazyFrame({"x": [1]}))

        assert out.collect()["marker"].to_list() == ["visible-to-project-code"]
        assert target.is_file()
        assert not (project / target.name).exists()
