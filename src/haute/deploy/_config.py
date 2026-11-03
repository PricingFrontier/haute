"""Deploy configuration - user input, TOML loading, and resolution."""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Collection, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem
from haute._graph_shape import validate_singleton_groups
from haute._io import read_user_text
from haute._logging import get_logger
from haute._sandbox import is_contained
from haute._workbench_config import WORKBENCH_TOML_KEYS, read_workbench_config
from haute._workbench_form import FormDocument
from haute._workbench_nodes import connection_errors, form_owner, read_project_form, with_tables
from haute._workbench_tables import workbench_tables
from haute.assistant._config import ASSISTANT_EGRESS_TOML_KEYS, ASSISTANT_TOML_KEYS
from haute.deploy._project_modules import ProjectModules, resolve_project_modules
from haute.deploy._pruner import (
    find_deploy_input_nodes,
    find_output_node,
    find_source_nodes,
    prune_for_deploy,
)
from haute.errors import DeployError
from haute.graph_utils import NodeType, PipelineGraph

if TYPE_CHECKING:
    from haute.deploy._request_rows import RequestRows

logger = get_logger(component="deploy.config")


# ---------------------------------------------------------------------------
# Base image pinning — reject floating tags
# ---------------------------------------------------------------------------
#
# Targets that build a Docker image and push it to a registry. Every one
# of these consumes ``container.base_image`` via build_and_push_image, so
# all of them must gate on a pinned image.
_CONTAINER_BASED_TARGETS: frozenset[str] = frozenset(
    {"container", "azure-container-apps", "aws-ecs", "gcp-run"}
)


# Deployed containers must start from a deterministic base image.  Floating
# tags (``python:3.11-slim``, ``python:3.11``, ``python:latest``, or no tag at
# all) silently drift to whatever "latest" means at build time, which means
# the image bytes you test against today are not the image bytes your users
# hit tomorrow.  The two acceptable forms are:
#
# * **Explicit patch version** — the full ``MAJOR.MINOR.PATCH`` triple
#   appears somewhere in the tag (e.g. ``python:3.11.9-slim``,
#   ``docker.io/library/python:3.11.9-slim``).
# * **Digest pin** — the image is pinned by content hash
#   (``python@sha256:<64-hex>``), the strongest form of pinning.

_PATCH_PINNED_RE = re.compile(r"^[^:]+:[^@]*\d+\.\d+\.\d+[^@]*$")
_DIGEST_PINNED_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def _validate_base_image_pinning(base_image: str) -> None:
    """Ensure ``base_image`` is pinned to a concrete, reproducible release.

    Accepts either a patch-version pin (``<image>:...M.m.p...``) or a digest
    pin (``<image>@sha256:<64-hex>``).  Raises :class:`DeployError` for every
    other form — including the empty string, floating tags, missing tags, and
    major- or minor-only tags.  The error message names the offending
    ``base_image`` value so users can diff it against their config.
    """
    if _PATCH_PINNED_RE.match(base_image) or _DIGEST_PINNED_RE.search(base_image):
        return
    raise DeployError(
        "container.base_image is not pinned to a concrete release. "
        "Use either a patch-version pin (e.g. 'python:3.11.9-slim') or a "
        "digest pin (e.g. 'python@sha256:<64-hex>'). "
        "Floating tags like 'python:3.11-slim' drift across rebuilds and are "
        "rejected to guarantee reproducible deployments.",
        base_image=base_image,
    )


#: The Unity Catalog catalog and schema a model registers under when haute.toml
#: names none; deploy and status read the same defaults.
DEFAULT_CATALOG = "main"
DEFAULT_SCHEMA = "pricing"


@dataclass
class DatabricksConfig:
    """Typed Databricks-specific settings from [deploy.databricks] in haute.toml."""

    experiment_name: str = "/Shared/haute/default"
    catalog: str = DEFAULT_CATALOG
    schema: str = DEFAULT_SCHEMA
    serving_workload_size: str = "Small"
    serving_scale_to_zero: bool = True


@dataclass
class ContainerConfig:
    """Container-specific settings from [deploy.container] in haute.toml.

    ``base_image`` has no usable default: users must explicitly set it to a
    patch- or digest-pinned image (see :func:`_validate_base_image_pinning`).
    The empty-string default here keeps ``ContainerConfig`` constructible
    without arguments; :meth:`DeployConfig.__post_init__` refuses to deploy
    when ``target == "container"`` and the image is unpinned.
    """

    registry: str = ""
    port: int = 8080
    base_image: str = ""


@dataclass
class AzureContainerAppsConfig:
    """Azure Container Apps settings from [deploy.azure-container-apps]."""

    resource_group: str = ""
    container_app_name: str = ""
    environment_name: str = ""


@dataclass
class AwsEcsConfig:
    """AWS ECS settings from [deploy.aws-ecs]."""

    region: str = "eu-west-1"
    cluster: str = ""
    service: str = ""


@dataclass
class GcpRunConfig:
    """GCP Cloud Run settings from [deploy.gcp-run]."""

    project: str = ""
    region: str = "europe-west1"
    service: str = ""


@dataclass
class SafetyConfig:
    """Safety settings from [safety] in haute.toml."""

    impact_dataset: str = ""
    min_approvers: int = 2


#: The suffix a staging deploy appends to the endpoint, model and experiment names when
#: ``[ci.staging]`` records none, as ``haute init`` writes it. The generated workflows pass
#: the recorded suffix to the staging deploy, smoke and impact steps.
DEFAULT_STAGING_ENDPOINT_SUFFIX = "-staging"


@dataclass
class CIConfig:
    """CI/CD settings from [ci] in haute.toml."""

    provider: str = "github"
    staging_endpoint_suffix: str = DEFAULT_STAGING_ENDPOINT_SUFFIX
    staging_endpoint_url: str = ""
    production_endpoint_url: str = ""
    # The environment the generated production job runs under, whose reviewers gate it;
    # empty names none, which works on every plan (``haute._scaffold.ci_files``).
    production_environment: str = ""


# ---------------------------------------------------------------------------
# TOML schema validation — reject unknown keys early
# ---------------------------------------------------------------------------

_VALID_TOML_SCHEMA: dict[str, set[str] | dict[str, set[str]]] = {
    "project": {"name", "pipeline"},
    "deploy": {
        "_self": {"target", "model_name", "endpoint_name", "output_fields"},
        "databricks": {
            "experiment_name",
            "catalog",
            "schema",
            "serving_workload_size",
            "serving_scale_to_zero",
        },
        "container": {"registry", "port", "base_image"},
        "azure-container-apps": {
            "resource_group",
            "container_app_name",
            "environment_name",
        },
        "aws-ecs": {"region", "cluster", "service"},
        "gcp-run": {"project", "region", "service"},
    },
    "test_quotes": {"dir"},
    # Owned by the MLflow settings endpoint (routes/mlflow.py), not deploy;
    # listed so whole-file validation accepts the file that endpoint writes.
    # The table is a destination inventory: the retired single-mode ``mode``
    # key is an unknown key like any other.
    "mlflow": {"tracking_uri", "folder"},
    "safety": {
        "_self": {"impact_dataset"},
        "approval": {"min_approvers"},
    },
    "ci": {
        "_self": {"provider"},
        "staging": {"endpoint_suffix", "endpoint_url"},
        "production": {"endpoint_url", "environment"},
    },
    "server": {"host"},
    "assistant": {
        "_self": set(ASSISTANT_TOML_KEYS - {"egress"}),
        "egress": set(ASSISTANT_EGRESS_TOML_KEYS),
    },
    # Owned by the workbench (haute._workbench_config); listed so whole-file
    # validation accepts a project whose workbench is enabled.
    "workbench": set(WORKBENCH_TOML_KEYS),
}


_T = TypeVar("_T", str, int, bool)

_TOML_TYPE_NAMES: dict[type, str] = {str: "a string", int: "an integer", bool: "true or false"}


class _TomlTable:
    """One ``haute.toml`` table, each value read as the type its key takes.

    A value of another type is refused naming the key, never loaded as given or
    replaced by the default: ``port = "x"`` or ``serving_scale_to_zero = "yes"``
    would otherwise reach a build or a serving endpoint.
    """

    def __init__(self, data: object, section: str) -> None:
        if not isinstance(data, dict):
            raise ValueError(f"[{section}] must be a table; got {data!r}.")
        self._data: dict[str, Any] = data
        self._section = section

    def table(self, key: str) -> _TomlTable:
        """The sub-table *key*, empty when the file has none."""
        section = f"{self._section}.{key}" if self._section else key
        return _TomlTable(self._data.get(key, {}), section)

    def get(self, key: str, kind: type[_T], default: _T) -> _T:
        """*key*'s value, *default* when the table does not set it."""
        value = self.optional(key, kind)
        return default if value is None else value

    def optional(self, key: str, kind: type[_T]) -> _T | None:
        """*key*'s value, ``None`` when the table does not set it."""
        if key not in self._data:
            return None
        value = self._data[key]
        # A TOML boolean is a Python int: an integer key never takes true or false.
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise ValueError(
                f"[{self._section}] {key} must be {_TOML_TYPE_NAMES[kind]}; got {value!r}."
            )
        return value

    def array(self, key: str) -> list[Any] | None:
        """*key*'s array, ``None`` when the table does not set it."""
        value = self._data.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"[{self._section}] {key} must be an array; got {value!r}.")
        return value


def _validate_toml_keys(data: dict[str, Any], path: Path) -> None:
    """Raise ValueError if haute.toml contains unknown keys."""
    errors: list[str] = []

    def _check(
        section_path: str,
        actual: dict[str, Any],
        schema: set[str] | dict[str, Any],
    ) -> None:
        if isinstance(schema, set):
            unknown = set(actual) - schema
            for k in sorted(unknown):
                errors.append(f"  [{section_path}] unknown key '{k}'")
        else:
            # Dict schema: _self holds top-level keys, rest are sub-sections
            top_keys = schema.get("_self", set())
            sub_sections = {k for k in schema if k != "_self"}
            allowed = top_keys | sub_sections if isinstance(top_keys, set) else sub_sections
            unknown = set(actual) - allowed
            for k in sorted(unknown):
                errors.append(f"  [{section_path}] unknown key '{k}'")
            for sub in sub_sections:
                if sub in actual and isinstance(actual[sub], dict):
                    _check(f"{section_path}.{sub}", actual[sub], schema[sub])

    top_unknown = set(data) - set(_VALID_TOML_SCHEMA)
    for k in sorted(top_unknown):
        errors.append(f"  unknown top-level section [{k}]")

    for section, schema in _VALID_TOML_SCHEMA.items():
        if section in data and isinstance(data[section], dict):
            _check(section, data[section], schema)

    if errors:
        raise ValueError(
            f"Invalid haute.toml ({path}):\n"
            + "\n".join(errors)
            + "\n\nCheck for typos in your configuration keys."
        )


@dataclass
class DeployConfig:
    """User-provided deployment configuration (from haute.toml + CLI)."""

    pipeline_file: Path
    model_name: str
    project_dir: Path | None = None
    target: str = "databricks"
    endpoint_name: str | None = None
    endpoint_suffix: str | None = None
    output_fields: list[str] | None = None
    test_quotes_dir: Path | None = None
    databricks: DatabricksConfig = field(default_factory=DatabricksConfig)
    container: ContainerConfig = field(default_factory=ContainerConfig)
    azure_container_apps: AzureContainerAppsConfig = field(
        default_factory=AzureContainerAppsConfig,
    )
    aws_ecs: AwsEcsConfig = field(default_factory=AwsEcsConfig)
    gcp_run: GcpRunConfig = field(default_factory=GcpRunConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    ci: CIConfig = field(default_factory=CIConfig)

    def __post_init__(self) -> None:
        """Validate fields that must be correct at construction time.

        Container-based deploys (``container``, ``azure-container-apps``,
        ``aws-ecs``, ``gcp-run``) all funnel through ``build_and_push_image``
        and pin ``container.base_image`` into the generated Dockerfile, so
        all of them require a reproducibly pinned image. Non-container
        targets (Databricks, etc.) never consume ``container.base_image``.
        """
        if self.target in _CONTAINER_BASED_TARGETS:
            _validate_base_image_pinning(self.container.base_image)

    @property
    def effective_endpoint_name(self) -> str | None:
        """Endpoint name with optional suffix applied (e.g. for staging).

        Returns None when endpoint_name is unset and no suffix is given,
        which signals that no serving endpoint should be created.
        """
        if self.endpoint_name is None and self.endpoint_suffix is None:
            return None
        base = self.endpoint_name or self.model_name
        if self.endpoint_suffix:
            return base + self.endpoint_suffix
        return base

    @classmethod
    def from_toml(cls, path: Path) -> DeployConfig:
        """Load from haute.toml, merging [project], [deploy], [safety], [ci].

        Validates that all TOML keys are recognised — unknown keys raise
        ``ValueError`` with a clear message listing the offending keys.
        """
        import tomllib

        _load_env(path.resolve().parent)

        text = read_user_text(path)
        data = tomllib.loads(text)

        _validate_toml_keys(data, path)

        root = _TomlTable(data, "")
        project = root.table("project")
        deploy = root.table("deploy")
        db_raw = deploy.table("databricks")
        ct_raw = deploy.table("container")
        aca_raw = deploy.table("azure-container-apps")
        ecs_raw = deploy.table("aws-ecs")
        gcr_raw = deploy.table("gcp-run")
        safety_raw = root.table("safety")
        ci_raw = root.table("ci")
        ci_staging = ci_raw.table("staging")
        ci_production = ci_raw.table("production")

        pipeline_file = Path(project.get("pipeline", str, "main.py"))
        model_name = deploy.get("model_name", str, project.get("name", str, pipeline_file.stem))
        output_fields_raw = deploy.array("output_fields")
        # Preserve an explicit empty array so resolution can reject it with the
        # output-schema context an operator needs to correct it.
        output_fields = list(output_fields_raw) if output_fields_raw is not None else None

        tq_dir = _test_quotes_dir(path.parent, root.table("test_quotes").optional("dir", str))

        db_config = DatabricksConfig(
            experiment_name=db_raw.get("experiment_name", str, "/Shared/haute/default"),
            catalog=db_raw.get("catalog", str, DEFAULT_CATALOG),
            schema=db_raw.get("schema", str, DEFAULT_SCHEMA),
            serving_workload_size=db_raw.get("serving_workload_size", str, "Small"),
            serving_scale_to_zero=db_raw.get("serving_scale_to_zero", bool, True),
        )

        # ``base_image`` has no implicit default — the empty string flows
        # through to the post-init validator, which refuses to deploy an
        # unpinned image for container targets.  Users must explicitly set a
        # patch- or digest-pinned image in [deploy.container].
        ct_config = ContainerConfig(
            registry=ct_raw.get("registry", str, ""),
            port=ct_raw.get("port", int, 8080),
            base_image=ct_raw.get("base_image", str, ""),
        )

        aca_config = AzureContainerAppsConfig(
            resource_group=aca_raw.get("resource_group", str, ""),
            container_app_name=aca_raw.get("container_app_name", str, ""),
            environment_name=aca_raw.get("environment_name", str, ""),
        )

        ecs_config = AwsEcsConfig(
            region=ecs_raw.get("region", str, "eu-west-1"),
            cluster=ecs_raw.get("cluster", str, ""),
            service=ecs_raw.get("service", str, ""),
        )

        gcr_config = GcpRunConfig(
            project=gcr_raw.get("project", str, ""),
            region=gcr_raw.get("region", str, "europe-west1"),
            service=gcr_raw.get("service", str, ""),
        )

        safety_config = SafetyConfig(
            impact_dataset=safety_raw.get("impact_dataset", str, ""),
            min_approvers=safety_raw.table("approval").get("min_approvers", int, 2),
        )

        ci_config = CIConfig(
            provider=ci_raw.get("provider", str, "github"),
            staging_endpoint_suffix=ci_staging.get(
                "endpoint_suffix", str, DEFAULT_STAGING_ENDPOINT_SUFFIX
            ),
            staging_endpoint_url=ci_staging.get("endpoint_url", str, ""),
            production_endpoint_url=ci_production.get("endpoint_url", str, ""),
            production_environment=ci_production.get("environment", str, ""),
        )

        config = cls(
            pipeline_file=pipeline_file,
            model_name=model_name,
            project_dir=path.parent,
            target=deploy.get("target", str, "databricks"),
            endpoint_name=deploy.optional("endpoint_name", str),
            output_fields=output_fields,
            test_quotes_dir=tq_dir,
            databricks=db_config,
            container=ct_config,
            azure_container_apps=aca_config,
            aws_ecs=ecs_config,
            gcp_run=gcr_config,
            safety=safety_config,
            ci=ci_config,
        )

        return _apply_env_overrides(config)

    @classmethod
    def from_cli_args(cls, **kwargs: Any) -> DeployConfig:
        """Build a :class:`DeployConfig` from CLI input — no TOML involved.

        Used when the user runs a deploy command outside a Haute project
        (no ``haute.toml``) and supplies every required field directly.
        Required fields are ``pipeline_file`` and ``model_name``; omitting
        either raises a clear ``ValueError`` naming the missing key.

        Parameters
        ----------
        **kwargs:
            Field values to populate.  ``pipeline_file`` and ``model_name``
            are required; any other documented field on :class:`DeployConfig`
            is optional.

        Returns
        -------
        DeployConfig
            A fully validated config ready to be passed to
            :func:`resolve_config` or :func:`~haute.deploy.deploy`.

        Raises
        ------
        ValueError
            When ``model_name`` or ``pipeline_file`` is missing or empty.
        TypeError
            When ``kwargs`` contains a key that is not a documented field
            on :class:`DeployConfig`.
        """
        missing: list[str] = []
        pipeline_file = kwargs.get("pipeline_file")
        model_name = kwargs.get("model_name")
        if not pipeline_file:
            missing.append("pipeline_file")
        if not model_name:
            missing.append("model_name")
        if missing:
            raise ValueError(
                "DeployConfig.from_cli_args() is missing required field(s): "
                + ", ".join(missing)
                + ". Pass them via --pipeline-file / --model-name on the "
                "command line, or run inside a Haute project with haute.toml."
            )

        return cls(**kwargs)

    def override(self, **cli_kwargs: Any) -> DeployConfig:
        """Return a copy with non-None CLI flags applied over TOML values.

        Raises:
            TypeError: A key that is not a ``DeployConfig`` field, which would otherwise
                be dropped without a word.
        """
        unknown = sorted(set(cli_kwargs) - {f.name for f in fields(self)})
        if unknown:
            raise TypeError(f"DeployConfig.override() got unknown key(s): {', '.join(unknown)}")
        c = copy.deepcopy(self)
        for key, val in cli_kwargs.items():
            if val is not None:
                setattr(c, key, val)
        return c


#: The test quotes' folder when ``haute.toml`` names none, as ``haute init`` writes it.
DEFAULT_TEST_QUOTES_DIR = "tests/quotes"


def test_quote_files(directory: Path) -> list[Path]:
    """The test-quote files in *directory*: its JSON files, by name."""
    return sorted(directory.glob("*.json"))


def _test_quotes_dir(project_dir: Path, configured: str | None) -> Path | None:
    """The test quotes' folder: ``[test_quotes] dir``, or ``tests/quotes`` when it names
    none, which is checked only while it holds test quotes, so a project with neither
    has no gate to fail. A ``dir`` that names no folder (an empty string) is refused rather
    than read as none, which would disable the gate. The folder is the project's own, as
    the Files panels take a path: one outside the project, or reached through a link or
    junction, is refused naming why."""
    if configured == "":
        raise ValueError(
            "[test_quotes] dir must name the project's test quotes folder; got an empty string."
        )
    relative = DEFAULT_TEST_QUOTES_DIR if configured is None else configured
    directory, problem = ordinary_path_problem(project_dir, relative)
    if problem is not None:
        shown = (
            Path(relative).as_posix()
            if problem == "not_ordinary"
            else directory.relative_to(project_dir.resolve()).as_posix()
        )
        raise ValueError(
            f"{shown} {PATH_PROBLEMS[problem]}: [test_quotes] dir must be a folder of the "
            "project's own."
        )
    return directory if configured is not None or test_quote_files(directory) else None


_ENV_BOOLEANS: dict[str, bool] = {
    "true": True,
    "1": True,
    "yes": True,
    "false": False,
    "0": False,
    "no": False,
}

#: Each ``HAUTE_`` override: the config attribute it sets (``section.field`` for a
#: nested one) and the type it is read as.
_ENV_OVERRIDES: dict[str, tuple[str, type[str] | type[bool]]] = {
    "HAUTE_MODEL_NAME": ("model_name", str),
    "HAUTE_ENDPOINT_NAME": ("endpoint_name", str),
    "HAUTE_TARGET": ("target", str),
    "HAUTE_SERVING_WORKLOAD_SIZE": ("databricks.serving_workload_size", str),
    "HAUTE_SERVING_SCALE_TO_ZERO": ("databricks.serving_scale_to_zero", bool),
}


def _env_bool(name: str, raw: str) -> bool:
    """*raw* as a boolean, from ``true``/``false``, ``1``/``0`` or ``yes``/``no`` in any
    case; any other value raises rather than reading as ``False`` (a typo would turn
    scale-to-zero off)."""
    try:
        return _ENV_BOOLEANS[raw.strip().lower()]
    except KeyError:
        raise ValueError(
            f"{name}={raw!r} is not a boolean: use true or false (or 1/0, yes/no)."
        ) from None


def _apply_env_overrides(config: DeployConfig) -> DeployConfig:
    """Apply HAUTE_ prefixed env vars over TOML values.

    Resolution order (highest wins):
      1. CLI flags          (applied later via .override())
      2. Environment vars   HAUTE_MODEL_NAME=foo
      3. haute.toml         model_name = "foo"
    """
    for env_key, (attr_path, attr_type) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        value: str | bool = _env_bool(env_key, raw) if attr_type is bool else raw
        owner: object = config
        *sections, field_name = attr_path.split(".")
        for section in sections:
            owner = getattr(owner, section)
        setattr(owner, field_name, value)
    return config


def output_fields_are_valid(fields: Sequence[object], available: Collection[str]) -> bool:
    """Whether *fields* is a non-empty, duplicate-free list of non-empty column names, each
    one of *available*: the ``[deploy] output_fields`` rule resolution and validation share."""
    names = [field for field in fields if isinstance(field, str) and field]
    return (
        bool(names)
        and len(names) == len(fields)
        and len(set(names)) == len(names)
        and all(name in available for name in names)
    )


@dataclass
class ResolvedDeploy:
    """Computed state after resolving a DeployConfig against a parsed pipeline.

    Created by ``resolve_config()`` - never constructed directly.
    """

    config: DeployConfig
    pipeline_file: Path
    """The pipeline as resolved: canonical, inside the project."""
    project_root: Path
    """The resolved project root every generated file and bundled path stays inside."""
    full_graph: PipelineGraph
    pruned_graph: PipelineGraph
    input_node_ids: list[str]
    output_node_id: str
    artifacts: dict[str, Path]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    project_modules: ProjectModules
    request_rows: RequestRows
    """How a request of several quotes is answered: in request order, or refused."""
    artifact_digests: dict[str, str]
    """Each artefact's content digest, recorded before anything read it."""
    execution_policy: dict[str, Any] = field(default_factory=dict)
    omittable_inputs: list[str] = field(default_factory=list)
    """The request columns a quote may leave out: each bundled model's exposure."""
    removed_node_ids: list[str] = field(default_factory=list)
    snapshot_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    workbench_form: FormDocument | None = None
    """The form the workbench nodes took their tables from; None without one."""
    _resources: ExitStack = field(default_factory=ExitStack, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def close(self) -> None:
        """Release snapshot leases and other resolution-owned resources."""
        if self._closed:
            return
        self._closed = True
        self._resources.close()

    def __enter__(self) -> ResolvedDeploy:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def _load_env(project_root: Path) -> None:
    """Load .env file into os.environ if it exists.

    Uses the required python-dotenv dependency for quoted values,
    interpolation, and multi-line entries.
    """
    env_path = project_root / ".env"
    if not env_path.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(env_path, override=False)


def _validate_python_versions(config: DeployConfig, project_root: Path) -> None:
    """Refuse a deploy whose Python differs from the project's (haute._project_python).

    The deployed model runs ``deployed_python_version``: the project's
    ``.python-version`` pin, or else the Python this deploy runs on, which CI tested.
    So the interpreter running the deploy must be the pinned one, a pinned project's
    container ``python`` base image must run that same version, and any project's must
    run one its ``requires-python`` admits (every release a minor-only tag can float
    to). A project that pins no Python (one ``haute init`` predates) otherwise keeps
    the base image it chose; an image Haute cannot read a version from (a custom
    image, a digest pin) is the project's to keep in step.
    """
    from haute._project_python import (
        admits,
        base_image_python_version,
        container_base_image,
        deploy_python_mismatch,
        pinned_python_version,
        python_version_matches,
        required_python,
    )

    mismatch = deploy_python_mismatch(project_root)
    if mismatch is not None:
        raise DeployError(mismatch, project_root=str(project_root))
    if config.target not in _CONTAINER_BASED_TARGETS:
        return
    base_image = config.container.base_image
    image_python = base_image_python_version(base_image)
    if image_python is None:
        return
    serving = pinned_python_version(project_root)
    if serving is not None and not python_version_matches(serving, image_python):
        raise DeployError(
            f"container.base_image {base_image!r} runs Python {image_python}, but the "
            f"project runs Python {serving}. Set base_image in haute.toml to "
            f"{container_base_image(serving)!r} so the container runs the Python the "
            "pipeline was tested on.",
            base_image=base_image,
            python_version=serving,
        )
    allowed = required_python(project_root)
    if allowed is not None and not admits(allowed, image_python):
        raise DeployError(
            f"container.base_image {base_image!r} runs Python {image_python}, which the "
            f"project's requires-python ({allowed}) excludes. Set base_image in haute.toml "
            "to a python image tagged with a release the project admits.",
            base_image=base_image,
            requires_python=str(allowed),
        )


def resolve_config(config: DeployConfig, *, form: FormDocument | None = None) -> ResolvedDeploy:
    """Parse pipeline, prune graph, detect I/O nodes, collect artifacts, infer schemas.

    This is the main resolution function that converts user-provided
    ``DeployConfig`` into a fully resolved ``ResolvedDeploy`` ready for
    deployment. The pipeline's workbench nodes take their tables from *form* when the caller
    read it (a release, which ships the form it read), else from the form folder, read here.
    """
    from haute._path_resolution import (
        RuntimePathError,
        resolve_runtime_file_path,
    )
    from haute._project import resolve_pipeline_file
    from haute._project_context import ProjectContext, project_scope
    from haute.deploy._bundler import (
        collect_artifacts,
        prepare_shipped_snapshots,
        refuse_data_outside_pipeline_folder,
        with_served_configs,
    )
    from haute.deploy._request_frame import request_frame_schema
    from haute.deploy._request_rows import record_digests, request_rows
    from haute.deploy._schema import (
        DeployBatchRuntime,
        infer_deploy_execution_policy,
        infer_input_schema,
        infer_output_schema,
    )
    from haute.parser import parse_pipeline_file

    # Re-run base-image pinning validation here.  ``__post_init__`` ran at
    # construction time, but ``target`` and ``container.base_image`` can be
    # mutated later via env overrides, ``override(target="container")``, or
    # direct attribute writes — all of which bypass ``__post_init__``.
    # Any container-based deploy (container / azure-container-apps / aws-ecs /
    # gcp-run) funnels through ``resolve_config``, so this is the last
    # chokepoint before we commit to a build.
    if config.target in _CONTAINER_BASED_TARGETS:
        _validate_base_image_pinning(config.container.base_image)

    # Ensure .env is loaded (idempotent — also called in from_toml, but
    # needed here for programmatic DeployConfig construction).
    # The canonical pipeline is resolution's own: the caller's config keeps the path it
    # gave, so a second resolution of it (staging, then production) starts the same.
    if config.project_dir is not None:
        project_root = config.project_dir.resolve()
        pipeline_candidate = config.pipeline_file
        if not pipeline_candidate.is_absolute():
            pipeline_candidate = project_root / pipeline_candidate
        pipeline_file = resolve_pipeline_file(pipeline_candidate)
        if not is_contained(project_root, pipeline_file):
            raise DeployError(
                "Configured pipeline resolves outside the configured project root. "
                "Move it inside the project or correct [project].pipeline.",
                pipeline=str(config.pipeline_file),
                project_root=str(project_root),
            )
    else:
        pipeline_file = resolve_pipeline_file(config.pipeline_file)
        project_root = pipeline_file.parent
    _load_env(project_root)
    _validate_python_versions(config, project_root)

    # Everything below works in the deployed project, whoever calls: its root anchors
    # every relative path, the pipeline settings and the project's `utility`.
    with project_scope(ProjectContext.at(project_root)):
        # Parse the pipeline as it runs, as `haute run` and the editor do: each submodel
        # occurrence expanded into its own nodes (``submodel_runtime/<occurrence>/<node>``),
        # so the response, the request input and the artefacts inside a submodel are
        # found, bundled and scored, and the submodel's nodes run in its place.
        full_graph = parse_pipeline_file(pipeline_file, flatten=True)
        if not full_graph.nodes:
            raise ValueError(f"No nodes found in {pipeline_file}")
        # One request input and one response, counting those inside submodels, as
        # save requires: a pipeline file written by hand can hold more.
        validate_singleton_groups(full_graph)
        # A workbench node's tables are the form's, read once here, so the bundle carries the
        # tables the deployed pipeline was validated on and never reads a folder.
        owner = form_owner(full_graph)
        if owner is not None:
            if form is None:
                form = read_project_form(project_root, owner=owner)
            full_graph = with_tables(full_graph, workbench_tables(form.spec))
            unconnectable = connection_errors(full_graph, read_workbench_config(project_root).form)
            if unconnectable:
                raise DeployError("\n".join(unconnectable))
        else:
            form = None

        # Find output node
        output_node_id = find_output_node(full_graph)

        # Prune to scoring path
        pruned_graph, _kept_ids, removed_ids = prune_for_deploy(full_graph, output_node_id)

        # Find input nodes
        deploy_inputs = find_deploy_input_nodes(pruned_graph)
        if not deploy_inputs:
            # Fallback: use the single source node in the pruned graph
            all_sources = find_source_nodes(pruned_graph)
            if len(all_sources) == 1:
                source_id = all_sources[0]
                source = next(node for node in pruned_graph.nodes if node.id == source_id)
                if source.data.nodeType != NodeType.DATA_INPUT:
                    raise ValueError(
                        f"Sole source node {source_id!r} is {source.data.nodeType.value!r}, "
                        "not a dataInput. Add a Quote Input (@pipeline.api_input(...)) or a "
                        "Workbench Input (@pipeline.workbench_input(...))."
                    )
                deploy_inputs = all_sources
            elif len(all_sources) == 0:
                raise ValueError("No source nodes in the pruned graph.")
            else:
                raise ValueError(
                    f"Multiple source nodes in pruned graph ({all_sources}) but none "
                    "is a request input. Add a Quote Input (@pipeline.api_input(...)) or a "
                    "Workbench Input (@pipeline.workbench_input(...))."
                )

        # Collect artifacts. Snapshot generations remain leased until the
        # resolved deployment is validated and shipped (or explicitly closed).
        pipeline_dir = pipeline_file.parent
        resources = ExitStack()
        snapshot_provenance: dict[str, dict[str, Any]] = {}
        model_sources: dict[str, dict[str, Any]] = {}
        try:
            # Validate every local runtime path before any bundle copy, schema
            # read, or sample load.  Reuse execution's maintained enumeration so
            # deploy cannot drift from runtime's definition of local file reads.
            from haute.execution import _local_runtime_input_path_fields

            for node in pruned_graph.nodes:
                for field_name in _local_runtime_input_path_fields(node):
                    raw_path = node.data.config.get(field_name)
                    if not isinstance(raw_path, str) or not raw_path:
                        continue
                    try:
                        resolve_runtime_file_path(
                            raw_path,
                            source_file=pipeline_file,
                            pipeline_dir=pipeline_dir,
                            project_root=project_root,
                            prefer="pipeline",
                            enforce_project_root=True,
                        )
                    except RuntimePathError as exc:
                        raise DeployError(
                            f"Node {node.id!r} field {field_name!r} has invalid local "
                            f"runtime path {raw_path!r} ({type(exc).__name__}): {exc}. "
                            "Use a path inside the configured project.",
                            node_id=node.id,
                            field=field_name,
                            path=raw_path,
                        ) from exc
            # Project code is read statically before anything runs the preamble: an
            # import the bundle does not carry is not importable here either, so the
            # schema dry run below could only fail on it with a bare import error.
            project_modules = resolve_project_modules(pruned_graph.preamble or "", pipeline_dir)
            if project_modules.unbundled_imports:
                raise DeployError(" ".join(project_modules.unbundled_imports))
            # Only data in the pipeline folder ships, from a snapshot that is current.
            refuse_data_outside_pipeline_folder(
                pruned_graph, deploy_inputs, pipeline_dir, project_root
            )
            prepare_shipped_snapshots(pruned_graph, deploy_inputs, pipeline_dir, project_root)
            served_configs: dict[str, dict[str, Any]] = {}
            artifacts = collect_artifacts(
                pruned_graph,
                deploy_inputs,
                pipeline_dir,
                project_root=project_root,
                resources=resources,
                snapshot_provenance=snapshot_provenance,
                model_sources=model_sources,
                served_configs=served_configs,
            )
            # The graph that ships names each MLflow model's bundled file; the parsed graph,
            # whose nodes it shared, keeps the configs the pipeline file gave.
            pruned_graph = with_served_configs(pruned_graph, served_configs)
            # Before anything below reads an artefact: every copy into a bundle is checked
            # against these, so it carries the bytes this resolution checked.
            artifact_digests = record_digests(artifacts)

            # Infer schemas. The output-schema dry-run scores with the exact bundled
            # artifacts the container serves so validate-time and serve-time load the
            # same model bytes (not a live MLflow lookup), and so the schema cache key
            # reflects the served model identity.
            artifact_paths = {name: str(path) for name, path in artifacts.items()}
            # Schema inference re-resolves the graph's relative paths; scoping them to
            # the project root validated above makes it read the files that check
            # admitted, never one outside the project.
            input_schema = infer_input_schema(pruned_graph, deploy_inputs[0])
            from haute.deploy._scorer import reads_records

            if not reads_records(pruned_graph, deploy_inputs):
                # A flat Quote Input's request is framed by this schema: a type a JSON
                # request cannot carry is refused before anything is built.
                request_frame_schema(input_schema)
            from haute.deploy._scorer import omittable_request_columns

            omittable_inputs = omittable_request_columns(pruned_graph, artifact_paths, input_schema)
            # Plan the served batch strategy first: it needs only the one-row
            # sample, and a target that scores batches in process must refuse an
            # unprovable group-by before the schema dry-run tries to run one.
            # Container targets score batches in a hard-capped worker; the
            # Databricks pyfunc target scores them in the serving process.
            batch_runtime: DeployBatchRuntime = (
                "hard_capped_worker" if config.target in _CONTAINER_BASED_TARGETS else "in_process"
            )
            execution_policy = infer_deploy_execution_policy(
                pruned_graph,
                output_node_id,
                deploy_inputs,
                artifact_paths=artifact_paths,
                batch_runtime=batch_runtime,
            )
            output_schema = infer_output_schema(
                pruned_graph,
                output_node_id,
                deploy_inputs,
                artifact_paths=artifact_paths,
                project_root=project_root,
            )
            if config.output_fields is not None:
                fields = config.output_fields
                available = list(output_schema)
                if not output_fields_are_valid(fields, output_schema):
                    raise DeployError(
                        "[deploy].output_fields must be a non-empty, duplicate-free list "
                        "of non-empty output column names present in the inferred schema. "
                        f"Available fields: {available!r}. Correct [deploy].output_fields.",
                        output_fields=fields,
                        available_fields=available,
                    )
                output_schema = {field: output_schema[field] for field in fields}
            # Whether a request of several quotes is answered in request order; a lookup
            # the bundle carries is read here for repeated join keys.
            rows = request_rows(pruned_graph, deploy_inputs, output_node_id, artifact_paths)
        except BaseException:
            resources.close()
            raise

        logger.info(
            "config_resolved",
            pipeline=str(pipeline_file),
            total_nodes=len(full_graph.nodes),
            pruned_nodes=len(pruned_graph.nodes),
            removed_nodes=len(removed_ids),
            input_nodes=len(deploy_inputs),
            artifacts=len(artifacts),
            multi_quote=rows.multi_quote,
        )
        return ResolvedDeploy(
            config=config,
            pipeline_file=pipeline_file,
            project_root=project_root,
            full_graph=full_graph,
            pruned_graph=pruned_graph,
            input_node_ids=deploy_inputs,
            output_node_id=output_node_id,
            artifacts=artifacts,
            input_schema=input_schema,
            output_schema=output_schema,
            project_modules=project_modules,
            request_rows=rows,
            artifact_digests=artifact_digests,
            omittable_inputs=omittable_inputs,
            execution_policy=execution_policy,
            removed_node_ids=removed_ids,
            snapshot_provenance=snapshot_provenance,
            model_sources=model_sources,
            workbench_form=form,
            _resources=resources,
        )
