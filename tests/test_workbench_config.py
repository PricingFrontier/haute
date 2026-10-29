"""The ``[workbench]`` table of ``haute.toml`` (specs/workbench): whether a project's workbench
is enabled, where its form folder is (the pipeline folder's ``workbench/``), and what is
refused."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from haute._workbench_config import WorkbenchConfigError, read_workbench_config
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
            "[workbench] has unknown key(s): enable, label; the keys are enabled and app",
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

    with pytest.raises(ValueError, match=r"\[workbench\] unknown key 'enable'"):
        _validate_toml_keys({"workbench": {"enable": True}}, path)
    with pytest.raises(ValueError, match=r"\[workbench\] unknown key 'form'"):
        _validate_toml_keys({"workbench": {"enabled": True, "form": "forms/form.json"}}, path)
