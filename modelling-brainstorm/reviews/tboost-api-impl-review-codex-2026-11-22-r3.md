The implementation fixes the ownership bug. I found no copied dict/list attribute that requires shared identity at this boundary; native model identity and inference serialization remain unchanged.

**P2 — The regression test misses seeded-sibling isolation and seeded application.**  
At [test_staged_postfit.py:238](/home/ralph/suite/t-boost/python/tests/test_staged_postfit.py:238), `second.result()` consumes the sibling’s seed, but its captured report is never checked. Line 244 checks a newly reconstructed model instead. Likewise, `first.result()` consumes the first seed before the `apply()` case.

I confirmed that replacing `_detached` with an identity function **still passes this test**, although mutating one retained smoothing result corrupts another retained result’s feature list.

Parameterize `result()` versus `apply()` using fresh families. Retain both seeded results, copy the sibling’s baseline reports, then assert that the same sibling remains unchanged after mutation. The test should fail with `_detached` disabled while diagnostics copying remains enabled.

Four focused tests and 12 independent regressor/classifier ownership probes passed against the current implementation. No files modified; full suite and oracle not rerun.

REQUEST_CHANGES