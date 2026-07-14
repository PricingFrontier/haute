"""Contract tests for the committed polars I/O argument schema.

The committed schema (``src/haute/_polars_io_arguments.json``) is the
interface contract for the polars I/O surface haute's data-input /
data-output machinery builds on. These tests re-extract the schema from the
*installed* polars and fail on any argument-level drift, so:

- a lockfile polars bump that changes the I/O surface fails at PR time, and
- the scheduled unlocked-resolve lane (which runs the core subset against a
  latest-within-caps resolve) catches an in-cap upstream release changing
  the surface before a user's fresh install does.

On a legitimate upstream change, the fix is deliberate: re-run
``uv run python scripts/extract_polars_io.py``, review the diff, and commit
the regenerated JSON together with whatever the drift implies for the
data-input/data-output argument surface.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from haute._polars_io_schema import (
    argument_names,
    io_function,
    io_functions_by_key,
    load_io_schema,
)

_REGENERATE_HINT = (
    "The installed polars I/O interface no longer matches the committed schema "
    "(src/haute/_polars_io_arguments.json). If this is a deliberate polars version "
    "change, re-run `uv run python scripts/extract_polars_io.py`, review the diff, "
    "and commit the regenerated JSON. If polars was NOT deliberately changed, an "
    "in-range upstream release has drifted the interface — triage before trusting "
    "data-input/data-output argument validation."
)


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


class TestCommittedSchemaMatchesInstalledPolars:
    """The drift tripwire: committed JSON == live introspection, argument-level."""

    def test_no_interface_drift_against_installed_polars(self) -> None:
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

    def test_committed_function_count_matches_functions_list(self) -> None:
        schema = load_io_schema()
        assert schema["function_count"] == len(schema["functions"])


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
