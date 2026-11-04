"""A workbench table and its columns are known by their names, with no ids (READ-23).

Spec: specs/workbench/high-level.md, the form and the form folder. No file, release or quote
holds an id for a table or a column; a rename is a removal and an addition, the store keeping
the removed column and the values saved in it; two names differing only in case are
refused. The store tests run on SQLite and on each server the environment names
(tests/_store_servers.py).
"""

from __future__ import annotations

import itertools
import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from haute._quote_store import PricedOutputs, offline_plan, open_book
from haute._quote_store._rows import KEPT_KEY
from haute._quote_store._schema import _named, store_columns, structure
from haute._workbench_folder import FormFolderNameError, read_form_document
from haute._workbench_folder_files import TableFile
from haute._workbench_form import (
    QUOTE_DETAILS_NAME,
    QUOTE_DETAILS_STORE_ONLY,
    FormSpec,
    SchemaColumn,
    SchemaTable,
    form_revision,
    identity_clashes,
    render_form,
    with_quote_details,
)
from tests._store_servers import Target, backends, database
from tests._workbench_helpers import enable, form_folder, write_files, write_form

ANA = "ana@example.com"
RELEASE = "release-1"
_keys = itertools.count(1)


def _form(*, limit: str = "limit", extra: list[dict[str, Any]] | None = None) -> FormSpec:
    """A form of a one-row input table ``policy`` (its limit column named *limit*) and an
    output ``premium``, a Collection showing both input columns."""
    return with_quote_details(
        FormSpec.model_validate(
            {
                "name": "names",
                "schema": {
                    "tables": [
                        {
                            "name": "policy",
                            "role": "input",
                            "rows": "one",
                            "columns": [
                                {"name": "region", "type": "str"},
                                {"name": limit, "type": "float"},
                                *(extra or []),
                            ],
                        },
                        {
                            "name": "premium",
                            "role": "output",
                            "rows": "one",
                            "columns": [{"name": "total", "type": "float"}],
                        },
                    ]
                },
                "pages": [
                    {
                        "id": "p",
                        "title": "Quote",
                        "width": 1280,
                        "sections": [
                            {
                                "id": "s",
                                "title": "Cover",
                                "height": 200,
                                "widgets": [
                                    {
                                        "id": "w",
                                        "type": "collection",
                                        "x": 0,
                                        "y": 0,
                                        "w": 400,
                                        "h": 100,
                                        "title": "Cover",
                                        "fields": [
                                            {"table": "policy", "column": "region"},
                                            {"table": "policy", "column": limit},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "sample": {"policy": [{"region": "north", limit: "1000"}]},
            }
        )
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    enable(root)
    return root


def _read(project: Path) -> FormSpec:
    from haute._workbench_config import read_workbench_config

    return read_form_document(read_workbench_config(project)).spec


# -- The form, its folder and its release ---------------------------------------------------


def test_no_table_or_column_holds_an_id_in_the_model_the_files_or_a_release(
    project: Path,
) -> None:
    assert "id" not in SchemaTable.model_fields
    assert "id" not in SchemaColumn.model_fields

    files = write_form(project, _form())

    for path, data in files.items():
        if path.startswith("schema/"):
            assert b'"id"' not in data, path
    released = json.loads(render_form(_read(project)))
    for table in released["schema"]["tables"]:
        assert "id" not in table
        assert all("id" not in column for column in table["columns"])
    assert released["sample"] == {"policy": [{"region": "north", "limit": "1000"}]}


def test_building_one_form_twice_gives_one_release_id(project: Path) -> None:
    write_form(project, _form())
    first = form_revision(render_form(_read(project)).encode("utf-8"))
    write_form(project, _read(project))
    assert form_revision(render_form(_read(project)).encode("utf-8")) == first


def test_a_schema_file_holding_an_id_is_refused(project: Path) -> None:
    write_form(project, _form())
    data = json.loads((form_folder(project) / "schema" / "policy.json").read_bytes())
    data["id"] = "t_policy"
    write_files(form_folder(project), {"schema/policy.json": json.dumps(data).encode("utf-8")})

    with pytest.raises(Exception, match="id"):
        _read(project)


def test_a_revision_saved_before_the_change_is_refused_naming_its_conversion(
    project: Path,
) -> None:
    """The schema pane's SQL from the default branch reads its form at that revision: one
    saved with ids is refused naming the conversion, not handed to the parser (READ-20)."""
    from haute._workbench_config import read_workbench_config
    from haute._workbench_folder import WorkbenchFormError, form_at_ref

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=project,
            check=True,
            capture_output=True,
        )

    write_form(project, _form())
    data = json.loads((form_folder(project) / "schema" / "policy.json").read_bytes())
    data["id"] = "t_policy"
    write_files(form_folder(project), {"schema/policy.json": json.dumps(data).encode("utf-8")})
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "form saved with ids")

    with pytest.raises(WorkbenchFormError, match="read_23_schema_names.py"):
        form_at_ref(read_workbench_config(project), "HEAD")


def test_two_columns_differing_only_in_case_are_refused_on_save_and_on_read(
    project: Path,
) -> None:
    form = _form(extra=[{"name": "Region", "type": "str"}])
    with pytest.raises(FormFolderNameError, match="Region"):
        write_form(project, form)
    with pytest.raises(ValidationError, match="whatever its case"):
        TableFile.model_validate(
            {
                "role": "input",
                "rows": "one",
                "columns": [{"name": "region", "type": "str"}, {"name": "REGION", "type": "str"}],
            }
        )


def test_a_tables_own_column_named_as_the_identity_is_kept_and_named_a_clash(
    project: Path,
) -> None:
    form = _form(extra=[{"name": "quote_option", "type": "str", "label": "Option"}])
    policy = next(table for table in form.data_schema.tables if table.name == "policy")

    assert [column.name for column in policy.columns][:1] == ["region"]
    assert identity_clashes(form) == [
        "policy.quote_option has a name Haute keeps for the quote's identity in every table: "
        "rename it, and the table takes quote_reference and quote_option at the next save"
    ]
    files = write_form(project, form)
    written = json.loads(files["schema/policy.json"])
    assert [column["name"] for column in written["columns"]] == ["region", "limit", "quote_option"]


# -- The quote store ------------------------------------------------------------------------


def test_the_store_plans_and_keeps_its_columns_by_name() -> None:
    form = _form()
    tables = {table["key"]: table for table in structure(form)["tables"]}

    assert set(tables) == {QUOTE_DETAILS_NAME, "policy", "premium"}
    assert [column["key"] for column in tables["policy"]["columns"]] == [
        "quote_reference",
        "quote_option",
        "region",
        "limit",
    ]
    # The store's own columns, and the quote_details columns no request holds, by name.
    assert set(store_columns(form)) == {QUOTE_DETAILS_NAME}
    details = next(table for table in form.data_schema.tables if table.name == QUOTE_DETAILS_NAME)
    assert {column.name for column in details.columns} >= QUOTE_DETAILS_STORE_ONLY


def test_a_renamed_column_is_one_removed_which_the_sql_keeps_and_one_added() -> None:
    steps = offline_plan(_form(), _form(limit="cover_limit")).steps

    kinds = {(step.kind, step.column) for step in steps if step.table == "policy"}
    assert ("add column", "cover_limit") in kinds
    assert ("keep column", "limit") in kinds
    assert not any(step.kind.startswith("rename") for step in steps)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture(params=backends())
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as target:
        yield target


def _open(store: Target, form: FormSpec) -> Any:
    return open_book(
        store.url,
        form,
        environment=store.environment,
        haute_version="9.9.9",
        clock=_Clock(),
        sign_in=store.sign_in,
    )


def _values(**policy: Any) -> dict[str, Any]:
    return {
        QUOTE_DETAILS_NAME: [{"quote_name": "Fleet", "insured": "Acme"}],
        "policy": [{"region": "north", **policy}],
    }


def _saved(book: Any, values: dict[str, Any]) -> tuple[str, int]:
    quote = book.create(
        operation=f"operation-{next(_keys):06d}",
        actor=ANA,
        at=book.now(),
        reference="N-1",
        values=values,
    ).quote
    book.save(
        quote.reference,
        quote.option,
        operation=f"operation-{next(_keys):06d}",
        version=quote.version,
        actor=ANA,
        values=values,
        priced=PricedOutputs(RELEASE, {"premium": {"total": 12.5}}),
        at=book.now(),
    )
    return quote.reference, quote.option


def test_a_renamed_column_starts_empty_and_the_old_keeps_its_values_through_a_save(
    store: Target,
) -> None:
    old, new = _form(), _form(limit="cover_limit")
    store.make(old)
    with _open(store, old) as book:
        identity = _saved(book, _values(limit="1000"))
    store.make(new, base=old)

    with _open(store, new) as book:
        opened = book.read(*identity)
        row = opened.values["policy"][0]
        assert "cover_limit" not in row
        assert json.loads(row[KEPT_KEY]) == {"limit": 1000.0}
        quote = book.get(*identity)
        book.save(
            *identity,
            operation=f"operation-{next(_keys):06d}",
            version=quote.version,
            actor=ANA,
            values=opened.values,
            priced=None,
            at=book.now(),
        )

    assert store.read('SELECT "limit", cover_limit FROM policy') == [(1000.0, None)]


def test_a_store_whose_constraints_an_earlier_haute_named_from_ids_opens_and_saves(
    store: Target,
) -> None:
    form = _form()
    sql = store.sql(form)
    for table in ("policy", "premium"):
        for kind in ("pk", "fk"):
            sql = sql.replace(_named(kind, table), _named(kind, f"t_{table}"))
    assert _named("pk", "t_policy") in sql
    store.script(sql)

    with _open(store, form) as book:
        identity = _saved(book, _values(limit="250"))
        assert book.read(*identity).values["policy"][0]["limit"] == "250"
