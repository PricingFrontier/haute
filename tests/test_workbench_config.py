"""The ``[workbench]`` table of ``haute.toml`` (specs/workbench): whether a project's workbench
is enabled and where its form is, and what is refused."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from haute._workbench_config import (
    DEFAULT_FORM_PATH,
    WorkbenchConfigError,
    read_workbench_config,
)
from haute.deploy._config import _validate_toml_keys
from haute.errors import ConfigError


def _write(project: Path, text: str) -> None:
    (project / "haute.toml").write_text(text, encoding="utf-8")


def test_a_project_without_the_table_has_no_workbench(tmp_path: Path) -> None:
    assert read_workbench_config(tmp_path).enabled is False

    _write(tmp_path, '[project]\nname = "motor"\n')
    config = read_workbench_config(tmp_path)

    assert config.enabled is False
    assert config.form == DEFAULT_FORM_PATH
    assert config.form_path == tmp_path / "forms" / "form.json"


def test_the_table_enables_the_workbench_with_the_default_form(tmp_path: Path) -> None:
    _write(tmp_path, '[project]\nname = "motor"\n\n[workbench]\nenabled = true\n')

    config = read_workbench_config(tmp_path)

    assert config.enabled is True
    assert config.form == "forms/form.json"
    assert config.form_path == tmp_path / "forms" / "form.json"
    assert config.project_root == tmp_path


def test_the_table_can_disable_the_workbench_and_name_its_form(tmp_path: Path) -> None:
    _write(tmp_path, '[workbench]\nenabled = false\nform = "workbench/quote.json"\n')

    config = read_workbench_config(tmp_path)

    assert config.enabled is False
    assert config.form == "workbench/quote.json"
    assert config.form_path == tmp_path / "workbench" / "quote.json"


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("[workbench]\n", "[workbench].enabled must be true or false"),
        ('[workbench]\nenabled = "yes"\n', "[workbench].enabled must be true or false"),
        ("workbench = true\n", "[workbench] must be a TOML table"),
        (
            "[workbench]\nenabled = true\nenable = true\nlabel = 'x'\n",
            "[workbench] has unknown key(s): enable, label; the keys are enabled and form",
        ),
        ("[workbench]\nenabled = true\nform = 3\n", "[workbench].form must be a path"),
        ("[workbench]\nenabled = true\nform = '  '\n", "[workbench].form must be a path"),
        (
            "[workbench]\nenabled = true\nform = '../elsewhere/form.json'\n",
            "[workbench].form must stay inside the project",
        ),
    ],
)
def test_a_bad_table_is_refused_naming_what_to_set(
    tmp_path: Path, table: str, message: str
) -> None:
    _write(tmp_path, table)

    with pytest.raises(WorkbenchConfigError, match=re.escape(message)):
        read_workbench_config(tmp_path)


def test_an_absolute_form_path_is_refused(tmp_path: Path) -> None:
    absolute = (tmp_path / "forms" / "form.json").resolve()
    _write(
        tmp_path, f"[workbench]\nenabled = true\nform = {str(absolute).replace(chr(92), '/')!r}\n"
    )

    with pytest.raises(WorkbenchConfigError, match="must be relative to the project root"):
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


def test_the_default_form_path_is_refused_when_forms_leads_outside_the_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "form.json").write_text("{}", encoding="utf-8")
    _link_directory(outside, project / "forms")

    # The default path resolves through the link to a file outside the project.
    _write(project, "[workbench]\nenabled = true\n")
    with pytest.raises(WorkbenchConfigError, match="must stay inside the project"):
        read_workbench_config(project)

    # A configured path through the same link is refused alike.
    _write(project, '[workbench]\nenabled = true\nform = "forms/form.json"\n')
    with pytest.raises(WorkbenchConfigError, match="must stay inside the project"):
        read_workbench_config(project)


def test_deploy_s_whole_file_check_accepts_the_table_and_refuses_an_unknown_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "haute.toml"
    _validate_toml_keys({"workbench": {"enabled": True, "form": "forms/form.json"}}, path)

    with pytest.raises(ValueError, match=r"\[workbench\] unknown key 'enable'"):
        _validate_toml_keys({"workbench": {"enable": True}}, path)
