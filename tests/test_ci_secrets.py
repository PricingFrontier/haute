"""The deploy's GitHub secrets on the editor's Push and its Set from .env.

A new project's first staging deploy failed because its repository had none of the secrets
the workflow reads, and setting them took a separate ``haute ci secrets``. The Push that
first publishes a project to GitHub now sets the ones the repository lacks, from ``.env``,
before the default branch lands; a later Push reports missing ones, which Set from .env
(``POST /api/git/ci-secrets``) sets. Neither overwrites a secret.

The remote is a local bare repository that ``remote_secrets`` is told is GitHub's
``acme/motor``; ``gh`` is stubbed at its subprocess boundary, and every other process runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from haute import _ci_secrets
from haute._ci_secrets import GH_TIMEOUT_SECONDS, CiSecretsError, github_repository, run_gh
from haute.cli import cli
from tests._git_helpers import git_run as _git
from tests._git_helpers import init_repo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_VALUES = {
    "DATABRICKS_MLFLOW_HOST": "https://adb-9.azuredatabricks.net",
    "DATABRICKS_MLFLOW_TOKEN": "dapi-mlflow-secret",
    "DATABRICKS_RATING_HOST": "https://adb-9.azuredatabricks.net",
    "DATABRICKS_RATING_TOKEN": "dapi-rating-secret",
}
_NAMES = list(_VALUES)
_WORKING = "pricing/test-user/dev"
_GH = "/usr/bin/gh"
_REPO_ARGS = ["--repo", "github.com/acme/motor"]
_REAL_RUN = subprocess.run
_REAL_WHICH = shutil.which


class _Gh:
    """``gh`` as ``subprocess.run`` runs it: one repository's secrets and every call.

    Each ``secret set`` is also written to *log*, the file the remote's pre-receive hook
    writes its sessions to, so the two can be ordered. Any other process runs for real.
    """

    def __init__(
        self,
        log: Path,
        existing: tuple[str, ...] = (),
        fail_set: str | None = None,
        fail_list: str | None = None,
    ) -> None:
        self.secrets: dict[str, str] = dict.fromkeys(existing, "set before")
        self.calls: list[list[str]] = []
        self.log = log
        self.fail_set = fail_set
        self.fail_list = fail_list

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[0] != _GH:
            return _REAL_RUN(args, **kwargs)
        assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        assert kwargs["timeout"] == GH_TIMEOUT_SECONDS
        command, stdin = list(args[1:]), kwargs["input"]
        self.calls.append(command)
        if command[:2] == ["secret", "list"]:
            if self.fail_list is not None:
                return subprocess.CompletedProcess(args, 1, "", self.fail_list)
            listed = json.dumps([{"name": name} for name in self.secrets])
            return subprocess.CompletedProcess(args, 0, listed, "")
        if command[:2] == ["secret", "set"]:
            name = command[2]
            with self.log.open("a", encoding="utf-8") as log:
                log.write(f"gh secret set {name}\n")
            if name == self.fail_set:
                # A message that echoes the value must not carry it any further.
                return subprocess.CompletedProcess(args, 1, "", f"HTTP 403: refused {stdin}")
            self.secrets[name] = stdin
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected gh call: {command}")

    @property
    def sets(self) -> list[str]:
        return [command[2] for command in self.calls if command[:2] == ["secret", "set"]]


@pytest.fixture
def project(runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient) -> Path:
    """A Databricks project on GitHub as ``haute init`` makes it, with a working branch and
    an empty bare remote ``origin`` whose pre-receive hook logs each receive session."""
    project_dir = tmp_path / "motor"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    for name in _VALUES:
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(cli, ["init", "--target", "databricks", "--ci", "github"])
    assert result.exit_code == 0, result.output
    init_repo(project_dir)
    bare = tmp_path / "origin.git"
    _git(project_dir, "init", "--bare", str(bare))
    _git(project_dir, "remote", "add", "origin", str(bare))
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        'echo "--- session" >> receive-sessions.log\n'
        'while read old new ref; do echo "$ref" >> receive-sessions.log; done\n',
        newline="\n",
    )
    hook.chmod(0o755)
    adopted = client.post("/api/git/working-branch", json={"branch": _WORKING, "create": True})
    assert adopted.status_code == 200, adopted.text
    return project_dir


@pytest.fixture
def log(project: Path) -> Path:
    return project.parent / "origin.git" / "receive-sessions.log"


def _point_origin_at(monkeypatch: pytest.MonkeyPatch, fetch: str, push: str | None = None) -> None:
    """``origin`` reads as fetching from *fetch* and pushing to *push* (else *fetch*);
    git itself still pushes to the bare remote."""
    from haute import _git

    real_url, real_pushes = _git.remote_url, _git.remote_push_urls

    def remote_url(name: str, cwd: Path | None = None) -> str | None:
        return fetch if name == "origin" else real_url(name, cwd)

    def remote_push_urls(name: str, cwd: Path | None = None) -> list[str]:
        return [push or fetch] if name == "origin" else real_pushes(name, cwd)

    monkeypatch.setattr(_git, "remote_url", remote_url)
    monkeypatch.setattr(_git, "remote_push_urls", remote_push_urls)


@pytest.fixture
def on_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """``origin`` reads as GitHub's ``acme/motor``; git still pushes to the bare remote."""
    _point_origin_at(monkeypatch, "https://github.com/acme/motor.git")


def _env_file(project: Path, values: dict[str, str]) -> None:
    (project / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8"
    )


def _post(
    client: TestClient,
    path: str,
    gh: _Gh,
    *,
    installed: bool = True,
    refuse: Exception | None = None,
    during_check: Callable[[], None] | None = None,
) -> Any:
    def user(host: str, token: str) -> str:
        if refuse is not None:
            raise refuse
        if during_check is not None:
            during_check()
        return "ci-bot@example.com"

    def which(name: str, *args: Any, **kwargs: Any) -> str | None:
        if name == "gh":
            return _GH if installed else None
        return _REAL_WHICH(name, *args, **kwargs)

    with (
        patch("haute._ci_secrets.shutil.which", side_effect=which),
        patch("haute._ci_secrets.subprocess.run", side_effect=gh),
        patch("haute.deploy._databricks_serving.workspace_user", side_effect=user),
    ):
        return client.post(path, json={"remote": "origin"})


def _sessions(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


def _published(project: Path) -> bool:
    return f"refs/heads/{_WORKING}" in _git(project, "ls-remote", "origin")


# ---------------------------------------------------------------------------
# The Push that first publishes a project
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("on_github")
def test_first_publication_sets_the_missing_secrets_before_the_default_lands(
    client: TestClient, project: Path, log: Path
) -> None:
    _env_file(project, _VALUES)
    gh = _Gh(log, existing=("DATABRICKS_MLFLOW_HOST",))

    res = _post(client, "/api/git/push", gh)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["bootstrapped_default"] is True
    assert body["ci_secrets"] == {
        "repository": "acme/motor",
        "state": "ready",
        "missing": [],
        "newly_set": _NAMES[1:],
        "problem": None,
    }
    # The secret the repository had is never overwritten; the rest come from .env.
    assert gh.secrets == {**_VALUES, "DATABRICKS_MLFLOW_HOST": "set before"}
    assert all(command[-2:] == _REPO_ARGS for command in gh.calls)
    # Every secret is stored before the default's receive session, the first of the push.
    lines = _sessions(log)
    first = lines.index("--- session")
    assert lines[:first] == [f"gh secret set {name}" for name in _NAMES[1:]]
    assert lines[first + 1] == "refs/heads/main"
    for value in _VALUES.values():
        assert value not in res.text


@pytest.mark.usefixtures("on_github")
@pytest.mark.parametrize(
    ("case", "problem"),
    [
        ("unset", "Not set in .env or the environment: DATABRICKS_RATING_TOKEN"),
        ("placeholder", "Still .env.example's placeholder: " + ", ".join(_NAMES)),
        ("refused", "Databricks refused DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN"),
    ],
)
def test_first_publication_that_cannot_read_env_sets_nothing_and_still_pushes(
    client: TestClient, project: Path, log: Path, case: str, problem: str
) -> None:
    if case == "unset":
        _env_file(project, {k: v for k, v in _VALUES.items() if k != "DATABRICKS_RATING_TOKEN"})
    elif case == "placeholder":
        shutil.copyfile(project / ".env.example", project / ".env")
    else:
        _env_file(project, _VALUES)
    gh = _Gh(log)
    refuse = PermissionError("Invalid token dapi-mlflow-secret") if case == "refused" else None

    res = _post(client, "/api/git/push", gh, refuse=refuse)

    assert res.status_code == 200, res.text
    report = res.json()["ci_secrets"]
    assert report["state"] == "missing"
    assert report["missing"] == _NAMES
    assert report["newly_set"] == []
    assert problem in report["problem"]
    assert "dapi-mlflow-secret" not in res.text
    assert gh.sets == []
    assert _published(project)


@pytest.mark.usefixtures("on_github")
def test_a_secret_gh_cannot_set_leaves_the_earlier_ones_set(
    client: TestClient, project: Path, log: Path
) -> None:
    _env_file(project, _VALUES)
    gh = _Gh(log, fail_set="DATABRICKS_RATING_HOST")

    res = _post(client, "/api/git/push", gh)

    assert res.status_code == 200, res.text
    report = res.json()["ci_secrets"]
    assert report["state"] == "missing"
    assert report["newly_set"] == ["DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"]
    assert report["missing"] == ["DATABRICKS_RATING_HOST", "DATABRICKS_RATING_TOKEN"]
    assert "HTTP 403: refused" in report["problem"]
    assert _VALUES["DATABRICKS_RATING_HOST"] not in report["problem"]
    assert _published(project)


@pytest.mark.usefixtures("on_github")
@pytest.mark.parametrize(
    ("installed", "fail_list", "problem"),
    [
        (False, None, "https://cli.github.com"),
        (True, "HTTP 403: Must have admin rights to Repository.", "Must have admin rights"),
    ],
)
def test_first_publication_that_cannot_read_the_secrets_reports_unknown(
    client: TestClient,
    project: Path,
    log: Path,
    installed: bool,
    fail_list: str | None,
    problem: str,
) -> None:
    _env_file(project, _VALUES)
    gh = _Gh(log, fail_list=fail_list)

    res = _post(client, "/api/git/push", gh, installed=installed)

    assert res.status_code == 200, res.text
    report = res.json()["ci_secrets"]
    assert report["state"] == "unknown"
    assert report["missing"] == []
    assert problem in report["problem"]
    assert gh.sets == []
    assert _published(project)


# ---------------------------------------------------------------------------
# Later Pushes and Set from .env
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("on_github")
def test_a_later_push_reports_missing_secrets_and_sets_none(
    client: TestClient, project: Path, log: Path
) -> None:
    _env_file(project, _VALUES)
    gh = _Gh(log, existing=tuple(_NAMES))
    first = _post(client, "/api/git/push", gh)
    assert first.json()["ci_secrets"]["state"] == "ready"
    del gh.secrets["DATABRICKS_RATING_TOKEN"]
    gh.calls.clear()

    res = _post(client, "/api/git/push", gh)

    assert res.status_code == 200, res.text
    assert res.json()["bootstrapped_default"] is False
    assert res.json()["ci_secrets"] == {
        "repository": "acme/motor",
        "state": "missing",
        "missing": ["DATABRICKS_RATING_TOKEN"],
        "newly_set": [],
        "problem": None,
    }
    assert gh.calls == [["secret", "list", "--json", "name", *_REPO_ARGS]]


@pytest.mark.usefixtures("on_github")
def test_set_from_env_sets_only_the_missing_secrets(
    client: TestClient, project: Path, log: Path
) -> None:
    _env_file(project, _VALUES)
    gh = _Gh(log, existing=("DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"))

    res = _post(client, "/api/git/ci-secrets", gh)

    assert res.status_code == 200, res.text
    assert res.json() == {
        "repository": "acme/motor",
        "state": "ready",
        "missing": [],
        "newly_set": ["DATABRICKS_RATING_HOST", "DATABRICKS_RATING_TOKEN"],
        "problem": None,
    }
    assert gh.secrets == {
        **_VALUES,
        "DATABRICKS_MLFLOW_HOST": "set before",
        "DATABRICKS_MLFLOW_TOKEN": "set before",
    }


@pytest.mark.usefixtures("on_github")
@pytest.mark.parametrize("path", ["/api/git/push", "/api/git/ci-secrets"])
def test_a_secret_set_during_the_databricks_check_is_not_replaced(
    client: TestClient, project: Path, log: Path, path: str
) -> None:
    """The pairs' check takes seconds; a secret someone sets meanwhile stays theirs."""
    _env_file(project, _VALUES)
    gh = _Gh(log)

    def someone_sets_it() -> None:
        gh.secrets.setdefault("DATABRICKS_RATING_TOKEN", "set meanwhile")

    res = _post(client, path, gh, during_check=someone_sets_it)

    assert res.status_code == 200, res.text
    report = res.json() if path.endswith("ci-secrets") else res.json()["ci_secrets"]
    assert report["state"] == "ready"
    assert report["newly_set"] == _NAMES[:3]
    assert gh.secrets["DATABRICKS_RATING_TOKEN"] == "set meanwhile"
    assert "DATABRICKS_RATING_TOKEN" not in gh.sets


_UNUSABLE_SETTINGS = (
    "malformed haute.toml",
    "undecodable .env",
    "unknown target",
    "unpinned container image",
)


def _break_settings(project: Path, case: str) -> None:
    toml = project / "haute.toml"
    text = toml.read_text(encoding="utf-8")
    if case == "malformed haute.toml":
        toml.write_text("[deploy\ntarget = ", encoding="utf-8")
    elif case == "undecodable .env":
        (project / ".env").write_bytes(b"DATABRICKS_RATING_TOKEN=\xff\xfe\n")
    elif case == "unknown target":
        toml.write_text(text.replace('target = "databricks"', 'target = "databrikcs"'), "utf-8")
    else:
        container = '\n[deploy.container]\nbase_image = "python:3.11-slim"\n'
        toml.write_text(
            text.replace('target = "databricks"', 'target = "container"') + container, "utf-8"
        )


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        ("placeholder", "Still .env.example's placeholder"),
        ("no gh", "https://cli.github.com"),
        ("off GitHub", "is not a repository on github.com"),
        ("pushes elsewhere", "pushes somewhere other than acme/motor"),
        *(
            (case, "Haute could not read this project's haute.toml and .env")
            for case in _UNUSABLE_SETTINGS
        ),
    ],
)
def test_set_from_env_that_cannot_set_them_is_a_400(
    client: TestClient,
    project: Path,
    log: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    detail: str,
) -> None:
    if case == "pushes elsewhere":
        _point_origin_at(
            monkeypatch, "git@github.com:acme/motor.git", "https://github.com/acme/other.git"
        )
    elif case != "off GitHub":
        _point_origin_at(monkeypatch, "git@github.com:acme/motor.git")
    if case == "placeholder":
        shutil.copyfile(project / ".env.example", project / ".env")
    else:
        _env_file(project, _VALUES)
    if case in _UNUSABLE_SETTINGS:
        _break_settings(project, case)
    gh = _Gh(log)

    res = _post(client, "/api/git/ci-secrets", gh, installed=case != "no gh")

    assert res.status_code == 400
    assert detail in res.json()["detail"]
    assert gh.sets == []


@pytest.mark.parametrize("case", ["off GitHub", "pushes elsewhere", "GitLab project"])
def test_a_push_the_secrets_do_not_apply_to_runs_no_gh(
    client: TestClient,
    project: Path,
    log: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    if case == "pushes elsewhere":
        _point_origin_at(
            monkeypatch, "https://github.com/acme/motor", "git@github.com:acme/other.git"
        )
    if case == "GitLab project":
        _point_origin_at(monkeypatch, "https://github.com/acme/motor")
        toml = project / "haute.toml"
        text = toml.read_text(encoding="utf-8")
        toml.write_text(text.replace('provider = "github"', 'provider = "gitlab"'), "utf-8")
    _env_file(project, _VALUES)
    gh = _Gh(log)

    res = _post(client, "/api/git/push", gh)

    assert res.status_code == 200, res.text
    assert res.json()["ci_secrets"] is None
    assert gh.calls == []
    assert _published(project)


def test_a_remote_naming_its_repository_in_another_case_still_applies(
    client: TestClient, project: Path, log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub's owner and repository names are case-insensitive."""
    _point_origin_at(
        monkeypatch, "https://github.com/Acme/Motor.git", "git@github.com:acme/motor.git"
    )
    _env_file(project, _VALUES)
    gh = _Gh(log)

    res = _post(client, "/api/git/ci-secrets", gh)

    assert res.status_code == 200, res.text
    assert res.json()["repository"] == "Acme/Motor"
    assert res.json()["newly_set"] == _NAMES


@pytest.mark.usefixtures("on_github")
@pytest.mark.parametrize("case", _UNUSABLE_SETTINGS)
def test_a_push_whose_settings_cannot_be_read_still_lands(
    client: TestClient, project: Path, log: Path, case: str
) -> None:
    _env_file(project, _VALUES)
    _break_settings(project, case)
    gh = _Gh(log)

    res = _post(client, "/api/git/push", gh)

    assert res.status_code == 200, res.text
    report = res.json()["ci_secrets"]
    assert report["repository"] == "acme/motor"
    assert report["state"] == "unknown"
    assert "Haute could not read this project's haute.toml and .env" in report["problem"]
    assert gh.calls == []
    assert _published(project)


# ---------------------------------------------------------------------------
# The repository a remote names, and the gh chokepoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/motor.git",
        "https://github.com/acme/motor",
        "https://x-access-token:tok@github.com/acme/motor.git/",
        "http://github.com/acme/motor",
        "https://GitHub.com/acme/motor.git",
        "ssh://git@github.com/acme/motor.git",
        "ssh://git@github.com:22/acme/motor.git",
        "git@github.com:acme/motor.git",
        "github.com:acme/motor",
    ],
)
def test_a_github_remote_names_its_repository(url: str) -> None:
    assert github_repository(url) == "acme/motor"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/acme/motor.git",
        "https://github.com.example.org/acme/motor.git",
        "https://github.com/acme",
        "https://github.com/acme/motor/tree/main",
        "https://github.com/acme/.git",
        "https://github.com/ac%20me/motor",
        "git@github.com:acme/../motor.git",
        "file:///srv/git/motor.git",
        "/srv/git/motor.git",
        "C:\\repos\\motor.git",
        "https://[::1/acme/motor",
    ],
)
def test_anything_else_names_no_github_repository(url: str) -> None:
    assert github_repository(url) is None


def test_a_gh_call_past_its_timeout_fails_as_ci_secrets_error(tmp_path: Path) -> None:
    timeout = subprocess.TimeoutExpired([_GH], GH_TIMEOUT_SECONDS)
    with (
        patch("haute._ci_secrets.subprocess.run", side_effect=timeout),
        pytest.raises(CiSecretsError, match=f"within {GH_TIMEOUT_SECONDS} seconds"),
    ):
        run_gh(_GH, tmp_path, "secret", "list")


def test_remote_secrets_need_haute_toml(tmp_path: Path) -> None:
    init_repo(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://github.com/acme/motor.git")

    assert _ci_secrets.remote_secrets("origin", tmp_path) is None
