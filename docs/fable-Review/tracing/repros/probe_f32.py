import polars as pl

# Probe: how does Polars compare a Float32 column against a Python float64 literal?
s32 = pl.Series("x", [0.1], dtype=pl.Float32)
print("f32(0.1) as f64 repr:", float(s32[0]))
print("literal 0.1 f64 repr:", 0.1)
print("s32 <= 0.1 :", (s32 <= 0.1).to_list())
print("s32 == 0.1 :", (s32 == 0.1).to_list())
print("s32 > 0.1  :", (s32 > 0.1).to_list())

# Now what the engine's _banding_condition does: col.le(0.1) on a f32 col
df = pl.DataFrame({"x": pl.Series([0.1], dtype=pl.Float32)})
res = df.select(pl.col("x").le(0.1).alias("le"), pl.col("x").gt(0.1).alias("gt"))
print("engine le(0.1):", res["le"].to_list(), "gt(0.1):", res["gt"].to_list())

# What enrichment's _coerce_pair_through_dtype does: coerce BOTH to f32
from haute._trace_enrichment import _coerce_pair_through_dtype
cv, ct = _coerce_pair_through_dtype(float(s32[0]), 0.1, pl.Float32)
print("coerced pair:", cv, ct, "-> cv<=ct:", cv <= ct, "cv>ct:", cv > ct)
