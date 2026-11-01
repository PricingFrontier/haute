"""Scaffold templates for ``haute init``.

Each function returns a string for a specific file, parameterised by
project name, deploy target, and CI provider.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from haute._project_python import DEFAULT_PYTHON, container_base_image
from haute._workbench_folder import folder_files
from haute._workbench_form import blank_form
from haute.deploy._config import DEFAULT_STAGING_ENDPOINT_SUFFIX, DEFAULT_TEST_QUOTES_DIR

# ── Per-target configuration ─────────────────────────────────────────
#
# Central registry of every deploy target ``haute init`` offers.  Each entry carries:
#   label        – human-readable name for .env.example header
#   env_body     – literal body appended after the .env.example header
#   secrets      – ordered list of CI secret / env-var names
#   toml_section – callable(name) -> TOML string for [deploy.*] block
#   registry_login – a container-based target's shell command that pipes its
#                  credential to `docker login "$registry"` (_registry_login_script)

_TargetConfig = dict[str, str | list[str] | Callable[[str], str]]

TARGETS: dict[str, _TargetConfig] = {
    "databricks": {
        "label": "Databricks",
        "env_body": """
# Data access credentials — SQL warehouse reads and writes, workspace browsing
DATABRICKS_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_TOKEN=your_databricks_token_here

# MLflow credentials — experiment tracking, the model registry, loading logged
# models, and deploy's model registration. MLflow never uses the data access
# pair above; copy its values here if one token's scopes cover both.
# (Alternatively set MLFLOW_TRACKING_URI=databricks://<profile> to use a profile.)
DATABRICKS_MLFLOW_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_MLFLOW_TOKEN=your_databricks_mlflow_token_here

# Production serving endpoint credentials
DATABRICKS_RATING_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_RATING_TOKEN=your_databricks_token_here
""",
        "secrets": [
            "DATABRICKS_MLFLOW_HOST",
            "DATABRICKS_MLFLOW_TOKEN",
            "DATABRICKS_RATING_HOST",
            "DATABRICKS_RATING_TOKEN",
        ],
        "toml_section": lambda name: (
            f"""\
[deploy.databricks]
experiment_name = "/Shared/haute/{name}"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"
serving_scale_to_zero = true
"""
        ),
    },
    "container": {
        "label": "Container registry",
        "env_body": """
DOCKER_USERNAME=
DOCKER_PASSWORD=
""",
        "secrets": [
            "DOCKER_USERNAME",
            "DOCKER_PASSWORD",
        ],
        "registry_login": (
            "printf '%s' \"$DOCKER_PASSWORD\""
            ' | docker login "$registry" --username "$DOCKER_USERNAME" --password-stdin'
        ),
        "toml_section": lambda name: (
            """\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"
"""
        ),
    },
    "azure-container-apps": {
        "label": "Azure Container Apps",
        "env_body": """
AZURE_SUBSCRIPTION_ID=
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
""",
        "secrets": [
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
        ],
        # The service principal logs in to the Azure Container Registry: it needs AcrPush.
        "registry_login": (
            "printf '%s' \"$AZURE_CLIENT_SECRET\""
            ' | docker login "$registry" --username "$AZURE_CLIENT_ID" --password-stdin'
        ),
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.azure-container-apps]
resource_group = ""
container_app_name = "{name}"
environment_name = ""
"""
        ),
    },
    "aws-ecs": {
        "label": "AWS ECS",
        "env_body": """
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-1
""",
        "secrets": [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION",
        ],
        # An ECR password lasts 12 hours, so each login fetches one with the AWS keys,
        # through the AWS CLI v1 that uvx runs.
        "registry_login": (
            'uvx --from "awscli>=1.18,<2" aws ecr get-login-password --region "$AWS_DEFAULT_REGION"'
            ' | docker login "$registry" --username AWS --password-stdin'
        ),
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.aws-ecs]
region = "eu-west-1"
cluster = ""
service = "{name}"
"""
        ),
    },
    "gcp-run": {
        "label": "GCP Cloud Run",
        "env_body": """
GCP_PROJECT_ID=
GCP_SERVICE_ACCOUNT_KEY=
""",
        "secrets": [
            "GCP_PROJECT_ID",
            "GCP_SERVICE_ACCOUNT_KEY",
        ],
        # The service account's JSON key logs in as Artifact Registry's `_json_key` user.
        "registry_login": (
            "printf '%s' \"$GCP_SERVICE_ACCOUNT_KEY\""
            ' | docker login "$registry" --username _json_key --password-stdin'
        ),
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.gcp-run]
project = ""
region = "europe-west1"
service = "{name}"
"""
        ),
    },
}


# Container platforms whose service update is not implemented: ``haute deploy``
# builds and pushes the image and finishes without updating the service. The same
# set as ``haute.deploy._CONTAINER_PLATFORM_TARGETS`` (a test keeps them equal).
BUILD_AND_PUSH_ONLY_TARGETS = ("azure-container-apps", "aws-ecs", "gcp-run")


# Targets whose deploy builds a container image. ``haute status`` reads the
# MLflow registry, so these have no registered version to tag a release with.
_CONTAINER_BASED_TARGETS = ("container", *BUILD_AND_PUSH_ONLY_TARGETS)


# What a release is built from: a push to main that changes none of these deploys
# nothing. The dependency files change what the deployed image or model installs.
#: The pipeline file a scaffolded project's ``haute.toml`` names.
PIPELINE_FILE = "rating/main.py"
#: The folders a scaffolded project's release is read from: the pipeline folder, which
#: holds its code, config and versioned files (``files/data``, ``files/models``), and the
#: test quotes. The root's ``data/`` is ignored and its ``models/`` is for a pipeline at
#: the root, which a scaffold never makes, so neither changes a release.
_RELEASE_INPUT_DIRS = (PurePosixPath(PIPELINE_FILE).parent.as_posix(), DEFAULT_TEST_QUOTES_DIR)
_RELEASE_INPUT_FILES = ("haute.toml", "pyproject.toml", "uv.lock")


#: The Python a generated CI job runs when the project names none (``haute init`` passes
#: the project's own, ``haute._project_python``). The templates below are written with
#: it, and ``_pin_ci_python`` swaps in the project's.
DEFAULT_CI_PYTHON = DEFAULT_PYTHON

# The base image a container target's template is written with; ``haute_toml`` swaps in
# one on the project's Python.
_TEMPLATE_BASE_IMAGE = 'base_image = "python:3.11.9-slim"'

# Every form a template pins the job's Python in: setup-uv, Azure's UsePythonVersion and
# GitLab's job image.
_CI_PYTHON_PINS = ('python-version: "{}"', 'versionSpec: "{}"', "image: python:{}\n")


def _pin_ci_python(text: str, python_version: str) -> str:
    """*text* with every job's Python pin moved from the default to *python_version*."""
    for pin in _CI_PYTHON_PINS:
        text = text.replace(pin.format(DEFAULT_CI_PYTHON), pin.format(python_version))
    return text


# The characters a Databricks serving endpoint name takes. A staging suffix is appended to
# the endpoint, model and experiment names, and is written into the generated YAML and
# shell, where these characters are inert.
_STAGING_SUFFIX_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

# How the templates' staging deploy steps, and a Databricks project's smoke and impact
# steps, name the staging suffix; ``_pin_staging_suffix`` swaps in the project's.
_STAGING_SUFFIX_FLAG = '--endpoint-suffix "{}"'


def _verification_suffix_option(target: str) -> str:
    """The staging suffix option a smoke or impact step passes, with its leading space.

    Databricks names its staging endpoint by the suffix. A container-based target's
    ``haute smoke`` and ``haute impact`` refuse the option and call the addresses in
    ``[ci.staging]`` and ``[ci.production]`` ``endpoint_url`` instead.
    """
    if target in _CONTAINER_BASED_TARGETS:
        return ""
    return " " + _STAGING_SUFFIX_FLAG.format(DEFAULT_STAGING_ENDPOINT_SUFFIX)


def staging_suffix_problem(suffix: object) -> str | None:
    """Why *suffix* cannot name a staging deploy, or ``None`` when it can."""
    if not isinstance(suffix, str):
        return f"is {suffix!r}: a staging suffix must be a string"
    if not suffix:
        return (
            "is empty, so the staging deploy would deploy staging over production; "
            f'set a staging suffix such as "{DEFAULT_STAGING_ENDPOINT_SUFFIX}"'
        )
    if _STAGING_SUFFIX_PATTERN.fullmatch(suffix) is None:
        return (
            f"is {suffix!r}: a staging suffix may hold only ASCII letters, digits, '-' and '_', "
            "the characters a Databricks endpoint name takes"
        )
    return None


def _pin_staging_suffix(text: str, staging_suffix: str) -> str:
    """*text* with the staging steps' suffix moved from the default to *staging_suffix*."""
    problem = staging_suffix_problem(staging_suffix)
    if problem is not None:
        raise ValueError(f"The staging suffix {problem}")
    return text.replace(
        _STAGING_SUFFIX_FLAG.format(DEFAULT_STAGING_ENDPOINT_SUFFIX),
        _STAGING_SUFFIX_FLAG.format(staging_suffix),
    )


# A name every provider reads as one environment, inert in the generated YAML, where it is
# quoted so that a name such as "true" or "001" stays a string. No dot: Azure DevOps reads
# "a.b" as resource "b" of environment "a".
_PRODUCTION_ENVIRONMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}")

# The environment Azure DevOps' production deployment runs under when none is named: a
# deployment job always needs one there.
_AZURE_PRODUCTION_ENVIRONMENT = "production"


def production_environment_problem(name: object) -> str | None:
    """Why *name* cannot name the production deploy's environment, or ``None`` when it can.

    Empty names none: the GitHub and GitLab production jobs then run for whoever may start
    them, which works on every plan.
    """
    if not isinstance(name, str):
        return f"is {name!r}: an environment name must be a string"
    if name and _PRODUCTION_ENVIRONMENT_PATTERN.fullmatch(name) is None:
        return (
            f"is {name!r}: an environment name may hold only ASCII letters, digits, '-' and "
            "'_', starting with a letter or digit, in at most 255 characters"
        )
    return None


def _production_environment(name: str) -> str:
    """*name*, checked to stand as the production job's ``environment``."""
    problem = production_environment_problem(name)
    if problem is not None:
        raise ValueError(f"The production environment {problem}")
    return name


def _github_trigger_paths() -> str:
    """The deploy workflow's ``on.push.paths`` entries, one per release input."""
    dirs = [f'      - "{name}/**"' for name in _RELEASE_INPUT_DIRS]
    files = [f'      - "{name}"' for name in _RELEASE_INPUT_FILES]
    return "\n".join([*dirs, *files])


def _azure_devops_trigger_paths() -> str:
    """The pipeline's ``trigger.paths.include`` entries, one per release input."""
    dirs = [f"      - {name}/" for name in _RELEASE_INPUT_DIRS]
    files = [f"      - {name}" for name in _RELEASE_INPUT_FILES]
    return "\n".join([*dirs, *files])


def _release_tag_script(target: str, indent: str) -> str:
    """Shell lines, indented, that tag the production release in git.

    Databricks tags the registered MLflow model version, which ``haute status`` reads
    from the registry with the MLflow credentials the step is given; a read that fails,
    or prints anything but a version number, fails the step rather than tagging a
    made-up version. A container-based target tags the deployed commit, the identity
    its image tag carries; a redeploy of a commit that is already tagged leaves the tag
    as it is.
    """
    if target in _CONTAINER_BASED_TARGETS:
        lines = [
            'TAG="deploy/$(git rev-parse --short HEAD)"',
            'if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null; then',
            '  echo "Release tag $TAG already exists."',
            "else",
            '  git tag "$TAG"',
            '  git push origin "$TAG"',
            "fi",
        ]
    else:
        lines = [
            'VERSION="$(uv run haute status --version-only)"',
            'case "$VERSION" in',
            "  ''|*[!0-9]*)",
            '    echo "haute status printed no model version: $VERSION" >&2',
            "    exit 1",
            "    ;;",
            "esac",
            'git tag "deploy/v$VERSION"',
            'git push origin "deploy/v$VERSION"',
        ]
    return "\n".join(indent + line for line in lines)


def _registry_login_script(target: str, indent: str) -> str:
    """Shell lines, indented, that log in to *target*'s registry before its deploy pushes.

    ``haute deploy`` pushes with a plain ``docker push``. ``haute ci registry-host`` prints
    the host of ``[deploy.container] registry`` as its last line, after any log line, and
    an empty one when the registry is empty: a ``container`` image then stays local, and a
    platform target's deploy refuses it. The target's ``registry_login`` pipes its
    credential to ``docker login``. No ``-u``: on GitLab the lines share one shell with the
    job's later entries and the runner's own.
    """
    login = _get_target(target)["registry_login"]
    assert isinstance(login, str)
    lines = [
        "set -eo pipefail",
        'registry="$(uv run haute ci registry-host | tail -n 1)"',
        'if [ -n "$registry" ]; then',
        f"  {login}",
        "fi",
    ]
    return "\n".join(indent + line for line in lines)


def _deploy_timeout_minutes(target: str) -> int:
    """Minutes a generated deploy job may run.

    A Databricks deploy waits until its endpoint serves the new version
    (``haute.deploy._databricks_serving.SERVING_READY_TIMEOUT_SECONDS``, 45 minutes), and
    a first deploy's build can take most of that; a container deploy builds and pushes
    an image.
    """
    return 60 if target == "databricks" else 15


def _get_target(target: str) -> _TargetConfig:
    """Look up *target* in :data:`TARGETS`, raising on unknown names."""
    try:
        return TARGETS[target]
    except KeyError:
        msg = f"Unknown target: {target}"
        raise ValueError(msg) from None


def _build_only_notice(target: str) -> str:
    """The comment that labels a build-and-push-only target in its generated files.

    Empty for a target that deploys end to end.
    """
    if target not in BUILD_AND_PUSH_ONLY_TARGETS:
        return ""
    return (
        f"# Build and push only: for {_get_target(target)['label']}, `haute deploy` builds the\n"
        "# scoring image, pushes it to the registry [deploy.container] names (required),\n"
        "# and finishes without updating the service, which is not implemented yet.\n"
        "# Point the service at the image yourself; the deploy output names the image tag.\n"
        "# The generated CI stops after the push: a smoke test or impact analysis would\n"
        "# test the service before it runs the new image, so run `haute smoke` and\n"
        "# `haute impact` once it does.\n"
    )


# ── haute.toml ────────────────────────────────────────────────────────


def haute_toml(
    name: str,
    target: str,
    ci: str,
    python_version: str = DEFAULT_PYTHON,
) -> str:
    """Generate an automated project's ``haute.toml`` with only the relevant target section.

    A container target's base image runs *python_version*, the project's Python. A workbench
    project's is :func:`workbench_haute_toml`.
    """
    min_approvers = 2

    sections = [
        f"""\
[project]
name = "{name}"
pipeline = "{PIPELINE_FILE}"

[deploy]
target = "{target}"
model_name = "{name}"
endpoint_name = "{name}"
""",
        _target_section(name, target).replace(
            _TEMPLATE_BASE_IMAGE, f'base_image = "{container_base_image(python_version)}"'
        ),
        f"""\
[test_quotes]
dir = "tests/quotes"

[safety]
# `haute impact` compares staging with production on these quotes. Without a
# sample it compares the test quotes; commit a Parquet sample of your book (not
# under data/, which is gitignored) and name it here to compare that instead:
# impact_dataset = "tests/impact/portfolio_sample.parquet"

[safety.approval]
min_approvers = {min_approvers}

[ci]
provider = "{ci}"

[ci.staging]
endpoint_suffix = "{DEFAULT_STAGING_ENDPOINT_SUFFIX}"
{_service_addresses(name, target)}""",
    ]
    return "\n".join(sections)


def workbench_haute_toml(
    name: str,
    ci: str,
    *,
    host: str,
    store: str,
    python_version: str = DEFAULT_PYTHON,
) -> str:
    """A workbench project's ``haute.toml``: it hosts no scoring API, so ``[deploy]`` holds the
    underwriter app's image alone, ``[deploy.container]`` with its base image on
    *python_version*, and no endpoint settings, beside the ``[workbench]`` table for the app's
    *host* and the quote store on *store* (specs/workbench)."""
    return f"""\
[project]
name = "{name}"
pipeline = "{PIPELINE_FILE}"

# The underwriter app's image: what it is built on, the registry the workbench's CI pushes
# it to (required there) and the port it serves on.
[deploy.container]
registry = ""
port = 8080
base_image = "{container_base_image(python_version)}"

[test_quotes]
dir = "tests/quotes"

[ci]
provider = "{ci}"

{workbench_table(host, store)}"""


# ── The workbench's table and its connections ─────────────────────────

#: The quote store's environments a new workbench declares: ``dev`` for ``haute app serve``,
#: ``staging`` and ``prod`` for the hosted app.
WORKBENCH_ENVIRONMENTS = ("dev", "staging", "prod")
#: The databases a new workbench's quote store can be on, the servers of
#: ``haute._workbench_config.STORE_PROVIDERS``: all three environments are on one.
WORKBENCH_STORES = ("sqlserver", "postgresql")
#: The prefix of the variables a hosted environment's sign-in names (``HAUTE_STORE_USERNAME``,
#: the role PostgreSQL maps the app's managed identity to), beside the host's
#: ``HAUTE_STORE_*`` location.
_STORE_VARIABLE_PREFIX = "HAUTE_STORE"


def _sign_in_variables(prefix: str, keys: Sequence[str]) -> str:
    """Each of a sign-in's *keys* as a TOML line naming its variable, ``username_env`` as
    ``<prefix>_USERNAME``."""
    return "".join(f'{key} = "{prefix}_{key.removesuffix("_env").upper()}"\n' for key in keys)


def workbench_table(host: str, store: str) -> str:
    """``haute.toml``'s ``[workbench]`` table as a workbench ``haute init`` writes it: the
    workbench enabled, the underwriter app run on *host* and the quote store's environments
    declared, each on *store* and signed in to by the hosted app's managed identity
    (specs/workbench)."""
    from haute._workbench_config import APP_HOSTS, store_sign_in_keys

    if host not in APP_HOSTS:
        raise ValueError(f"Unknown app host: {host}")
    if store not in WORKBENCH_STORES:
        raise ValueError(f"Unknown quote store: {store}")
    sign_in = _sign_in_variables(
        _STORE_VARIABLE_PREFIX, store_sign_in_keys(store, "managed-identity")
    )
    environments = "\n".join(
        f'[workbench.store.{name}]\nprovider = "{store}"\nauth = "managed-identity"\n{sign_in}'
        for name in WORKBENCH_ENVIRONMENTS
    )
    return f"""\
[workbench]
enabled = true

[workbench.app]
host = "{host}"

# The quote store's environments. A hosted app finds its database through the host's
# HAUTE_STORE_* variables; the editor and haute app serve through connections.toml.
{environments}"""


def connections_toml(store: str, sign_in: str) -> str:
    """``connections.toml`` as a workbench ``haute init`` writes it: a connection for each of
    the quote store's environments, on *store*, its server and database left for the analyst
    to fill in, signed in with *sign_in*, one of ``CONNECTION_SIGN_INS``, whose variables,
    ``HAUTE_<ENVIRONMENT>_<KEY>``, ``.env`` holds."""
    from haute._connections import CONNECTION_SIGN_INS, connection_sign_in_keys

    if store not in WORKBENCH_STORES:
        raise ValueError(f"Unknown quote store: {store}")
    if sign_in not in CONNECTION_SIGN_INS:
        raise ValueError(f"Unknown sign-in: {sign_in}")
    keys = connection_sign_in_keys(store, sign_in)
    connections = "\n".join(
        f'[{name}]\nprovider = "{store}"\nserver = ""\ndatabase = ""\nauth = "{sign_in}"\n'
        + _sign_in_variables(f"HAUTE_{name.upper()}", keys)
        for name in WORKBENCH_ENVIRONMENTS
    )
    return f"""\
# Where each of the quote store's databases is, for the editor and haute app serve.
# The values of the variables a sign-in names are read from .env, never from this file.

{connections}"""


def connection_variables(project_root: Path) -> list[str]:
    """The variables the connections of *project_root*'s ``connections.toml`` read their
    sign-ins from, in the file's order; none without the file."""
    from haute._connections import read_connections

    named = (
        variable
        for connection in read_connections(project_root).values()
        for variable in connection.variables.values()
    )
    return list(dict.fromkeys(named))


def _service_addresses(name: str, target: str) -> str:
    """A container-based target's staging and production addresses, commented out.

    Its ``haute smoke`` and ``haute impact`` call the running services at ``[ci.staging]``
    and ``[ci.production]`` ``endpoint_url``, which only IT can supply; until they are
    uncommented both commands stop naming the missing address. Databricks reaches its
    endpoints by name and has none.
    """
    if target not in _CONTAINER_BASED_TARGETS:
        return ""
    return f"""\
# The services the smoke test and impact analysis call: uncomment both addresses
# once IT runs them.
# endpoint_url = "https://{name}-staging.example.com"

[ci.production]
# endpoint_url = "https://{name}.example.com"
"""


def _target_section(name: str, target: str) -> str:
    cfg = _get_target(target)
    fn = cfg["toml_section"]
    assert callable(fn)
    return _build_only_notice(target) + fn(name)


# ── .env.example ──────────────────────────────────────────────────────

_ENV_EXAMPLE_HEADER = """\
# Haute - {label} credentials
# Copy this file to .env and fill in your values.
# .env is gitignored and will never be committed.
#
#   cp .env.example .env
"""


def env_example(target: str, connection_variables: Sequence[str] = ()) -> str:
    """Generate an automated project's ``.env.example`` with only the credentials for the
    chosen target, and the variables ``connections.toml``'s sign-ins read,
    *connection_variables*, when it names any."""
    return _env_example_text(target, _build_only_notice(target), connection_variables)


def app_env_example(host: str, connection_variables: Sequence[str] = ()) -> str:
    """A workbench project's ``.env.example``: the credentials its CI publishes the underwriter
    app's image to *host*'s registry with, and the variables ``connections.toml``'s sign-ins
    read, *connection_variables*."""
    return _env_example_text(host, "", connection_variables)


def _env_example_text(key: str, notice: str, connection_variables: Sequence[str]) -> str:
    """``.env.example`` with *key*'s credentials, :data:`TARGETS`' entry, under *notice*."""
    cfg = _get_target(key)
    label = cfg["label"]
    assert isinstance(label, str)
    env_body = cfg["env_body"]
    assert isinstance(env_body, str)
    text = _ENV_EXAMPLE_HEADER.format(label=label) + (f"#\n{notice}" if notice else "") + env_body
    if connection_variables:
        named = "".join(f"{variable}=\n" for variable in connection_variables)
        text += (
            f"\n# The sign-ins connections.toml names, for the editor and haute app serve\n{named}"
        )
    return text


# What marks a value in ``.env.example`` as a stand-in for the real one: the token
# placeholders and the example workspace's number. Other values there are usable
# defaults (``AWS_DEFAULT_REGION=eu-west-1``).
_PLACEHOLDER_MARKERS = ("your_", "1234567890123456")


def env_example_placeholders(target: str) -> dict[str, str]:
    """The stand-in values ``.env.example`` gives *target*'s variables, by name.

    ``haute ci secrets`` refuses a value still equal to its placeholder, so an unedited
    copy of the template is never uploaded as a CI secret. A usable default, such as an
    AWS region, is no placeholder.
    """
    env_body = _get_target(target)["env_body"]
    assert isinstance(env_body, str)
    placeholders: dict[str, str] = {}
    for line in env_body.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(marker in value for marker in _PLACEHOLDER_MARKERS):
            placeholders[key.strip()] = value.strip()
    return placeholders


# ── CI secrets helpers ────────────────────────────────────────────────


def ci_secret_names(target: str) -> list[str]:
    """The CI secrets *target*'s generated deploy, registry login, smoke, impact and tag
    steps read, or, for an app host, its publish job's registry login."""
    secrets = _get_target(target)["secrets"]
    assert isinstance(secrets, list)
    return list(secrets)


def ci_credentials(project_root: Path, target: str) -> str:
    """The :data:`TARGETS` key whose credentials *project_root*'s generated CI reads: a
    workbench project's ``[workbench.app] host``, since it publishes the underwriter app and
    deploys no endpoint, else *target*, its ``[deploy] target``.

    Raises :class:`~haute._workbench_config.WorkbenchConfigError` for a ``[workbench]`` table
    that cannot be read, and for a workbench project naming no host.
    """
    from haute._workbench_config import APP_HOSTS, WorkbenchConfigError, read_workbench_config

    config = read_workbench_config(project_root)
    if not config.enabled:
        return target
    if config.app_host is None:
        raise WorkbenchConfigError(
            "haute.toml's [workbench.app] names no host: the workbench's CI publishes the "
            f"app with its host's credentials, so set host ({', '.join(sorted(APP_HOSTS))})."
        )
    return config.app_host


def _format_secrets(target: str, indent: str, fmt: str) -> str:
    """Build an indented env-var block for any CI provider.

    *fmt* is a format string with ``{key}`` placeholder, e.g.
    ``"{key}: ${{{{ secrets.{key} }}}}"`` for GitHub Actions.
    """
    cfg = _get_target(target)
    secrets = cfg["secrets"]
    assert isinstance(secrets, list)
    lines = [f"{indent}{fmt.format(key=s)}" for s in secrets]
    return "\n".join(lines)


def _github_secrets_env(target: str) -> str:
    """Return the env: block for GitHub Actions secrets, indented for YAML."""
    return _format_secrets(
        target,
        indent="          ",
        fmt="{key}: ${{{{ secrets.{key} }}}}",
    )


def _gitlab_secrets_env(target: str) -> str:
    """Return the variables: block for GitLab CI, indented for YAML."""
    return _format_secrets(
        target,
        indent="    ",
        fmt="{key}: ${key}",
    )


def _azure_devops_secrets_env(target: str, indent: str = "              ") -> str:
    """Return the env: block for Azure DevOps pipeline secrets, indented for YAML.

    The block nests under ``env:`` at more than one depth. Job-level steps place
    ``env:`` at 12 spaces (keys must sit at 14), while the ``DeployProduction``
    deployment strategy nests ``env:`` at 18 spaces (keys must sit at 20). The
    caller supplies the matching *indent* so the emitted keys are always MORE
    indented than their parent ``env:`` — sharing one indent across both depths
    produces YAML whose production secrets are under-indented and unparseable.
    """
    return _format_secrets(
        target,
        indent=indent,
        fmt="{key}: $({key})",
    )


def _github_login_step(target: str, secrets_env: str) -> str:
    """A container-based target's registry login step, before its deploy step.

    Empty for Databricks, which pushes no image.
    """
    if target not in _CONTAINER_BASED_TARGETS:
        return ""
    return f"""\
      - name: Log in to the registry
        env:
{secrets_env}
        run: |
{_registry_login_script(target, " " * 10)}
"""


# ── GitHub Actions: ci.yml ────────────────────────────────────────────


# The step that ends each validate job: an automated project's dry-run deploy, which scores
# the test quotes, or a workbench project's release build, which scores them as a deploy does
# and holds the form to the pipeline. Each is a display name and the command it runs.
_DRY_RUN_CHECK = ("Dry-run deploy (score test quotes)", "uv run haute deploy --dry-run")
_APP_BUILD_CHECK = ("Build the app's release (score test quotes)", "uv run haute app build")
#: What a workbench project's publish job runs once logged in to the registry.
_APP_PUBLISH = "uv run haute app build --push"
# Minutes a workbench project's publish job may run: it builds and pushes one image.
_PUBLISH_TIMEOUT_MINUTES = 15


def github_ci_yml(*, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_github_ci_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_github_ci_yml(_DRY_RUN_CHECK), python_version)


def github_app_ci_yml(*, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """A workbench project's PR checks: :func:`_github_ci_yml` ending in the app's release
    build, every job's Python pinned to *python_version*."""
    return _pin_ci_python(_github_ci_yml(_APP_BUILD_CHECK), python_version)


def _github_ci_yml(check: tuple[str, str]) -> str:
    """PR checks workflow for GitHub Actions, its pipeline validation ending in *check*."""
    check_name, check_run = check
    return f"""\
name: CI

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  lint:
    name: Lint & Format
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  typecheck:
    name: Type Check
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run mypy .

  test:
    name: Test
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run pytest -v

  pipeline-validate:
    name: Pipeline Validation
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Lint pipeline
        run: uv run haute lint
      - name: {check_name}
        run: {check_run}
"""


def github_deploy_yml(
    target: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
    staging_suffix: str = DEFAULT_STAGING_ENDPOINT_SUFFIX,
) -> str:
    """:func:`_github_deploy_yml` on *python_version*, its staging steps on *staging_suffix*."""
    text = _pin_ci_python(_github_deploy_yml(target), python_version)
    return _pin_staging_suffix(text, staging_suffix)


def _github_deploy_yml(target: str) -> str:
    """Merge-to-main deploy workflow for GitHub Actions.

    Runs automatically: validate → staging → smoke test → impact analysis.
    Production is a separate manual workflow (``deploy-production.yml``)
    so it works on GitHub Free without environment protection rules.

    The impact-analysis job outputs the deployed git SHA so the
    production workflow can verify it is deploying exactly what was tested.
    A build-and-push-only target stops after staging's image push: there is
    no updated service to smoke-test or compare.
    """
    secrets_env = _github_secrets_env(target)
    verify_suffix = _verification_suffix_option(target)
    verification_jobs = (
        ""
        if target in BUILD_AND_PUSH_ONLY_TARGETS
        else f"""
  smoke-test:
    name: Smoke Test Staging
    needs: deploy-staging
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Score test quotes against staging endpoint
        env:
{secrets_env}
        run: uv run haute smoke{verify_suffix}

  impact-analysis:
    name: Impact Analysis
    needs: smoke-test
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Compare staging vs production predictions
        env:
{secrets_env}
        run: uv run haute impact{verify_suffix}
      - name: Upload impact report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: impact-report
          path: impact_report.md
      - name: Record deployed SHA
        run: >-
          echo "Staged commit: $GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
"""
    )

    return (
        _build_only_notice(target)
        + f"""\
name: Deploy

on:
  push:
    branches: [main]
    paths:
{_github_trigger_paths()}
  workflow_dispatch:

concurrency:
  group: deploy
  cancel-in-progress: false

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Type check
        run: uv run mypy .
      - name: Test
        run: uv run pytest -v
      - name: Pipeline lint
        run: uv run haute lint
      - name: Dry-run deploy
        run: uv run haute deploy --dry-run

  deploy-staging:
    name: Deploy → Staging
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: {_deploy_timeout_minutes(target)}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
{_github_login_step(target, secrets_env)}      - name: Deploy to staging
        env:
{secrets_env}
        run: uv run haute deploy --endpoint-suffix "-staging"
"""
        + verification_jobs
    )


def github_deploy_prod_yml(
    target: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
    production_environment: str = "",
) -> str:
    """:func:`_github_deploy_prod_yml` on *python_version*, under *production_environment*."""
    text = _github_deploy_prod_yml(target, _production_environment(production_environment))
    return _pin_ci_python(text, python_version)


def _github_deploy_prod_yml(target: str, environment: str) -> str:
    """Manual production deploy workflow for GitHub Actions.

    Triggered via the GitHub Actions UI (workflow_dispatch) after
    reviewing the impact report from the deploy workflow. Without an
    *environment* it works on GitHub Free, which offers a private repository
    no environments; with one, the job runs under it, so the reviewers
    configured on it gate the deploy.

    Accepts a ``sha`` input so the deployer can pin the exact commit
    that was staged and impact-analysed.  The workflow verifies that the
    checked-out HEAD matches, preventing accidental deployment of
    untested code.
    """
    secrets_env = _github_secrets_env(target)
    release_tag = _release_tag_script(target, " " * 10)
    # A Databricks release tag reads the registered version from MLflow.
    tag_env = "" if target in _CONTAINER_BASED_TARGETS else f"        env:\n{secrets_env}\n"
    environment_line = f'    environment: "{environment}"\n' if environment else ""

    return (
        _build_only_notice(target)
        + f"""\
name: Deploy → Production

on:
  workflow_dispatch:
    inputs:
      sha:
        description: >
          Git SHA that was staged & impact-analysed.
          Leave blank to deploy current HEAD of main (less safe).
        required: false
        type: string

concurrency:
  group: deploy
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  deploy-production:
    name: Deploy → Production
    runs-on: ubuntu-latest
{environment_line}    timeout-minutes: {_deploy_timeout_minutes(target)}
    steps:
      - uses: actions/checkout@v4
      - name: Verify commit matches staged SHA
        if: inputs.sha != ''
        run: |
          if [ "$GITHUB_SHA" != "${{{{ inputs.sha }}}}" ]; then
            echo "::error::HEAD ($GITHUB_SHA) does not match" \\
              "staged SHA (${{{{ inputs.sha }}}}). Merge may have occurred since staging."
            exit 1
          fi
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
{_github_login_step(target, secrets_env)}      - name: Deploy to production
        env:
{secrets_env}
        run: uv run haute deploy
      - name: Tag release
{tag_env}        run: |
          set -euo pipefail
{release_tag}
"""
    )


# ── GitLab CI ────────────────────────────────────────────────────────

# The folder, under the project, that the GitLab pipeline keeps between runs and where
# it tells uv to put its package cache. GitLab caches only paths inside the project.
_GITLAB_UV_CACHE = ".cache/uv"

# What every GitLab job runs before its script: uv, then the locked environment.
_GITLAB_UV_SYNC = 'pip install "uv>=0.5,<1" && uv sync --frozen'

# The Docker a GitLab container deploy runs: the Docker-in-Docker service's version, and
# the static Docker CLI's, which the job installs since its Python image has none.
_GITLAB_DOCKER_VERSION = "29.9.2"


def _gitlab_docker_job(target: str) -> str:
    """The hidden job a container-based target's GitLab deploy jobs extend.

    The job image has no Docker, so a Docker-in-Docker service, reached over TLS, builds
    and pushes the image. The runner must be privileged and share ``/certs/client`` with
    the service, as GitLab.com's instance runners do. A job's ``before_script`` replaces
    the default one, so this one installs the Docker CLI, then runs the default's. The
    static CLI has no buildx: ``docker build`` uses the classic builder, which the
    generated Dockerfile's plain instructions suffice for. Empty for Databricks.
    """
    if target not in _CONTAINER_BASED_TARGETS:
        return ""
    cli = (
        "https://download.docker.com/linux/static/stable/$(uname -m)/"
        f"docker-{_GITLAB_DOCKER_VERSION}.tgz"
    )
    return f"""\
# ── Docker for the container deploys ──────────────────────────
# The job image has no Docker: the deploys build and push through a Docker-in-Docker
# service, which needs a privileged runner sharing /certs/client (GitLab.com's are).
.docker:
  services:
    - docker:{_GITLAB_DOCKER_VERSION}-dind
  variables:
    DOCKER_HOST: tcp://docker:2376
    DOCKER_TLS_CERTDIR: "/certs"
    DOCKER_TLS_VERIFY: "1"
    DOCKER_CERT_PATH: "/certs/client"
  before_script:
    - curl -fsSL "{cli}" | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
    - {_GITLAB_UV_SYNC}

"""


def _gitlab_head(stages: Sequence[str], check: str) -> str:
    """The head every GitLab pipeline Haute generates opens with: its *stages*, uv's cache and
    the default job, then the validate job, run on a merge request and on the default branch,
    ending in *check*."""
    stage_lines = "".join(f"  - {stage}\n" for stage in stages)
    return f"""\
stages:
{stage_lines}
variables:
  # uv keeps its package cache in ~/.cache/uv unless told otherwise; this points it
  # at the folder the cache below keeps between runs.
  UV_CACHE_DIR: "$CI_PROJECT_DIR/{_GITLAB_UV_CACHE}"

default:
  image: python:3.11
  cache:
    key: uv-$CI_COMMIT_REF_SLUG
    paths:
      - {_GITLAB_UV_CACHE}
  before_script:
    - {_GITLAB_UV_SYNC}

# ── Validate ──────────────────────────────────────────────────
lint:
  stage: validate
  timeout: 10 minutes
  script:
    - uv run ruff check .
    - uv run mypy .
    - uv run pytest -v
    - uv run haute lint
    - {check}
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""


def gitlab_ci_yml(
    target: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
    staging_suffix: str = DEFAULT_STAGING_ENDPOINT_SUFFIX,
    production_environment: str = "",
) -> str:
    """:func:`_gitlab_ci_yml` on *python_version* and *staging_suffix*, production under
    *production_environment*."""
    text = _gitlab_ci_yml(target, _production_environment(production_environment))
    return _pin_staging_suffix(_pin_ci_python(text, python_version), staging_suffix)


def _gitlab_ci_yml(target: str, environment: str) -> str:
    """Combined CI + deploy pipeline for GitLab CI/CD.

    Uses GitLab stages to enforce ordering.  The ``deploy-production``
    job uses ``when: manual``, so someone who may run it must click to
    deploy; with an *environment* the job runs under it, so a protected
    environment's approvers gate it.

    Credentials are only available in deploy/smoke/impact/production
    jobs (protected-branch jobs), not in the MR validation job.
    """
    secrets_env = _gitlab_secrets_env(target)
    release_tag = _release_tag_script(target, " " * 6)
    environment_line = f'  environment: "{environment}"\n' if environment else ""
    verify = target not in BUILD_AND_PUSH_ONLY_TARGETS
    verify_suffix = _verification_suffix_option(target)
    verification_stages = ["smoke-test", "impact-analysis"] if verify else []
    stages = ["validate", "deploy-staging", *verification_stages, "deploy-production"]
    logs_in = target in _CONTAINER_BASED_TARGETS
    extends_docker = "  extends: .docker\n" if logs_in else ""
    login_entry = f"    - |\n{_registry_login_script(target, ' ' * 6)}\n" if logs_in else ""
    verification_jobs = (
        f"""\
# ── Smoke test ────────────────────────────────────────────────
smoke-test:
  stage: smoke-test
  timeout: 10 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute smoke{verify_suffix}
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

# ── Impact analysis ──────────────────────────────────────────
impact-analysis:
  stage: impact-analysis
  timeout: 10 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute impact{verify_suffix}
  artifacts:
    paths:
      - impact_report.md
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

"""
        if verify
        else ""
    )

    return (
        _build_only_notice(target)
        + _gitlab_head(stages, _DRY_RUN_CHECK[1])
        + f"""\
{_gitlab_docker_job(target)}# ── Staging ───────────────────────────────────────────────────
deploy-staging:
{extends_docker}  stage: deploy-staging
  timeout: {_deploy_timeout_minutes(target)} minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
{login_entry}    - uv run haute deploy --endpoint-suffix "-staging"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

{verification_jobs}# ── Production (manual approval) ─────────────────────────────
deploy-production:
{extends_docker}  stage: deploy-production
{environment_line}  timeout: {_deploy_timeout_minutes(target)} minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
{login_entry}    - uv run haute deploy
    - |
{release_tag}
  when: manual
  allow_failure: false
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""
    )


# ── Azure DevOps ─────────────────────────────────────────────────────


# The step that ends Azure DevOps' Validate stage: its script and display name.
_AZURE_DRY_RUN_CHECK = ("uv run haute deploy --dry-run", "Dry-run deploy")
_AZURE_APP_BUILD_CHECK = ("uv run haute app build", "Build the app's release")


def _azure_devops_head(check: tuple[str, str]) -> str:
    """The head every Azure DevOps pipeline Haute generates opens with: its triggers and the
    Validate stage, run on a pull request and on a push to main, its pipeline validation
    ending in *check*."""
    check_script, check_name = check
    return f"""\
trigger:
  branches:
    include: [main]
  paths:
    include:
{_azure_devops_trigger_paths()}

pr:
  branches:
    include: [main]

variables:
  CI: "true"

stages:
  # ── Validate (runs on PR and push to main) ───────────────────
  - stage: Validate
    jobs:
      - job: lint
        displayName: Lint & Format
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run ruff check .
            displayName: Ruff check
          - script: uv run ruff format --check .
            displayName: Ruff format check

      - job: typecheck
        displayName: Type Check
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run mypy .
            displayName: Mypy

      - job: test
        displayName: Test
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run pytest -v
            displayName: Pytest

      - job: pipeline_validate
        displayName: Pipeline Validation
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute lint
            displayName: Lint pipeline
          - script: {check_script}
            displayName: {check_name}
"""


def azure_devops_yml(
    target: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
    staging_suffix: str = DEFAULT_STAGING_ENDPOINT_SUFFIX,
    production_environment: str = "",
) -> str:
    """:func:`_azure_devops_yml` on *python_version* and *staging_suffix*, production under
    *production_environment*, ``production`` when it is empty."""
    environment = _production_environment(production_environment) or _AZURE_PRODUCTION_ENVIRONMENT
    text = _azure_devops_yml(target, environment=environment)
    return _pin_staging_suffix(_pin_ci_python(text, python_version), staging_suffix)


def _azure_devops_yml(target: str, *, environment: str) -> str:
    """Combined CI + deploy pipeline for Azure DevOps.

    Uses stages to enforce ordering.  The ``DeployProduction`` stage
    runs under *environment*, whose approval checks, configured in the
    portal, make a reviewer approve before production deployment proceeds.

    Credentials are read from an Azure DevOps variable group named
    ``haute-credentials`` - the env var names are identical to other
    CI providers.
    """
    secrets_env = _azure_devops_secrets_env(target)
    # The DeployProduction deployment strategy nests ``env:`` at 18 spaces, so
    # its secret keys must sit at 20 — deeper than the 14-space job-level block.
    secrets_env_production = _azure_devops_secrets_env(target, indent=" " * 20)
    release_tag = _release_tag_script(target, " " * 20)
    # A Databricks release tag reads the registered version from MLflow.
    tag_env = (
        ""
        if target in _CONTAINER_BASED_TARGETS
        else f"                  env:\n{secrets_env_production}\n"
    )
    verify = target not in BUILD_AND_PUSH_ONLY_TARGETS
    verify_suffix = _verification_suffix_option(target)
    production_depends_on = "ImpactAnalysis" if verify else "DeployStaging"
    # A container-based target logs in to its registry before each deploy step.
    if target in _CONTAINER_BASED_TARGETS:
        login_staging = f"""\
          - script: |
{_registry_login_script(target, " " * 14)}
            displayName: Log in to the registry
            env:
{secrets_env}
"""
        login_production = f"""\
                - script: |
{_registry_login_script(target, " " * 20)}
                  displayName: Log in to the registry
                  env:
{secrets_env_production}
"""
    else:
        login_staging = login_production = ""
    verification_stages = (
        f"""\
  # ── Smoke test staging ───────────────────────────────────────
  - stage: SmokeTest
    displayName: Smoke Test Staging
    dependsOn: DeployStaging
    variables:
      - group: haute-credentials
    jobs:
      - job: smoke_test
        displayName: Score test quotes against staging
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute smoke{verify_suffix}
            displayName: Smoke test
            env:
{secrets_env}

  # ── Impact analysis ──────────────────────────────────────────
  - stage: ImpactAnalysis
    displayName: Impact Analysis
    dependsOn: SmokeTest
    variables:
      - group: haute-credentials
    jobs:
      - job: impact
        displayName: Compare staging vs production
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute impact{verify_suffix}
            displayName: Impact analysis
            env:
{secrets_env}
          - publish: impact_report.md
            artifact: impact-report
            condition: succeededOrFailed()

"""
        if verify
        else ""
    )

    return (
        _build_only_notice(target)
        + _azure_devops_head(_AZURE_DRY_RUN_CHECK)
        + f"""\
  # ── Deploy to staging (only on push to main) ─────────────────
  - stage: DeployStaging
    displayName: Deploy → Staging
    dependsOn: Validate
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    variables:
      - group: haute-credentials
    jobs:
      - job: deploy_staging
        displayName: Deploy to staging
        timeoutInMinutes: {_deploy_timeout_minutes(target)}
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
{login_staging}          - script: uv run haute deploy --endpoint-suffix "-staging"
            displayName: Deploy staging
            env:
{secrets_env}

{verification_stages}  # ── Deploy to production (manual approval) ───────────────────
  - stage: DeployProduction
    displayName: Deploy → Production
    dependsOn: {production_depends_on}
    variables:
      - group: haute-credentials
    jobs:
      - deployment: deploy_production
        displayName: Deploy to production
        timeoutInMinutes: {_deploy_timeout_minutes(target)}
        pool:
          vmImage: ubuntu-latest
        environment: "{environment}"
        strategy:
          runOnce:
            deploy:
              steps:
                # The release tag is pushed with the pipeline's token.
                - checkout: self
                  persistCredentials: true
                - task: UsePythonVersion@0
                  inputs:
                    versionSpec: "3.11"
                - script: pip install "uv>=0.5,<1" && uv sync --frozen
                  displayName: Install dependencies
{login_production}                - script: uv run haute deploy
                  displayName: Deploy production
                  env:
{secrets_env_production}
                - script: |
                    set -euo pipefail
{release_tag}
                  displayName: Tag release
{tag_env}"""
    )


# ── Every CI file of a provider ───────────────────────────────────────


def ci_files(
    target: str,
    ci: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
    staging_suffix: str = DEFAULT_STAGING_ENDPOINT_SUFFIX,
    production_environment: str = "",
) -> dict[str, str]:
    """The CI files ``haute init`` and ``haute ci update`` write for *ci*: path -> content.

    Paths are relative to the project root, in the order they are reported; ``"none"``
    has no files. Every job runs *python_version*, the project's Python; the staging
    deploy steps pass *staging_suffix*, its ``[ci.staging] endpoint_suffix``, as a
    Databricks project's smoke and impact steps do; and the production job runs under
    *production_environment*, its ``[ci.production] environment``, when it names one.
    """
    if ci == "github":
        return {
            ".github/workflows/ci.yml": github_ci_yml(python_version=python_version),
            ".github/workflows/deploy-staging.yml": github_deploy_yml(
                target,
                python_version=python_version,
                staging_suffix=staging_suffix,
            ),
            ".github/workflows/deploy-production.yml": github_deploy_prod_yml(
                target,
                python_version=python_version,
                production_environment=production_environment,
            ),
        }
    if ci == "gitlab":
        return {
            ".gitlab-ci.yml": gitlab_ci_yml(
                target,
                python_version=python_version,
                staging_suffix=staging_suffix,
                production_environment=production_environment,
            )
        }
    if ci == "azure-devops":
        return {
            "azure-pipelines.yml": azure_devops_yml(
                target,
                python_version=python_version,
                staging_suffix=staging_suffix,
                production_environment=production_environment,
            )
        }
    if ci == "none":
        return {}
    raise ValueError(f"Unknown CI provider: {ci}")


# ── A workbench project's CI/CD ───────────────────────────────────────


def _app_publish_notice(host: str) -> str:
    """The comment that heads a workbench project's publish workflow: it publishes the app's
    image and rolls no hosted app to it yet."""
    return (
        "# Publish only: on a push to main this builds the underwriter app's image once,\n"
        "# pushes it to the registry [deploy.container] names (required) and prints the\n"
        "# image by digest. Nothing rolls a hosted app to it yet: point the app on\n"
        f"# {_get_target(host)['label']} at that digest yourself.\n"
    )


def github_release_yml(host: str, *, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_github_release_yml` on *python_version*."""
    return _pin_ci_python(_github_release_yml(host), python_version)


def _github_release_yml(host: str) -> str:
    """A workbench project's merge-to-main workflow for GitHub Actions: validate, then
    publish the underwriter app's image once, logged in to the registry with *host*'s
    credentials."""
    secrets_env = _github_secrets_env(host)
    check_name, check_run = _APP_BUILD_CHECK
    return (
        _app_publish_notice(host)
        + f"""\
name: Release

on:
  push:
    branches: [main]
    paths:
{_github_trigger_paths()}
  workflow_dispatch:

concurrency:
  group: release
  cancel-in-progress: false

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Type check
        run: uv run mypy .
      - name: Test
        run: uv run pytest -v
      - name: Pipeline lint
        run: uv run haute lint
      - name: {check_name}
        run: {check_run}

  publish:
    name: Publish the app's image
    needs: validate
    # A manual run may pick any branch: only main's commits are published.
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    timeout-minutes: {_PUBLISH_TIMEOUT_MINUTES}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
{_github_login_step(host, secrets_env)}      - name: Build and push the app's image
        run: {_APP_PUBLISH}
"""
    )


def gitlab_app_ci_yml(host: str, *, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_gitlab_app_ci_yml` on *python_version*."""
    return _pin_ci_python(_gitlab_app_ci_yml(host), python_version)


def _gitlab_app_ci_yml(host: str) -> str:
    """A workbench project's pipeline for GitLab CI/CD: validate on a merge request and on the
    default branch, then, on the default branch, publish the underwriter app's image once,
    logged in to the registry with *host*'s credentials."""
    return (
        _app_publish_notice(host)
        + _gitlab_head(["validate", "publish"], _APP_BUILD_CHECK[1])
        + f"""\
{_gitlab_docker_job(host)}# ── Publish ───────────────────────────────────────────────────
publish:
  extends: .docker
  stage: publish
  timeout: {_PUBLISH_TIMEOUT_MINUTES} minutes
  resource_group: release
  variables:
{_gitlab_secrets_env(host)}
  script:
    - |
{_registry_login_script(host, " " * 6)}
    - {_APP_PUBLISH}
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""
    )


def azure_devops_app_yml(host: str, *, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_azure_devops_app_yml` on *python_version*."""
    return _pin_ci_python(_azure_devops_app_yml(host), python_version)


def _azure_devops_app_yml(host: str) -> str:
    """A workbench project's pipeline for Azure DevOps: validate on a pull request and on a push
    to main, then, on main, publish the underwriter app's image once, logged in to the
    registry with *host*'s credentials from the ``haute-credentials`` variable group."""
    secrets_env = _azure_devops_secrets_env(host)
    return (
        _app_publish_notice(host)
        + _azure_devops_head(_AZURE_APP_BUILD_CHECK)
        + f"""\
  # ── Publish the app's image (only on push to main) ───────────
  - stage: Publish
    displayName: Publish the app's image
    dependsOn: Validate
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    variables:
      - group: haute-credentials
    jobs:
      - job: publish
        displayName: Build and push the app's image
        timeoutInMinutes: {_PUBLISH_TIMEOUT_MINUTES}
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: |
{_registry_login_script(host, " " * 14)}
            displayName: Log in to the registry
            env:
{secrets_env}
          - script: {_APP_PUBLISH}
            displayName: Build and push the app's image
"""
    )


def workbench_ci_files(
    host: str,
    ci: str,
    *,
    python_version: str = DEFAULT_CI_PYTHON,
) -> dict[str, str]:
    """The CI files a workbench ``haute init`` and ``haute ci update`` write for *ci*: path ->
    content, in the order they are reported; ``"none"`` has none. They validate the project and
    the app's release on every change and, on the default branch, publish the underwriter app's
    image, logged in to the registry with *host*'s credentials; none deploys an endpoint. Every
    job runs *python_version*, the project's Python."""
    from haute._workbench_config import APP_HOSTS

    if host not in APP_HOSTS:
        raise ValueError(f"Unknown app host: {host}")
    if ci == "github":
        return {
            ".github/workflows/ci.yml": github_app_ci_yml(python_version=python_version),
            ".github/workflows/release.yml": github_release_yml(
                host, python_version=python_version
            ),
        }
    if ci == "gitlab":
        return {".gitlab-ci.yml": gitlab_app_ci_yml(host, python_version=python_version)}
    if ci == "azure-devops":
        return {"azure-pipelines.yml": azure_devops_app_yml(host, python_version=python_version)}
    if ci == "none":
        return {}
    raise ValueError(f"Unknown CI provider: {ci}")


# ── Pre-commit hook ───────────────────────────────────────────────────


def pre_commit_hook() -> str:
    """Generate a Git pre-commit hook that auto-formats with ruff."""
    return """\
#!/bin/sh
# Haute pre-commit hook - auto-format staged Python files with ruff.
# Installed by `haute init`. To reinstall: cp .githooks/pre-commit .git/hooks/

FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.py')
[ -z "$FILES" ] && exit 0

uv run ruff format $FILES
uv run ruff check --fix $FILES 2>/dev/null || true
git add $FILES
"""


# ── Starter files ─────────────────────────────────────────────────────


def starter_pipeline(name: str) -> str:
    """Generate a valid, discoverable pipeline with an empty canvas."""
    return f'''\
"""Pipeline: {name}

Blank pipeline scaffolded by ``haute init``. Add nodes in the Haute editor.
"""

import haute

pipeline = haute.Pipeline("{name}")
'''


def starter_utility_init() -> str:
    """Generate ``utility/__init__.py``."""
    return '"""Project-level utilities \u2014 reusable functions for pipeline nodes."""\n'


def starter_utility_features() -> str:
    """Generate ``utility/features.py`` with common Polars utilities."""
    return '''\
"""Feature engineering utilities for pipeline nodes.

Add project-specific utility functions, constants, and column mappings here.
These are imported into main.py so your pipeline nodes stay clean and readable.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl


# ── Date helpers ──────────────────────────────────────────────────────


def to_date(col_name: str, fmt: str = "%Y-%m-%d") -> pl.Expr:
    """Parse a string column to a date.

    Example::

        to_date("proposer.date_of_birth")
    """
    return pl.col(col_name).str.to_date(fmt)


def years_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Whole years between two date expressions (floor).

    Example::

        years_between(to_date("date_of_birth"), to_date("cover_start_date")).alias("age")
    """
    return ((later - earlier).dt.total_days() / 365.25).floor().cast(pl.Int64)


def months_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Calendar months between two date expressions.

    Uses year/month subtraction so Jan 1 to Jul 1 = 6, not 5.

    Example::

        months_between(to_date("start_date"), to_date("end_date")).alias("tenure_months")
    """
    return (later.dt.year() - earlier.dt.year()) * 12 + (later.dt.month() - earlier.dt.month())


def days_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Days between two date expressions.

    Example::

        days_between(to_date("order_date"), to_date("ship_date")).alias("fulfillment_days")
    """
    return (later - earlier).dt.total_days()


# ── String helpers ────────────────────────────────────────────────────


def postcode_area(col_name: str) -> pl.Expr:
    """Extract the outward code (first part) from a UK postcode.

    ``"SW1A 2AA"`` → ``"SW1A"``, ``"B1 1BB"`` → ``"B1"``

    Example::

        postcode_area("postcode").alias("postcode_area")
    """
    return pl.col(col_name).str.split(" ").list.first()


# ── Column cleaning ──────────────────────────────────────────────────


def clean_columns(df: pl.LazyFrame) -> pl.LazyFrame:
    """Replace every ``.`` with ``_`` in column names.

    Example::

        df = clean_columns(quotes)
    """
    rename = {c: c.replace(".", "_") for c in df.collect_schema().names() if "." in c}
    return df.rename(rename) if rename else df


# ── Column matching ──────────────────────────────────────────────────


def cols_matching(all_cols: list[str], pattern_fn: Callable[[str], bool]) -> list[str]:
    """Return columns from *all_cols* where pattern_fn(col) is True.

    Example::

        age_cols = cols_matching(df.collect_schema().names(),
                                 lambda c: c.endswith("_age"))
    """
    return [c for c in all_cols if pattern_fn(c)]
'''


def starter_test(name: str) -> str:
    """Generate a starter test that validates the pipeline file."""
    return f'''\
"""Starter tests for {name}.

Extend these tests with execution assertions as your pipeline grows.
"""

from __future__ import annotations

from pathlib import Path

from haute.parser import parse_pipeline_file


PIPELINE_FILE = Path(__file__).resolve().parent.parent / "rating" / "main.py"

def test_pipeline_parses() -> None:
    """The pipeline remains valid Haute source."""
    graph = parse_pipeline_file(PIPELINE_FILE)
    assert graph.pipeline_name == "{name}"
'''


def starter_test_quote() -> str:
    """Generate the starter ``tests/quotes/example.json``, a row in the test-quote shape."""
    return """\
[
  {
    "_description": "Example quote - replace the input and add the outputs you expect",
    "input": {
      "id": 1,
      "field_a": "value",
      "field_b": 42
    }
  }
]
"""


def starter_form(name: str, folder: Path) -> dict[str, str]:
    """The workbench's blank form folder for ``haute init --workbench`` at *folder*: each
    file's text by its path from the folder."""
    return {
        path: data.decode("utf-8") for path, data in folder_files(blank_form(name), folder).items()
    }
