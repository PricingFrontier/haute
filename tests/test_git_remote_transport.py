"""Remote Git commands run through the analyst's own transport, and never hang.

Each test drives a real ``git`` against an ``ssh://`` remote whose transport is a fake
ssh: a Python script that logs the arguments Git gives it and then serves the request
from a local bare repository, hangs with a child process of its own, or asks for a
password at the terminal. Runs on Windows, macOS and Linux (the CI platform smoke).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
from haute._git import (
    GitRemoteUnavailableError,
    fast_forward_pair,
    push_working_pair,
    remote_is_empty,
    set_working_branch,
)

WORKING = "pricing"
LEDGER = f"{WORKING}-save"
REFRESH = "Could not refresh 'origin'. Check your connection or credentials and try again."
# The deadline the tests give a remote read, and how much later the call may return.
_DEADLINE_SECONDS = 1.0
_CLEANUP_SLACK_SECONDS = 8.0

FAKE_SSH = '''\
"""A fake ssh: logs its arguments, then serves, hangs or asks at the terminal."""
import json, os, shlex, subprocess, sys, time
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["FAKE_SSH_LOG"])
with log.open("a", encoding="utf-8") as out:
    out.write(json.dumps(args) + "\\n")
if "-G" in args:
    sys.exit(1)  # Git probing for OpenSSH: this transport is something else.
mode = os.environ["FAKE_SSH_MODE"]
if mode == "orphan":
    # Gone at once, leaving a child that keeps git's stderr, the caller's pipe, open.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(os.environ["FAKE_SSH_PIDS"]).write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
    sys.exit(255)
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(os.environ["FAKE_SSH_PIDS"]).write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
    time.sleep(120)
    sys.exit(255)
if mode == "prompt":
    try:
        terminal = open("CONIN$" if os.name == "nt" else "/dev/tty", encoding="utf-8")
    except OSError:
        with log.open("a", encoding="utf-8") as out:
            out.write("no terminal\\n")
        sys.exit(255)
    with terminal:
        terminal.readline()
    sys.exit(255)
service, path = shlex.split(args[-1])
repository = Path(os.environ["FAKE_SSH_ROOT"]) / path.lstrip("/")
sys.exit(subprocess.call(["git", service.removeprefix("git-"), str(repository)]))
'''


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _shell_path(path: Path) -> str:
    """A path as Git's shell on every platform reads it: forward slashes, quoted."""
    return '"' + path.as_posix() + '"'


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A project on a working branch whose ``origin`` is an empty repository over ssh."""
    for name in ("GIT_SSH_COMMAND", "GIT_SSH", "GIT_SSH_VARIANT"):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q", "-b", "main")
    _git(project, "config", "user.name", "Test User")
    _git(project, "config", "user.email", "test@example.com")
    (project / "main.py").write_text("# pipeline\n", encoding="utf-8")
    _git(project, "add", "main.py")
    _git(project, "commit", "-q", "-m", "Initial project")
    _git(project, "checkout", "-q", "-b", WORKING)
    monkeypatch.chdir(project)
    set_working_branch(WORKING, project, cwd=project)

    served = tmp_path / "served"
    served.mkdir()
    _git(served, "init", "-q", "--bare", "remote.git")
    _git(project, "remote", "add", "origin", "ssh://fakehost/remote.git")

    fake = tmp_path / "fake_ssh.py"
    fake.write_text(FAKE_SSH, encoding="utf-8")
    log = tmp_path / "ssh.log"
    log.touch()
    monkeypatch.setenv("FAKE_SSH_ROOT", str(served))
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("FAKE_SSH_PIDS", str(tmp_path / "pids"))
    monkeypatch.setenv("FAKE_SSH_MODE", "serve")
    return {"project": project, "served": served / "remote.git", "fake": fake, "log": log}


def _command(remote: dict[str, Path], *options: str) -> str:
    return " ".join([_shell_path(Path(sys.executable)), _shell_path(remote["fake"]), *options])


def _calls(remote: dict[str, Path]) -> list[list[str]]:
    """The argument lists the fake ssh received, leaving out Git's OpenSSH probe."""
    lines = remote["log"].read_text(encoding="utf-8").splitlines()
    return [args for args in map(json.loads, lines) if "-G" not in args]


def _configure_through_environment(
    remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", _command(remote))


def _configure_through_git_config(remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _git(remote["project"], "config", "core.sshCommand", _command(remote))


def _configure_as_a_program(remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """GIT_SSH names a program, not a command line: a script that runs the fake."""
    launcher = remote["fake"].with_name("fake-ssh")
    launcher.write_text(
        f'#!/bin/sh\nexec {_command(remote)} "$@"\n', encoding="utf-8", newline="\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setenv("GIT_SSH", str(launcher))


@pytest.mark.parametrize(
    "configure",
    [_configure_through_environment, _configure_through_git_config, _configure_as_a_program],
    ids=["GIT_SSH_COMMAND", "core.sshCommand", "GIT_SSH"],
)
def test_a_transport_of_the_analysts_own_publishes_with_nothing_added(
    remote: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    configure: Callable[[dict[str, Path], pytest.MonkeyPatch], None],
) -> None:
    """An ssh remote that works from the shell works from Haute: Git runs the configured
    transport as configured, and Haute adds none of its own OpenSSH options to it."""
    configure(remote, monkeypatch)

    assert remote_is_empty("origin", cwd=remote["project"]) is True
    pushed = push_working_pair("origin", remote["project"], cwd=remote["project"])

    assert pushed.pushed_refs == ["main", WORKING, LEDGER]
    published = _git(remote["served"], "for-each-ref", "--format=%(refname)")
    assert set(published.splitlines()) == {
        "refs/heads/main",
        f"refs/heads/{WORKING}",
        f"refs/heads/{LEDGER}",
    }
    calls = _calls(remote)
    services = [args[-1].split()[0] for args in calls]
    assert "git-upload-pack" in services and "git-receive-pack" in services
    for args in calls:
        assert not any(arg.startswith("-o") for arg in args), args
        assert args[-2] == "fakehost"


def test_conflicting_openssh_options_reach_the_transport_unchanged(
    remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With OpenSSH the first value of an option wins, so appending Haute's would not
    override the analyst's anyway; their options reach the transport as they wrote them."""
    monkeypatch.setenv("GIT_SSH_COMMAND", _command(remote, "-oBatchMode=no", "-oPort=2222"))
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")

    assert remote_is_empty("origin", cwd=remote["project"]) is True

    (args,) = _calls(remote)
    assert args[:2] == ["-oBatchMode=no", "-oPort=2222"]
    assert "-oBatchMode=yes" not in args
    assert not any(arg.startswith("-oConnectTimeout") for arg in args)


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _push(project: Path) -> object:
    return push_working_pair("origin", project, cwd=project)


def _catch_up(project: Path) -> object:
    return fast_forward_pair("origin", project, cwd=project)


@pytest.mark.parametrize("action", [_push, _catch_up], ids=["push", "catch-up"])
def test_a_hanging_transport_is_ended_with_its_children_at_the_deadline(
    remote: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action: Callable[[Path], object],
) -> None:
    _configure_through_git_config(remote, monkeypatch)
    monkeypatch.setenv("FAKE_SSH_MODE", "hang")
    # Push inspects through the remote module; Catch up fetches through the core.
    monkeypatch.setattr(git_remote, "_FETCH_TIMEOUT_SECONDS", _DEADLINE_SECONDS)
    monkeypatch.setattr(git_core, "_FETCH_TIMEOUT_SECONDS", _DEADLINE_SECONDS)

    started = time.monotonic()
    with pytest.raises(GitRemoteUnavailableError) as refused:
        action(remote["project"])
    elapsed = time.monotonic() - started

    assert str(refused.value) == REFRESH
    assert elapsed < _DEADLINE_SECONDS + _CLEANUP_SLACK_SECONDS
    transport, child = map(int, (tmp_path / "pids").read_text(encoding="utf-8").split())
    gone_by = time.monotonic() + _CLEANUP_SLACK_SECONDS
    while (_alive(transport) or _alive(child)) and time.monotonic() < gone_by:
        time.sleep(0.05)
    assert not _alive(transport), "the transport outlived its deadline"
    assert not _alive(child), "the transport's child outlived its deadline"


def test_a_child_its_transport_left_behind_is_ended_at_the_deadline(
    remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The transport exits at once, its child still holding the output pipe: no walk of
    the process tree reaches that child, but it was started under git, so it goes too."""
    _configure_through_git_config(remote, monkeypatch)
    monkeypatch.setenv("FAKE_SSH_MODE", "orphan")
    monkeypatch.setattr(git_remote, "_FETCH_TIMEOUT_SECONDS", _DEADLINE_SECONDS)

    started = time.monotonic()
    with pytest.raises(GitRemoteUnavailableError, match=REFRESH):
        push_working_pair("origin", remote["project"], cwd=remote["project"])

    assert time.monotonic() - started < _DEADLINE_SECONDS + _CLEANUP_SLACK_SECONDS
    _, child = map(int, (tmp_path / "pids").read_text(encoding="utf-8").split())
    gone_by = time.monotonic() + _CLEANUP_SLACK_SECONDS
    while _alive(child) and time.monotonic() < gone_by:
        time.sleep(0.05)
    assert not _alive(child), "the transport's orphaned child outlived the deadline"


def test_a_transport_asking_for_a_password_cannot_wait_for_one(
    remote: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_through_git_config(remote, monkeypatch)
    monkeypatch.setenv("FAKE_SSH_MODE", "prompt")
    monkeypatch.setattr(git_remote, "_FETCH_TIMEOUT_SECONDS", _DEADLINE_SECONDS)

    started = time.monotonic()
    with pytest.raises(GitRemoteUnavailableError, match=REFRESH):
        push_working_pair("origin", remote["project"], cwd=remote["project"])

    assert time.monotonic() - started < _DEADLINE_SECONDS + _CLEANUP_SLACK_SECONDS
    if os.name != "nt":
        # No controlling terminal: the prompt fails at once instead of waiting out the deadline.
        assert "no terminal" in remote["log"].read_text(encoding="utf-8").splitlines()
