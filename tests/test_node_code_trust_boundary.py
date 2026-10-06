"""Node code is trusted project code; the exec guard is an accident guard (ENG-T04, F1).

The 5 September 2026 review showed node text reaching the operating system
through the injected Polars module (``pl.io.csv.functions.os``). The product
decision of 6 September 2026 is that project code (node text, preambles,
utility modules, training scripts) is trusted first-party code: it runs with
the privileges of the process that runs haute, and haute does not contain it.
SBX-R02 made the guard match: it rejects only a direct call that would hang or
stop the server, and ordinary Python runs. These witnesses run through the real
node entry point ``_exec_user_code`` and pin both halves of that decision: the
server-stopping calls are rejected before any code runs, ordinary Python
(classes, reflection, ``global``, imports) executes, and the reachability of the
process environment and of paths outside the project is stated rather than
silently assumed away.
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
    @pytest.mark.parametrize("call", ["input()", "exit()", "quit()", "breakpoint()"])
    def test_a_server_stopping_call_is_rejected_before_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str
    ) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        sentinel = workdir / "sentinel.txt"
        sentinel.write_text("intact", encoding="utf-8")
        monkeypatch.chdir(workdir)
        code = f'open("sentinel.txt", "w").write("clobbered")\ndf = rows\n{call}'

        with pytest.raises(UnsafeCodeError, match="pipeline code cannot call it"):
            _run(code, pl.LazyFrame({"x": [1]}))

        assert sentinel.read_text(encoding="utf-8") == "intact"

    def test_ordinary_python_runs_in_a_node(self) -> None:
        """Classes, reflection, ``global`` and imports are ordinary Python in a node."""
        code = (
            "import math\n"
            "class Scale:\n"
            "    factor = 2\n"
            "count = 0\n"
            "def bump():\n"
            "    global count\n"
            "    count += 1\n"
            "bump()\n"
            "factor = getattr(Scale, 'factor')\n"
            "assert type(Scale()).__name__ == 'Scale' and 'factor' in vars(Scale)\n"
            "scale = factor * int(math.sqrt(count))\n"
            "df = rows.with_columns((pl.col('x') * scale).alias('scaled'))\n"
        )
        out = _run(code, pl.LazyFrame({"x": [1, 2]}))
        assert out.collect()["scaled"].to_list() == [2, 4]

    def test_a_dataclass_in_node_code_behaves_as_in_a_module(self) -> None:
        """The class records the project-code module and its annotations are objects,
        so a ClassVar default and get_type_hints on an imported type work."""
        code = (
            "from dataclasses import dataclass\n"
            "from decimal import Decimal\n"
            "from typing import ClassVar, get_type_hints\n"
            "@dataclass\n"
            "class Scale:\n"
            "    factor: ClassVar[int] = 2\n"
            "    value: Decimal\n"
            "assert get_type_hints(Scale)['value'] is Decimal\n"
            "assert Scale.__module__.startswith('haute_project_code_')\n"
            "scale = Scale(Decimal(3))\n"
            "df = rows.with_columns(pl.lit(int(scale.value) * Scale.factor).alias('scaled'))\n"
        )
        out = _run(code, pl.LazyFrame({"x": [1]}))
        assert out.collect()["scaled"].to_list() == [6]

    @pytest.mark.parametrize(
        ("header", "classvar", "decimal"),
        [
            pytest.param("", "'ClassVar[int]'", "'Decimal'", id="quoted"),
            pytest.param(
                "from __future__ import annotations\n", "ClassVar[int]", "Decimal", id="postponed"
            ),
        ],
    )
    def test_string_annotations_resolve_in_node_code(
        self, header: str, classvar: str, decimal: str
    ) -> None:
        """A quoted or explicitly postponed annotation resolves through the
        namespace's module, as in an importable module."""
        code = (
            header + "from dataclasses import dataclass\n"
            "from decimal import Decimal\n"
            "from typing import ClassVar, get_type_hints\n"
            "@dataclass\n"
            "class Scale:\n"
            f"    factor: {classvar} = 2\n"
            f"    value: {decimal}\n"
            "assert get_type_hints(Scale)['value'] is Decimal\n"
            "scaled = int(Scale(Decimal(3)).value) * Scale.factor\n"
            "df = rows.with_columns(pl.lit(scaled).alias('s'))\n"
        )
        out = _run(code, pl.LazyFrame({"x": [1]}))
        assert out.collect()["s"].to_list() == [6]

    @pytest.mark.parametrize(
        ("header", "classvar", "decimal"),
        [
            pytest.param("", "'ClassVar[int]'", "'Decimal'", id="quoted"),
            pytest.param(
                "from __future__ import annotations\n", "ClassVar[int]", "Decimal", id="postponed"
            ),
        ],
    )
    def test_string_annotations_resolve_in_the_preamble(
        self, header: str, classvar: str, decimal: str
    ) -> None:
        from haute.executor import _compile_preamble

        preamble = (
            header + "from dataclasses import dataclass\n"
            "from decimal import Decimal\n"
            "from typing import ClassVar, get_type_hints\n"
            "@dataclass\n"
            "class Scale:\n"
            f"    factor: {classvar} = 2\n"
            f"    value: {decimal}\n"
            "SCALE_HINTS = get_type_hints(Scale)\n"
        )
        namespace = _compile_preamble(preamble)
        from decimal import Decimal

        assert namespace["SCALE_HINTS"]["value"] is Decimal
        out = _exec_user_code(
            "df = rows.with_columns(pl.lit(int(Scale(3).value) * Scale.factor).alias('s'))",
            ["rows"],
            (pl.LazyFrame({"x": [1]}),),
            extra_ns=namespace,
        )
        assert out.collect()["s"].to_list() == [6]

    def test_the_project_code_module_is_unregistered_after_execution(self) -> None:
        import sys

        before = {name for name in sys.modules if name.startswith("haute_project_code_")}
        _run("df = rows", pl.LazyFrame({"x": [1]}))
        after = {name for name in sys.modules if name.startswith("haute_project_code_")}
        assert after == before

    def test_a_dataclass_from_the_preamble_reaches_node_code(self) -> None:
        from haute.executor import _compile_preamble

        preamble = (
            "from dataclasses import dataclass\n"
            "from typing import ClassVar\n"
            "@dataclass\n"
            "class Scale:\n"
            "    factor: ClassVar[int] = 2\n"
            "    value: int\n"
        )
        namespace = _compile_preamble(preamble)
        assert namespace["Scale"].__module__.startswith("haute_project_code_")
        out = _exec_user_code(
            "df = rows.with_columns(pl.lit(Scale(3).value * Scale.factor).alias('scaled'))",
            ["rows"],
            (pl.LazyFrame({"x": [1]}),),
            extra_ns=namespace,
        )
        assert out.collect()["scaled"].to_list() == [6]

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
