"""Reproduction for V040.

Claim: ``extract_submodel_calls`` only recognises the single, positional,
non-chained form ``pipeline.submodel("path")``. The documented chained form
``pipeline.submodel("a.py").submodel("b.py")`` and the keyword form
``pipeline.submodel(file="a.py")`` are silently dropped, returning an empty
path list. Because parser.py gates submodel handling on
``if submodel_paths and ...`` (parser.py:184), an empty list means every
referenced submodel — and all of its nodes/edges — silently vanishes from the
parsed graph with no warning.

This repro is pure in-memory AST analysis: ``extract_submodel_calls`` takes an
``ast.Module`` and returns ``list[str]``. No disk I/O, no project root, no
reading of rating/, src/, or tests/ files.

The chaining contract is real: Pipeline.submodel returns ``self``
(src/haute/pipeline.py:480) and is asserted chainable by
tests/test_pipeline.py:786,792.

We assert on the SPECIFIC WRONG VALUE (expected vs actual), not merely that
something raised.
"""

from __future__ import annotations

import ast

from haute._parser_submodels import extract_submodel_calls

failures: list[str] = []


def check(label: str, expected: list[str], actual: list[str]) -> None:
    status = "OK" if actual == expected else "BUG"
    print(f"[{status}] {label}: expected={expected!r} actual={actual!r}")
    if actual != expected:
        failures.append(label)


# --- Baseline: the one-per-line positional form works (sanity) --------------
one_per_line = "pipeline.submodel('modules/a.py')\npipeline.submodel('modules/b.py')\n"
check(
    "one-per-line positional (baseline, should pass)",
    ["modules/a.py", "modules/b.py"],
    extract_submodel_calls(ast.parse(one_per_line)),
)

# --- Bug 1: documented chained form -----------------------------------------
# pipeline.submodel("a.py").submodel("b.py") is the EXACT spelling documented
# in the runtime docstring (pipeline.py:477) and exercised by test_pipeline.py.
chained = "pipeline.submodel('modules/a.py').submodel('modules/b.py')\n"
check(
    "documented chained form (BUG: both submodels dropped)",
    ["modules/a.py", "modules/b.py"],
    extract_submodel_calls(ast.parse(chained)),
)

# --- Bug 2: keyword form ----------------------------------------------------
# Pipeline.submodel's signature is ``def submodel(self, file: str)``, so
# pipeline.submodel(file="a.py") is a valid runtime call.
keyword = "pipeline.submodel(file='modules/a.py')\n"
check(
    "keyword form (BUG: submodel dropped)",
    ["modules/a.py"],
    extract_submodel_calls(ast.parse(keyword)),
)

# --- Guard preservation note ------------------------------------------------
# Any fix MUST keep rejecting module.pipeline.submodel(...) and non-pipeline
# receivers (tests/test_parser_submodels.py:43-56). We confirm those remain
# correctly rejected today so the fix is well-scoped (walk the chain but keep
# requiring the base receiver to be a bare ``ast.Name == "pipeline"``).
check(
    "module.pipeline.submodel(...) correctly rejected (must stay rejected)",
    [],
    extract_submodel_calls(ast.parse("module.pipeline.submodel('path.py')\n")),
)

print()
if failures:
    raise AssertionError(
        "V040 REPRODUCED: extract_submodel_calls silently drops valid submodel "
        f"spellings -> {failures}. Each dropped path discards an entire submodel "
        "(its nodes + edges) from the parsed graph with no warning "
        "(parser.py:184 gates on a non-empty list)."
    )
print("V040 NOT reproduced: all spellings returned the expected paths.")
