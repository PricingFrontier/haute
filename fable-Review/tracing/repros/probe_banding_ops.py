import polars as pl
from haute._rating import _apply_banding, _OP_MAP
from haute._trace_enrichment import enrich_banding, _match_continuous_rule

print("engine _OP_MAP keys:", sorted(_OP_MAP.keys()))
from haute._trace_enrichment import _match_continuous_rule
import inspect
# show op_fn keys the enrichment supports
src = inspect.getsource(_match_continuous_rule)
print("enrichment supports != / <> ?", "'!='" in src, "'<>'" in src)

# Rules: a '!=' rule (engine SKIPS) sharing assignment 'X' with a real '>' rule
rules = [
    {"op1": "!=", "val1": 3, "assignment": "X"},   # engine: '!=' not in _OP_MAP -> skipped entirely
    {"op1": ">",  "val1": 0, "assignment": "X"},    # engine: 5>0 -> band 'X'
]
df = pl.DataFrame({"v": pl.Series([5.0], dtype=pl.Float64)})
out = _apply_banding(df.lazy(), "v", "band", "continuous", rules, default="dflt").collect()
engine_band = out["band"][0]
print("\nENGINE band for v=5:", engine_band, "(comes from rule 1 '>0'; rule 0 '!=3' is skipped)")

cfg = {"factors":[{"column":"v","outputColumn":"band","banding":"continuous","rules":rules,"default":"dflt"}]}
detail = enrich_banding(cfg, {"v":5.0}, {"band":engine_band}, traced_column="band",
                        factor_input_dtypes={"v": pl.Float64})
f = detail["factors"][0]
print("ENRICHMENT rule_index:", f["rule_index"], " matched_rule:", f["matched_rule"])
print("ENRICHMENT conditions/bounds:", {k:f.get(k) for k in ("lower_bound","upper_bound","conditions")})
print()
print(">> Engine used rule 1 ('>0'); enrichment attributes band to rule",
      f["rule_index"], "which is the '!=3' rule the engine never evaluates.")
