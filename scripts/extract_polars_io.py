#!/usr/bin/env python3
"""Extract the polars I/O argument schema by introspection.

Version-agnostic: enumerates I/O callables heuristically from the live
``polars`` package (no hardcoded signatures), captures per-argument metadata
via ``inspect.signature``, cross-checks each argument against the numpydoc
"Parameters" section of the docstring, and emits deterministic sorted JSON.

The committed schema lives at ``src/haute/_polars_io_arguments.json`` and is
the interface contract the data-input/data-output node machinery validates
against. ``tests/test_polars_io_interface_contracts.py`` re-runs this
extraction against the installed polars and fails on any argument-level
drift, with instructions to re-run this script and review the diff.

Usage:
    uv run python scripts/extract_polars_io.py [output.json]

Run from the repo root; the default output path is the committed schema.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars

# ---------------------------------------------------------------------------
# Heuristic: what counts as an I/O callable.
# Transparent include/exclude lists; everything else is prefix-driven.
# ---------------------------------------------------------------------------

# Module-level functions on the `polars` namespace whose name starts with one
# of these prefixes are considered I/O candidates.
MODULE_PREFIXES = ("read_", "scan_", "from_", "sink_")

# DataFrame methods with these prefixes (plus explicit names below) are
# considered I/O candidates.
DATAFRAME_PREFIXES = ("write_", "to_")
DATAFRAME_EXPLICIT = ("serialize",)  # binary/JSON serialization is I/O

# LazyFrame: sink_* stream to external storage; serialize round-trips the
# plan. collect/collect_async/collect_batches/collect_schema are compute /
# schema resolution, not external I/O, and are excluded (see EXCLUDE below).
LAZYFRAME_PREFIXES = ("sink_",)
LAZYFRAME_EXPLICIT = ("serialize",)

# Names matching a prefix above but which are NOT I/O. Keyed by owner.
EXCLUDE: dict[str, frozenset[str]] = {
    "polars": frozenset(
        {
            "from_epoch",  # int->datetime conversion on Series/Expr, not data ingestion
        }
    ),
    "DataFrame": frozenset(
        {
            "to_dummies",  # one-hot encoding transform
            "to_series",  # column extraction
            "to_struct",  # column packing transform
            "to_init_repr",  # code-generation helper, not data interchange
        }
    ),
    "LazyFrame": frozenset(),
}


def _is_io_name(
    name: str,
    prefixes: tuple[str, ...],
    explicit: tuple[str, ...],
    excluded: frozenset[str],
) -> bool:
    if name.startswith("_") or name in excluded:
        return False
    return name in explicit or any(name.startswith(p) for p in prefixes)


def enumerate_io_callables() -> list[tuple[str, str, Any]]:
    """Return sorted list of (owner, name, callable)."""
    found: list[tuple[str, str, Any]] = []

    for name in dir(polars):
        obj = getattr(polars, name)
        if (
            callable(obj)
            and not inspect.isclass(obj)
            and _is_io_name(name, MODULE_PREFIXES, (), EXCLUDE["polars"])
        ):
            found.append(("polars", name, obj))

    for name in dir(polars.DataFrame):
        obj = getattr(polars.DataFrame, name)
        if callable(obj) and _is_io_name(
            name, DATAFRAME_PREFIXES, DATAFRAME_EXPLICIT, EXCLUDE["DataFrame"]
        ):
            found.append(("DataFrame", name, obj))

    for name in dir(polars.LazyFrame):
        obj = getattr(polars.LazyFrame, name)
        if callable(obj) and _is_io_name(
            name, LAZYFRAME_PREFIXES, LAZYFRAME_EXPLICIT, EXCLUDE["LazyFrame"]
        ):
            found.append(("LazyFrame", name, obj))

    return sorted(found, key=lambda t: (t[0], t[1]))


# ---------------------------------------------------------------------------
# Docstring parsing: numpydoc "Parameters" section.
# ---------------------------------------------------------------------------

_SECTION_UNDERLINE = re.compile(r"^\s*-{3,}\s*$")
# numpydoc param line: "name : type" | "name1, name2 : type" | bare "name"
_PARAM_LINE = re.compile(r"^(?P<names>\*{0,2}\w+(?:\s*,\s*\*{0,2}\w+)*)\s*(?::.*)?$")


def parse_documented_params(docstring: str | None) -> tuple[bool, set[str]]:
    """Return (has_parameters_section, set of documented parameter names)."""
    if not docstring:
        return False, set()
    lines = inspect.cleandoc(docstring).splitlines()

    # Locate "Parameters" header followed by a dashed underline.
    start = None
    for i in range(len(lines) - 1):
        if lines[i].strip() == "Parameters" and _SECTION_UNDERLINE.match(lines[i + 1]):
            start = i + 2
            break
    if start is None:
        return False, set()

    documented: set[str] = set()
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        # Next section header ("Returns", "Examples", ...) at indent 0
        # followed by dashes ends the Parameters section.
        if indent == 0 and i + 1 < len(lines) and _SECTION_UNDERLINE.match(lines[i + 1]):
            break
        if indent == 0:
            m = _PARAM_LINE.match(stripped)
            if m:
                for nm in m.group("names").split(","):
                    documented.add(nm.strip().lstrip("*"))
        i += 1
    return True, documented


# ---------------------------------------------------------------------------
# Signature extraction.
# ---------------------------------------------------------------------------


def get_signature(func: Any) -> tuple[inspect.Signature | None, str | None]:
    """inspect.signature with eval_str, falling back to string annotations."""
    try:
        return inspect.signature(func, eval_str=True), None
    except Exception:  # noqa: BLE001, S110 - fall through to string annotations
        pass
    try:
        return inspect.signature(func), None
    except Exception as exc:  # builtins with no signature, etc.
        return None, f"{type(exc).__name__}: {exc}"


_MEM_ADDR = re.compile(r" at 0x[0-9a-fA-F]+")


def default_repr(value: Any) -> str:
    """repr() with memory addresses stripped, for run-to-run determinism."""
    return _MEM_ADDR.sub("", repr(value))


def annotation_str(ann: Any) -> str | None:
    if ann is inspect.Parameter.empty:
        return None
    if isinstance(ann, str):
        return ann
    return inspect.formatannotation(ann)


def extract_function(owner: str, name: str, func: Any) -> dict[str, Any]:
    qualname = getattr(func, "__qualname__", f"{owner}.{name}")
    kind = "module_function" if owner == "polars" else f"{owner}_method"
    record: dict[str, Any] = {
        "name": name,
        "owner": owner,
        "qualified_name": f"polars.{qualname}" if not qualname.startswith("polars") else qualname,
        "kind": kind,
    }

    sig, err = get_signature(func)
    doc = inspect.getdoc(func)
    has_params_section, documented = parse_documented_params(doc)
    record["doc_has_parameters_section"] = has_params_section

    if sig is None:
        record["introspection_error"] = err
        record["arguments"] = []
        return record

    args = []
    pos = 0
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        has_default = param.default is not inspect.Parameter.empty
        args.append(
            {
                "name": pname,
                "kind": param.kind.name,
                "position": pos,
                "annotation": annotation_str(param.annotation),
                "has_default": has_default,
                "default": default_repr(param.default) if has_default else None,
                # Undocumented = present in the signature but absent from the
                # docstring's Parameters section (VAR_* params are conventionally
                # documented as *args/**kwargs; we strip the stars on both sides).
                "undocumented": pname not in documented,
            }
        )
        pos += 1

    record["arguments"] = args
    record["return_annotation"] = annotation_str(sig.return_annotation)
    return record


def extract_schema() -> dict[str, Any]:
    """Extract the full I/O argument schema from the installed polars."""
    functions = [
        extract_function(owner, name, func) for owner, name, func in enumerate_io_callables()
    ]
    functions.sort(key=lambda f: (f["owner"], f["name"]))
    return {
        "polars_version": polars.__version__,
        "python_version": sys.version.split()[0],
        "extracted_at": datetime.now(UTC).isoformat(),
        "function_count": len(functions),
        "functions": functions,
    }


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "src" / "haute" / "_polars_io_arguments.json"


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    payload = extract_schema()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    functions = payload["functions"]
    n_undoc = sum(1 for f in functions for a in f["arguments"] if a["undocumented"])
    n_err = sum(1 for f in functions if "introspection_error" in f)
    print(
        f"polars {polars.__version__}: {len(functions)} I/O callables, "
        f"{n_undoc} undocumented arguments, {n_err} introspection failures"
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
