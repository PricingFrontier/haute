**P2 — Seeding prepared models introduces shared mutable reports.**  
At [_paths.py:311](/home/ralph/suite/t-boost/python/t_boost/_paths.py:311), the seeded estimator shares nested report objects with candidate diagnostics. Smoothing also shares report feature lists between candidates at [_paths.py:421](/home/ralph/suite/t-boost/python/t_boost/_paths.py:421).

I reproduced both failures:

- Mutating `result.model.pruning_report_["banding"]` or `graduation_validation_` changes the candidate’s diagnostics and `to_bytes()`, through both `candidate.result()` and `session.apply(candidate)`.
- Appending to one smoothing result’s `graduation_report_[0]["features"]` changes another candidate’s result report.

Detach mutable reports before transferring model ownership, and independently own candidate diagnostics. Extend the seed test to mutate nested reports and verify candidate bytes, diagnostics, and sibling results remain unchanged. The existing test’s object-identity assertions miss these aliases.

Previous findings:

- **P2.1 addressed:** all five corruption probes are refused; valid available/unavailable evidence and single-bag checkpoints round-trip byte-identically.
- **P2.2 rebuild issue addressed**, but the ownership regression above remains.
- **P2.3 addressed:** public operation decoders round-trip and reject invalid input.
- **P3 addressed:** the spec paragraph matches the typed API.

Read-only review. Three focused tests passed; additional in-memory probes verified the findings above. I did not rerun the full suite or oracle.

REQUEST_CHANGES