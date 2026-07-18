"""Adversarial repro for claim: preamble-overcapture-aliased-import.

Hypothesis under test
---------------------
`_extract_preamble` (src/haute/_ast_helpers.py:462-508) bounds the user
"preamble" region by:

  * a START sentinel: the LAST source line whose stripped text is in
    ``_STANDARD_IMPORTS = {"import polars as pl", "import haute"}`` (line 459),
  * an END sentinel: the first line that either starts with ``pipeline =`` AND
    contains ``haute.Pipeline`` (the pipeline-start detector, 484-487), or is a
    recognised ``@pipeline.<type>`` decorator.

When haute is imported under an ALIAS, e.g. ``import haute as ht`` with
``pipeline = ht.Pipeline('p')``:

  * ``import haute as ht`` is NOT in ``_STANDARD_IMPORTS`` -> the start sentinel
    only advances to the ``import polars as pl`` line, leaving ``import haute as
    ht`` INSIDE the preamble region.
  * ``pipeline = ht.Pipeline('p')`` does NOT contain the literal substring
    ``haute.Pipeline`` -> the end detector does NOT fire on it, so the
    construction line is ALSO swept into the preamble.

Consequence (round-trip corruption):
codegen (_generate_pipeline_lines, src/haute/codegen.py:711-725) emits the
preamble VERBATIM (719) and then emits its OWN canonical
``pipeline = haute.Pipeline(...)`` line (722). So a saved file ends up with the
pipeline construction DUPLICATED (once aliased from the preamble, once
canonical) plus a duplicated/aliased ``import haute as ht``.

We assert the SPECIFIC wrong VALUES:
  (1) the preamble string returned by `_extract_preamble` CONTAINS
      ``pipeline = ht.Pipeline`` (the construction line was wrongly captured),
      and also contains ``import haute as ht`` (the import was wrongly captured);
  (2) a full parse -> graph_to_code round-trip produces source text in which the
      ``Pipeline(`` construction appears MORE THAN ONCE (duplication), proving
      real round-trip corruption rather than a harmless internal detail.

ISOLATION: no real project files touched. (1) is pure in-memory string work.
(2) uses haute.parser.parse_pipeline_file with an in-memory source via a
tempfile, set under a tempdir project root through haute._sandbox.set_project_root.
sys.path / project-root mutations reverted in finally.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Unit under test (imported exactly as production callers do).
from haute._ast_helpers import _STANDARD_IMPORTS, _extract_preamble

# Sanity-check the precondition the claim relies on: the sentinel set really is
# the two literal lines, with no alias-aware entry.
assert _STANDARD_IMPORTS == {"import polars as pl", "import haute"}, _STANDARD_IMPORTS

# A realistic user file that imports haute under an alias.
ALIASED_SOURCE = (
    "import polars as pl\n"
    "import haute as ht\n"
    "\n"
    "pipeline = ht.Pipeline('p')\n"
    "\n"
    "\n"
    "@pipeline.polars\n"
    "def load() -> pl.DataFrame:\n"
    "    return pl.DataFrame({'a': [1]})\n"
)

# ---------------------------------------------------------------------------
# Part 1 — direct unit assertion on _extract_preamble (claim's repro_strategy)
# ---------------------------------------------------------------------------
preamble = _extract_preamble(ALIASED_SOURCE)
print("=== captured preamble (repr) ===")
print(repr(preamble))

# The construction line must NOT be part of any sane "preamble", yet the
# heuristics sweep it in.
construction_captured = "pipeline = ht.Pipeline" in preamble
import_captured = "import haute as ht" in preamble

print(f"construction line captured into preamble : {construction_captured}")
print(f"aliased import captured into preamble    : {import_captured}")

assert construction_captured, (
    "EXPECTED BUG NOT REPRODUCED: _extract_preamble did NOT capture the "
    "pipeline construction line; got preamble=" + repr(preamble)
)
assert import_captured, (
    "EXPECTED BUG NOT REPRODUCED: _extract_preamble did NOT capture the aliased "
    "import; got preamble=" + repr(preamble)
)

# ---------------------------------------------------------------------------
# Part 2 — full round-trip: prove the duplicated Pipeline construction on save
# ---------------------------------------------------------------------------
from haute import _sandbox
from haute.codegen import graph_to_code
from haute.parser import parse_pipeline_file

_prev_root = getattr(_sandbox, "_PROJECT_ROOT", None)
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _sandbox.set_project_root(root)

        src_path = root / "p.py"
        src_path.write_text(ALIASED_SOURCE, encoding="utf-8")

        graph = parse_pipeline_file(src_path)
        print("\n=== parsed pipeline_name ===")
        print(repr(graph.pipeline_name))
        print("=== graph.preamble (repr) ===")
        print(repr(graph.preamble))

        regenerated = graph_to_code(
            graph,
            pipeline_name=graph.pipeline_name,
            description=graph.pipeline_description,
            preamble=graph.preamble,
            preserved_blocks=graph.preserved_blocks,
        )
        print("\n=== regenerated source ===")
        print(regenerated)

        pipeline_ctor_count = regenerated.count("Pipeline(")
        print(f"\n'Pipeline(' occurrences in regenerated source: {pipeline_ctor_count}")

        assert pipeline_ctor_count >= 2, (
            "EXPECTED ROUND-TRIP CORRUPTION NOT REPRODUCED: the regenerated "
            f"source contains the Pipeline constructor only {pipeline_ctor_count} "
            "time(s); expected >= 2 (duplicated construction line)."
        )

        # And specifically: the aliased construction from the preamble survives
        # verbatim alongside the canonical one.
        assert "pipeline = ht.Pipeline('p')" in regenerated, (
            "EXPECTED: the aliased construction line should be re-emitted "
            "verbatim from the preamble; not found."
        )
finally:
    if _prev_root is not None:
        _sandbox.set_project_root(_prev_root)

print(
    "\nREPRODUCED: aliased import causes _extract_preamble to over-capture the "
    "pipeline construction line, and the parse->codegen round-trip DUPLICATES "
    "the Pipeline construction in the saved file."
)
