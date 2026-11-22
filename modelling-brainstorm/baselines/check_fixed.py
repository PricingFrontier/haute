import sys, time
from t_boost import TBoostTrainingSession, TBoostPathFamily
def lap(label, f):
    t = time.perf_counter(); r = f(); print(f"{label}: {time.perf_counter()-t:.3f} s"); return r
s = lap("load session", lambda: TBoostTrainingSession.load(sys.argv[1]))
lap("prune", lambda: s.prune())
for stage, parent in (("pruned", "full"), ("banded", "pruned"), ("smoothed", "pruned")):
    fam = lap(f"prepare_path {stage}", lambda: s.prepare_path(stage, parent=parent))
    for p in fam.points:
        pid = p["id"]
        if p["identity"]:
            continue
        blob = fam.candidate_bytes(pid)
        snap = fam._point(pid).snapshot
        doc = snap.inference
        t = time.perf_counter(); m = s._estimator_type.from_json(doc); t_json = time.perf_counter() - t
        fixed = m.to_json() == doc
        t = time.perf_counter(); one = TBoostPathFamily.from_candidate_bytes(s.fork(), blob); t_dec = time.perf_counter() - t
        branch = s.fork(); t = time.perf_counter(); fam.apply(branch, pid); t_apply = time.perf_counter() - t
        same_rev = branch.stages[stage]["revision"] == p["revision"]
        print(f"  {stage} {pid} {p['status']}: bytes {len(blob)/1e6:.1f} MB, from_json {t_json:.3f} s, decode {t_dec:.3f} s, apply {t_apply:.3f} s, doc fixed point {fixed}, applied revision == point {same_rev}")
        break
