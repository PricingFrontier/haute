"""The params files a schema column's allowed values come from (specs/workbench): a CSV in the
pipeline folder's ``files/params/``, its header the columns offered, a column's distinct values
the allowed values, read as written, and read again for a release."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from haute import _workbench_params
from haute._workbench_form import FormSpec, OptionsSource
from haute._workbench_params import ParamFileError, read_param_file, with_param_options


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project whose pipeline sits in ``rating/``, its params folder made."""
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n', encoding="utf-8"
    )
    (tmp_path / "rating" / "files" / "params").mkdir(parents=True)
    return tmp_path


def params(project: Path, name: str, data: bytes) -> None:
    (project / "rating" / "files" / "params" / name).write_bytes(data)


SIC = (
    "occupancy,sic_number,sic_name\n"
    "Food,5147,Meats and Meat Products\n"
    'Cloth,02399,"Fabricated Textile Products, Not Elsewhere Classified"\n'
    "Food, 5147 ,Meats and Meat Products\n"
    "Builders,,\n"
    "Hangars,4581,  Airports  \n"
)


def test_a_file_s_header_names_its_columns_and_gives_no_values(project: Path) -> None:
    params(project, "sic_codes.csv", SIC.encode("utf-8"))

    read = read_param_file(project, "sic_codes.csv")

    assert read.columns == ["occupancy", "sic_number", "sic_name"]
    assert read.values == []


def test_a_column_gives_its_distinct_values_as_written_in_the_order_the_file_gives_them(
    project: Path,
) -> None:
    params(project, "sic_codes.csv", SIC.encode("utf-8"))

    # Text as written: the zero kept, each value trimmed, blanks left out, each value once.
    assert read_param_file(project, "sic_codes.csv", "sic_number").values == [
        "5147",
        "02399",
        "4581",
    ]
    assert read_param_file(project, "sic_codes.csv", "sic_name").values == [
        "Meats and Meat Products",
        "Fabricated Textile Products, Not Elsewhere Classified",
        "Airports",
    ]


def test_a_file_saved_with_a_byte_order_mark_reads_as_written(project: Path) -> None:
    params(project, "states.csv", "﻿state\nNY\nCA\n".encode())

    assert read_param_file(project, "states.csv", "state").values == ["NY", "CA"]


def test_a_file_excel_saved_as_windows_1252_reads_as_written(project: Path) -> None:
    # Excel's "CSV (Comma delimited)" on Windows: its non-breaking space trimmed like any space.
    params(project, "states.csv", "state\nQuébec\n Ontario\n".encode("cp1252"))

    assert read_param_file(project, "states.csv", "state").values == ["Québec", "Ontario"]


def test_a_header_excel_saved_as_windows_1252_is_offered_as_written(project: Path) -> None:
    # A header's bytes are read as the file's encoding, not replaced where UTF-8 fails.
    params(project, "regions.csv", "Région,Limit (£)\nÎle,5\n".encode("cp1252"))

    read = read_param_file(project, "regions.csv", "Région")
    assert (read.columns, read.values) == (["Région", "Limit (£)"], ["Île"])


def test_a_header_mangled_by_an_earlier_save_is_refused(project: Path) -> None:
    params(project, "regions.csv", "R�gion\nNorth\n".encode())

    with pytest.raises(ParamFileError, match="R�gion.*save it as CSV UTF-8"):
        read_param_file(project, "regions.csv")


@pytest.mark.parametrize(
    ("name", "data", "column", "message"),
    [
        ("gone.csv", None, None, "rating/files/params/gone.csv doesn't exist."),
        ("states.csv", b"", None, "rating/files/params/states.csv is empty."),
        (
            "states.csv",
            b"state\nA\x81B\n",
            None,
            "rating/files/params/states.csv isn't UTF-8 or Windows-1252 text: save it as CSV "
            "UTF-8.",
        ),
        (
            "states.csv",
            b"state\nNY\n",
            "code",
            "rating/files/params/states.csv has no column code.",
        ),
        (
            "states.csv",
            b"state,code\nNY,\nCA, \n",
            "code",
            "Column code of rating/files/params/states.csv holds no values.",
        ),
    ],
    ids=["missing", "empty", "undecodable", "no-such-column", "no-values"],
)
def test_a_file_or_column_that_cannot_give_values_says_why(
    project: Path, name: str, data: bytes | None, column: str | None, message: str
) -> None:
    if data is not None:
        params(project, name, data)

    with pytest.raises(ParamFileError) as caught:
        read_param_file(project, name, column)

    assert caught.value.message == message


def test_a_column_of_more_values_than_the_limit_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_workbench_params, "PARAM_VALUES_LIMIT", 2)
    params(project, "states.csv", b"state\nNY\nCA\nNY\n")
    assert read_param_file(project, "states.csv", "state").values == ["NY", "CA"]

    params(project, "states.csv", b"state\nNY\nCA\nTX\n")
    with pytest.raises(ParamFileError, match="holds more than 2 distinct values"):
        read_param_file(project, "states.csv", "state")


@pytest.mark.parametrize(
    "file",
    ["states.txt", "states", "../states.csv", "sub/states.csv", "sub\\states.csv", ".states.csv"],
)
def test_only_a_csv_directly_in_the_params_folder_is_read(project: Path, file: str) -> None:
    with pytest.raises(ParamFileError, match="isn't the name of a CSV file in the params folder"):
        read_param_file(project, file)


def test_a_project_whose_pipeline_sits_at_its_root_has_no_params_folder(tmp_path: Path) -> None:
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "main.py"\n', encoding="utf-8")

    with pytest.raises(ParamFileError, match="has no params folder"):
        read_param_file(tmp_path, "states.csv")


def _spec(*columns: dict[str, Any], role: str = "input") -> FormSpec:
    table = {"id": "t_policy", "name": "policy", "role": role, "columns": list(columns)}
    page = {
        "id": "p",
        "title": "Sheet 1",
        "width": 1280,
        "sections": [{"id": "s", "title": "s", "height": 120}],
    }
    return FormSpec.model_validate(
        {"version": 1, "name": "x", "schema": {"tables": [table]}, "pages": [page]}
    )


STATE_SOURCE = {"file": "states.csv", "column": "state"}


def test_a_column_s_values_are_read_again_from_its_file(project: Path) -> None:
    params(project, "states.csv", b"state\nNY\nCA\nTX\n")
    spec = _spec(
        {"id": "c_state", "name": "state", "options": ["NY"], "options_source": STATE_SOURCE},
        {"id": "c_cover", "name": "cover", "options": ["Full", "Basic"]},
    )

    read = with_param_options(spec, project)

    state, cover = read.data_schema.tables[0].columns
    assert state.options == ["NY", "CA", "TX"]
    assert state.options_source == OptionsSource(file="states.csv", column="state")
    # Values typed are the form's own.
    assert cover.options == ["Full", "Basic"]


def test_an_output_column_s_values_are_not_read_again(project: Path) -> None:
    # Output tables ignore the rules, so a file gone does not stop them.
    spec = _spec(
        {"id": "c_state", "name": "state", "options": ["NY"], "options_source": STATE_SOURCE},
        role="output",
    )

    assert with_param_options(spec, project) == spec


def test_a_column_whose_file_can_no_longer_give_its_values_is_named(project: Path) -> None:
    spec = _spec(
        {"id": "c_state", "name": "state", "options": ["NY"], "options_source": STATE_SOURCE}
    )

    with pytest.raises(ParamFileError) as caught:
        with_param_options(spec, project)

    assert caught.value.message == (
        "The allowed values of policy.state could not be read again: "
        "rating/files/params/states.csv doesn't exist."
    )
