"""The safety-critical modules are one list, named three times.

The changed-code gate (``[tool.haute.changed_coverage].paths``), the per-file
critical floors (``[tool.haute.critical_coverage]``) and the engineering-quality
specification each name the safety-critical modules. A module on one list and not
another is held by one gate and silently skipped by the other, so the three are
compared as the gates read them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import check_changed_coverage, check_critical_coverage
from tests._source_files import REPO_ROOT

pytestmark = pytest.mark.meta

PYPROJECT = REPO_ROOT / "pyproject.toml"
SPEC = REPO_ROOT / "specs" / "engineering-quality" / "high-level.md"
_SPEC_LIST_START = "Under `src/haute/` they are:"
_SPEC_LIST_END = "list exactly this set"
_EXTRA = "src/haute/_not_safety_critical.py"


def spec_paths(text: str) -> frozenset[str]:
    """The modules the specification's list names, as repository paths.

    The list names modules relative to ``src/haute/``, except where a bare name
    would be ambiguous and is written in full (``src/haute/_cache.py``).
    """
    prose = " ".join(text.split())
    start = prose.index(_SPEC_LIST_START) + len(_SPEC_LIST_START)
    end = prose.index(_SPEC_LIST_END, start)
    names = re.findall(r"`([^`]+\.py)`", prose[start:end])
    return frozenset(name if name.startswith("src/") else f"src/haute/{name}" for name in names)


def list_differences(pyproject: Path, spec_text: str) -> list[str]:
    """Each module some list names and another leaves out."""
    lists = {
        "changed-code paths": set(check_changed_coverage._load_config(pyproject).paths),
        "critical floors": {
            entry.path for entry in check_critical_coverage._load_config(pyproject).files
        },
        "specification": set(spec_paths(spec_text)),
    }
    named = set().union(*lists.values())
    return sorted(
        f"{path} is missing from the {label}"
        for path in named
        for label, members in lists.items()
        if path not in members
    )


def test_the_gates_and_the_specification_name_the_same_modules() -> None:
    assert list_differences(PYPROJECT, SPEC.read_text(encoding="utf-8")) == []


def test_the_specification_list_is_read() -> None:
    assert "src/haute/_rating.py" in spec_paths(SPEC.read_text(encoding="utf-8"))


def _pyproject_with_extra_path(tmp_path: Path, table: str) -> Path:
    text = PYPROJECT.read_text(encoding="utf-8")
    if table == "changed":
        marker = "[tool.haute.changed_coverage]"
        head, tail = text.split(marker, maxsplit=1)
        tail = tail.replace("paths = [\n", f'paths = [\n    "{_EXTRA}",\n', 1)
        text = head + marker + tail
    else:
        text += (
            "\n[[tool.haute.critical_coverage.files]]\n"
            f'path = "{_EXTRA}"\n'
            "min_statement_coverage = 90.0\n"
            "min_branch_coverage = 90.0\n"
            'rationale = "A module added to one list only."\n'
        )
    copy = tmp_path / "pyproject.toml"
    copy.write_text(text, encoding="utf-8")
    return copy


@pytest.mark.parametrize(
    ("table", "missing_from"),
    [
        ("changed", ["critical floors", "specification"]),
        ("critical", ["changed-code paths", "specification"]),
    ],
)
def test_a_module_added_to_one_gate_alone_is_named(
    tmp_path: Path, table: str, missing_from: list[str]
) -> None:
    pyproject = _pyproject_with_extra_path(tmp_path, table)

    assert list_differences(pyproject, SPEC.read_text(encoding="utf-8")) == [
        f"{_EXTRA} is missing from the {label}" for label in missing_from
    ]


def test_a_module_added_to_the_specification_alone_is_named() -> None:
    text = SPEC.read_text(encoding="utf-8").replace(
        "  - rating: `_rating.py`,", "  - rating: `_not_safety_critical.py`, `_rating.py`,", 1
    )

    assert list_differences(PYPROJECT, text) == [
        f"{_EXTRA} is missing from the changed-code paths",
        f"{_EXTRA} is missing from the critical floors",
    ]
