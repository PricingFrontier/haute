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
import json
import os
import sys
from pathlib import Path
from typing import Any

import polars
import pytest

from haute import _polars_io_registry as registry_module
from haute import _polars_io_schema as schema_module
from haute._polars_io_registry import (
    FORMATS,
    FORMATS_BY_NAME,
    PolarsIoConfigError,
    input_callable_key,
    registry_capabilities,
    validate_arguments,
)
from haute._polars_io_schema import (
    argument_names,
    installed_argument_names,
    installed_provides_callable,
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

# Polars keyword arguments haute writes as literals OUTSIDE the config surface.
# The committed-schema intersection cannot protect these — it governs only what
# a node config may carry — so they need naming here or a rename inside the cap
# turns into a TypeError at execution. Keep in step with the call sites:
#   haute.executor._output_row_count_scan_kwargs()  — exact artifact row counts
#   haute._io.read_source()                         — declared-dtype CSV scans
#   haute._polars_utils                             — bounded/atomic sink writes
_LITERAL_KEYWORDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "polars",
        "scan_csv",
        (
            "decimal_comma",
            "eol_char",
            "has_header",
            "infer_schema",
            "quote_char",
            "raise_if_empty",
            "schema_overrides",
            "separator",
        ),
    ),
    ("DataFrame", "write_parquet", ("compression",)),
    ("LazyFrame", "sink_parquet", ("compression",)),
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


def _registry_callables() -> list[tuple[str, int]]:
    """Every ``owner.name`` the registry dispatches to, with how many leading
    positional arguments haute supplies to it.

    Inputs get their source from ``_resolve_input_source``: one positional for
    a path or inline records, two (query, uri) for a database. Outputs get the
    resolved target path, always one.
    """
    supplied: dict[str, int] = {}
    for fmt in FORMATS:
        leading = 2 if fmt.source_kind == "database" else 1
        for owner, name, count in (
            ("polars", fmt.reader, leading),
            ("polars", fmt.scanner, leading),
            ("DataFrame", fmt.writer, 1),
            ("LazyFrame", fmt.sinker, 1),
        ):
            if name is None:
                continue
            key = f"{owner}.{name}"
            supplied[key] = max(supplied.get(key, 0), count)
    return sorted(supplied.items())


def _registry_callable_keys() -> list[str]:
    return [key for key, _ in _registry_callables()]


def _positional_names(record: dict[str, Any]) -> list[str]:
    return [a["name"] for a in record["arguments"] if a["kind"] in _POSITIONAL_KINDS]


class TestNoBreakingDriftAcrossTheCap:
    """What must hold for every polars inside the specifier, not just the pinned one."""

    def test_registry_callables_survive_the_installed_polars(self) -> None:
        committed = _functions_by_key(load_io_schema())
        fresh = _functions_by_key(extract_polars_io.extract_schema())

        problems: list[str] = []
        for key, supplied in _registry_callables():
            if key not in committed:
                problems.append(f"{key}: dispatched by the registry but absent from the schema")
                continue
            if key not in fresh:
                problems.append(f"{key}: absent from the installed polars {polars.__version__}")
                continue
            if "introspection_error" in fresh[key]:
                problems.append(f"{key}: not introspectable: {fresh[key]['introspection_error']}")
                continue
            # Only the leading positionals haute actually supplies. Polars may
            # append a new trailing positional-or-keyword parameter inside the
            # cap without touching how haute calls it, and that must not be a
            # failure — a reorder or a rename of what haute does supply must.
            was = _positional_names(committed[key])[:supplied]
            now = _positional_names(fresh[key])[:supplied]
            if was != now:
                problems.append(
                    f"{key}: the {supplied} leading positional parameter(s) haute supplies "
                    f"moved: {was} -> {now}"
                )

        assert not problems, _BREAKING_HINT + "\n\nBreaking drift:\n- " + "\n- ".join(problems)

    def test_keywords_haute_writes_as_literals_still_exist(self) -> None:
        """The call sites the config-argument intersection cannot cover.

        `allowed_arguments` protects what a node config may carry. It says
        nothing about the keyword names haute itself hardcodes when it calls
        polars, and those break with a TypeError rather than a validation
        error. This is the invariant that used to ride on exact snapshot
        equality; naming the keywords keeps it across the whole cap.
        """
        problems: list[str] = []
        for owner, name, keywords in _LITERAL_KEYWORDS:
            live = installed_argument_names(owner, name)
            missing = sorted(set(keywords) - live)
            if missing:
                problems.append(f"{owner}.{name}: {missing}")
        assert not problems, (
            f"The installed polars {polars.__version__} no longer accepts keyword argument(s) "
            f"haute passes literally: {problems}. Those call sites will raise TypeError; fix "
            "them, and keep _LITERAL_KEYWORDS in step with the call sites it names."
        )

    def test_every_registry_callable_keeps_a_usable_argument_surface(self) -> None:
        """No registry callable may intersect down to nothing.

        The guard against a signature this contract cannot read literally: a
        callable wrapped so ``inspect.signature`` reports ``(*args, **kwargs)``
        introspects cleanly and matches on positionals, yet intersects with the
        committed names to the empty set — silently costing that format every
        configurable argument rather than failing.
        """
        empty = [
            key
            for key in _registry_callable_keys()
            if not supported_argument_names(*key.split(".", 1))
        ]
        assert empty == [], (
            f"These callables share no argument name between the committed schema and the "
            f"installed polars {polars.__version__}: {empty}. Their formats would publish an "
            "empty argument surface."
        )

    def test_committed_function_count_matches_functions_list(self) -> None:
        schema = load_io_schema()
        assert schema["function_count"] == len(schema["functions"])


class TestCommittedSnapshotMatchesPinnedPolars:
    """The regenerate-on-bump gate: committed JSON == live introspection, exactly."""

    def test_no_interface_drift_against_installed_polars(self) -> None:
        recorded = load_io_schema()["polars_version"]
        if polars.__version__ != recorded and os.environ.get(_UNPINNED_ENV) == "1":
            # A lane that resolved polars away from the lockfile on purpose
            # (unlocked-resolve, dependency-floors) landed on a version this
            # snapshot cannot describe. TestNoBreakingDriftAcrossTheCap is the
            # applicable contract there. Skipped rather than silently returned
            # so the run log says which contract was applied — and the version
            # guard comes first, so a declared-unpinned lane that happens to
            # resolve the recorded version still runs the full comparison. The
            # declaration can never turn a check off while it is meaningful.
            pytest.skip(
                "HAUTE_POLARS_UNPINNED: the installed polars is not the version the "
                "committed schema records, so snapshot equality cannot hold; "
                "TestNoBreakingDriftAcrossTheCap is the applicable contract"
            )

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

    def test_installed_provides_callable_answers_both_ways(self) -> None:
        assert installed_provides_callable("polars", "read_csv")
        assert installed_provides_callable("LazyFrame", "sink_parquet")
        assert not installed_provides_callable("polars", "read_nonexistent")
        assert not installed_provides_callable("Nonexistent", "read_csv")


class TestCapabilitiesSurviveAMissingCallable:
    """One callable the installed polars lacks costs one mode, not the catalogue."""

    def test_capabilities_drop_only_the_affected_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def absent_scan_csv(owner: str, name: str) -> bool:
            return not (owner == "polars" and name == "scan_csv")

        monkeypatch.setattr(registry_module, "installed_provides_callable", absent_scan_csv)
        payload = registry_capabilities()
        formats = {f["name"]: f for group in payload["groups"] for f in group["formats"]}
        assert formats["csv"]["input"]["modes"] == []
        assert formats["csv"]["input"]["arguments"] == {}
        # Every other format, and CSV's own output side, still published.
        assert formats["parquet"]["input"]["modes"] == ["scan"]
        assert formats["csv"]["output"]["modes"]
        assert len(formats) >= 14

    def test_configuring_the_absent_format_fails_in_the_config_error_family(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A pipeline configured before the polars underneath it changed still
        # reaches validation. It must land in the curated error family, not as
        # a raw AttributeError out of the schema layer.
        monkeypatch.setattr(
            registry_module,
            "installed_provides_callable",
            lambda owner, name: not (owner == "polars" and name == "scan_csv"),
        )
        fmt = FORMATS_BY_NAME["csv"]
        with pytest.raises(PolarsIoConfigError) as excinfo:
            validate_arguments(fmt, "polars", "scan_csv", {})
        assert "scan_csv" in str(excinfo.value)
        assert polars.__version__ in str(excinfo.value)


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

    def test_a_retired_argument_is_rejected_with_the_version_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The user-facing half of a withdrawn argument. Monkeypatched rather
        # than left to a polars that happens to have withdrawn something: on a
        # pinned install nothing is retired, so the message would otherwise be
        # exercised only on versions nobody runs in CI.
        fmt = FORMATS_BY_NAME["csv"]
        owner, callable_name = input_callable_key(fmt, "scan")
        committed = set(argument_names(owner, callable_name))
        assert "low_memory" in committed
        monkeypatch.setattr(
            schema_module,
            "installed_argument_names",
            lambda o, n: frozenset(committed - {"low_memory"}),
        )
        with pytest.raises(PolarsIoConfigError) as excinfo:
            validate_arguments(fmt, owner, callable_name, {"low_memory": True})
        message = str(excinfo.value)
        assert "low_memory" in message
        assert polars.__version__ in message
        assert "no longer in the installed polars" in message


class TestFreshnessReport:
    """`extract_polars_io.py --diff` — the signal that replaced the alarm."""

    def test_matching_versions_report_no_drift(self) -> None:
        report = "\n".join(extract_polars_io.diff_report())
        assert "Polars I/O interface freshness" in report
        if polars.__version__ == load_io_schema()["polars_version"]:
            assert "is the version the committed schema records" in report
        else:
            assert f"this resolve installed {polars.__version__}" in report

    def test_drift_is_sorted_into_names_defaults_and_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        committed = extract_polars_io.extract_schema()
        committed["polars_version"] = "0.0.0-committed"
        for fn in committed["functions"]:
            if fn["owner"] != "polars" or fn["name"] != "read_csv":
                continue
            fn["arguments"] = [dict(arg) for arg in fn["arguments"]]
            fn["arguments"][1]["name"] = "gone_upstream"
            fn["arguments"][2]["default"] = "'was'"
            fn["arguments"][3]["annotation"] = "OldAnnotation"
        snapshot = tmp_path / "committed.json"
        snapshot.write_text(json.dumps(committed), encoding="utf-8")
        monkeypatch.setattr(extract_polars_io, "DEFAULT_OUTPUT", snapshot)

        report = "\n".join(extract_polars_io.diff_report())
        assert "no longer declares: gone_upstream" in report
        assert "**Changed defaults**" in report
        assert "default 'was' →" in report
        assert "A further 1 argument(s) differ only in kind, position or annotation" in report

    def test_an_introspection_failure_is_not_reported_as_a_removal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        committed = extract_polars_io.extract_schema()
        committed["polars_version"] = "0.0.0-committed"
        snapshot = tmp_path / "committed.json"
        snapshot.write_text(json.dumps(committed), encoding="utf-8")
        monkeypatch.setattr(extract_polars_io, "DEFAULT_OUTPUT", snapshot)

        broken = extract_polars_io.extract_schema()
        for fn in broken["functions"]:
            if fn["owner"] == "polars" and fn["name"] == "read_csv":
                fn["arguments"] = []
                fn["introspection_error"] = "ValueError: no signature found"
        monkeypatch.setattr(extract_polars_io, "extract_schema", lambda: broken)

        report = "\n".join(extract_polars_io.diff_report())
        assert "could not be introspected" in report
        assert "no longer declares" not in report
