"""``[mlflow]`` tracking-destination settings and three-mode resolution.

Owns the tracking-destination domain defined in
``specs/modelling/low-level.md`` (Shared MLflow tracking/experiment-name
resolution): the ``haute.toml`` ``[mlflow]`` table round trip, standard
``MLFLOW_TRACKING_URI`` form classification, and ``resolve_tracking_config()``
— the single precedence resolver every tracking consumer sits behind.

Tracking resolves to exactly one of three modes:

- ``"databricks"`` — the Databricks workspace tracking backend.
- ``"server"``     — an ``http(s)`` MLflow tracking server.
- ``"local"``      — MLflow's file store in a project folder.

Misconfiguration raises :class:`haute.errors.MlflowConfigError` with an
actionable, non-secret reason. A selected mode never silently degrades to a
different one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from haute.errors import MlflowConfigError

_MODES = ("databricks", "server", "local")
_ALLOWED_KEYS = frozenset({"mode", "tracking_uri", "folder"})
_DEFAULT_FOLDER = "mlruns"


@dataclass(frozen=True)
class MlflowSettings:
    """The stored ``[mlflow]`` table, verbatim (unresolved)."""

    mode: str
    tracking_uri: str = ""
    folder: str = ""


@dataclass(frozen=True)
class TrackingConfig:
    """A resolved tracking destination.

    ``destination`` is the human-readable place runs go (workspace host,
    server URI, or absolute runs-folder path) and never contains a secret.
    ``config_source`` records which precedence tier decided:
    ``"toml"`` | ``"env"`` | ``"default"``.
    """

    mode: str
    tracking_uri: str
    destination: str
    config_source: str


def _toml_path(project_root: Path | None) -> Path:
    return (project_root if project_root is not None else Path.cwd()) / "haute.toml"


def classify_tracking_uri(value: str) -> tuple[str, str]:
    """Classify a tracking URI/path by form into ``(mode, uri)``.

    ``databricks`` and ``databricks://profile`` select databricks mode;
    ``http(s)://`` selects server mode; a ``file:`` URI, a plain path, or a
    Windows drive path selects local mode. Any other scheme raises
    :class:`MlflowConfigError` naming the unsupported scheme.
    """
    uri = value.strip()
    if not uri:
        raise MlflowConfigError("MLflow tracking URI is empty.")
    if uri == "databricks" or uri.startswith("databricks://"):
        return "databricks", uri
    scheme = urlsplit(uri).scheme.lower()
    if scheme in ("http", "https"):
        return "server", uri
    # No scheme is a plain path; a single-letter "scheme" is a Windows
    # drive letter, not a URI scheme.
    if scheme in ("", "file") or len(scheme) == 1:
        return "local", uri
    raise MlflowConfigError(
        f"Unsupported MLflow tracking URI scheme '{scheme}'. Supported forms: "
        "'databricks', 'databricks://<profile>', 'http(s)://<server>', "
        "a 'file:' URI, or a filesystem path."
    )


def _file_uri_to_path(uri: str) -> Path:
    parts = urlsplit(uri)
    path = unquote(parts.path)
    if parts.netloc:  # UNC form: file://server/share
        return Path(f"//{parts.netloc}{path}")
    if re.match(r"^/[A-Za-z]:", path):  # /C:/runs -> C:/runs
        path = path[1:]
    return Path(path)


def _local_folder(raw: str, project_root: Path) -> Path:
    """Resolve a local destination (``file:`` URI or path) to an absolute folder."""
    folder = _file_uri_to_path(raw) if urlsplit(raw).scheme.lower() == "file" else Path(raw)
    return folder if folder.is_absolute() else project_root / folder


def _validate_settings(settings: MlflowSettings) -> None:
    if settings.mode not in _MODES:
        raise MlflowConfigError(
            f"[mlflow] mode must be one of {list(_MODES)}, not '{settings.mode}'."
        )
    if settings.mode == "server":
        scheme = urlsplit(settings.tracking_uri).scheme.lower()
        if not settings.tracking_uri or scheme not in ("http", "https"):
            raise MlflowConfigError(
                "[mlflow] server mode requires an http(s):// tracking_uri, "
                f"got '{settings.tracking_uri}'."
            )
    elif settings.tracking_uri:
        raise MlflowConfigError(
            f"[mlflow] tracking_uri is only valid for server mode, not '{settings.mode}'."
        )
    if settings.mode != "local" and settings.folder:
        raise MlflowConfigError(
            f"[mlflow] folder is only valid for local mode, not '{settings.mode}'."
        )


def load_mlflow_settings(project_root: Path | None = None) -> MlflowSettings | None:
    """Read and validate the ``[mlflow]`` table; ``None`` when absent."""
    import tomllib

    toml_path = _toml_path(project_root)
    if not toml_path.exists():
        return None
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MlflowConfigError(f"haute.toml is not valid TOML: {exc}") from exc
    section = data.get("mlflow")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise MlflowConfigError("[mlflow] must be a table.")
    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        raise MlflowConfigError(
            f"[mlflow] has unknown key(s) {unknown}; allowed keys are {sorted(_ALLOWED_KEYS)}."
        )
    for key, value in section.items():
        if not isinstance(value, str):
            raise MlflowConfigError(f"[mlflow] {key} must be a string.")
    settings = MlflowSettings(
        mode=str(section.get("mode", "")),
        tracking_uri=str(section.get("tracking_uri", "")),
        folder=str(section.get("folder", "")),
    )
    _validate_settings(settings)
    return settings


def save_mlflow_settings(settings: MlflowSettings, project_root: Path | None = None) -> None:
    """Validate *settings* and rewrite only the ``[mlflow]`` table.

    The write goes through tomlkit, preserving every other section, comment,
    and layout choice. Saving local mode with an empty ``folder`` persists
    the currently *resolved* local folder, so saving an unchanged env-derived
    local configuration keeps its custom folder instead of being redirected
    to the default ``./mlruns``.
    """
    import tomlkit

    _validate_settings(settings)

    effective = settings
    if settings.mode == "local" and not settings.folder:
        effective = MlflowSettings(mode="local", folder=_folder_to_persist(project_root))

    toml_path = _toml_path(project_root)
    document = (
        tomlkit.parse(toml_path.read_text(encoding="utf-8"))
        if toml_path.exists()
        else tomlkit.document()
    )
    table = tomlkit.table()
    table["mode"] = effective.mode
    if effective.tracking_uri:
        table["tracking_uri"] = effective.tracking_uri
    if effective.folder:
        table["folder"] = effective.folder
    document["mlflow"] = table
    toml_path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _folder_to_persist(project_root: Path | None) -> str:
    """The folder value a bare local-mode save must write.

    Preserves the currently resolved local destination: a stored toml folder
    keeps its stored spelling, an env-derived folder is persisted as its
    absolute path, and anything else (default, another mode, or a currently
    misconfigured selection being replaced by this save) uses the default.
    """
    try:
        current = resolve_tracking_config(project_root)
    except MlflowConfigError:
        return _DEFAULT_FOLDER
    if current.mode != "local":
        return _DEFAULT_FOLDER
    if current.config_source == "toml":
        stored = load_mlflow_settings(project_root)
        if stored is not None and stored.folder:
            return stored.folder
        return _DEFAULT_FOLDER
    if current.config_source == "env":
        return current.destination
    return _DEFAULT_FOLDER


def resolve_tracking_config(project_root: Path | None = None) -> TrackingConfig:
    """Resolve the tracking destination: toml first, env second, default last."""
    root = project_root if project_root is not None else Path.cwd()

    stored = load_mlflow_settings(project_root)
    if stored is not None:
        return _config_from_settings(stored, root)

    env_uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    if env_uri:
        mode, uri = classify_tracking_uri(env_uri)
        if mode == "databricks":
            return _databricks_config(uri, config_source="env")
        if mode == "server":
            return TrackingConfig("server", uri, uri, "env")
        folder = _local_folder(uri, root)
        return _local_tracking(folder, config_source="env")

    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    if host and token:
        return TrackingConfig("databricks", "databricks", host.rstrip("/"), "env")

    return _local_tracking(root / _DEFAULT_FOLDER, config_source="default")


def _config_from_settings(settings: MlflowSettings, root: Path) -> TrackingConfig:
    if settings.mode == "databricks":
        return _databricks_config("databricks", config_source="toml")
    if settings.mode == "server":
        return TrackingConfig("server", settings.tracking_uri, settings.tracking_uri, "toml")
    folder = _local_folder(settings.folder or _DEFAULT_FOLDER, root)
    return _local_tracking(folder, config_source="toml")


def _databricks_config(uri: str, *, config_source: str) -> TrackingConfig:
    if uri != "databricks":
        # databricks://<profile> carries its own credential reference.
        return TrackingConfig("databricks", uri, uri, config_source)
    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    missing = [
        name
        for name, value in (("DATABRICKS_HOST", host), ("DATABRICKS_TOKEN", token))
        if not value
    ]
    if missing:
        raise MlflowConfigError(
            "Databricks tracking is selected but "
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
            "not set in the environment (.env)."
        )
    return TrackingConfig("databricks", "databricks", host.rstrip("/"), config_source)


def _local_tracking(folder: Path, *, config_source: str) -> TrackingConfig:
    return TrackingConfig("local", folder.as_uri(), str(folder), config_source)
