"""Contract tests for the committed polars I/O argument schema.

The committed schema (``src/haute/_polars_io_arguments.json``) records one
polars version, but the ``polars`` specifier admits a range, so the schema and
an installed polars can legitimately differ. Two contracts follow from that,
and this module keeps them apart:

- **Cap-range contract** (:class:`TestNoBreakingDriftAcrossTheCap`) — what
  must hold for *every* polars inside the specifier, and therefore for a
  user's fresh install. Argument-name differences are not in it: the registry
  intersects the committed schema with the installed signature
  (``haute._polars_io_schema.supported_argument_names``), so a name present on
  only one side is simply not config-expressible. What remains is the surface
  haute uses *positionally* or by identity: the callables must exist and their
  positional parameters must not move.
- **Snapshot contract** (:class:`TestCommittedSnapshotMatchesPinnedPolars`) —
  exact equality with the version the schema records. This is the
  regenerate-on-bump gate for the pinned lanes: move polars in ``uv.lock``
  without re-running the extractor and it fails. A lane that deliberately
  resolves polars away from the lockfile declares itself with
  ``HAUTE_POLARS_UNPINNED=1``; there the cap-range contract is the applicable
  one.

On a legitimate upstream change, the fix is deliberate: re-run
``uv run python scripts/extract_polars_io.py``, review the diff, and commit
the regenerated JSON together with whatever the drift implies for the
data-input/data-output argument surface.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any

import polars
import pytest

from haute import _polars_io_schema as schema_module
from haute._polars_io_registry import FORMATS
from haute._polars_io_schema import (
    argument_names,
    installed_argument_names,
    io_function,
    io_functions_by_key,
    load_io_schema,
    retired_argument_names,
    supported_argument_names,
)

_UNPINNED_ENV = "HAUTE_POLARS_UNPINNED"

_REGENERATE_HINT = (
    "The installed polars I/O interface no longer matches the committed schema "
    "(src/haute/_polars_io_arguments.json). If this is a deliberate polars version "
    "change, re-run `uv run python scripts/extract_polars_io.py`, review the diff, "
    "and commit the regenerated JSON. If polars was NOT deliberately changed, an "
    "in-range upstream release has drifted the interface — triage before trusting "
    "data-input/data-output argument validation."
)

_BREAKING_HINT = (
    "The installed polars has moved part of the I/O surface haute depends on by "
    "identity or by position, which no committed schema can absorb: a callable the "
    "registry dispatches to is gone, or a parameter haute passes positionally has "
    "shifted. Fix the registry (src/haute/_polars_io_registry.py) or the polars "
    "specifier in pyproject.toml — regenerating the schema alone does not make a "
    "fresh install correct."
)

# Parameter kinds haute may supply positionally; the node's own source/target
# fields become leading positional arguments in `_resolve_input_source`.
_POSITIONAL_KINDS = frozenset({"POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD", "VAR_POSITIONAL"})


def _load_extractor() -> Any:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "extract_polars_io.py"
    spec = importlib.util.spec_from_file_location("extract_polars_io", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


extract_polars_io = _load_extractor()


def _functions_by_key(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{fn['owner']}.{fn['name']}": fn for fn in schema["functions"]}


def _registry_callable_keys() -> list[str]:
    """Every ``owner.name`` the format registry dispatches to."""
    keys = {
        f"{owner}.{name}"
        for fmt in FORMATS
        for owner, name in (
            ("polars", fmt.reader),
            ("polars", fmt.scanner),
            ("DataFrame", fmt.writer),
            ("LazyFrame", fmt.sinker),
        )
        if name is not None
    }
    return sorted(keys)


def _positional_names(record: dict[str, Any]) -> list[str]:
    return [a["name"] for a in record["arguments"] if a["kind"] in _POSITIONAL_KINDS]


class TestNoBreakingDriftAcrossTheCap:
    """What must hold for every polars inside the specifier, not just the pinned one."""

    def test_registry_callables_survive_the_installed_polars(self) -> None:
        committed = _functions_by_key(load_io_schema())
        fresh = _functions_by_key(extract_polars_io.extract_schema())

        problems: list[str] = []
        for key in _registry_callable_keys():
            if key not in committed:
                problems.append(f"{key}: dispatched by the registry but absent from the schema")
                continue
            if key not in fresh:
                problems.append(f"{key}: absent from the installed polars {polars.__version__}")
                continue
            if "introspection_error" in fresh[key]:
                problems.append(f"{key}: not introspectable: {fresh[key]['introspection_error']}")
                continue
            was, now = _positional_names(committed[key]), _positional_names(fresh[key])
            if was != now:
                problems.append(f"{key}: positional parameters moved: {was} -> {now}")

        assert not problems, _BREAKING_HINT + "\n\nBreaking drift:\n- " + "\n- ".join(problems)

    def test_committed_function_count_matches_functions_list(self) -> None:
        schema = load_io_schema()
        assert schema["function_count"] == len(schema["functions"])


class TestCommittedSnapshotMatchesPinnedPolars:
    """The regenerate-on-bump gate: committed JSON == live introspection, exactly."""

    def test_no_interface_drift_against_installed_polars(self) -> None:
        recorded = load_io_schema()["polars_version"]
        if os.environ.get(_UNPINNED_ENV) == "1":
            # A lane that resolved polars away from the lockfile on purpose
            # (unlocked-resolve, dependency-floors). Snapshot equality is not
            # the contract there; TestNoBreakingDriftAcrossTheCap is.
            return

        assert polars.__version__ == recorded, (
            f"{_REGENERATE_HINT} Installed polars is {polars.__version__}, the committed "
            f"schema records {recorded}."
        )

        committed = _functions_by_key(load_io_schema())
        fresh = _functions_by_key(extract_polars_io.extract_schema())

        problems: list[str] = []

        removed = sorted(set(committed) - set(fresh))
        added = sorted(set(fresh) - set(committed))
        if removed:
            problems.append(f"callables no longer present in polars: {removed}")
        if added:
            problems.append(f"new polars I/O callables not in the committed schema: {added}")

        for key in sorted(set(committed) & set(fresh)):
            old_args = {a["name"]: a for a in committed[key]["arguments"]}
            new_args = {a["name"]: a for a in fresh[key]["arguments"]}
            gone = sorted(set(old_args) - set(new_args))
            new = sorted(set(new_args) - set(old_args))
            if gone:
                problems.append(f"{key}: arguments removed: {gone}")
            if new:
                problems.append(f"{key}: arguments added: {new}")
            for name in sorted(set(old_args) & set(new_args)):
                old, fresh_arg = old_args[name], new_args[name]
                changed = [
                    field
                    for field in ("kind", "position", "annotation", "has_default", "default")
                    if old[field] != fresh_arg[field]
                ]
                if changed:
                    details = {field: (old[field], fresh_arg[field]) for field in changed}
                    problems.append(f"{key}.{name}: changed {details}")

        assert not problems, _REGENERATE_HINT + "\n\nDrift:\n- " + "\n- ".join(problems)


class TestCommittedSchemaInvariants:
    """Properties of the committed schema the node machinery relies on."""

    def test_every_undocumented_argument_is_underscore_private(self) -> None:
        # Pre-build audit finding: no PUBLIC argument of the polars I/O
        # surface lacks documentation — the only signature-present-but-
        # undocumented arguments are deliberately-private underscore plumbing.
        # If this fails on a polars bump, a new public argument shipped
        # undocumented and the config surface decision for it must be explicit.
        offenders = [
            f"{fn['owner']}.{fn['name']}.{arg['name']}"
            for fn in load_io_schema()["functions"]
            for arg in fn["arguments"]
            if arg["undocumented"] and not arg["name"].startswith("_")
        ]
        assert offenders == []

    def test_functions_sorted_and_unique(self) -> None:
        keys = [f"{fn['owner']}.{fn['name']}" for fn in load_io_schema()["functions"]]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))

    def test_no_introspection_failures_recorded(self) -> None:
        failures = [
            f"{fn['owner']}.{fn['name']}"
            for fn in load_io_schema()["functions"]
            if "introspection_error" in fn
        ]
        assert failures == []


class TestSchemaLoaderHelpers:
    def test_io_function_lookup_and_argument_names(self) -> None:
        record = io_function("polars", "read_csv")
        assert record["kind"] == "module_function"
        names = argument_names("polars", "read_csv")
        assert names[0] == "source"
        assert "schema_overrides" in names

    def test_io_function_miss_raises_with_context(self) -> None:
        try:
            io_function("polars", "read_nonexistent")
        except KeyError as exc:
            assert "read_nonexistent" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected KeyError")

    def test_known_width_floor(self) -> None:
        # The surface can legitimately grow on a polars bump (the drift test
        # forces the regeneration to be reviewed); it must never silently
        # shrink below the researched 63-callable width.
        assert len(io_functions_by_key()) >= 63

    def test_installed_argument_names_reads_the_live_signature(self) -> None:
        live = {p for p in inspect.signature(polars.read_csv).parameters}
        assert installed_argument_names("polars", "read_csv") == live

    def test_installed_argument_names_drops_the_method_receiver(self) -> None:
        assert "self" not in installed_argument_names("DataFrame", "write_csv")

    def test_installed_argument_names_names_a_callable_this_polars_lacks(self) -> None:
        try:
            installed_argument_names("polars", "read_nonexistent")
        except AttributeError as exc:
            assert "read_nonexistent" in str(exc)
            assert polars.__version__ in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected AttributeError")


class TestSupportedArgumentNames:
    """The intersection that lets one committed snapshot serve the whole cap."""

    def test_a_committed_argument_the_installed_polars_dropped_is_not_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        committed = set(argument_names("polars", "read_csv"))
        assert "schema_overrides" in committed
        monkeypatch.setattr(
            schema_module,
            "installed_argument_names",
            lambda owner, name: frozenset(committed - {"schema_overrides"}),
        )
        assert "schema_overrides" not in supported_argument_names("polars", "read_csv")
        assert retired_argument_names("polars", "read_csv") == frozenset({"schema_overrides"})

    def test_an_argument_only_the_installed_polars_has_is_not_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A newer in-cap polars introducing an argument must not make it
        # config-expressible: the exclusion classes in the registry are
        # subtractive, so an unclassified argument would be allowed by default.
        committed = set(argument_names("polars", "read_csv"))
        monkeypatch.setattr(
            schema_module,
            "installed_argument_names",
            lambda o, n: frozenset(committed | {"empty_string_is_null"}),
        )
        assert "empty_string_is_null" not in supported_argument_names("polars", "read_csv")
        assert retired_argument_names("polars", "read_csv") == frozenset()

    def test_supported_equals_committed_when_the_versions_agree(self) -> None:
        for key in _registry_callable_keys():
            owner, name = key.split(".", 1)
            assert supported_argument_names(owner, name) <= frozenset(argument_names(owner, name))
