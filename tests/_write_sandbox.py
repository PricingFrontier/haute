"""Per-test write-sandbox: containment env + ``open`` interception.

Layers 1 and 3 of the test write-sandbox (layer 2, the static AST gate, is
``tests/test_write_sandbox_lint.py``). The autouse fixture in
``tests/conftest.py`` drives this module for every test:

* **strict** (converted modules, listed in :data:`STRICT_FILES`, or tests
  marked ``sandbox_strict``): the test runs chdir'd into its ``tmp_path``,
  with ``TMPDIR``/``TEMP``/``TMP`` and ``HOME``/``USERPROFILE`` pointed at
  subdirectories of it, and any *write-intent* ``open``/``os.open`` whose
  resolved path escapes the sandbox raises :class:`OutOfSandboxWriteError`.
* **observe** (everything else): the same interception, but escapes are
  recorded and reported in the terminal summary instead of failing — the
  violation census that drives the conversion ratchet.
* **off**: escape hatch (``HAUTE_TEST_WRITE_SANDBOX=off``) and the mode for
  perf-marked tests, whose wall-clock budgets must not pay for interception.

The sandbox root is exported per test as ``HAUTE_TEST_SANDBOX_ROOT`` and
readable via :func:`sandbox_root`. That surface is deliberate: the permission
layer can later scope an exec grant such as "run tests" to "writes under the
advertised sandbox root" without knowing anything else about pytest
(the grant-scoping design this pilot feeds).

Known gaps, stated: writes made by native code (polars sinks, catboost
artifact dumps) do not pass through Python ``open`` — in strict mode they are
still *contained* by the chdir + temp-dir redirect, but not intercepted.
``os.rename``/``os.mkdir``/fd-inheritance tricks are likewise not
intercepted, and file descriptors passed as integers cannot be resolved to
paths. The static gate and the census exist to shrink those corners over
time; deliberately obfuscated escapes are out of scope (review's job).
"""

from __future__ import annotations

import builtins
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_ROOT = "HAUTE_TEST_SANDBOX_ROOT"
ENV_MODE = "HAUTE_TEST_WRITE_SANDBOX"  # "off" | "observe" | "strict" (override)
ENV_CENSUS_DIR = "HAUTE_TEST_WRITE_SANDBOX_CENSUS_DIR"

# Test files that run in strict mode: the converted pilot slice. Grows as the
# conversion ratchet advances; a file listed here must also be absent from the
# layer-2 allowlist in tests/test_write_sandbox_lint.py.
STRICT_FILES = frozenset(
    {
        "test_write_sandbox_guard.py",
        "test_optimiser_apply.py",
        "test_bugfixes.py",
        "test_streaming_chunk_size_threading.py",
        "test_partial_failure.py",
    }
)

_WRITE_MODE_CHARS = frozenset("wax+")

# O_EXCL matters only alongside O_CREAT; O_CREAT/O_TRUNC/O_APPEND all imply
# an intent to change the filesystem even when paired with read-only access.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def sandbox_root() -> Path | None:
    """The current test's sandbox root, or ``None`` outside a sandboxed test.

    Reads the environment variable the autouse fixture exports, so it works
    from any process that inherits the test's environment — including a
    future permission hook inspecting what a test run is allowed to touch.
    """
    value = os.environ.get(ENV_ROOT)
    return Path(value) if value else None


def mode_writes(mode: str) -> bool:
    """True if an ``open`` mode string implies write intent."""
    return bool(_WRITE_MODE_CHARS & set(mode))


def flags_write(flags: int) -> bool:
    """True if ``os.open`` flags imply write intent."""
    return bool(flags & _WRITE_FLAGS)


def _realpath(target: object) -> str | None:
    """Resolve *target* to a real absolute path, or ``None`` if unresolvable.

    Relative paths resolve against the current working directory — which in
    strict mode is the sandbox itself, so relative writes are contained by
    construction. Integer file descriptors and exotic path types return
    ``None`` (documented gap: allow rather than crash the interposer).
    """
    if isinstance(target, int):
        return None
    try:
        return os.path.realpath(os.fsdecode(os.fspath(target)))
    except (TypeError, ValueError):
        return None


def _is_within(child_real: str, root_real: str) -> bool:
    child = os.path.normcase(child_real)
    root = os.path.normcase(root_real)
    return child == root or child.startswith(root.rstrip(os.sep) + os.sep)


@dataclass(frozen=True)
class Violation:
    nodeid: str
    api: str  # "open" | "os.open"
    path: str  # resolved real path
    detail: str  # mode string or flags repr


#: Per-process record of observe-mode escapes (and blocked strict writes).
VIOLATIONS: list[Violation] = []


class OutOfSandboxWriteError(PermissionError):
    """A strict-mode test attempted a write outside its sandbox."""


@dataclass
class Guard:
    """Installable interposer over ``builtins.open``/``io.open``/``os.open``.

    ``allowed_roots`` are pre-resolved real paths; a write-intent call whose
    resolved target is inside none of them is blocked (strict) or recorded
    (observe). ``os.devnull`` is always allowed.
    """

    mode: str  # "strict" | "observe"
    nodeid: str
    allowed_roots: tuple[str, ...]
    _installed: bool = False
    _orig_open: object = field(default=None, repr=False)
    _orig_os_open: object = field(default=None, repr=False)

    def _check(self, api: str, target: object, detail: str) -> None:
        real = _realpath(target)
        if real is None:
            return
        if os.path.normcase(real) == os.path.normcase(os.path.realpath(os.devnull)):
            return
        if any(_is_within(real, root) for root in self.allowed_roots):
            return
        if self.mode == "strict":
            raise OutOfSandboxWriteError(
                f"{self.nodeid}: {api}({real!r}, {detail}) writes outside the test "
                f"write-sandbox (allowed roots: {list(self.allowed_roots)}). Derive "
                "the path from the haute_scratch fixture; see tests/_write_sandbox.py."
            )
        VIOLATIONS.append(Violation(nodeid=self.nodeid, api=api, path=real, detail=detail))

    def install(self) -> None:
        assert not self._installed
        orig_open = builtins.open
        orig_os_open = os.open
        guard = self

        def guarded_open(file, mode="r", *args, **kwargs):
            if isinstance(mode, str) and mode_writes(mode):
                guard._check("open", file, f"mode={mode!r}")
            return orig_open(file, mode, *args, **kwargs)

        def guarded_os_open(path, flags, *args, **kwargs):
            if flags_write(flags):
                guard._check("os.open", path, f"flags={flags:#o}")
            return orig_os_open(path, flags, *args, **kwargs)

        self._orig_open = orig_open
        self._orig_os_open = orig_os_open
        builtins.open = guarded_open
        io.open = guarded_open
        os.open = guarded_os_open
        self._installed = True

    def uninstall(self) -> None:
        assert self._installed
        builtins.open = self._orig_open
        io.open = self._orig_open
        os.open = self._orig_os_open
        self._installed = False


def resolve_mode(env_mode: str | None, *, is_perf: bool, filename: str, marked_strict: bool) -> str:
    """Pick the guard mode for one test. Pure — unit-tested directly."""
    env = (env_mode or "").strip().lower()
    if env == "off":
        return "off"
    if is_perf:
        return "off"
    if env == "strict":
        return "strict"
    if marked_strict or filename in STRICT_FILES:
        return "strict"
    return "observe"


def dump_census(census_dir: str, worker_id: str, violations: list[Violation] | None = None) -> None:
    """Write this process's violations as JSON into *census_dir*.

    Used to aggregate across pytest-xdist workers: each worker dumps at
    session finish, the controller merges in the terminal summary.
    """
    out = Path(census_dir) / f"census-{worker_id}-{os.getpid()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = VIOLATIONS if violations is None else violations
    payload = [violation.__dict__ for violation in rows]
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_census(census_dir: str) -> list[Violation]:
    merged: list[Violation] = []
    directory = Path(census_dir)
    if not directory.is_dir():
        return merged
    for file in sorted(directory.glob("census-*.json")):
        for row in json.loads(file.read_text(encoding="utf-8")):
            merged.append(Violation(**row))
    return merged


def summarize(violations: list[Violation]) -> list[str]:
    """Group violations into human-readable census lines (path → tests)."""
    by_path: dict[str, set[str]] = {}
    for violation in violations:
        by_path.setdefault(violation.path, set()).add(violation.nodeid)
    lines = []
    for path in sorted(by_path):
        tests = sorted(by_path[path])
        shown = ", ".join(tests[:3]) + (f" (+{len(tests) - 3} more)" if len(tests) > 3 else "")
        lines.append(f"{path}  <-  {shown}")
    return lines
