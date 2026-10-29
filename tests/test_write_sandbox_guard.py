"""Self-tests for the write-sandbox runtime guard (layer 3).

This module is listed in ``strict`` in ``tests/write_sandbox.toml``, so every test
here runs under the strict guard — the placement assertions below are live
proof that strict mode is what the suite actually applies, not a simulation.
This file is also part of the CI ``platform-smoke`` lane's explicit list:
path resolution, temp-dir env names, and home-dir semantics all differ on
Windows, and the guard must hold there too.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _write_sandbox as ws

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _real(path: object) -> str:
    return os.path.realpath(str(path))


# ---------------------------------------------------------------------------
# Strict-mode placement: cwd, temp, home, env export
# ---------------------------------------------------------------------------


def test_strict_mode_chdirs_into_sandbox(tmp_path: Path) -> None:
    assert _real(Path.cwd()) == _real(tmp_path)


def test_sandbox_root_exported_for_hooks(tmp_path: Path) -> None:
    root = ws.sandbox_root()
    assert root is not None
    assert _real(root) == _real(tmp_path)


def test_tempfile_machinery_points_into_sandbox(tmp_path: Path) -> None:
    assert ws._is_within(_real(tempfile.gettempdir()), _real(tmp_path))
    with tempfile.NamedTemporaryFile() as handle:
        assert ws._is_within(_real(handle.name), _real(tmp_path))


def test_home_points_into_sandbox(tmp_path: Path) -> None:
    env_name = "USERPROFILE" if os.name == "nt" else "HOME"
    assert ws._is_within(_real(os.environ[env_name]), _real(tmp_path))
    assert ws._is_within(_real(Path.home()), _real(tmp_path))


# ---------------------------------------------------------------------------
# Strict-mode enforcement
# ---------------------------------------------------------------------------


def test_writes_inside_sandbox_succeed(haute_scratch: Path) -> None:
    target = haute_scratch / "inside.txt"
    target.write_text("ok", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "ok"


def test_relative_writes_land_in_sandbox(tmp_path: Path) -> None:
    with open("relative.txt", "w", encoding="utf-8") as handle:  # write-sandbox: deliberate
        handle.write("contained")
    assert (tmp_path / "relative.txt").read_text(encoding="utf-8") == "contained"


def test_open_write_outside_sandbox_is_blocked() -> None:
    outside = _REPO_ROOT / "_write_sandbox_probe.txt"
    try:
        with pytest.raises(ws.OutOfSandboxWriteError):
            open(outside, "w", encoding="utf-8")  # write-sandbox: deliberate
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_os_open_write_outside_sandbox_is_blocked() -> None:
    outside = _REPO_ROOT / "_write_sandbox_probe_os.txt"
    try:
        with pytest.raises(ws.OutOfSandboxWriteError):
            os.open(outside, os.O_WRONLY | os.O_CREAT)
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_reads_outside_sandbox_still_allowed() -> None:
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project]" in text


def test_devnull_always_writable() -> None:
    with open(os.devnull, "w", encoding="utf-8") as handle:
        handle.write("discard")


# ---------------------------------------------------------------------------
# Observe mode (the census): recorded, never blocked
# ---------------------------------------------------------------------------


def test_observe_mode_records_instead_of_blocking(haute_scratch: Path) -> None:
    """A nested observe guard with a narrower root records the escape.

    The stray write stays inside the *real* strict sandbox (so the outer
    guard permits it) but outside the nested guard's pretend root — exactly
    an unconverted test writing outside its sandbox, in miniature.
    """
    pretend_root = haute_scratch / "pretend_sandbox"
    pretend_root.mkdir()
    stray = haute_scratch / "stray.txt"
    before = len(ws.VIOLATIONS)
    guard = ws.Guard(mode="observe", nodeid="census-probe", allowed_roots=(_real(pretend_root),))
    guard.install()
    try:
        with open(stray, "w", encoding="utf-8") as handle:
            handle.write("observed")
    finally:
        guard.uninstall()
        recorded = [v for v in ws.VIOLATIONS[before:] if v.nodeid == "census-probe"]
        del ws.VIOLATIONS[before:]  # keep the probe out of the real census
    assert stray.read_text(encoding="utf-8") == "observed"  # observe never blocks
    assert any(v.api == "open" and v.path == _real(stray) for v in recorded)


def test_observe_mode_ignores_reads_and_in_root_writes(haute_scratch: Path) -> None:
    inside = haute_scratch / "fine.txt"
    before = len(ws.VIOLATIONS)
    guard = ws.Guard(mode="observe", nodeid="census-probe-2", allowed_roots=(_real(haute_scratch),))
    guard.install()
    try:
        inside.write_text("fine", encoding="utf-8")
        inside.read_text(encoding="utf-8")
    finally:
        guard.uninstall()
    assert [v for v in ws.VIOLATIONS[before:] if v.nodeid == "census-probe-2"] == []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_resolve_mode_precedence() -> None:
    strict_file = "test_write_sandbox_guard.py"

    def mode(env: str | None, *, perf: bool = False, name: str = "other.py", mark: bool = False):
        return ws.resolve_mode(env, is_perf=perf, filename=name, marked_strict=mark)

    assert mode("off", name=strict_file, mark=True) == "off"
    assert mode(None, perf=True, name=strict_file) == "off"
    assert mode(None, name=strict_file) == "strict"
    assert mode(None, mark=True) == "strict"
    assert mode(None) == "observe"
    assert mode("strict") == "strict"


def test_write_intent_detection() -> None:
    for mode in ("w", "wb", "a", "ab", "x", "xb", "r+", "rb+", "w+"):
        assert ws.mode_writes(mode), mode
    for mode in ("r", "rb", "rt"):
        assert not ws.mode_writes(mode), mode
    assert ws.flags_write(os.O_WRONLY | os.O_CREAT)
    assert ws.flags_write(os.O_RDWR)
    assert ws.flags_write(os.O_RDONLY | os.O_CREAT)
    assert not ws.flags_write(os.O_RDONLY)


def test_is_within_boundaries() -> None:
    root = _real(Path("alpha")) if os.name == "nt" else "/alpha"
    assert ws._is_within(root, root)
    assert ws._is_within(root + os.sep + "child.txt", root)
    assert not ws._is_within(root + "-sibling" + os.sep + "x", root)


def test_census_dump_load_summarize_roundtrip(haute_scratch: Path) -> None:
    rows = [
        ws.Violation(nodeid="t::one", api="open", path="/x/shared", detail="mode='w'"),
        ws.Violation(nodeid="t::two", api="os.open", path="/x/shared", detail="flags=0o1101"),
        ws.Violation(nodeid="t::three", api="open", path="/y/other", detail="mode='a'"),
    ]
    census_dir = haute_scratch / "census"
    ws.dump_census(str(census_dir), "probe", rows)
    loaded = ws.load_census(str(census_dir))
    assert loaded == rows
    lines = ws.summarize(loaded)
    assert len(lines) == 2
    assert any("/x/shared" in line and "t::one" in line for line in lines)


def _summarise_run(config: SimpleNamespace) -> None:
    """Run the session's terminal-summary hook, the one that writes the census."""
    from tests import conftest

    reporter = SimpleNamespace(config=config, section=lambda *_: None, line=lambda *_: None)
    conftest.pytest_terminal_summary(reporter)


def test_the_run_writes_its_census_where_the_coverage_gate_reads_it(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = ws.Violation(nodeid="t::one", api="open", path="/x/shared", detail="mode='w'")
    out = haute_scratch / "census" / "census-shard1.json"
    monkeypatch.setattr(ws, "VIOLATIONS", [row])
    monkeypatch.delenv(ws.ENV_CENSUS_DIR, raising=False)  # this session's own spool
    monkeypatch.setenv(ws.ENV_CENSUS_OUT, str(out))
    monkeypatch.setenv("RUNNER_TEMP", str(haute_scratch / "runner-temp"))

    _summarise_run(SimpleNamespace())

    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["version"] == ws.CENSUS_VERSION
    assert record["writes"] == [row.__dict__]
    assert record["roots"] == {
        "repo": _real(_REPO_ROOT),
        "home": _real(Path.home()),
        "runner-temp": _real(haute_scratch / "runner-temp"),
    }


def test_a_run_that_recorded_nothing_still_writes_its_census(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = haute_scratch / "census-shard1.json"
    monkeypatch.setattr(ws, "VIOLATIONS", [])
    monkeypatch.delenv(ws.ENV_CENSUS_DIR, raising=False)
    monkeypatch.setenv(ws.ENV_CENSUS_OUT, str(out))
    monkeypatch.delenv("RUNNER_TEMP", raising=False)

    _summarise_run(SimpleNamespace())

    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["writes"] == []
    assert set(record["roots"]) == {"repo", "home"}


def test_an_xdist_worker_leaves_the_census_to_the_controller(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = haute_scratch / "census-shard1.json"
    monkeypatch.setattr(ws, "VIOLATIONS", [])
    monkeypatch.delenv(ws.ENV_CENSUS_DIR, raising=False)
    monkeypatch.setenv(ws.ENV_CENSUS_OUT, str(out))

    _summarise_run(SimpleNamespace(workerinput={}))

    assert not out.exists()


# ---------------------------------------------------------------------------
# What strict mode lets through besides the test's tmp_path
# ---------------------------------------------------------------------------


def test_strict_mode_lets_the_test_write_its_own_source_proof_store(
    source_proof_records: Path,
) -> None:
    # The harness keeps the store outside tmp_path, which a test may list.
    source_proof_records.mkdir(parents=True, exist_ok=True)
    record = source_proof_records / "record.json"
    record.write_text("{}", encoding="utf-8")
    assert record.read_text(encoding="utf-8") == "{}"


def test_strict_mode_lets_a_widened_root_test_write_its_source_cache(
    _widen_sandbox_root: Path,
) -> None:
    probe = _widen_sandbox_root / "probe.txt"
    probe.write_text("cached", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "cached"


def test_strict_mode_does_not_widen_to_the_runs_base_directory(tmp_path: Path) -> None:
    sibling = tmp_path.parent / f"{tmp_path.name}-sibling.txt"
    with pytest.raises(ws.OutOfSandboxWriteError):
        open(sibling, "w", encoding="utf-8")  # write-sandbox: deliberate
    assert not sibling.exists()


def test_multiprocessing_shared_memory_is_not_a_test_write() -> None:
    from multiprocessing import heap

    # On Linux each arena is a file under /dev/shm, unlinked at once.
    arena = heap.Arena(4096)
    assert arena.size == 4096
    assert ws._is_multiprocessing_arena("/dev/shm/pym-12-abcd1234")
    assert not ws._is_multiprocessing_arena("/dev/shm/haute-cache")
    assert not ws._is_multiprocessing_arena("/tmp/pym-12-abcd1234")


def test_a_strict_test_creates_the_repository_mlflow_store_in_the_session(
    _isolate_repository_mlflow_store: Path,
) -> None:
    """The local destination resolved against the repository root is the
    session's store, so whichever test creates it first, strict or not, may."""
    from mlflow.tracking import MlflowClient

    from haute._mlflow_utils import allow_file_store_if_local
    from haute.modelling._mlflow_settings import resolve_destination

    config = resolve_destination("local", project_root=_REPO_ROOT)
    assert ws._is_within(_real(config.destination), _real(_isolate_repository_mlflow_store))

    # Opening the store writes its default experiment on first use.
    allow_file_store_if_local(config.tracking_uri, config.mode)
    client = MlflowClient(tracking_uri=config.tracking_uri)
    assert client.get_experiment("0") is not None
