"""``[mlflow]`` tracking destination inventory and resolution.

Owns the tracking-destination domain defined in
``specs/modelling/low-level.md`` (Shared MLflow tracking/experiment-name
resolution): the ``haute.toml`` ``[mlflow]`` inventory table (tracking_uri,
folder) round trip, standard ``MLFLOW_TRACKING_URI`` form classification,
per-destination resolution via ``resolve_destination()``, the destination
inventory via ``list_destinations()``, candidate draft resolution for
connection testing via ``candidate_tracking_config()``, and
``node_destination_key()`` — a node without a chosen destination uses the local
folder. There is no automatic choice: Databricks or an MLflow server is used
only when a node names it, and a remote that is not working is reported, never
replaced by another destination.

Destinations are keyed:
- ``"databricks"`` — Databricks workspace tracking backend.
- ``"server"``     — an ``http(s)`` MLflow tracking server.
- ``"local"``      — MLflow's file store in a project folder.

Misconfiguration raises :class:`haute.errors.MlflowConfigError` with an
actionable, non-secret reason. An unconfigured destination raises
:class:`MlflowDestinationUnconfigured` (subclass of ``MlflowConfigError``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from haute._mlflow_utils import tracking_uri_from_environment
from haute.errors import MlflowConfigError

DESTINATION_KEYS: tuple[str, ...] = ("databricks", "server", "local")
_ALLOWED_KEYS = frozenset({"tracking_uri", "folder"})
_DEFAULT_FOLDER = "mlruns"
_TRUTHY_ENV = frozenset({"true", "1"})


class MlflowDestinationUnconfigured(MlflowConfigError):  # noqa: N818
    """Raised when a destination is not configured in the workspace environment."""


@dataclass(frozen=True)
class MlflowSettings:
    """The stored ``[mlflow]`` inventory table, verbatim (unresolved)."""

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


@dataclass(frozen=True)
class DestinationEntry:
    """One inventory row: whether *key* is configured and, if so, where it points."""

    key: str
    configured: bool
    destination: str = ""
    config_source: str = ""
    detail: str = ""


def validate_destination_key(value: str) -> str:
    """Return *value* when it is a destination key or ``""`` (local); else raise."""
    if value == "" or value in DESTINATION_KEYS:
        return value
    raise MlflowConfigError(
        f"Unknown MLflow destination {value!r}; expected databricks, server, or local."
    )


def node_destination_key(value: str) -> str:
    """The destination a node or request uses: the one it names, else the local folder."""
    return validate_destination_key(value) or "local"


def _project_root_default() -> Path:
    """The process-wide project root every tracking consumer shares.

    Uses ``haute._sandbox._get_project_root`` (cwd at import, overridable by
    tests/CLI) so the settings surface, discovery routes, and logging paths
    all resolve the same ``haute.toml`` and default runs folder.
    """
    from haute._sandbox import _get_project_root

    return _get_project_root()


def _toml_path(project_root: Path | None) -> Path:
    return (project_root if project_root is not None else _project_root_default()) / "haute.toml"


def _split_uri(value: str, *, source: str) -> SplitResult:
    """``urlsplit`` with parse failures translated to :class:`MlflowConfigError`.

    The error never echoes the value — a malformed URI can still carry
    embedded credentials.
    """
    try:
        return urlsplit(value)
    except ValueError as exc:
        raise MlflowConfigError(f"{source} is not a parseable URI.") from exc


def _parse_http_uri(value: str, *, source: str) -> SplitResult:
    """Parse and validate an ``http(s)`` URI: scheme, non-empty host, valid port."""
    parts = _split_uri(value, source=source)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise MlflowConfigError(f"{source} must be an http(s):// URL with a host.")
    try:
        parts.port  # noqa: B018 — SplitResult.port raises on out-of-range/non-numeric ports
    except ValueError as exc:
        raise MlflowConfigError(f"{source} has an invalid port.") from exc
    return parts


def redact_uri(value: str) -> str:
    """Strip any userinfo credentials from a URI for display purposes.

    The host/port authority is kept byte-for-byte (IPv6 brackets included)
    by splitting the raw netloc on its final ``@`` rather than
    reconstructing it from parsed components.
    """
    try:
        parts = urlsplit(value)
        if "@" not in parts.netloc:
            return value
        return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[-1]))
    except ValueError:
        # Never let an unparseable value surface — it may carry the very
        # credentials being stripped.
        return "<unparseable URI>"


def classify_tracking_uri(value: str) -> tuple[str, str]:
    """Classify a tracking URI/path by form into ``(mode, uri)``.

    ``databricks`` and ``databricks://profile`` select databricks mode;
    ``http(s)://`` selects server mode (host required); a ``file:`` URI, a
    plain path, or a Windows drive path selects local mode. Any other
    scheme raises :class:`MlflowConfigError` naming the unsupported scheme.
    """
    uri = value.strip()
    if not uri:
        raise MlflowConfigError("MLflow tracking URI is empty.")
    if uri == "databricks" or uri.startswith("databricks://"):
        return "databricks", uri
    scheme = _split_uri(uri, source="MLFLOW_TRACKING_URI").scheme.lower()
    if scheme in ("http", "https"):
        _parse_http_uri(uri, source="MLFLOW_TRACKING_URI")
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
    if settings.tracking_uri:
        parts = _parse_http_uri(settings.tracking_uri, source="[mlflow] tracking_uri")
        if "@" in parts.netloc:
            raise MlflowConfigError(
                "[mlflow] tracking_uri must not embed credentials; "
                "keep secrets in .env, not haute.toml."
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
        tracking_uri=str(section.get("tracking_uri", "")),
        folder=str(section.get("folder", "")),
    )
    _validate_settings(settings)
    return settings


def save_mlflow_settings(settings: MlflowSettings, project_root: Path | None = None) -> None:
    """Validate and rewrite only the ``[mlflow]`` table (tomlkit round trip).

    ``tracking_uri`` is written only when non-empty (empty clears the key).
    An empty ``folder`` persists the currently *resolved* local folder, so a
    bare save of an env-derived folder keeps it.
    """
    import tomlkit

    effective = _effective_settings(settings, project_root)
    toml_path = _toml_path(project_root)
    write_target = _project_write_target(toml_path, project_root)
    document = (
        tomlkit.parse(toml_path.read_text(encoding="utf-8"))
        if toml_path.exists()
        else tomlkit.document()
    )
    table = tomlkit.table()
    if effective.tracking_uri:
        table["tracking_uri"] = effective.tracking_uri
    table["folder"] = effective.folder
    document["mlflow"] = table
    write_target.write_text(tomlkit.dumps(document), encoding="utf-8")


def _project_write_target(toml_path: Path, project_root: Path | None) -> Path:
    """Resolve the settings write target and require project containment.

    A ``haute.toml`` that is a symlink escaping the project root would let a
    settings save modify a file outside the project; refuse it instead. The
    containment check folds case and fully resolves both sides, mirroring
    ``haute._sandbox.validate_project_path``.
    """
    root = (project_root if project_root is not None else _project_root_default()).resolve()
    resolved = toml_path.resolve()
    root_cmp = os.path.normcase(str(root))
    target_cmp = os.path.normcase(str(resolved))
    try:
        contained = os.path.commonpath([root_cmp, target_cmp]) == root_cmp
    except ValueError:  # different drives on Windows
        contained = False
    if not contained:
        raise MlflowConfigError(
            "Refusing to write MLflow settings: haute.toml resolves outside the project root."
        )
    return resolved


def _effective_settings(settings: MlflowSettings, project_root: Path | None) -> MlflowSettings:
    _validate_settings(settings)
    if settings.folder:
        return settings
    return MlflowSettings(
        tracking_uri=settings.tracking_uri, folder=_folder_to_persist(project_root)
    )


def _folder_to_persist(project_root: Path | None) -> str:
    """Stored spelling if the toml has a folder; env-derived absolute path; else default."""
    stored = load_mlflow_settings(project_root)
    if stored is not None and stored.folder:
        return stored.folder
    try:
        current = resolve_destination("local", project_root)
    except MlflowConfigError:
        return _DEFAULT_FOLDER
    if current.config_source == "env":
        return current.destination
    return _DEFAULT_FOLDER


def _env_uri_form() -> tuple[str, str] | None:
    """``(mode, uri)`` for ``MLFLOW_TRACKING_URI`` by form, or ``None`` when unset."""
    env_uri = tracking_uri_from_environment().strip()
    if not env_uri:
        return None
    return classify_tracking_uri(env_uri)  # raises MlflowConfigError for unsupported schemes


def _reject_databricks_sdk_mode() -> None:
    """haute binds Databricks credentials only through MLflow's per-request providers.

    Reads the environment directly: destination resolution must never require the
    optional mlflow package (the inventory is reported even when mlflow is absent).
    """
    if os.environ.get("MLFLOW_ENABLE_DB_SDK", "").strip().lower() in _TRUTHY_ENV:
        raise MlflowConfigError(
            "MLFLOW_ENABLE_DB_SDK=true is not supported: haute binds Databricks credentials "
            "through the selected profile or DATABRICKS_MLFLOW_HOST/DATABRICKS_MLFLOW_TOKEN on "
            "every request; unset MLFLOW_ENABLE_DB_SDK."
        )


def _reject_ambient_databricks_profile() -> None:
    """The pair form must not coexist with ``DATABRICKS_CONFIG_PROFILE``.

    MLflow reads that variable in place of its credential providers for a bare
    ``databricks`` URI, so the profile would silently replace the MLflow pair.
    """
    if os.environ.get("DATABRICKS_CONFIG_PROFILE", "").strip():
        raise MlflowConfigError(
            "DATABRICKS_CONFIG_PROFILE is set, which makes MLflow read that profile instead of "
            "DATABRICKS_MLFLOW_HOST/DATABRICKS_MLFLOW_TOKEN: unset it, or select the profile "
            "with MLFLOW_TRACKING_URI=databricks://<profile>."
        )


def _general_pair_hint() -> str:
    """Say so when the data-access pair is set but cannot configure MLflow."""
    if os.getenv("DATABRICKS_HOST", "").strip() or os.getenv("DATABRICKS_TOKEN", "").strip():
        return (
            " DATABRICKS_HOST/DATABRICKS_TOKEN are set but are never used for MLflow; copy "
            "their values into the MLflow pair if one token covers both."
        )
    return ""


def _bound_databricks_config(config: TrackingConfig) -> TrackingConfig:
    """Bind MLflow's Databricks credentials, then hand the config to the consumer.

    Every Databricks ``TrackingConfig`` leaves this module through here, so each
    consumer (deploy included) is bound before its first Databricks credential
    lookup, while resolving a server or local destination never imports MLflow.
    """
    from haute._mlflow_utils import bind_mlflow_databricks_credentials

    bind_mlflow_databricks_credentials()
    return config


def _resolve_databricks() -> TrackingConfig:
    env_uri = tracking_uri_from_environment().strip()
    if env_uri.startswith("databricks://"):
        _reject_databricks_sdk_mode()  # only a *configured* Databricks trips this
        return _bound_databricks_config(TrackingConfig("databricks", env_uri, env_uri, "env"))
    host = os.getenv("DATABRICKS_MLFLOW_HOST", "").strip()
    token = os.getenv("DATABRICKS_MLFLOW_TOKEN", "").strip()
    if host and token:
        _reject_databricks_sdk_mode()
        _reject_ambient_databricks_profile()
        return _bound_databricks_config(
            TrackingConfig("databricks", "databricks", host.rstrip("/"), "env")
        )
    if host or token or env_uri == "databricks":
        missing = [
            n
            for n, v in (("DATABRICKS_MLFLOW_HOST", host), ("DATABRICKS_MLFLOW_TOKEN", token))
            if not v
        ]
        verb = "is" if len(missing) == 1 else "are"
        raise MlflowDestinationUnconfigured(
            f"Databricks MLflow tracking is not configured: {' and '.join(missing)} {verb} "
            f"not set in the environment (.env).{_general_pair_hint()}"
        )
    raise MlflowDestinationUnconfigured(
        "Databricks is not configured for MLflow: set MLFLOW_TRACKING_URI=databricks://<profile> "
        "or both DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN in the environment (.env)."
        f"{_general_pair_hint()}"
    )


def _resolve_server(stored: MlflowSettings | None) -> TrackingConfig:
    if stored is not None and stored.tracking_uri:
        uri = _server_uri_with_env_credentials(stored.tracking_uri)
        return TrackingConfig("server", uri, redact_uri(uri), "toml")
    form = _env_uri_form()
    if form is not None and form[0] == "server":
        return TrackingConfig("server", form[1], redact_uri(form[1]), "env")
    raise MlflowDestinationUnconfigured(
        "MLflow server is not configured: set [mlflow] tracking_uri in haute.toml "
        "or an http(s) MLFLOW_TRACKING_URI in the environment (.env)."
    )


def _resolve_local(stored: MlflowSettings | None, root: Path) -> TrackingConfig:
    if stored is not None and stored.folder:
        return _local_tracking(_local_folder(stored.folder, root), config_source="toml")
    form = _env_uri_form()
    if form is not None and form[0] == "local":
        return _local_tracking(_local_folder(form[1], root), config_source="env")
    return _local_tracking(root / _DEFAULT_FOLDER, config_source="default")


def resolve_destination(key: str, project_root: Path | None = None) -> TrackingConfig:
    """Resolve one destination key or raise ``MlflowConfigError`` naming the prerequisite."""
    if key not in DESTINATION_KEYS:
        validate_destination_key(key)
        raise MlflowConfigError(
            "A destination key is required; node_destination_key() maps '' to local."
        )
    root = project_root if project_root is not None else _project_root_default()
    if key == "databricks":
        return _resolve_databricks()
    stored = load_mlflow_settings(project_root)
    if key == "server":
        return _resolve_server(stored)
    return _resolve_local(stored, root)


def list_destinations(project_root: Path | None = None) -> list[DestinationEntry]:
    entries: list[DestinationEntry] = []
    for key in DESTINATION_KEYS:
        try:
            config = resolve_destination(key, project_root)
        except MlflowConfigError as exc:
            entries.append(DestinationEntry(key, False, detail=str(exc)))
            continue
        entries.append(DestinationEntry(key, True, config.destination, config.config_source))
    return entries


def _server_uri_with_env_credentials(stored_uri: str) -> str:
    """Re-attach matching env credentials to a stored (non-secret) server URI.

    ``haute.toml`` persists the credential-free destination; ``.env`` may
    carry the same server with embedded auth. When the stored URI equals the
    redaction of a credentialed env server URI, the env value wins for
    connecting — so saving the displayed (redacted) configuration never
    silently drops working authentication.
    """
    env_uri = tracking_uri_from_environment().strip()
    if not env_uri:
        return stored_uri
    try:
        env_mode, env_value = classify_tracking_uri(env_uri)
    except MlflowConfigError:
        return stored_uri
    if (
        env_mode == "server"
        and "@" in urlsplit(env_value).netloc
        and redact_uri(env_value) == redact_uri(stored_uri)
    ):
        return env_value
    return stored_uri


def candidate_tracking_config(
    key: str, settings: MlflowSettings, project_root: Path | None = None
) -> TrackingConfig:
    """Resolve an unsaved draft for *key* exactly as a save would make it."""
    if key not in DESTINATION_KEYS:
        validate_destination_key(key)
        raise MlflowConfigError("A destination key is required for a candidate probe.")
    root = project_root if project_root is not None else _project_root_default()
    if key == "databricks":
        return _resolve_databricks()
    if key == "server":
        if not settings.tracking_uri:
            raise MlflowConfigError("[mlflow] tracking_uri is required to test the MLflow server.")
        _validate_settings(settings)
        uri = _server_uri_with_env_credentials(settings.tracking_uri)
        return TrackingConfig("server", uri, redact_uri(uri), "toml")
    effective = _effective_settings(MlflowSettings(folder=settings.folder), project_root)
    return _local_tracking(_local_folder(effective.folder, root), config_source="toml")


def _local_tracking(folder: Path, *, config_source: str) -> TrackingConfig:
    return TrackingConfig("local", folder.as_uri(), str(folder), config_source)
