"""The ``[workbench]`` table of ``haute.toml`` (specs/workbench): whether a project's workbench
is enabled, where its form folder is (the pipeline folder's ``workbench/``), and what is
refused."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from haute._workbench_config import (
    AppEnvironment,
    WorkbenchConfigError,
    read_workbench_config,
    store_environments,
)
from haute.deploy._config import _validate_toml_keys
from haute.errors import ConfigError
from tests._workbench_helpers import PIPELINE_SOURCE, enable


def _write(project: Path, text: str) -> None:
    (project / "haute.toml").write_text(text, encoding="utf-8")


def test_a_project_without_the_table_has_no_workbench(tmp_path: Path) -> None:
    assert read_workbench_config(tmp_path).enabled is False

    _write(tmp_path, '[project]\nname = "motor"\n')
    config = read_workbench_config(tmp_path)

    assert config.enabled is False
    with pytest.raises(WorkbenchConfigError, match="not enabled"):
        _ = config.form_folder


def test_the_table_enables_the_workbench_in_the_pipeline_folder(tmp_path: Path) -> None:
    enable(tmp_path)

    config = read_workbench_config(tmp_path)

    assert config.enabled is True
    assert config.form == "rating/workbench"
    assert config.form_folder == tmp_path / "rating" / "workbench"
    assert config.project_root == tmp_path


def test_a_pipeline_at_the_root_keeps_its_form_folder_at_the_root(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    _write(tmp_path, '[project]\nname = "x"\npipeline = "main.py"\n\n[workbench]\nenabled = true\n')

    assert read_workbench_config(tmp_path).form == "workbench"


def test_an_enabled_workbench_needs_the_project_s_pipeline_file(tmp_path: Path) -> None:
    named = '[project]\nname = "x"\npipeline = "rating/main.py"\n\n'
    _write(tmp_path, named + "[workbench]\nenabled = true\n")

    with pytest.raises(WorkbenchConfigError, match="needs the project's pipeline file"):
        read_workbench_config(tmp_path)

    # A disabled one does not look for it.
    _write(tmp_path, named + "[workbench]\nenabled = false\n")
    assert read_workbench_config(tmp_path).enabled is False


def test_the_app_table_names_where_the_underwriter_app_runs(tmp_path: Path) -> None:
    enable(tmp_path)
    assert read_workbench_config(tmp_path).app_host is None

    enable(
        tmp_path, '[workbench]\nenabled = true\n\n[workbench.app]\nhost = "azure-container-apps"\n'
    )
    assert read_workbench_config(tmp_path).app_host == "azure-container-apps"
    assert read_workbench_config(tmp_path).app_edit_role is None


def test_each_hosted_environment_names_the_container_app_it_runs_as(tmp_path: Path) -> None:
    """``[workbench.app.<name>]`` names where the host runs a store environment's app, each
    value left empty until IT fills it in (specs/workbench)."""
    stores = (
        "[workbench.store.staging]\nprovider = 'sqlite'\n\n"
        "[workbench.store.prod]\nprovider = 'sqlite'\n"
    )
    enable(
        tmp_path,
        "[workbench]\nenabled = true\n\n[workbench.app]\nhost = 'azure-container-apps'\n\n"
        "[workbench.app.staging]\nresource_group = ' pricing-rg '\n"
        "container_app = 'motor-staging'\n\n"
        "[workbench.app.prod]\n\n" + stores,
    )

    config = read_workbench_config(tmp_path)

    assert config.app_host == "azure-container-apps"
    assert config.app_environments == (
        AppEnvironment("staging", "pricing-rg", "motor-staging"),
        AppEnvironment("prod", "", ""),
    )


@pytest.mark.parametrize(
    ("app", "message"),
    [
        (
            "[workbench.app.test]\n",
            "[workbench.app.test] names no environment [workbench.store] declares",
        ),
        (
            "[workbench.app.prod]\nregion = 'uk'\n",
            "[workbench.app.prod] has unknown key(s): region",
        ),
        (
            "[workbench.app.prod]\ncontainer_app = 3\n",
            "[workbench.app.prod].container_app must be text",
        ),
        # host and edit_role are the app's own settings, never an environment's table.
        ("host = { name = 'azure' }\n", "[workbench.app].host must be one of azure-container-apps"),
        ("label = 'x'\n", "[workbench.app] has unknown key(s): label"),
    ],
    ids=["undeclared", "unknown-key", "not-text", "host-table", "unknown-setting"],
)
def test_a_hosted_environments_table_is_refused_naming_what_is_wrong(
    tmp_path: Path, app: str, message: str
) -> None:
    host = "host = 'azure-container-apps'\n" if not app.startswith("host") else ""
    enable(
        tmp_path,
        "[workbench]\nenabled = true\n\n[workbench.store.prod]\nprovider = 'sqlite'\n\n"
        f"[workbench.app]\n{host}{app}",
    )

    with pytest.raises(WorkbenchConfigError, match=re.escape(message)):
        read_workbench_config(tmp_path)


def test_the_store_tables_declare_the_hosted_environments_by_provider(tmp_path: Path) -> None:
    enable(tmp_path)
    assert read_workbench_config(tmp_path).store == ()

    # dev is declared as any other environment is.
    enable(
        tmp_path,
        "[workbench]\nenabled = true\n\n[workbench.store.dev]\nprovider = 'sqlite'\n\n"
        "[workbench.store.test]\nprovider = 'sqlite'\n\n"
        "[workbench.store.prod]\nprovider = 'sqlite'\n",
    )

    store = read_workbench_config(tmp_path).store
    assert [(each.name, each.provider, each.auth) for each in store] == [
        ("dev", "sqlite", None),
        ("test", "sqlite", None),
        ("prod", "sqlite", None),
    ]
    assert store[0].declared() == {"provider": "sqlite"}


def test_a_provider_that_signs_in_names_the_variables_its_sign_in_reads() -> None:
    # A database on a server signs the app in, by the variables the environment names.
    principal = {
        "provider": "sqlserver",
        "auth": "service-principal",
        "tenant_id_env": "STORE_TENANT",
        "client_id_env": "STORE_CLIENT",
        "client_secret_env": "STORE_SECRET",
    }

    [environment] = store_environments({"prod": principal}, "[workbench.store]")

    assert (environment.auth, dict(environment.variables)) == (
        "service-principal",
        {
            "tenant_id_env": "STORE_TENANT",
            "client_id_env": "STORE_CLIENT",
            "client_secret_env": "STORE_SECRET",
        },
    )
    assert environment.declared() == principal

    def declared(**settings: str) -> dict[str, str]:
        [one] = store_environments({"prod": settings}, "[workbench.store]")
        return dict(one.variables)

    # A system-assigned managed identity names nothing; a user-assigned one its client id.
    assert declared(provider="sqlserver", auth="managed-identity") == {}
    assert declared(provider="sqlserver", auth="managed-identity", client_id_env="APP_ID") == {
        "client_id_env": "APP_ID"
    }
    # On PostgreSQL an Entra ID sign-in names the role it takes.
    assert declared(provider="postgresql", auth="managed-identity", username_env="ROLE") == {
        "username_env": "ROLE"
    }
    for settings, says in (
        ({"provider": "sqlserver"}, "auth must be one of managed-identity"),
        ({**principal, "client_secret_env": "not a name"}, "must name the environment variable"),
        ({**principal, "username_env": "STORE_USER"}, "unknown key(s): username_env"),
        (
            {"provider": "postgresql", "auth": "managed-identity"},
            "username_env must name the environment variable",
        ),
    ):
        with pytest.raises(ValueError, match=re.escape(says)):
            store_environments({"prod": settings}, "[workbench.store]")


def test_the_app_table_names_the_role_that_may_change_quotes(tmp_path: Path) -> None:
    enable(
        tmp_path,
        '[workbench]\nenabled = true\n\n[workbench.app]\nhost = "azure-container-apps"\n'
        'edit_role = " Quotes.Edit "\n',
    )

    assert read_workbench_config(tmp_path).app_edit_role == "Quotes.Edit"


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("[workbench]\n", "[workbench].enabled must be true or false"),
        ('[workbench]\nenabled = "yes"\n', "[workbench].enabled must be true or false"),
        ("workbench = true\n", "[workbench] must be a TOML table"),
        (
            "[workbench]\nenabled = true\nenable = true\nlabel = 'x'\n",
            "[workbench] has unknown key(s): enable, label; the keys are enabled, app and store",
        ),
        (
            "[workbench]\nenabled = true\nform = 'forms/form.json'\n",
            "[workbench].form was removed: the form is the pipeline folder's workbench/ folder; "
            "delete the key",
        ),
        ("[workbench]\nenabled = true\napp = 'azure'\n", "[workbench.app] must be a TOML table"),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = 'azure-container-apps'\n"
            "port = 1\n",
            "[workbench.app] has unknown key(s): port; the keys are host and edit_role",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = 'azure-container-apps'\n"
            "edit_role = ' '\n",
            "[workbench.app].edit_role must be the name of a role the host signs underwriters in "
            "with; got ' '",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = 'azure-container-apps'\n"
            "edit_role = 3\n",
            "[workbench.app].edit_role must be the name of a role",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = 'heroku'\n",
            "[workbench.app].host must be one of azure-container-apps; got 'heroku'",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = []\n",
            "[workbench.app].host must be one of azure-container-apps; got []",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.app]\nhost = { name = 'azure' }\n",
            "[workbench.app].host must be one of azure-container-apps; got {'name': 'azure'}",
        ),
        ("[workbench]\nenabled = true\nstore = 'test'\n", "[workbench.store] must be a table"),
        (
            "[workbench]\nenabled = true\n[workbench.store.Prod]\nprovider = 'sqlite'\n",
            "[workbench.store.Prod]: an environment's name is up to 32 lower-case letters",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.store.test]\nprovider = 'oracle'\n",
            "[workbench.store.test].provider must be one of postgresql, sqlite, sqlserver; got "
            "'oracle'",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.store.test]\nprovider = 'sqlite'\n"
            "auth = 'password'\n",
            "[workbench.store.test].auth: a sqlite database is a file, which takes no sign-in",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.store.test]\nprovider = 'sqlite'\npool = 4\n",
            "[workbench.store.test] has unknown key(s): pool; the keys are auth, provider",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.store.test]\nprovider = 'sqlite'\n"
            "database = 'quotes.sqlite'\n",
            "[workbench.store.test].database: where an environment's database is comes from the "
            "host's environment (HAUTE_STORE_DATABASE), never from a file",
        ),
        (
            "[workbench]\nenabled = true\n[workbench.store.test]\nprovider = 'sqlite'\n"
            "password = 'hunter2'\n",
            "[workbench.store.test].password would hold a secret's value: name the variable that "
            "holds it (password_env) instead",
        ),
    ],
)
def test_a_bad_table_is_refused_naming_what_to_set(
    tmp_path: Path, table: str, message: str
) -> None:
    # Each is refused before the pipeline file is looked for.
    _write(tmp_path, table)

    with pytest.raises(WorkbenchConfigError, match=re.escape(message)):
        read_workbench_config(tmp_path)


def test_a_malformed_haute_toml_is_refused_as_a_config_error(tmp_path: Path) -> None:
    _write(tmp_path, "[workbench\nenabled = true\n")

    with pytest.raises(WorkbenchConfigError, match="malformed") as caught:
        read_workbench_config(tmp_path)
    # A caller reading haute.toml for another purpose catches it as a ConfigError.
    assert isinstance(caught.value, ConfigError)


def test_a_haute_toml_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    (tmp_path / "haute.toml").write_bytes(b"[workbench]\nenabled = true\n# \xff\xfe\n")

    with pytest.raises(WorkbenchConfigError, match="haute.toml is not UTF-8 text"):
        read_workbench_config(tmp_path)


def _link_directory(target: Path, link: Path) -> None:
    """A directory link anyone can make: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_form_folder_leading_outside_the_project_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    enable(project)
    _link_directory(outside, project / "rating" / "workbench")

    with pytest.raises(WorkbenchConfigError, match="must stay inside the project"):
        read_workbench_config(project)


def test_deploy_s_whole_file_check_accepts_the_table_and_refuses_an_unknown_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "haute.toml"
    _validate_toml_keys({"workbench": {"enabled": True}}, path)
    _validate_toml_keys(
        {"workbench": {"enabled": True, "app": {"host": "azure-container-apps"}}}, path
    )
    _validate_toml_keys(
        {"workbench": {"enabled": True, "store": {"test": {"provider": "x"}}}}, path
    )

    with pytest.raises(ValueError, match=r"\[workbench\] unknown key 'enable'"):
        _validate_toml_keys({"workbench": {"enable": True}}, path)
    with pytest.raises(ValueError, match=r"\[workbench\] unknown key 'form'"):
        _validate_toml_keys({"workbench": {"enabled": True, "form": "forms/form.json"}}, path)
