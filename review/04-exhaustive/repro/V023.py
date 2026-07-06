"""Reproduction for V023.

Claim: ``_source_load_boilerplate_end_index`` (src/haute/_code_extraction.py
:481-497) silently drops the FIRST user statement when a DataSource body has no
recognized source-load line.

Mechanism: the scanner skips leading import lines and source-load statements.
If it never sees a source-load statement (``saw_source_load`` stays False) but
``idx`` is still in range, the final branch returns
``_statement_end_index(cleaned, idx)`` (the END index of the first user
statement) instead of ``idx`` (its START). That makes ``_match_source`` report
the first real user statement as boilerplate, so ``extract_user_code`` slices it
off via ``cleaned[start_idx:]`` BEFORE the source-load finaliser's
``has_source_load`` guard ever runs.

This repro ISOLATES on the pure, in-memory string function
``_extract_source_user_code`` (no disk, no project root). It ASSERTS the
specific WRONG VALUES for the three claimed scenarios, and contrasts against:
  * the codegen-side sibling ``_strip_source_load_boilerplate_from_code`` which
    guards ``has_source_load`` and returns the code UNCHANGED, and
  * ``_extract_external_user_code`` (the externalFile path) which rewinds to the
    first import when no load is present.
Both correctly preserve the no-load body; only the source matcher regresses.
"""

import sys

from haute._code_extraction import (
    _extract_external_user_code,
    _extract_source_user_code,
    _source_load_boilerplate_end_index,
    _strip_source_load_boilerplate_from_code,
)


def main() -> None:
    failures: list[str] = []

    # ------------------------------------------------------------------ #
    # Direct probe of the index helper. With no source-load line and a
    # real first statement, the contract-correct start index is 0 (keep
    # everything). The bug returns the END index of cleaned[0] instead.
    # ------------------------------------------------------------------ #
    cleaned = ["a = 1", "df = df.with_columns(x=a)", "return df"]
    end_idx = _source_load_boilerplate_end_index(cleaned)
    print(f"[probe] _source_load_boilerplate_end_index({cleaned!r}) = {end_idx}")
    assert end_idx == 1, (
        "expected the buggy index helper to return 1 (END of first stmt); "
        f"got {end_idx} — bug mechanism changed"
    )
    if end_idx != 0:
        failures.append(
            f"index helper returns {end_idx} for a no-load body; correct value is 0 "
            f"(keep all user code). cleaned[0]={cleaned[0]!r} is wrongly classed as "
            "boilerplate."
        )

    # ------------------------------------------------------------------ #
    # Scenario (b): multi-statement no-load body. The first statement
    # `a = 1` is dropped, and the surviving line references an undefined
    # `a` -> the extracted snippet is broken code.
    # ------------------------------------------------------------------ #
    body_b = "    a = 1\n    df = df.with_columns(x=a)\n    return df"
    result_b = _extract_source_user_code(body_b)
    print(f"[scenario b] _extract_source_user_code(...) -> {result_b!r}")
    expected_b = "a = 1\ndf = df.with_columns(x=a)"
    if result_b != expected_b:
        failures.append(
            f"multi-statement no-load body: expected {expected_b!r}, got {result_b!r} "
            "(first statement `a = 1` dropped; surviving code references undefined `a`)"
        )
    assert "a = 1" not in result_b, (
        "BUG NOT PRESENT: `a = 1` survived extraction — first statement kept"
    )

    # ------------------------------------------------------------------ #
    # Scenario (a): single-statement no-load body -> ALL user code lost.
    # ------------------------------------------------------------------ #
    body_a = '    """d"""\n    df = df.with_columns(x=1)\n    return df'
    result_a = _extract_source_user_code(body_a)
    print(f"[scenario a] _extract_source_user_code(...) -> {result_a!r}")
    expected_a = "df = df.with_columns(x=1)"
    if result_a != expected_a:
        failures.append(
            f"single-statement no-load body: expected {expected_a!r}, got {result_a!r} "
            "(ALL user code lost)"
        )

    # ------------------------------------------------------------------ #
    # Scenario (c): user `import` + a single transform -> import treated as
    # boilerplate, then the lone transform skipped -> empty result.
    # ------------------------------------------------------------------ #
    body_c = "    import numpy as np\n    df = df.with_columns(x=np.pi)\n    return df"
    result_c = _extract_source_user_code(body_c)
    print(f"[scenario c] _extract_source_user_code(...) -> {result_c!r}")
    expected_c = "import numpy as np\ndf = df.with_columns(x=np.pi)"
    if result_c != expected_c:
        failures.append(
            f"import + transform no-load body: expected {expected_c!r}, got {result_c!r} "
            "(transform dropped after import skipped)"
        )

    # ------------------------------------------------------------------ #
    # CONTRAST 1 — codegen-side sibling preserves the SAME no-load code.
    # ``_strip_source_load_boilerplate_from_code`` guards has_source_load.
    # ------------------------------------------------------------------ #
    codegen_in = "a = 1\ndf = df.with_columns(x=a)"
    codegen_out = _strip_source_load_boilerplate_from_code(codegen_in)
    print(f"[contrast codegen] _strip_source_load_boilerplate_from_code({codegen_in!r}) -> {codegen_out!r}")
    assert codegen_out == codegen_in, (
        "expected the codegen-side helper to preserve no-load code unchanged; "
        f"got {codegen_out!r}"
    )

    # ------------------------------------------------------------------ #
    # CONTRAST 2 — externalFile path preserves a no-load import + transform.
    # ``_match_external`` rewinds to first_import_idx when no load was seen.
    # ------------------------------------------------------------------ #
    ext_out = _extract_external_user_code(body_c, ["obj"])
    print(f"[contrast external] _extract_external_user_code(import+transform) -> {ext_out!r}")
    assert ext_out == expected_c, (
        "expected the externalFile extractor to preserve the no-load import + "
        f"transform; got {ext_out!r}"
    )

    # ------------------------------------------------------------------ #
    # Verdict.
    # ------------------------------------------------------------------ #
    if failures:
        print("\nV023 REPRODUCED — source matcher drops first user statement on no-load bodies:")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nContrast: codegen-side _strip_source_load_boilerplate_from_code and the "
            "externalFile extractor both PRESERVE the identical no-load code."
        )
    else:
        raise AssertionError("no discrepancies found — V023 not reproduced")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"REPRO ASSERTION FAILED (bug NOT demonstrated as predicted): {exc}", file=sys.stderr)
        sys.exit(1)
