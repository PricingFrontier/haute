"""ISOLATED reproduction for NEWBUG-01.

Claim: ModellingConfig's synchronous / immediate-error training path calls
`completeTrainJob(nodeId, result)` WITHOUT a prior `startTrainJob`, so inside
`completeTrainJob` the active `job` is undefined and the cached result is stored
with `configHash: job?.configHash ?? ""` (useNodeResultsStore.ts:942).

Downstream, `useStaleConfigEstimate` (useStaleConfigEstimate.ts:60) computes
    isStale = !!cachedResult && cachedResult.configHash !== hashConfig(config)

Because `hashConfig` of any (even empty) config is a non-empty djb2 string, the
stored "" can never equal the live config hash, so `isStale` is TRUE the instant
a just-computed result lands -> the "Config changed since last training" banner
(TrainingActionsAndResults.tsx:101-104) is shown for a result computed for
EXACTLY the current config.

This script ports the three pure functions verbatim from the TS source and
drives the same predicate the UI uses. NO project src/tests are imported.
Pure in-memory data only.

Run: uv run python review/03-simplification/repro/frontend__NEWBUG-01.py
"""

import json


# ── Verbatim port: djb2 (useNodeResultsStore.ts:168-174) ──
def djb2(s: str) -> str:
    h = 5381
    for ch in s:
        # ((h << 5) + h + charCode) | 0  -> wrap to signed 32-bit like JS `| 0`
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    # (hash >>> 0).toString(36) -> unsigned 32-bit, base36
    u = h & 0xFFFFFFFF

    def to_base36(n: int) -> str:
        if n == 0:
            return "0"
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while n:
            n, r = divmod(n, 36)
            out = digits[r] + out
        return out

    return to_base36(u)


# ── Verbatim port: hashConfig (useNodeResultsStore.ts:176-187) ──
_INTERNAL_KEYS = {"_nodeId", "_columns", "_schemaWarnings", "_availableColumns"}


def _sort_keys(o):
    if o is None or not isinstance(o, (dict, list)):
        return o
    if isinstance(o, list):
        return [_sort_keys(x) for x in o]
    return {k: _sort_keys(o[k]) for k in sorted(o.keys())}


def hash_config(config: dict) -> str:
    rest = {k: v for k, v in config.items() if k not in _INTERNAL_KEYS}
    # JSON.stringify with no spaces == json.dumps(separators=(",", ":"))
    canonical = json.dumps(_sort_keys(rest), separators=(",", ":"))
    return djb2(canonical)


# ── Verbatim port: completeTrainJob's configHash assignment ──
# useNodeResultsStore.ts:942 `configHash: job?.configHash ?? ""`
def complete_train_job_config_hash(active_job: dict | None) -> str:
    return (active_job or {}).get("configHash", "") if active_job else ""


# ── Verbatim port: useStaleConfigEstimate isStale (line 60) ──
def is_stale(cached_result: dict | None, live_config: dict) -> bool:
    config_hash = hash_config(live_config)
    return bool(cached_result) and cached_result["configHash"] != config_hash


def main() -> None:
    failures = []

    # Sanity: djb2/hashConfig never produce the empty string, even for {}.
    assert hash_config({}) != "", "hashConfig({}) unexpectedly empty"
    print(f"hashConfig({{}})                       = {hash_config({})!r}")

    # ── Scenario: user just ran a synchronous training for THIS config ──
    # ModellingConfig.handleTrain -> sync branch (ModellingConfig.tsx:148) calls
    # completeTrainJob(nodeId, result) with NO startTrainJob first => job undefined.
    live_config = {
        "algorithm": "glm",
        "target": "loss",
        "weight": "exposure",
        "split": {"strategy": "random", "validation_size": 0.2, "seed": 42},
        "metrics": ["gini", "rmse"],
    }
    active_job = None  # the sync/error path never registered a job

    stored_config_hash = complete_train_job_config_hash(active_job)
    print(f"stored configHash (sync completion)   = {stored_config_hash!r}")

    cached_result = {"configHash": stored_config_hash, "result": {"status": "completed"}}

    # The live config hash is exactly what handleTrain passed as currentConfigHash;
    # the result was computed for THIS config, so the banner must NOT show.
    live_hash = hash_config(live_config)
    print(f"live hashConfig(config)               = {live_hash!r}")

    stale_now = is_stale(cached_result, live_config)
    print(f"isStale immediately after sync train  = {stale_now}  (SHOULD be False)")

    if stale_now is not True:
        failures.append(
            "Expected isStale==True (the bug); got "
            f"{stale_now}. Bug NOT reproduced."
        )
    # The specific wrong value: stored hash is "" while live hash is non-empty.
    if stored_config_hash != "":
        failures.append(
            f"Expected stored configHash=='' on direct completion; got {stored_config_hash!r}"
        )

    # ── Control: async (JobStore) completion carries the real job hash ──
    # useBackgroundJobs.ts:109 completeTrainJob runs AFTER startTrainJob registered
    # job.configHash == hashConfig(config) (ModellingConfig.tsx:142).
    async_job = {"configHash": hash_config(live_config)}
    async_stored = complete_train_job_config_hash(async_job)
    async_cached = {"configHash": async_stored}
    async_stale = is_stale(async_cached, live_config)
    print(f"isStale after ASYNC train (control)   = {async_stale}  (correctly False)")
    if async_stale is not False:
        failures.append(
            "Control failed: async completion should be fresh (isStale False) but "
            f"got {async_stale}"
        )

    print()
    if failures:
        for f in failures:
            print("SETUP/ASSERT PROBLEM:", f)
        raise SystemExit(1)

    print(
        "BUG REPRODUCED: a synchronously-completed training result is stored with "
        "configHash='' and is reported isStale=True for the exact config it was "
        "computed for, while the equivalent async result is correctly fresh."
    )


if __name__ == "__main__":
    main()
