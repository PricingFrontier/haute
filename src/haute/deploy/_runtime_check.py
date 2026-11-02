"""The check a scoring image runs on its own installed runtime as it is built.

``python -m haute.deploy._runtime_check dependency_lock.json`` reads the lock record a
bundle carries (``haute.deploy._lock``) and refuses the image unless:

* every locked distribution is installed at its locked version, and nothing is installed
  that the lock, ``haute`` and the base image's own tooling do not account for;
* every requirement of every locked distribution (its markers evaluated for this
  interpreter, with the extras the lock's roots selected) is installed at a version it
  admits, so a missing transitive dependency or an incompatible version is never silent;
* ``haute``, installed without its dependencies, has each of its own requirements
  installed at a version it admits, or named in the record's ``haute_omitted``: the
  reviewed list of what a scoring container deliberately leaves out (the editor,
  assistant, training and tuning stacks, the engines no artefact needs).

``pip check`` cannot be the gate: haute's deliberate omissions are exactly what it reports.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

#: Distributions a base image installs for its own use, which the lock does not hold.
BASE_IMAGE_TOOLING = frozenset({"pip", "setuptools", "wheel"})

_EXTRAS = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\[(?P<extras>[^\]]+)\]")


def _name(value: object) -> str:
    """A distribution name as installers compare it (PEP 503)."""
    return str(canonicalize_name(str(value)))


def selected_extras(roots: Iterable[str]) -> dict[str, frozenset[str]]:
    """The extras each root requirement selects, by distribution: ``uvicorn[standard]``."""
    selected: dict[str, frozenset[str]] = {}
    for root in roots:
        match = _EXTRAS.match(root)
        if match:
            extras = {extra.strip() for extra in match["extras"].split(",") if extra.strip()}
            name = _name(match["name"])
            selected[name] = selected.get(name, frozenset()) | frozenset(extras)
    return selected


def runtime_problems(
    record: Mapping[str, object],
    distributions: Callable[[], Iterable[metadata.Distribution]] | None = None,
) -> list[str]:
    """Everything wrong with the installed runtime against the lock *record*.

    *distributions* lists what is installed: this interpreter's, unless given.
    """
    distributions = distributions or metadata.distributions
    pins = {_name(name): str(version) for name, version in _mapping(record, "pins")}
    omitted = frozenset(_name(name) for name in _sequence(record, "haute_omitted"))
    extras = selected_extras(str(root) for root in _sequence(record, "roots"))
    installed = {
        _name(dist.metadata["Name"]): dist for dist in distributions() if dist.metadata["Name"]
    }
    problems: list[str] = []
    for name, version in sorted(pins.items()):
        dist = installed.get(name)
        if dist is None:
            problems.append(f"{name}=={version} is locked but not installed.")
        elif dist.version != version:
            problems.append(f"{name} is installed at {dist.version}, but locked at {version}.")
    for name in sorted(set(installed) - set(pins) - BASE_IMAGE_TOOLING - {"haute"}):
        problems.append(f"{name} is installed but the lock does not hold it.")
    if "haute" not in installed:
        problems.append("haute is not installed.")
    for name in sorted({*pins, "haute"} & set(installed)):
        problems.extend(
            _requirement_problems(
                installed[name],
                installed,
                extras=extras.get(name, frozenset()),
                omitted=omitted if name == "haute" else frozenset(),
            )
        )
    return problems


def _requirement_problems(
    dist: metadata.Distribution,
    installed: Mapping[str, metadata.Distribution],
    *,
    extras: frozenset[str],
    omitted: frozenset[str],
) -> list[str]:
    owner = _name(dist.metadata["Name"])
    problems: list[str] = []
    for line in dist.requires or ():
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            problems.append(f"{owner} declares a requirement that does not parse: {line} ({exc})")
            continue
        if requirement.marker is not None and not any(
            requirement.marker.evaluate({"extra": extra}) for extra in ("", *extras)
        ):
            continue
        name = _name(requirement.name)
        required = installed.get(name)
        if required is None:
            if name not in omitted:
                problems.append(f"{owner} requires {requirement}, which is not installed.")
            continue
        if not requirement.specifier.contains(required.version, prereleases=True):
            problems.append(
                f"{owner} requires {requirement}, but {name} {required.version} is installed."
            )
    return problems


def _mapping(record: Mapping[str, object], key: str) -> Iterable[tuple[object, object]]:
    value = record.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"The lock record's {key!r} is not a mapping.")
    return value.items()


def _sequence(record: Mapping[str, object], key: str) -> list[object]:
    value = record.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"The lock record's {key!r} is not a list.")
    return value


def main(argv: list[str] | None = None) -> int:
    """Check the installed runtime against the lock record named on the command line."""
    [path] = argv if argv is not None else sys.argv[1:]
    problems = runtime_problems(json.loads(Path(path).read_text(encoding="utf-8")))
    for problem in problems:
        print(f"runtime check: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("runtime check: the installed runtime matches the lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
