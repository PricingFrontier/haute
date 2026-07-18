"""V052 verification repro.

Claim under test (V052):
  fallback_parse (src/haute/_parser_regex.py:759-767) builds the returned
  PipelineGraph WITHOUT extracting `# haute:preserve-start/end` blocks,
  whereas the healthy parser (parser.py:165-176) does. The finding asserts
  this causes *data loss on the next GUI save*, because codegen reads
  graph.preserved_blocks (codegen.py:932) and emits them.

This repro establishes, by execution against the real code:

  PART A (parser-level discrepancy — the verifiable core of the claim):
    A1. Healthy parser populates graph.preserved_blocks for a valid file.
    A2. graph_to_code round-trips the block on the healthy path.
    A3. fallback_parse returns preserved_blocks == [] for the SAME content
        with a syntax error injected elsewhere (the preserve block itself
        intact and recoverable). => discrepancy CONFIRMED.
    A4. graph_to_code(fallback_graph) drops the preserved block. => the
        codegen consequence the finding describes is real at that layer.

  PART B (the finding's *data-loss-on-save* causal mechanism):
    The finding's mechanism presumes that if the parser HAD populated
    preserved_blocks, the GUI save would have preserved them. We reproduce
    the EXACT payload the GUI sends on save (frontend client.ts savePipeline
    + handleSave usePipelineAPI.ts:762-770): graph carries only
    {nodes, edges, submodels} and there is NO top-level preserved_blocks.
    We show that even starting from the HEALTHY graph (preserved_blocks
    populated), codegen via the save path drops the block. => the GUI save
    round-trip itself is the loss point; the fallback parser's omission is
    NOT the operative cause of save-time data loss.
"""

from __future__ import annotations

import ast

from haute.codegen import graph_to_code
from haute.parser import parse_pipeline_source

PRESERVE = "MAGIC_CONSTANT = 4242"

VALID_SOURCE = "\n".join(
    [
        "import polars as pl",
        "import haute",
        "",
        'pipeline = haute.Pipeline("p", description="d")',
        "",
        "# haute:preserve-start",
        PRESERVE,
        "# haute:preserve-end",
        "",
        "@pipeline.polars",
        "def source() -> pl.LazyFrame:",
        '    """Source."""',
        '    return pl.LazyFrame({"x": [1]})',
        "",
    ]
)

# Same content, but a self-contained broken statement is appended at module
# level (dedented, AFTER a complete & valid decorated function). This forces
# ast.parse to fail -> regex fallback path -> while keeping the decorated
# function fully recoverable (no _load_error) so the node still codegens.
# The preserve block at module level is byte-for-byte identical and fully
# recoverable by the text-only _extract_preserved_blocks scanner.
BROKEN_SOURCE = "\n".join(
    [
        "import polars as pl",
        "import haute",
        "",
        'pipeline = haute.Pipeline("p", description="d")',
        "",
        "# haute:preserve-start",
        PRESERVE,
        "# haute:preserve-end",
        "",
        "@pipeline.polars",
        "def source() -> pl.LazyFrame:",
        '    """Source."""',
        '    return pl.LazyFrame({"x": [1]})',
        "",
        "broken ==== syntax error here",  # module-level SyntaxError, function intact
        "",
    ]
)


def main() -> None:
    # Sanity: the "valid" source really parses and the "broken" source really
    # does not (so we genuinely exercise the two parser paths, not a typo).
    ast.parse(VALID_SOURCE)
    broken_raises = False
    try:
        ast.parse(BROKEN_SOURCE)
    except SyntaxError:
        broken_raises = True
    assert broken_raises, "BROKEN_SOURCE was expected to be syntactically invalid"

    # ---- PART A: parser-level discrepancy ---------------------------------
    healthy = parse_pipeline_source(VALID_SOURCE)
    assert healthy.warning is None or "regex fallback" not in (healthy.warning or ""), (
        f"expected the HEALTHY (AST) path; got warning={healthy.warning!r}"
    )
    # A1
    assert healthy.preserved_blocks == [PRESERVE], (
        f"A1 FAILED: healthy parser should populate preserved_blocks; "
        f"got {healthy.preserved_blocks!r}"
    )
    # A2: healthy round-trip re-emits the block
    healthy_code = graph_to_code(healthy, pipeline_name="p", description="d")
    assert PRESERVE in healthy_code, "A2 FAILED: healthy codegen dropped the preserved block"
    assert "# haute:preserve-start" in healthy_code

    fallback = parse_pipeline_source(BROKEN_SOURCE)
    assert "regex fallback" in (fallback.warning or ""), (
        f"expected the FALLBACK path; got warning={fallback.warning!r}"
    )
    # A3: THE BUG — fallback graph has NO preserved blocks even though the
    # block content was intact and recoverable.
    assert fallback.preserved_blocks == [], (
        f"A3: expected fallback_parse to (buggily) return [] preserved_blocks; "
        f"got {fallback.preserved_blocks!r}"
    )
    print(
        "[A3] CONFIRMED parser discrepancy: "
        f"healthy.preserved_blocks={healthy.preserved_blocks!r} "
        f"vs fallback.preserved_blocks={fallback.preserved_blocks!r}"
    )

    # Guard: ensure the decorated node recovered cleanly (no _load_error),
    # so the A4 codegen exercises the preserve-block path rather than failing
    # for an unrelated empty-body reason.
    assert all(
        not n.data.config.get("_load_error") for n in fallback.nodes
    ), f"fallback nodes unexpectedly carry _load_error: {[n.data.config for n in fallback.nodes]}"

    # A4: codegen on the fallback graph drops the block (the codegen
    # consequence the finding cites at codegen.py:932/728-729).
    fallback_code = graph_to_code(fallback, pipeline_name="p", description="d")
    assert PRESERVE not in fallback_code, (
        "A4: expected the preserved constant to be ABSENT from codegen of the "
        f"fallback graph; unexpectedly present.\n{fallback_code}"
    )
    print("[A4] CONFIRMED: graph_to_code(fallback_graph) drops the preserved block.")

    # ---- PART B: the finding's data-loss-ON-SAVE mechanism ----------------
    # Reproduce EXACTLY what the GUI save sends. The frontend save payload
    # (client.ts savePipeline + usePipelineAPI.ts handleSave) is:
    #   graph: { nodes, edges, submodels }     (NO preserved_blocks)
    #   <no top-level preserved_blocks field>
    # Mirror that here using the SavePipelineRequest schema + the same
    # graph_to_code(..., preserved_blocks=body.preserved_blocks or None)
    # call the save service makes (_save_pipeline.py:527-535).
    from haute.schemas import SavePipelineRequest

    # Start from the HEALTHY graph (preserved_blocks populated) to isolate
    # whether the *save round-trip* loses the block independently of the
    # parser. The GUI rebuilds `graph` from nodes/edges only:
    gui_graph_payload = {
        "nodes": [n.model_dump() for n in healthy.nodes],
        "edges": [e.model_dump() for e in healthy.edges],
        # submodels omitted/None, exactly like a no-submodel save
    }
    body = SavePipelineRequest(
        name="p",
        description="d",
        graph=gui_graph_payload,  # type: ignore[arg-type]
        preamble=healthy.preamble,
        source_file="p.py",
        # NOTE: deliberately NOT setting preserved_blocks — the GUI never does.
    )
    # The body.graph that the backend reconstructs has empty preserved_blocks
    # because the GUI payload never carried them:
    assert body.graph.preserved_blocks == [], (
        f"B-precondition: GUI-shaped body.graph.preserved_blocks expected []; "
        f"got {body.graph.preserved_blocks!r}"
    )
    assert body.preserved_blocks == [], (
        f"B-precondition: GUI never sends top-level preserved_blocks; "
        f"got {body.preserved_blocks!r}"
    )
    # Exact codegen call the save service performs:
    save_code = graph_to_code(
        body.graph,
        pipeline_name=body.name,
        description=body.description,
        preamble=body.preamble or "",
        preserved_blocks=body.preserved_blocks or None,
    )
    assert PRESERVE not in save_code, (
        "B: expected the GUI-shaped save to DROP the preserved block even from "
        f"a HEALTHY graph; unexpectedly present.\n{save_code}"
    )
    print(
        "[B] CONFIRMED: a normal GUI save drops the preserved block even on the "
        "HEALTHY path, because the frontend payload carries no preserved_blocks. "
        "=> The fallback parser's omission is NOT the operative cause of "
        "save-time data loss."
    )

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
