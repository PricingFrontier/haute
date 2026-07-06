# E05 — Binary columns: count natively, decode only the ≤50 survivors

**Severity:** MEDIUM (perf; catastrophic when triggered, rare trigger) · **Effort:** S · **Review:** batch
Files: `src/haute/routes/_explore_service.py` · Tests: `tests/test_explore_routes.py`

## EF-12 [MEDIUM]

### Current behaviour (verified at af3eb2ea; benchmarked on 1.39.2)

`_categorical_value_label_expr` maps Binary columns through a per-row Python UDF:
`pl.col(name).map_elements(_lossy_decode_binary, return_dtype=pl.String)`
(`_explore_service.py:336-350`, UDF `:328-333`), feeding
`value_counts(sort=True).head(50).implode()` (`:353-360`) inside the big batched collect.

- One Python call per row across the Rust/Python boundary, GIL held: 50M calls on a 50M-row frame,
  embedded in the already-heavy all-columns collect.
- Measured on 3M rows / 5 distinct values (incl. invalid UTF-8): UDF decode-then-count **1.56 s**
  vs native count-then-decode **0.27 s** (5.8×). The gap widens with row count.
- The justification comment is factually correct and stays: `cast(pl.String, strict=False)` on
  Binary genuinely raises `ComputeError: invalid utf8` (verified — see CLEARED.md). Only the
  *placement* of the decode is wrong.

### Fix design

Binary is natively hashable — count raw, decode survivors:

1. In `_categorical_value_counts_expr`, run `value_counts(sort=True).head(50).implode()` directly
   on the raw Binary column (drop the label-expr indirection for Binary).
2. Decode in the parse step: `_parse_categorical_value_counts` / `_format_display_value`
   (`:363-380`, `:112-123`) gain a `bytes` arm — `value.decode("utf-8", errors="replace")` — so at
   most 50 decodes happen in Python, outside the plan.
3. **Conscious semantics change, must be stated in the PR:** today the decode happens *before*
   counting, so distinct undecodable byte-strings collapsing to the same replacement text are
   **merged** into one displayed group (CLEARED.md). After this change they count as **separate
   groups** that may *display* identically (`b"\xff"` and `b"\xfe"` both render `�`). That is the
   more truthful representation (they ARE different values); disambiguate identical rendered labels
   by suffixing a short hex tag when collisions occur in the ≤50 set (e.g. `� (0xff)`), so two
   chips never look byte-identical.
4. `min::{name}`/`max::{name}` for Binary stay as they are (Binary is in `_TEXT_DTYPE_BASES` but
   not `_LEXICAL_MIN_MAX_DTYPE_BASES` / `_STRING_MIN_MAX_DTYPE_BASES` — no cast on that path,
   verify with the boundary test).
5. Rejected alternative: `bin.encode("hex")` natively — degrades readable UTF-8 binaries to hex
   soup; decode-survivors is strictly better.

### TDD plan (failing tests first)

1. `test_binary_value_counts_no_udf_in_plan` — build the aggregation lazyframe for a Binary column
   and assert `explain()` contains no `map_elements`/`python` marker. **Fails today.**
2. `test_binary_values_decoded_lossily` — Binary column mixing valid UTF-8 (`b"motor"`) and invalid
   (`b"\xff\xfe"`): chips show `motor` and a U+FFFD-containing label; counts correct.
3. `test_binary_identical_render_disambiguated` — `b"\xff"` and `b"\xfe"` both present: two chips,
   labels not byte-identical (hex-tag rule).
4. Keep/extend the existing Binary regression from the W5 fix (`git log bc96b077` — "Binary
   explore"): whole-report survival with a Binary column, now asserting the faster path.
