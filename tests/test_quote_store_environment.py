"""Where a hosted app's quote store is (specs/workbench): the environment the host names,
among those the release declares, at the database the host's own variables say it is at,
with nothing of where it is taken from a file and nothing of it said back."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._quote_store import QuoteStoreEnvironmentError, resolve_store, store_url
from haute._workbench_config import StoreEnvironment

DECLARED = (StoreEnvironment("test", "sqlite"), StoreEnvironment("prod", "sqlite"))


def test_a_sqlite_environment_is_the_file_its_database_variable_names(tmp_path: Path) -> None:
    path = tmp_path / "test.sqlite"

    store = resolve_store(DECLARED, "test", {"HAUTE_STORE_DATABASE": str(path)})

    assert (store.environment, store.url) == ("test", store_url(path))
    # The URL is the store's alone: never shown.
    assert str(path.name) not in repr(store)


def test_a_whole_url_locates_the_environment_too(tmp_path: Path) -> None:
    url = store_url(tmp_path / "prod.sqlite")

    assert resolve_store(DECLARED, "prod", {"HAUTE_STORE_URL": url}).url == url


def test_an_environment_the_release_does_not_declare_is_refused_naming_those_it_does() -> None:
    with pytest.raises(QuoteStoreEnvironmentError, match="'staging'.*declares test, prod"):
        resolve_store(DECLARED, "staging", {"HAUTE_STORE_DATABASE": "x.sqlite"})
    with pytest.raises(QuoteStoreEnvironmentError, match="declares none"):
        resolve_store((), "test", {})


@pytest.mark.parametrize(
    ("environ", "says"),
    [
        ({}, "was not given"),
        (
            {"HAUTE_STORE_URL": "sqlite:///hidden-a.sqlite", "HAUTE_STORE_DATABASE": "hidden-b"},
            "given twice",
        ),
        ({"HAUTE_STORE_SERVER": "hidden.db.example"}, "has no server and no schema"),
        ({"HAUTE_STORE_DATABASE": "hidden-a", "HAUTE_STORE_SCHEMA": "hidden-quotes"}, "no schema"),
        ({"HAUTE_STORE_URL": "postgresql://hidden.db.example/quotes"}, "not a sqlite URL"),
    ],
    ids=["not at all", "twice", "a server", "a schema", "another provider's URL"],
)
def test_a_location_given_twice_not_at_all_or_unfit_is_refused_naming_no_location(
    environ: dict[str, str], says: str
) -> None:
    with pytest.raises(QuoteStoreEnvironmentError, match=says) as refused:
        resolve_store(DECLARED, "test", environ)

    assert refused.value.error_code == "quote_store_environment"
    for value in environ.values():
        assert value not in str(refused.value)
