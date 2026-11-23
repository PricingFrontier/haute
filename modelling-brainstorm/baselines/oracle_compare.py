"""Check the typed staged API against the oracle recorded from the old API.

Usage: python oracle_compare.py ORACLE.json

Runs oracle_record.py's matrix through the new API and reports every difference. It
also checks what the old API could not: candidate bytes and chain checkpoints survive a
round trip byte for byte, and restored candidates score identically.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import oracle_record as rec  # noqa: E402  (pins the fit clock on import)

from t_boost import (  # noqa: E402
    Band, BandPath, Prune, PrunePath, PruningPathSettings, Smooth, SmoothPath,
    TBoostCandidate, TBoostTrainingSession,
)


def old_recipe(recipe) -> list[dict]:
    return [dict(stage=op.stage, parent=op.parent, settings=op.settings()) for op in recipe.operations]


def stage_record(session, name, score, sx) -> dict:
    snap = session._stages[name]
    info = session.stages[name]
    result = session.stage(name)
    assert result.info == info and result.recipe == session.stage(name).recipe
    return dict(revision=info.revision, parent=info.parent, parent_revision=info.parent_revision,
                settings=snap.settings, keep=snap.keep, recipe=old_recipe(result.recipe),
                working=rec.h(snap.working), inference_doc=rec.h(snap.inference),
                **rec.model_record(result.model, score, sx))


CHECKS = {"candidate_roundtrips": 0, "restored_scores_equal": 0}


def family_record(session, family, score, sx) -> dict:
    points = []
    for candidate in family.candidates:
        meta = candidate.to_plain_data()
        entry = {k: meta[k] for k in ("id", "stage", "identity", "status", "revision", "settings")}
        entry["diagnostics"] = rec.jh(meta["diagnostics"])
        entry["outcome_id"] = candidate.outcome_id
        entry["keep"] = candidate._snapshot.keep
        if not candidate.identity:
            entry["working"] = rec.h(candidate._snapshot.working)
            entry["inference_doc"] = rec.h(candidate._snapshot.inference)
            entry.update(rec.model_record(candidate.result().model, score, sx))
        branch = session.fork()
        branch.apply(candidate)
        entry["applied_stages"] = {k: v.revision for k, v in branch.stages.items()}
        # New: bytes round trip, and a restored candidate scores identically.
        payload = candidate.to_bytes()
        restored = TBoostCandidate.from_bytes(session.fork(), payload)
        assert restored.to_bytes() == payload, "candidate bytes do not round-trip"
        CHECKS["candidate_roundtrips"] += 1
        if not candidate.identity:
            assert rec.model_record(restored.result().model, score, sx) == {
                k: entry[k] for k in ("native", "inference", "raw_scores", "table_export")}
            CHECKS["restored_scores_equal"] += 1
        points.append(entry)
    return dict(path_id=family.path_id, points=points)


def record_case(case: dict) -> dict:
    session, score, sx = rec.fit(case)
    out: dict = {"stages": {}, "families": {}, "recipes": {}}
    out["pruning_path"] = rec.jh(session.pruning_path())
    out["data_digest"], out["evidence_digest"] = session.data_digest, session.evidence_digest
    out["selection"] = session.pruning_selection()
    out["budgets"] = session.pruning_budgets()
    stages = out["stages"]
    stages["full"] = stage_record(session, "full", score, sx)

    a = session.fork()
    a.apply(Prune()); a.apply(Band("pruned")); a.apply(Smooth("banded"))
    for stage in ("pruned", "banded", "smoothed"):
        stages[f"A/{stage}"] = stage_record(a, stage, score, sx)

    b = session.fork()
    b.apply(Prune(max_tables=max(1, out["budgets"][len(out["budgets"]) // 2])))
    b.apply(Band("full", tolerance=1, deviance_cap=0.002))
    b.apply(Smooth("pruned", alpha=0.5))
    for stage in ("pruned", "banded", "smoothed"):
        stages[f"B/{stage}"] = stage_record(b, stage, score, sx)

    c = session.fork()
    path_c = PruningPathSettings(steps=8, main_effects=True, min_rows=50)
    out["pruning_path_C"] = rec.jh(c.pruning_path(path_c))
    c.apply(Prune(path=path_c)); c.apply(Smooth("pruned", strength=2.0))
    for stage in ("pruned", "smoothed"):
        stages[f"C/{stage}"] = stage_record(c, stage, score, sx)

    d = session.fork()
    recipe = a.stage("smoothed").recipe
    d.apply(recipe)
    out["recipes"]["A"] = old_recipe(recipe)
    stages["D/smoothed"] = stage_record(d, "smoothed", score, sx)

    fam = out["families"]
    budgets = tuple(sorted({out["budgets"][0], out["budgets"][-1], out["budgets"][0] + 1}))
    fam["pruned_default"] = family_record(a, a.prepare_path(PrunePath()), score, sx)
    fam["pruned_budgets"] = family_record(a, a.prepare_path(PrunePath(budgets=budgets)), score, sx)
    fam["banded_pruned"] = family_record(a, a.prepare_path(BandPath(Band("pruned"))), score, sx)
    fam["banded_full_int"] = family_record(a, a.prepare_path(
        BandPath(Band("full", tolerance=1, deviance_cap=0.002))), score, sx)
    fam["smoothed_auto"] = family_record(a, a.prepare_path(SmoothPath(Smooth("banded"))), score, sx)
    fam["smoothed_auto_strength3"] = family_record(a, a.prepare_path(
        SmoothPath(Smooth("banded", strength=3.0))), score, sx)
    fam["smoothed_manual"] = family_record(a, a.prepare_path(
        SmoothPath(Smooth("pruned", alpha=0.3), strengths=(0.5, 2.0))), score, sx)
    fam["pruned_path_C"] = family_record(c, c.prepare_path(
        PrunePath(Prune(path=path_c), budgets=(2, 5))), score, sx)
    out["statuses"] = sorted({p["status"] for f in fam.values() for p in f["points"]})

    # New: chains survive save -> load -> save byte for byte, with stages and scores intact.
    with tempfile.TemporaryDirectory() as tmp:
      for i, branch in enumerate((a, b, c)):
        first, second = Path(tmp) / f"{i}-first", Path(tmp) / f"{i}-second"
        branch.save(first)
        restored = TBoostTrainingSession.load(first)
        restored.save(second)
        assert first.read_bytes() == second.read_bytes(), "checkpoint does not round-trip"
        assert restored.stages == branch.stages
        CHECKS["checkpoint_roundtrips"] = CHECKS.get("checkpoint_roundtrips", 0) + 1
        for name in branch.stages:
            assert rec.model_record(restored.stage(name).model, score, sx) == \
                rec.model_record(branch.stage(name).model, score, sx)
    return out


def diff(old, new, path="", out=None):
    out = [] if out is None else out
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f"{path}/{key}: only in {'new' if key in new else 'oracle'}")
            else:
                diff(old[key], new[key], f"{path}/{key}", out)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new)):
            diff(a, b, f"{path}[{i}]", out)
    elif old != new:
        out.append(f"{path}: oracle {str(old)[:60]} != new {str(new)[:60]}")
    return out


def main() -> None:
    oracle = json.load(open(sys.argv[1]))
    total = 0
    for name, case in rec.CASES.items():
        new = json.loads(json.dumps(record_case(case), sort_keys=True, default=str))
        differences = diff(oracle[name], new)
        total += len(differences)
        print(f"{name}: {len(differences)} differences", flush=True)
        for line in differences[:25]:
            print("   ", line)
    print("checks", CHECKS)
    print("TOTAL DIFFERENCES", total)


if __name__ == "__main__":
    main()
