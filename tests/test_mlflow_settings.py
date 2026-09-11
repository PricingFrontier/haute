"""Tests for haute.modelling._mlflow_settings.

Covers the [mlflow] haute.toml round trip, MLFLOW_TRACKING_URI form
classification, and the three-mode resolve_tracking_config() precedence
matrix defined in specs/modelling/low-level.md (Shared MLflow
tracking/experiment-name resolution).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from haute.errors import MlflowConfigError
from haute.modelling._mlflow_settings import (
    MlflowSettings,
    classify_tracking_uri,
    load_mlflow_settings,
    resolve_tracking_config,
    save_mlflow_settings,
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
    for var in ("MLFLOW_TRACKING_URI", "DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(var, raising=False)


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


# ---------------------------------------------------------------------------
# load / save round trip
# ---------------------------------------------------------------------------


class TestLoadSaveSettings:
    def test_load_returns_none_when_section_absent(self, project_root: Path) -> None:
        assert load_mlflow_settings(project_root) is None

    def test_save_server_then_load_round_trips(self, project_root: Path) -> None:
        save_mlflow_settings(
            MlflowSettings(mode="server", tracking_uri="http://localhost:5000"),
            project_root,
        )
        loaded = load_mlflow_settings(project_root)
        assert loaded == MlflowSettings(mode="server", tracking_uri="http://localhost:5000")

    def test_save_preserves_other_sections_and_comments(self, project_root: Path) -> None:
        save_mlflow_settings(
            MlflowSettings(mode="server", tracking_uri="http://localhost:5000"),
            project_root,
        )
        text = (project_root / "haute.toml").read_text(encoding="utf-8")
        assert "# Haute project configuration" in text
        assert "# project name comment" in text
        assert "# deployment target" in text
        parsed = tomllib.loads(text)
        assert parsed["project"] == {"name": "main", "pipeline": "rating/main.py"}
        assert parsed["deploy"] == {"target": "databricks", "model_name": "motor-pricing"}
        assert parsed["mlflow"] == {"mode": "server", "tracking_uri": "http://localhost:5000"}

    def test_save_replaces_existing_mlflow_table(self, project_root: Path) -> None:
        save_mlflow_settings(
            MlflowSettings(mode="server", tracking_uri="http://localhost:5000"),
            project_root,
        )
        save_mlflow_settings(MlflowSettings(mode="local", folder="mlruns"), project_root)
        parsed = tomllib.loads((project_root / "haute.toml").read_text(encoding="utf-8"))
        assert parsed["mlflow"] == {"mode": "local", "folder": "mlruns"}

    def test_save_local_with_empty_folder_persists_resolved_folder(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An env-derived custom local folder must survive an unchanged save:
        # saving mode="local" with no folder writes the *resolved* folder,
        # never silently redirecting tracking to ./mlruns.
        team_runs = project_root / "team-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", team_runs.as_uri())
        save_mlflow_settings(MlflowSettings(mode="local"), project_root)

        parsed = tomllib.loads((project_root / "haute.toml").read_text(encoding="utf-8"))
        assert Path(parsed["mlflow"]["folder"]) == team_runs

        config = resolve_tracking_config(project_root)
        assert config.mode == "local"
        assert Path(config.destination) == team_runs

    @pytest.mark.parametrize(
        ("settings", "match"),
        [
            (MlflowSettings(mode="server"), "tracking_uri"),
            (MlflowSettings(mode="server", tracking_uri="ftp://x"), "tracking_uri"),
            (MlflowSettings(mode="server", tracking_uri="http://x", folder="y"), "folder"),
            (MlflowSettings(mode="local", tracking_uri="http://x"), "tracking_uri"),
            (MlflowSettings(mode="databricks", tracking_uri="databricks"), "tracking_uri"),
            (MlflowSettings(mode="databricks", folder="mlruns"), "folder"),
            (MlflowSettings(mode="filesystem"), "mode"),
        ],
    )
    def test_save_rejects_invalid_settings(
        self, project_root: Path, settings: MlflowSettings, match: str
    ) -> None:
        with pytest.raises(MlflowConfigError, match=match):
            save_mlflow_settings(settings, project_root)
        assert "mlflow" not in tomllib.loads(
            (project_root / "haute.toml").read_text(encoding="utf-8")
        )

    def test_load_rejects_unknown_key(self, project_root: Path) -> None:
        with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
            f.write('\n[mlflow]\nmode = "local"\nexperiment = "nope"\n')
        with pytest.raises(MlflowConfigError, match="experiment"):
            load_mlflow_settings(project_root)

    def test_load_rejects_invalid_mode(self, project_root: Path) -> None:
        with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
            f.write('\n[mlflow]\nmode = "filesystem"\n')
        with pytest.raises(MlflowConfigError, match="filesystem"):
            load_mlflow_settings(project_root)


# ---------------------------------------------------------------------------
# resolve_tracking_config precedence
# ---------------------------------------------------------------------------


def _write_mlflow_section(project_root: Path, body: str) -> None:
    with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
        f.write("\n[mlflow]\n" + body)


class TestResolvePrecedence:
    def test_default_is_local_mlruns(self, project_root: Path) -> None:
        config = resolve_tracking_config(project_root)
        assert config.mode == "local"
        assert config.config_source == "default"
        assert Path(config.destination) == project_root / "mlruns"
        assert config.tracking_uri == (project_root / "mlruns").as_uri()

    def test_toml_local_wins_over_databricks_credentials(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
        _write_mlflow_section(project_root, 'mode = "local"\n')
        config = resolve_tracking_config(project_root)
        assert config.mode == "local"
        assert config.config_source == "toml"
        assert Path(config.destination) == project_root / "mlruns"

    def test_toml_local_relative_folder_resolves_under_project_root(
        self, project_root: Path
    ) -> None:
        _write_mlflow_section(project_root, 'mode = "local"\nfolder = "runs/mlflow"\n')
        config = resolve_tracking_config(project_root)
        assert Path(config.destination) == project_root / "runs" / "mlflow"

    def test_toml_local_absolute_folder_used_as_is(
        self, project_root: Path, tmp_path: Path
    ) -> None:
        absolute = tmp_path / "elsewhere"
        _write_mlflow_section(
            project_root, f'mode = "local"\nfolder = {str(absolute.as_posix())!r}\n'
        )
        config = resolve_tracking_config(project_root)
        assert Path(config.destination) == absolute

    def test_toml_databricks_with_credentials(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
        _write_mlflow_section(project_root, 'mode = "databricks"\n')
        config = resolve_tracking_config(project_root)
        assert config.mode == "databricks"
        assert config.tracking_uri == "databricks"
        assert config.config_source == "toml"
        assert config.destination == "https://adb.example.net"
        assert "dapi-secret" not in config.destination

    def test_toml_databricks_without_token_fails_naming_the_variable(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        _write_mlflow_section(project_root, 'mode = "databricks"\n')
        with pytest.raises(MlflowConfigError, match="DATABRICKS_TOKEN"):
            resolve_tracking_config(project_root)
        # Loud failure, not a fallback: proven by the raise above; a config
        # is never returned for a misconfigured explicit selection.

    def test_toml_server_uses_its_uri(self, project_root: Path) -> None:
        _write_mlflow_section(
            project_root, 'mode = "server"\ntracking_uri = "http://localhost:5000"\n'
        )
        config = resolve_tracking_config(project_root)
        assert config.mode == "server"
        assert config.tracking_uri == "http://localhost:5000"
        assert config.destination == "http://localhost:5000"
        assert config.config_source == "toml"

    def test_env_uri_databricks_value_is_databricks_not_server(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
        config = resolve_tracking_config(project_root)
        assert config.mode == "databricks"
        assert config.config_source == "env"

    def test_env_uri_http_is_server(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
        config = resolve_tracking_config(project_root)
        assert config.mode == "server"
        assert config.tracking_uri == "http://localhost:5000"
        assert config.config_source == "env"

    def test_env_uri_file_is_local_at_that_folder(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team_runs = project_root / "team-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", team_runs.as_uri())
        config = resolve_tracking_config(project_root)
        assert config.mode == "local"
        assert Path(config.destination) == team_runs
        assert config.config_source == "env"

    def test_env_uri_unsupported_scheme_fails(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
        with pytest.raises(MlflowConfigError, match="sqlite"):
            resolve_tracking_config(project_root)

    def test_env_credentials_alone_select_databricks(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
        config = resolve_tracking_config(project_root)
        assert config.mode == "databricks"
        assert config.config_source == "env"

    def test_toml_takes_precedence_over_env_uri(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
        _write_mlflow_section(project_root, 'mode = "local"\n')
        config = resolve_tracking_config(project_root)
        assert config.mode == "local"
        assert config.config_source == "toml"
