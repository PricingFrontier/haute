"""Mutation-killing witness tests for record iteration + inference dispatch.

Targets in ``haute._json_shred``:
  * ``_iter_records``                 (~lines 328-376)
  * ``_iter_records_for_inference``   (~lines 496-507)

Each test is built to fail under a specific Cosmic Ray survivor mutation
while passing on the real implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._json_shred._records import ShredSkipStats, _iter_records, _iter_records_for_inference


# ---------------------------------------------------------------------------
# Line 348:  if stats is not None:   (AddNot -> `if stats is None:`)
# ---------------------------------------------------------------------------
def test_count_record_skip_increments_when_stats_present(tmp_path: Path) -> None:
    """A non-object JSONL line must bump stats.skipped_records.

    Real: stats is not None -> count -> 1.
    Mutant `stats is None`: stats present -> branch False -> no count -> 0.
    """
    p = tmp_path / "data.jsonl"
    # First line is a real object record; second line is a bare number which
    # is valid JSON but not an object -> a skipped record.
    p.write_text('{"a": 1}\n5\n', encoding="utf-8")

    stats = ShredSkipStats()
    records = list(_iter_records(p, stats=stats))

    assert records == [{"a": 1}]
    # Tight: exactly one skip was counted. The `is None` mutant leaves it at 0.
    assert stats.skipped_records == 1


def test_count_record_skip_json_array_non_object(tmp_path: Path) -> None:
    """The whole-file (.json) branch also routes non-object items to the skip
    counter; another witness for line 348 via the list branch (line 372)."""
    p = tmp_path / "data.json"
    p.write_text('[{"a": 1}, 5, "x"]', encoding="utf-8")

    stats = ShredSkipStats()
    records = list(_iter_records(p, stats=stats))

    assert records == [{"a": 1}]
    assert stats.skipped_records == 2


# ---------------------------------------------------------------------------
# Line 351:  if data_path.suffix.lower() == ".jsonl":   (Eq -> GtE)
# ---------------------------------------------------------------------------
def test_suffix_dispatch_jsonl_vs_json_semantics(tmp_path: Path) -> None:
    """JSONL parses line-by-line; non-.jsonl parses the whole file.

    A pretty-printed JSON array spread across multiple lines is valid as a
    whole-file JSON parse (-> 2 records) but each individual line is NOT valid
    JSON, so the JSONL line-parser would raise.

    The suffix ``.jsonx`` is lexically GREATER than ``.jsonl`` but not equal:
      * Real `== ".jsonl"`  -> False -> whole-file JSON branch -> 2 records.
      * Mutant `>= ".jsonl"` -> True  -> JSONL branch -> orjson raises on `[`.
    """
    assert ".jsonx" > ".jsonl"  # guard: the mutant really would misroute here

    p = tmp_path / "data.jsonx"
    p.write_text('[\n  {"a": 1},\n  {"b": 2}\n]', encoding="utf-8")

    records = list(_iter_records(p))

    assert records == [{"a": 1}, {"b": 2}]


def test_suffix_dispatch_jsonl_is_line_oriented(tmp_path: Path) -> None:
    """Positive companion: a real .jsonl file yields one record per line.

    This content is NOT a single valid JSON document (two objects, no array),
    so it only succeeds under the line-oriented JSONL branch -> nails the `==`
    True case as genuinely the JSONL path."""
    p = tmp_path / "data.jsonl"
    p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    records = list(_iter_records(p))

    assert records == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# Line 501:  if sample_size is None or sample_size <= 0:
#            (NumberReplacer 0->1, comparison mutations, None branch)
# ---------------------------------------------------------------------------
def _write_jsonl_three(tmp_path: Path) -> Path:
    p = tmp_path / "three.jsonl"
    p.write_text('{"i": 0}\n{"i": 1}\n{"i": 2}\n', encoding="utf-8")
    return p


def _write_json_three(tmp_path: Path) -> Path:
    p = tmp_path / "three.json"
    p.write_text('[{"i": 0}, {"i": 1}, {"i": 2}]', encoding="utf-8")
    return p


@pytest.mark.parametrize("writer", [_write_jsonl_three, _write_json_three])
def test_inference_sample_none_reads_all(tmp_path: Path, writer) -> None:
    """sample_size=None -> read ALL records (the `is None` half of line 501)."""
    p = writer(tmp_path)
    records = list(_iter_records_for_inference(p, sample_size=None))
    assert len(records) == 3


@pytest.mark.parametrize("writer", [_write_jsonl_three, _write_json_three])
def test_inference_sample_zero_reads_all(tmp_path: Path, writer) -> None:
    """sample_size=0 -> read ALL (the `<= 0` half: 0 satisfies, so all)."""
    p = writer(tmp_path)
    records = list(_iter_records_for_inference(p, sample_size=0))
    assert len(records) == 3


@pytest.mark.parametrize("writer", [_write_jsonl_three, _write_json_three])
def test_inference_sample_one_caps_count(tmp_path: Path, writer) -> None:
    """sample_size=1 -> cap at exactly 1.

    Real: `1 <= 0` is False -> NOT the read-all branch -> capped to 1.
    Mutant `<= 1` (NumberReplacer 0->1): `1 <= 1` True -> reads ALL -> 3.
    Mutant `< 0` / `is None`-only also misbehave; 1 vs 3 is the discriminator.
    """
    p = writer(tmp_path)
    records = list(_iter_records_for_inference(p, sample_size=1))
    assert len(records) == 1


@pytest.mark.parametrize("writer", [_write_jsonl_three, _write_json_three])
def test_inference_sample_two_caps_count(tmp_path: Path, writer) -> None:
    """sample_size=2 -> cap at exactly 2 (further pins the boundary)."""
    p = writer(tmp_path)
    records = list(_iter_records_for_inference(p, sample_size=2))
    assert len(records) == 2


def test_inference_negative_sample_reads_all(tmp_path: Path) -> None:
    """A negative sample_size hits the `<= 0` branch -> read ALL.

    Real: `-1 <= 0` True -> read all -> 3.
    A comparison flip (e.g. `>= 0`) would make `-1 >= 0` False -> sampled
    branch with a negative cap -> 0 records. 3 vs 0 discriminates.
    """
    p = _write_jsonl_three(tmp_path)
    records = list(_iter_records_for_inference(p, sample_size=-1))
    assert len(records) == 3


# ---------------------------------------------------------------------------
# Line 504:  if data_path.suffix.lower() == ".jsonl":   (Eq cluster)
# ---------------------------------------------------------------------------
def test_inference_suffix_dispatch_uses_sampled_array_reader(tmp_path: Path) -> None:
    """For a positive sample, non-.jsonl uses the *streaming* sampled array
    reader, which stops after `sample_size` records WITHOUT parsing the rest
    of the file. JSONL routing would islice over a whole-file parse and choke
    on the trailing garbage.

    File content: a root array whose first element is a valid object followed
    by invalid trailing bytes.
      * Real `== ".jsonl"` False (suffix is .jsonx) -> sampled array reader ->
        yields the 1 object and returns before reaching the garbage.
      * Mutant `>= ".jsonl"` True -> islice(_iter_records, 1) -> _iter_records
        does a whole-file orjson parse -> raises on the garbage.
    """
    assert ".jsonx" > ".jsonl"  # guard

    p = tmp_path / "data.jsonx"
    p.write_text('[{"a": 1}, @@@not-json@@@]', encoding="utf-8")

    records = list(_iter_records_for_inference(p, sample_size=1))

    assert records == [{"a": 1}]


def test_inference_suffix_dispatch_jsonl_islice_path(tmp_path: Path) -> None:
    """Positive companion for line 504: a real .jsonl file with a positive
    sample uses islice over the line reader.

    The content is two separate objects (not a single JSON doc); only the
    line-oriented JSONL branch can yield them. We cap at 1 to also show the
    islice cap is active on that branch.
    """
    p = tmp_path / "data.jsonl"
    p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    records = list(_iter_records_for_inference(p, sample_size=1))

    assert records == [{"a": 1}]
