# _sniff_operation_type false positives from substring matching on user code
from haute._trace_enrichment import _sniff_operation_type, detect_row_lineage_type

cases = {
    "list.join (string concat, NOT a row join)": "df = df.with_columns(tags=pl.col('parts').list.join(','))",
    "str.join": "df = df.with_columns(x=pl.col('a').str.join('-'))",
    "comment mentioning .filter(": "df = df.with_columns(y=pl.col('a')+1)  # do not .filter( here",
    "column named cross_join": "df = df.with_columns(z=pl.col('cross_join_flag'))",
    "real filter": "df = df.filter(pl.col('a') > 0)",
}
for label, code in cases.items():
    op = _sniff_operation_type(code)
    # emulate enrich_steps: passthrough row counts equal
    lineage = detect_row_lineage_type(input_row_count=5, output_row_count=5, node_type="polars", operation_type=op)
    print(f"{label!r:60} -> op={op!r:12} lineage={lineage!r}")
