"""Record the staged-training oracle from the current (old) t-boost API.

Usage: python oracle_record.py OUT.json

For a matrix of models and operations it records, per stage and per path candidate:
- the stage identity (revision, parent, settings, keep-set, recipe);
- hashes of the working bank, inference document and native inference bytes;
- hashes of raw-score bytes (predict_raw, or decision_function for classifiers) and table_export on fixed scoring rows;
- point metadata, and the revision a candidate gets when applied to a fork.

The fit clock is pinned, so documents and revisions are reproducible across runs.
The replacement API is checked against this record by a separate script
(oracle_compare.py), written against the new API.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
import types

import numpy as np
import polars as pl

import t_boost.sklearn as _sk

# Pin the fit clock: `fitted_at_` and `fit_duration_` are the only per-run fields.
_FIXED = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)


class _Clock(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return _FIXED


_sk.datetime = _Clock
_sk.time = types.SimpleNamespace(perf_counter=lambda: 0.0, **{
    k: getattr(_sk.time, k) for k in dir(_sk.time) if not k.startswith("_") and k != "perf_counter"})

from t_boost import TBoostClassifier, TBoostRegressor  # noqa: E402


def h(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def jh(value) -> str:
    return h(json.dumps(value, sort_keys=True, allow_nan=True, default=str))


def data(n: int, binary: bool, seed: int):
    rng = np.random.default_rng(seed)
    x0, x1, x2 = rng.normal(size=n), rng.uniform(0, 10, n), rng.integers(0, 6, n).astype(float)
    c0 = rng.choice(["a", "b", "c", "d", "e"], n)
    c1 = rng.choice(["p", "q", "r"], n, p=[0.6, 0.3, 0.1])
    eta = 0.4 * x0 + 0.08 * x1 - 0.1 * x2 + (c0 == "b") * 0.5 - (c1 == "r") * 0.4 + 0.2 * x0 * (c1 == "q")
    frame = pl.DataFrame({"x0": x0, "x1": x1, "x2": x2, "c0": c0, "c1": c1})
    exposure = rng.uniform(0.2, 1.0, n)
    if binary:
        y = (rng.uniform(size=n) < 1 / (1 + np.exp(-eta))).astype(float)
    else:
        y = rng.poisson(exposure * np.exp(eta - 1.0)).astype(float)
    return frame, y, exposure, rng.uniform(0.5, 2.0, n), rng.integers(0, 40, n)


COMMON = dict(n_trees=150, max_interaction_order=2, n_bags=4, seed=7, categorical_features=["c0", "c1"])
CASES = {
    "poisson_exposure_monotone_j1": dict(kind="reg", objective="poisson", n_jobs=1, exposure=True,
                                         monotone_constraints={"x1": 1}),
    "poisson_exposure_monotone_j4": dict(kind="reg", objective="poisson", n_jobs=4, exposure=True,
                                         monotone_constraints={"x1": 1}),
    "squared_error_weights_groups": dict(kind="reg", objective="squared_error", n_jobs=4, weights=True,
                                         groups=True),
    "binary_categoricals": dict(kind="clf", n_jobs=4),
}


def fit(case: dict):
    frame, y, exposure, weights, groups = data(6000, case["kind"] == "clf", 11)
    params = dict(COMMON, n_jobs=case["n_jobs"])
    if "objective" in case:
        params["objective"] = case["objective"]
    if "monotone_constraints" in case:
        params["monotone_constraints"] = case["monotone_constraints"]
    estimator = (TBoostClassifier if case["kind"] == "clf" else TBoostRegressor)(**params)
    kwargs = {}
    if case.get("exposure"):
        kwargs["exposure"] = exposure
    if case.get("weights"):
        kwargs["sample_weight"] = weights
    if case.get("groups"):
        kwargs["groups"] = groups.astype(str)
    session = estimator.fit_staged(frame, y, **kwargs)
    score, _, score_exposure, _, _ = data(800, case["kind"] == "clf", 99)
    return session, score, score_exposure if case.get("exposure") else None


def model_record(model, score, score_exposure) -> dict:
    scores = model.decision_function if hasattr(model, "decision_function") else model.predict_raw
    raw = np.ascontiguousarray(scores(score))
    export = model.table_export(score, exposure=score_exposure) if score_exposure is not None \
        else model.table_export(score)
    return dict(native=h(bytes(model._model.to_bytes())), inference=h(model.to_json()),
                raw_scores=h(raw.tobytes()), table_export=jh(export))


def stage_record(session, name: str, score, score_exposure) -> dict:
    info = session.stages[name]
    snap = session._stages[name]
    return dict(revision=info["revision"], parent=info["parent"], parent_revision=info["parent_revision"],
                settings=info["settings"], keep=snap.keep, recipe=session.recipe(name),
                working=h(snap.working), inference_doc=h(snap.inference),
                **model_record(session.model(name), score, score_exposure))


def family_record(session, family, score, score_exposure) -> dict:
    points = []
    for point in family.points:
        entry = {k: point[k] for k in ("id", "stage", "identity", "status", "revision", "settings")}
        entry["diagnostics"] = jh(point["diagnostics"])
        entry["outcome_id"] = point["diagnostics"].get("outcome_id")
        snapshot = family._point(point["id"]).snapshot
        entry["keep"] = snapshot.keep
        if not point["identity"]:
            entry["working"] = h(snapshot.working)
            entry["inference_doc"] = h(snapshot.inference)
            entry.update(model_record(family.model(point["id"]), score, score_exposure))
        branch = session.fork()
        family.apply(branch, point["id"])
        entry["applied_stages"] = {k: v["revision"] for k, v in branch.stages.items()}
        points.append(entry)
    return dict(path_id=family.path_id, points=points)


def record_case(name: str, case: dict) -> dict:
    session, score, sx = fit(case)
    out: dict = {"stages": {}, "families": {}, "recipes": {}}
    out["pruning_path"] = jh(session.pruning_path)
    out["data_digest"], out["evidence_digest"] = session.data_digest, session.evidence_digest
    out["selection"] = session.pruning_selection()
    out["budgets"] = session.pruning_budgets()
    stages = out["stages"]
    stages["full"] = stage_record(session, "full", score, sx)

    # A: the default chain.
    a = session.fork()
    a.prune(); a.band(parent="pruned"); a.smooth(parent="banded")
    for stage in ("pruned", "banded", "smoothed"):
        stages[f"A/{stage}"] = stage_record(a, stage, score, sx)

    # B: a table budget, a band on full with an integer tolerance, manual smoothing on pruned.
    b = session.fork()
    b.prune(max_tables=max(1, out["budgets"][len(out["budgets"]) // 2]))
    b.band(parent="full", tolerance=1, deviance_cap=0.002)
    b.smooth(parent="pruned", alpha=0.5)
    for stage in ("pruned", "banded", "smoothed"):
        stages[f"B/{stage}"] = stage_record(b, stage, score, sx)

    # C: non-default path settings, and Auto strength 2 (dispatched through a path).
    c = session.fork()
    c.configure_path(steps=8, main_effects=True, min_rows=50)
    out["pruning_path_C"] = jh(c.pruning_path)
    c.prune(); c.smooth(parent="pruned", strength=2.0)
    for stage in ("pruned", "smoothed"):
        stages[f"C/{stage}"] = stage_record(c, stage, score, sx)

    # D: replaying A's recipe on a fresh fork.
    d = session.fork()
    recipe = a.recipe("smoothed")
    d.apply_recipe(recipe)
    out["recipes"]["A"] = recipe
    stages["D/smoothed"] = stage_record(d, "smoothed", score, sx)

    # Families, prepared on A's chain and on full.
    fam = out["families"]
    fam["pruned_default"] = family_record(a, a.prepare_path("pruned"), score, sx)
    fam["pruned_budgets"] = family_record(a, a.prepare_path("pruned", values=sorted({out["budgets"][0], out["budgets"][-1], out["budgets"][0] + 1})), score, sx)
    fam["banded_pruned"] = family_record(a, a.prepare_path("banded", parent="pruned"), score, sx)
    fam["banded_full_int"] = family_record(a, a.prepare_path(
        "banded", parent="full", settings=dict(tolerance=1, deviance_cap=0.002)), score, sx)
    fam["smoothed_auto"] = family_record(a, a.prepare_path("smoothed", parent="banded"), score, sx)
    fam["smoothed_auto_strength3"] = family_record(a, a.prepare_path(
        "smoothed", parent="banded", settings=dict(strength=3.0)), score, sx)
    fam["smoothed_manual"] = family_record(a, a.prepare_path(
        "smoothed", parent="pruned", settings=dict(alpha=0.3), values=[0.5, 2.0]), score, sx)
    fam["pruned_path_C"] = family_record(c, c.prepare_path("pruned", values=[2, 5]), score, sx)
    out["statuses"] = sorted({p["status"] for f in fam.values() for p in f["points"]})
    return out


def main() -> None:
    result = {}
    for name, case in CASES.items():
        result[name] = record_case(name, case)
        print(name, "statuses", result[name]["statuses"], flush=True)
    # Reproducibility: refitting a case yields the same identities.
    again = record_case("poisson_exposure_monotone_j1", CASES["poisson_exposure_monotone_j1"])
    assert again == result["poisson_exposure_monotone_j1"], "oracle is not reproducible"
    j1, j4 = result["poisson_exposure_monotone_j1"], result["poisson_exposure_monotone_j4"]
    same = [k for k in j1["stages"] if j1["stages"][k]["native"] == j4["stages"][k]["native"]]
    print("n_jobs 1 vs 4: native-equal stages", len(same), "of", len(j1["stages"]))
    with open(sys.argv[1], "w") as f:
        json.dump(result, f, indent=1, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
