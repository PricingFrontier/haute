"""Fail the build when a dependency manifest drifts from the pinning doctrine.

The doctrine is deliberately ASYMMETRIC, because the right posture differs by
who resolves the manifest:

  * Published runtime dependencies -- ``[project] dependencies`` and
    ``[project.optional-dependencies]`` -- are what an end user resolves fresh
    against, with no lockfile of ours (the lockfiles are git-tracked but never
    shipped in the wheel).  These need a FLOOR AND A CAP.  Exact-pinning them is
    counterproductive: it gives the user no integrity protection (a version
    string is a label, not a hash), and it can render the package uninstallable
    when a transitive artefact is yanked from the index -- which has already
    happened here once, to polars.

  * Development and tooling dependencies -- ``[dependency-groups]`` and
    everything in ``frontend/package.json`` (private, never published) -- are
    EXACT-PINNED in the manifest.  These never reach an end user; they are the
    toolchain this repo runs on, so manifest exactness reinforces the lockfile
    and stops a stray ``npm install`` / ``uv add`` quietly relaxing one.

What this check is NOT: it is not the supply-chain control.  That is the
committed lockfile, which records a sha256 per package so a re-uploaded
artefact fails at install time.  ``uv sync --locked`` and ``npm ci`` already
enforce that on every CI job.  This check guards the DECLARATION layer only --
the failure mode where a new entrant silently adopts a different posture from
the twenty-odd entries around it and nothing notices.  That is exactly how
``anthropic`` and ``openai`` entered uncapped, six days after the floor+cap
lanes shipped, and rode along until an audit found them.

Jurisdiction, stated plainly: this reads DECLARATIONS. A dependency the
manifest never names -- one arriving transitively through another package that
does not bound it -- is not under-checked here, it is outside this check
entirely, and no amount of tightening the rules below would reach it.
Constraining one of those means declaring a dependency the project does not
otherwise need, which is a policy decision rather than a lint.

Deliberately NOT enforced: how TIGHT a cap is.  The doctrine suggests capping
below the next major for >=1.0 packages and below the next minor for 0.x ones,
but that is guidance for a human choosing a bound, not a mechanical rule -- a
literal 0.x reading would have demanded ``anthropic<0.41``, which the currently
locked 0.117.0 does not even satisfy.  We assert that a bound EXISTS and leave
its value to review.

Run:  uv run python scripts/lint_pins.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parent.parent

# Specifier operators that establish each half of a published dependency's band.
_LOWER_OPS = frozenset({">=", ">"})
_UPPER_OPS = frozenset({"<", "<="})
_EXACT_OPS = frozenset({"==", "==="})
# `~=X.Y` implies BOTH a floor and a cap, so it must be judged before the
# floor/cap checks or it trips both of them with misleading text.
_COMPATIBLE_OP = "~="

# An exact npm version: no range operators, no dist-tags, no protocols
# (`npm:`, `git+`, `file:`, `workspace:`, `link:`), no wildcards.
_EXACT_NPM_VERSION = re.compile(
    r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z",
)

# Executable surfaces scanned for `npx`, which falls back to the registry when
# local resolution fails and then silently runs a different version than the
# lockfile pins.  Prose is deliberately NOT scanned: the documentation that
# teaches this rule has to quote the offending command, and a check whose first
# act is to allowlist its own documentation is not earning its keep.  These are
# the surfaces where an `npx` would actually EXECUTE.
_NPX_SCAN_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/actions/*/action.yml",
    ".github/actions/*/action.yaml",
    "scripts/**/*.sh",
    "scripts/**/*.ps1",
)

# Files permitted to invoke `npx`, as explicit repo-relative paths.  Empty by
# design -- no invocation remains.  Add a path here only with a comment saying
# why the lockfile-respecting form (`npm exec`, `node_modules/.bin/<bin>`) will
# not do.
_NPX_ALLOWLIST: frozenset[str] = frozenset()

# `npx` as a command word. The lookarounds matter: `mynpx` and `./npx-shim`
# are not this command, while `(npx vitest)`, `{npx vitest;}`, and a trailing
# `npx \` line-continuation all are -- a naive "whitespace then npx then
# whitespace" pattern misses every one of those real invocations.
_NPX_CALL = re.compile(r"(?<![\w./-])npx(?![\w-])")

# Known limits. Only line-oriented shell/YAML surfaces are scanned: a Python
# script shelling out via `subprocess.run(["npx", ...])` is not caught, because
# distinguishing code from a string literal needs a parser, and this module's
# own diagnostics would be the first false positive. Within a scanned line the
# detection is lexical: the token anywhere outside a comment is reported, so a
# quoted mention (`echo "do not use npx"`) or a heredoc body is flagged too.
# That is the chosen direction -- rewording a mention on an executable surface
# is cheap and visible, while an invocation hidden by quoting would be silent.


def _strip_comment(line: str) -> str:
    """Drop a trailing shell/YAML comment so prose inside a script is not scanned.

    Without this, `# never use npx here` in a shell script fails the build --
    the check is meant to find invocations, not mentions.

    Quote-aware, because a naive cut at the first `#` would truncate
    `echo "shard #1"; npx playwright install` before the invocation and let a
    real bypass through -- turning a false-positive fix into a false negative.
    """
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


class Violation(NamedTuple):
    """One manifest defect, rendered as a single line (CI truncates at the first)."""

    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _rel(path: Path) -> str:
    """Repo-relative path for display, falling back to the path as given.

    The fallback matters when the checker is pointed at a manifest outside the
    repository (tests do exactly this); a display helper must never be the
    thing that raises.
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _line_of(text: str, needle: str) -> int:
    """1-indexed line of *needle*, preferring a line that is not a comment.

    A superseded spec often survives as a TOML comment above the live one
    ("# was anthropic>=0.40"), and a plain first-match would annotate that
    line instead of the declaration the violation is actually about.
    """
    fallback = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if needle not in line:
            continue
        if not line.lstrip().startswith("#"):
            return number
        fallback = fallback or number
    return fallback or 1


def _ops(requirement: Requirement) -> set[str]:
    return {spec.operator for spec in requirement.specifier}


def _has_wildcard(requirement: Requirement) -> bool:
    """Whether any specifier uses PEP 440 prefix matching (`==9.*`).

    The operator alone does not tell you a version is concrete: `pytest==9.*`
    parses with operator `==` but matches the whole 9.x line, so an
    operator-only exactness test would wave a range through as a pin.
    """
    return any(spec.version.endswith(".*") for spec in requirement.specifier)


def check_pyproject(path: Path) -> list[Violation]:
    """Apply the published-vs-tooling split to pyproject.toml."""
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    rel = _rel(path)
    violations: list[Violation] = []

    project = data.get("project") or {}
    own_name = canonicalize_name(project.get("name", ""))

    # `requires-python` is never inspected: it is the downstream-user
    # interpreter contract, not a dependency spec, and correctly carries a bare
    # floor.  It does not live in any of the lists walked below.
    published: list[tuple[str, str]] = [
        ("[project] dependencies", spec) for spec in project.get("dependencies") or []
    ]
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        published += [(f"[project.optional-dependencies.{extra}]", spec) for spec in specs]

    for where, spec in published:
        line = _line_of(text, spec)
        try:
            requirement = Requirement(spec)
        except Exception as exc:  # pragma: no cover - malformed manifest
            violations.append(Violation(rel, line, f"{where}: cannot parse {spec!r} ({exc})"))
            continue
        ops = _ops(requirement)
        if requirement.url is not None:
            # `foo @ https://...` pins an artefact the index cannot resolve, so
            # a fresh install of the published wheel would fail outright.
            violations.append(
                Violation(
                    rel,
                    line,
                    f"{where}: {spec!r} is a direct reference. A published dependency "
                    f"must resolve from the index -- use '{requirement.name}>=X,<Y'.",
                ),
            )
            continue
        if _COMPATIBLE_OP in ops:
            # `~=1.39` does bound both sides, so the floor/cap messages below
            # would both fire and both read as wrong. Say the real thing instead.
            violations.append(
                Violation(
                    rel,
                    line,
                    f"{where}: {spec!r} uses the compatible-release operator. It bounds "
                    f"both sides, but the bound it implies is not visible at a glance -- "
                    f"state the band explicitly as '{requirement.name}>=X,<Y'.",
                ),
            )
            continue
        if ops & _EXACT_OPS and _has_wildcard(requirement):
            # `==1.*` is a prefix RANGE, not a pin. Saying "exact-pinned" here
            # would send the author towards an uncapped floor to loosen it.
            violations.append(
                Violation(
                    rel,
                    line,
                    f"{where}: {spec!r} uses prefix matching, which has no real upper "
                    f"bound within the matched series -- state the band explicitly as "
                    f"'{requirement.name}>=X,<Y'.",
                ),
            )
            continue
        if ops & _EXACT_OPS:
            violations.append(
                Violation(
                    rel,
                    line,
                    f"{where}: {spec!r} is exact-pinned. Published dependencies must "
                    f"declare a floor and a cap so a fresh install stays resolvable "
                    f"when an artefact is yanked -- e.g. '{requirement.name}>=X,<Y'.",
                ),
            )
            continue
        if not ops & _LOWER_OPS:
            violations.append(
                Violation(rel, line, f"{where}: {spec!r} has no floor. Add a '>=' lower bound."),
            )
        if not ops & _UPPER_OPS:
            violations.append(
                Violation(
                    rel,
                    line,
                    f"{where}: {spec!r} has no cap. An unattended fresh install would "
                    f"take the next major release unreviewed -- add a '<' upper bound.",
                ),
            )

    for group, specs in (data.get("dependency-groups") or {}).items():
        for spec in specs:
            if not isinstance(spec, str):
                # Group includes ({"include-group": ...}) carry no version.
                continue
            line = _line_of(text, spec)
            try:
                requirement = Requirement(spec)
            except Exception as exc:  # pragma: no cover - malformed manifest
                violations.append(
                    Violation(
                        rel, line, f"[dependency-groups.{group}]: cannot parse {spec!r} ({exc})"
                    ),
                )
                continue
            # A self-reference (`haute[databricks]`) installs the project itself
            # and carries no version by design. Only the bare form is exempt: a
            # specifier, URL or marker on the project's own name would have uv
            # resolve it from the index rather than the checkout.
            if canonicalize_name(requirement.name) == own_name:
                if requirement.specifier or requirement.url or requirement.marker:
                    violations.append(
                        Violation(
                            rel,
                            line,
                            f"[dependency-groups.{group}]: {spec!r} is a self-reference "
                            f"with a specifier, URL or marker. The project installs itself "
                            f"from the checkout; write it bare, as "
                            f"'{project.get('name')}[extra]'.",
                        ),
                    )
                continue
            # Exactness is one `==` with a concrete version: `pytest==9.*`
            # carries the exact operator but matches the whole 9.x line, and
            # `pytest==9.0.3,<9` or `===` are not the single pin the message
            # promises, so an operator-only test would wave those through.
            specifiers = list(requirement.specifier)
            if len(specifiers) != 1 or specifiers[0].operator != "==" or _has_wildcard(requirement):
                violations.append(
                    Violation(
                        rel,
                        line,
                        f"[dependency-groups.{group}]: {spec!r} is not a single exact pin. "
                        f"Tooling is pinned in the manifest as well as the lockfile, so "
                        f"a stray 'uv add' cannot quietly relax it -- use "
                        f"'{requirement.name}==X.Y.Z' with a concrete version.",
                    ),
                )

    return violations


def check_package_json(path: Path) -> list[Violation]:
    """Every npm dependency section must carry an exact version."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    rel = _rel(path)
    violations: list[Violation] = []

    # `peerDependencies` is deliberately absent: a peer dependency declares
    # which HOSTS are acceptable, not what gets installed, so a range is the
    # correct and idiomatic form there. The other three describe an install.
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, version in (data.get(section) or {}).items():
            if _EXACT_NPM_VERSION.fullmatch(str(version)):
                continue
            violations.append(
                Violation(
                    rel,
                    _line_of(text, f'"{name}":'),
                    f"{section}: {name!r} is {version!r}, not an exact version. "
                    f"Pin it to the version the lockfile resolves ('save-exact=true' in "
                    f"frontend/.npmrc makes that the default for 'npm install').",
                ),
            )

    # package.json's own `scripts` are an executable surface.
    for name, command in (data.get("scripts") or {}).items():
        if _NPX_CALL.search(str(command)):
            violations.append(
                Violation(
                    rel,
                    _line_of(text, f'"{name}":'),
                    f"scripts.{name} invokes 'npx', which falls back to the registry and "
                    f"can silently run a different version than the lockfile pins. "
                    f"Use 'npm exec' or the binary in node_modules/.bin.",
                ),
            )

    return violations


def check_npx(root: Path) -> list[Violation]:
    """Scan executable surfaces for lockfile-bypassing `npx` invocations."""
    violations: list[Violation] = []
    for glob in _NPX_SCAN_GLOBS:
        for path in sorted(root.glob(glob)):
            rel = path.relative_to(root).as_posix()
            if rel in _NPX_ALLOWLIST:
                continue
            for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if _NPX_CALL.search(_strip_comment(text)):
                    violations.append(
                        Violation(
                            rel,
                            number,
                            "invokes 'npx', which falls back to the registry and can silently "
                            "run a different version than the lockfile pins. Use 'npm exec' or "
                            "the binary in node_modules/.bin.",
                        ),
                    )
    return violations


def collect(root: Path = REPO_ROOT) -> list[Violation]:
    """Every violation across every manifest, in file order."""
    return [
        *check_pyproject(root / "pyproject.toml"),
        *check_package_json(root / "frontend" / "package.json"),
        *check_npx(root),
    ]


def _annotation(violation: Violation) -> str:
    """A GitHub workflow command; `%`, CR and LF are reserved in its grammar."""
    message = violation.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::error file={violation.path},line={violation.line},title=Dependency pin::{message}"


def main() -> int:
    violations = collect()
    if not violations:
        print("lint_pins: dependency manifests match the pinning doctrine.")
        return 0
    for violation in violations:
        print(violation.render(), file=sys.stderr)
        # Surfaced on the run summary, not just in the log.
        print(_annotation(violation))
    plural = "" if len(violations) == 1 else "s"
    print(
        f"\nlint_pins: {len(violations)} violation{plural}. "
        f"See scripts/lint_pins.py for the rule and its rationale.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
