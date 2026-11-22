import sys, time, zipfile, io, json
import polars as pl
from t_boost import TBoostRegressor
df = pl.read_parquet(io.BytesIO(zipfile.ZipFile(sys.argv[1]).read('source.parquet'))).sample(fraction=0.8, seed=1)
feats = ["VehPower","VehAge","DrivAge","BonusMalus","VehBrand","VehGas","Area","Density","Region","Group"]
X = df.select(feats); cats=[c for c in feats if X[c].dtype == pl.String]; y = df["ClaimCount"].to_numpy().astype("float64"); e = df["Exposure"].to_numpy()
est = TBoostRegressor(objective="poisson", n_trees=4000, max_interaction_order=3, n_bags=8, interaction_gain_hurdle=2, categorical_features=cats)
t = time.perf_counter(); s = est.fit_staged(X, y, exposure=e); print("fit_staged", round(time.perf_counter()-t,1), "rows", len(y))
ev = s.pruning_path; print("evidence json bytes", len(json.dumps(ev)), "steps", len(ev["table_counts"]), "tables", ev["table_counts"][-1] if ev["table_counts"] else None)
for steps in (24, 48, 32):
    t = time.perf_counter(); s.configure_path(steps=steps, main_effects=False, min_rows=500); print("configure_path steps", steps, round(time.perf_counter()-t,2), "s")
for i in range(2):
    t = time.perf_counter(); s.pruning_selection(); print("pruning_selection", round(time.perf_counter()-t,3), "s")
t = time.perf_counter(); s.full_model; print("full_model", round(time.perf_counter()-t,3), "s")
s.configure_path(steps=32, main_effects=True, min_rows=500)
t = time.perf_counter(); s.configure_path(steps=32, main_effects=True, min_rows=500); 
s.configure_path(steps=32, main_effects=False, min_rows=500)
t = time.perf_counter(); s.configure_path(steps=32, main_effects=True, min_rows=500); print("configure_path main_effects", round(time.perf_counter()-t,2), "s")
s.configure_path(steps=32, main_effects=False, min_rows=500)
s.save(sys.argv[2]); df.write_parquet(sys.argv[3])
