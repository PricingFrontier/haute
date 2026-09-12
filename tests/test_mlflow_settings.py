"""Tests for haute.modelling._mlflow_settings.

Covers the [mlflow] haute.toml round trip, MLFLOW_TRACKING_URI form
classification, and the destination inventory precedence matrix defined in
specs/modelling/low-level.md (Shared MLflow tracking/experiment-name resolution).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from haute.errors import MlflowConfigError
from haute.modelling._mlflow_settings import (
    DestinationEntry,
    MlflowDestinationUnconfigured,
    MlflowSettings,
    TrackingConfig,
    candidate_tracking_config,
    classify_tracking_uri,
    list_destinations,
    load_mlflow_settings,
    resolve_destination,
    resolve_tracking_config,
    save_mlflow_settings,
    validate_destination_key,
)

_TOML_WITH_COMMENTS = """\
# Haute project configuration
# Created by: haute init

[project]
name = "main"                  # project name comment
pipeline = "rating/main.py"

[deploy]
target = "databricks"          # deployment target
model_name = "motor-pricing"
"""


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "haute.toml").write_text(_TOML_WITH_COMMENTS, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
        "MLFLOW_ENABLE_DB_SDK",
    ):
        monkeypatch.delenv(var, raising=False)


_DATABRICKS_UNCONFIGURED_DETAIL = (
    "Databricks is not configured for MLflow: set MLFLOW_TRACKING_URI=databricks://<profile> "
    "or both DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN in the environment (.env)."
)


# ---------------------------------------------------------------------------
# classify_tracking_uri
# ---------------------------------------------------------------------------


class TestClassifyTrackingUri:
    def test_bare_databricks_is_databricks_mode(self) -> None:
        assert classify_tracking_uri("databricks") == ("databricks", "databricks")

    def test_databricks_profile_uri_is_databricks_mode(self) -> None:
        mode, uri = classify_tracking_uri("databricks://myprofile")
        assert mode == "databricks"
        assert uri == "databricks://myprofile"

    @pytest.mark.parametrize(
        "value",
        ["http://localhost:5000", "https://mlflow.example.com/"],
    )
    def test_http_and_https_are_server_mode(self, value: str) -> None:
        mode, uri = classify_tracking_uri(value)
        assert mode == "server"
        assert uri == value

    def test_file_uri_is_local_mode_at_that_folder(self, tmp_path: Path) -> None:
        target = tmp_path / "team-runs"
        mode, uri = classify_tracking_uri(target.as_uri())
        assert mode == "local"
        assert uri == target.as_uri()

    def test_plain_relative_path_is_local_mode(self) -> None:
        mode, _uri = classify_tracking_uri("mlruns-alt")
        assert mode == "local"

    def test_windows_drive_path_is_local_mode(self) -> None:
        # A single-letter "scheme" is a drive letter, not a URI scheme.
        mode, _uri = classify_tracking_uri("C:/team-runs")
        assert mode == "local"

    @pytest.mark.parametrize("value", ["sqlite:///mlflow.db", "postgresql://db/mlflow"])
    def test_unsupported_scheme_raises_naming_the_scheme(self, value: str) -> None:
        scheme = value.split(":", 1)[0]
        with pytest.raises(MlflowConfigError, match=scheme):
            classify_tracking_uri(value)

    @pytest.mark.parametrize("value", ["http://", "https:example.com"])
    def test_http_uri_without_a_host_is_rejected(self, value: str) -> None:
        with pytest.raises(MlflowConfigError, match="host"):
            classify_tracking_uri(value)

    def test_unparseable_uri_is_config_error_without_echoing_the_value(self) -> None:
        with pytest.raises(MlflowConfigError) as excinfo:
            classify_tracking_uri("https://user:secret@[")
        assert "secret" not in str(excinfo.value)

    def test_credentialed_env_uri_still_classifies_as_server(self) -> None:
        mode, uri = classify_tracking_uri("https://alice:secret@mlflow.example.com")
        assert mode == "server"
        assert uri == "https://alice:secret@mlflow.example.com"

    def test_redaction_preserves_ipv6_brackets(self) -> None:
        from haute.modelling._mlflow_settings import redact_uri

        assert redact_uri("https://alice:pw@[::1]:5000/base") == "https://[::1]:5000/base"

    def test_invalid_port_is_config_error_without_echoing_credentials(self) -> None:
        with pytest.raises(MlflowConfigError) as excinfo:
            classify_tracking_uri("https://alice:hunter2xyz@mlflow.example.com:70000")
        message = str(excinfo.value)
        assert "port" in message.lower()
        assert "hunter2xyz" not in message


# ---------------------------------------------------------------------------
# Destination inventory tests
# ---------------------------------------------------------------------------


def _write_mlflow(project_root: Path, body: str) -> None:
    with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
        f.write("\n[mlflow]\n" + body)


class TestLoadSaveSettings:
    def test_load_returns_none_when_section_absent(self, project_root: Path) -> None:
        assert load_mlflow_settings(project_root) is None

    def test_mode_key_is_rejected_loudly(self, project_root: Path) -> None:
        _write_mlflow(project_root, 'mode = "local"\n')
        with pytest.raises(MlflowConfigError, match="mode"):
            load_mlflow_settings(project_root)

    def test_load_rejects_unknown_key(self, project_root: Path) -> None:
        _write_mlflow(project_root, 'registry = "x"\n')
        with pytest.raises(MlflowConfigError, match="registry"):
            load_mlflow_settings(project_root)

    def test_save_then_load_round_trips_both_keys(self, project_root: Path) -> None:
        save_mlflow_settings(
            MlflowSettings(tracking_uri="http://localhost:5000", folder="team-runs"), project_root
        )
        assert load_mlflow_settings(project_root) == MlflowSettings(
            tracking_uri="http://localhost:5000", folder="team-runs"
        )

    def test_save_preserves_other_sections_and_comments(self, project_root: Path) -> None:
        save_mlflow_settings(MlflowSettings(tracking_uri="http://localhost:5000"), project_root)
        text = (project_root / "haute.toml").read_text(encoding="utf-8")
        assert "# project name comment" in text
        assert 'target = "databricks"          # deployment target' in text
        data = tomllib.loads(text)
        assert data["project"]["name"] == "main"
        assert data["mlflow"] == {"tracking_uri": "http://localhost:5000", "folder": "mlruns"}

    def test_empty_tracking_uri_clears_the_key(self, project_root: Path) -> None:
        save_mlflow_settings(MlflowSettings(tracking_uri="http://localhost:5000"), project_root)
        save_mlflow_settings(MlflowSettings(tracking_uri=""), project_root)
        data = tomllib.loads((project_root / "haute.toml").read_text(encoding="utf-8"))
        assert "tracking_uri" not in data["mlflow"]

    def test_bare_save_persists_env_derived_folder(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = project_root / "custom-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", custom.as_uri())
        save_mlflow_settings(MlflowSettings(), project_root)
        stored = load_mlflow_settings(project_root)
        assert stored is not None
        assert Path(stored.folder) == custom

    def test_bare_save_keeps_stored_folder_spelling(self, project_root: Path) -> None:
        _write_mlflow(project_root, 'folder = "team-runs"\n')
        save_mlflow_settings(MlflowSettings(), project_root)
        assert load_mlflow_settings(project_root) == MlflowSettings(folder="team-runs")

    def test_save_rejects_non_http_tracking_uri(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError, match="http"):
            save_mlflow_settings(MlflowSettings(tracking_uri="sqlite:///x.db"), project_root)
        assert "mlflow" not in tomllib.loads((project_root / "haute.toml").read_text())

    def test_save_rejects_credentialed_uri_without_echoing_secret(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError) as excinfo:
            save_mlflow_settings(
                MlflowSettings(tracking_uri="https://alice:hunter2xyz@mlflow.example.com"),
                project_root,
            )
        assert "hunter2xyz" not in str(excinfo.value)

    def test_save_refuses_symlinked_haute_toml_escaping_the_project(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.toml"
        outside.write_text("[project]\nname='x'\n", encoding="utf-8")
        project = tmp_path / "project"
        project.mkdir()
        try:
            (project / "haute.toml").symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable")
        with pytest.raises(MlflowConfigError, match="outside the project root"):
            save_mlflow_settings(MlflowSettings(folder="mlruns"), project)
        assert "mlflow" not in outside.read_text(encoding="utf-8")


class TestValidateDestinationKey:
    @pytest.mark.parametrize("value", ["", "databricks", "server", "local"])
    def test_accepts_known_keys_and_auto(self, value: str) -> None:
        assert validate_destination_key(value) == value

    @pytest.mark.parametrize("value", ["Databricks", "file", "managed", "auto"])
    def test_rejects_unknown_key_naming_the_choices(self, value: str) -> None:
        with pytest.raises(MlflowConfigError, match="databricks, server, or local"):
            validate_destination_key(value)


class TestResolveDestination:
    # --- databricks ---
    def test_databricks_profile_reference_configures_without_host_token(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        config = resolve_destination("databricks", project_root)
        assert config.mode == "databricks"
        assert config.tracking_uri == "databricks://team"
        assert config.destination == "databricks://team"
        assert config.config_source == "env"

    def test_databricks_profile_takes_precedence_over_host_token(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-secret")
        config = resolve_destination("databricks", project_root)
        assert config.tracking_uri == "databricks://team"
        assert "dapi-secret" not in config.destination

    def test_databricks_host_token_pair(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net/")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-secret")
        config = resolve_destination("databricks", project_root)
        assert config.tracking_uri == "databricks"
        assert config.destination == "https://adb.example.net"
        assert config.config_source == "env"

    def test_databricks_missing_token_names_the_variable(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        with pytest.raises(MlflowConfigError, match="DATABRICKS_MLFLOW_TOKEN is not set"):
            resolve_destination("databricks", project_root)

    def test_databricks_unconfigured_names_both_options(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError) as excinfo:
            resolve_destination("databricks", project_root)
        message = str(excinfo.value)
        assert "databricks://<profile>" in message
        assert "DATABRICKS_MLFLOW_HOST" in message and "DATABRICKS_MLFLOW_TOKEN" in message
        assert message == _DATABRICKS_UNCONFIGURED_DETAIL

    def test_general_pair_alone_leaves_databricks_unconfigured_with_hint(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-general")
        with pytest.raises(MlflowDestinationUnconfigured) as excinfo:
            resolve_destination("databricks", project_root)
        message = str(excinfo.value)
        assert "DATABRICKS_MLFLOW_HOST" in message and "DATABRICKS_MLFLOW_TOKEN" in message
        assert "DATABRICKS_HOST/DATABRICKS_TOKEN are set but are never used for MLflow" in message
        assert "dapi-general" not in message
        assert resolve_tracking_config(project_root).mode == "local"

    def test_pair_form_rejects_ambient_databricks_config_profile(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-secret")
        monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "team")
        with pytest.raises(MlflowConfigError, match="DATABRICKS_CONFIG_PROFILE") as excinfo:
            resolve_destination("databricks", project_root)
        assert not isinstance(excinfo.value, MlflowDestinationUnconfigured)
        by_key = {e.key: e for e in list_destinations(project_root)}
        assert by_key["databricks"].configured is False
        assert by_key["databricks"].detail == str(excinfo.value)
        assert by_key["server"].configured is False
        assert "tracking_uri" in by_key["server"].detail
        assert by_key["local"].configured is True
        assert Path(by_key["local"].destination) == project_root / "mlruns"
        with pytest.raises(MlflowConfigError, match="DATABRICKS_CONFIG_PROFILE"):
            resolve_tracking_config(project_root)

    def test_profile_uri_ignores_ambient_databricks_config_profile(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "other")
        config = resolve_destination("databricks", project_root)
        assert config.mode == "databricks"
        assert config.tracking_uri == "databricks://team"
        assert config.destination == "databricks://team"

    @pytest.mark.parametrize("form", ["profile", "pair"])
    def test_databricks_sdk_mode_is_rejected_naming_the_variable(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch, form: str
    ) -> None:
        if form == "profile":
            monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        else:
            monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
            monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")
        with pytest.raises(MlflowConfigError, match="MLFLOW_ENABLE_DB_SDK") as excinfo:
            resolve_destination("databricks", project_root)
        assert not isinstance(excinfo.value, MlflowDestinationUnconfigured)
        # The inventory reports it and the auto rule fails loudly instead of skipping past it.
        entry = next(e for e in list_destinations(project_root) if e.key == "databricks")
        assert entry.configured is False and "MLFLOW_ENABLE_DB_SDK" in entry.detail
        with pytest.raises(MlflowConfigError, match="MLFLOW_ENABLE_DB_SDK"):
            resolve_tracking_config(project_root)

    def test_sdk_mode_without_any_databricks_configuration_is_just_unconfigured(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")
        with pytest.raises(MlflowDestinationUnconfigured):
            resolve_destination("databricks", project_root)
        assert resolve_tracking_config(project_root).mode == "local"

    def test_pin_is_applied_by_the_resolver(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        with pytest.raises(MlflowDestinationUnconfigured):
            resolve_destination("databricks", project_root)
        assert os.environ["MLFLOW_ENABLE_DB_SDK"] == "false"

    def test_resolution_never_imports_mlflow(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The inventory is reported even when the optional mlflow package is absent."""
        import sys

        monkeypatch.setitem(
            sys.modules, "mlflow", None
        )  # makes ``import mlflow`` raise ImportError
        monkeypatch.setitem(sys.modules, "mlflow.environment_variables", None)
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        assert resolve_destination("databricks", project_root).mode == "databricks"
        assert [e.configured for e in list_destinations(project_root)] == [True, False, True]
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")
        with pytest.raises(MlflowConfigError, match="MLFLOW_ENABLE_DB_SDK"):
            resolve_destination("databricks", project_root)

    def test_databricks_ignores_server_and_toml(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'tracking_uri = "http://localhost:5000"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://env:5000")
        with pytest.raises(MlflowConfigError):
            resolve_destination("databricks", project_root)

    # --- server ---
    def test_server_from_toml(self, project_root: Path) -> None:
        _write_mlflow(project_root, 'tracking_uri = "http://localhost:5000"\n')
        config = resolve_destination("server", project_root)
        assert config == TrackingConfig(
            "server", "http://localhost:5000", "http://localhost:5000", "toml"
        )

    def test_server_from_env_when_toml_has_no_tracking_uri(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'folder = "team-runs"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://env:5000")
        config = resolve_destination("server", project_root)
        assert config.tracking_uri == "http://env:5000"
        assert config.config_source == "env"

    def test_toml_tracking_uri_wins_over_env_server_uri(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'tracking_uri = "http://toml:5000"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://env:5000")
        assert resolve_destination("server", project_root).tracking_uri == "http://toml:5000"

    def test_server_toml_uri_inherits_matching_env_credentials(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'tracking_uri = "https://mlflow.example.com"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:secret@mlflow.example.com")
        config = resolve_destination("server", project_root)
        assert config.tracking_uri == "https://alice:secret@mlflow.example.com"
        assert config.destination == "https://mlflow.example.com"

    def test_server_unconfigured_names_the_prerequisites(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError) as excinfo:
            resolve_destination("server", project_root)
        message = str(excinfo.value)
        assert "tracking_uri" in message and "MLFLOW_TRACKING_URI" in message

    def test_server_ignores_databricks_env_uri(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        with pytest.raises(MlflowConfigError):
            resolve_destination("server", project_root)

    # --- local ---
    def test_local_default_is_project_mlruns(self, project_root: Path) -> None:
        config = resolve_destination("local", project_root)
        assert config.mode == "local"
        assert Path(config.destination) == project_root / "mlruns"
        assert config.config_source == "default"

    def test_local_toml_relative_folder_resolves_under_project_root(
        self, project_root: Path
    ) -> None:
        _write_mlflow(project_root, 'folder = "team-runs"\n')
        config = resolve_destination("local", project_root)
        assert Path(config.destination) == project_root / "team-runs"
        assert config.config_source == "toml"

    def test_local_env_file_uri_when_toml_has_no_folder(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = project_root / "env-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", target.as_uri())
        config = resolve_destination("local", project_root)
        assert Path(config.destination) == target
        assert config.config_source == "env"

    def test_local_toml_folder_wins_over_env_path(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'folder = "team-runs"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "env-runs")
        assert (
            Path(resolve_destination("local", project_root).destination)
            == project_root / "team-runs"
        )

    def test_unsupported_env_scheme_fails_server_and_local_not_databricks(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        with pytest.raises(MlflowConfigError, match="sqlite"):
            resolve_destination("server", project_root)
        with pytest.raises(MlflowConfigError, match="sqlite"):
            resolve_destination("local", project_root)
        assert resolve_destination("databricks", project_root).mode == "databricks"

    def test_unknown_key_is_rejected(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError, match="databricks, server, or local"):
            resolve_destination("file", project_root)

    def test_empty_key_is_rejected_by_resolve_destination(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError):
            resolve_destination("", project_root)


class TestAutoRule:
    def test_default_is_local(self, project_root: Path) -> None:
        assert resolve_tracking_config(project_root).mode == "local"

    def test_server_beats_local(self, project_root: Path) -> None:
        _write_mlflow(project_root, 'tracking_uri = "http://localhost:5000"\n')
        assert resolve_tracking_config(project_root).mode == "server"

    def test_databricks_beats_server(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow(project_root, 'tracking_uri = "http://localhost:5000"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        assert resolve_tracking_config(project_root).mode == "databricks"

    def test_host_token_pair_selects_databricks(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        assert resolve_tracking_config(project_root).mode == "databricks"

    def test_partial_host_token_is_not_databricks(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        assert resolve_tracking_config(project_root).mode == "local"

    def test_unsupported_env_scheme_fails_auto_loudly(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
        with pytest.raises(MlflowConfigError, match="sqlite"):
            resolve_tracking_config(project_root)


class TestListDestinations:
    def test_reports_all_three_entries_in_auto_order(self, project_root: Path) -> None:
        entries = list_destinations(project_root)
        assert [e.key for e in entries] == ["databricks", "server", "local"]
        by_key = {e.key: e for e in entries}
        assert by_key["databricks"].configured is False
        assert by_key["databricks"].detail == _DATABRICKS_UNCONFIGURED_DETAIL
        assert by_key["server"].configured is False
        assert "tracking_uri" in by_key["server"].detail
        assert by_key["local"].configured is True
        assert Path(by_key["local"].destination) == project_root / "mlruns"
        assert by_key["local"].config_source == "default"

    def test_profile_entry_identifies_the_profile_without_secrets(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-secret")
        entry = next(e for e in list_destinations(project_root) if e.key == "databricks")
        assert entry == DestinationEntry("databricks", True, "databricks://team", "env", "")

    def test_credentialed_env_server_uri_is_redacted(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:secret@mlflow.example.com")
        entry = next(e for e in list_destinations(project_root) if e.key == "server")
        assert entry.configured is True
        assert entry.destination == "https://mlflow.example.com"
        assert "secret" not in repr(entry)


class TestCandidateTrackingConfig:
    def test_server_draft_is_resolved_without_writing(self, project_root: Path) -> None:
        config = candidate_tracking_config(
            "server", MlflowSettings(tracking_uri="http://draft:5000"), project_root
        )
        assert config.tracking_uri == "http://draft:5000"
        assert load_mlflow_settings(project_root) is None

    def test_server_draft_inherits_matching_env_credentials(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:secret@mlflow.example.com")
        config = candidate_tracking_config(
            "server", MlflowSettings(tracking_uri="https://mlflow.example.com"), project_root
        )
        assert config.tracking_uri == "https://alice:secret@mlflow.example.com"

    def test_server_draft_without_uri_fails_naming_the_field(self, project_root: Path) -> None:
        with pytest.raises(MlflowConfigError, match="tracking_uri"):
            candidate_tracking_config("server", MlflowSettings(), project_root)

    def test_local_draft_folder_is_used(self, project_root: Path) -> None:
        config = candidate_tracking_config(
            "local", MlflowSettings(folder="draft-runs"), project_root
        )
        assert Path(config.destination) == project_root / "draft-runs"

    def test_bare_local_draft_matches_what_a_save_would_keep(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = project_root / "custom-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", custom.as_uri())
        config = candidate_tracking_config("local", MlflowSettings(), project_root)
        assert Path(config.destination) == custom

    def test_databricks_candidate_ignores_draft_fields(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        config = candidate_tracking_config(
            "databricks", MlflowSettings(tracking_uri="http://ignored:5000"), project_root
        )
        assert config.tracking_uri == "databricks://team"
