# price-contour ratebook frontier repro

This folder is a standalone repro for the slow ratebook efficient frontier seen from Haute's `rating/main.py` `online_optimiser` node.

It contains projected data only, so it does not depend on Haute:

- `data/scored_optimiser_input.parquet`: long scenario dataframe used to build the `QuoteGrid`.
- `data/ratebook_factors_by_quote.parquet`: one row per quote with the selected ratebook factor columns.
- `metadata.json`: optimiser config, column names, frontier range, and data stats.
- `reproduce_ratebook_frontier.py`: timing script using only `polars` and `price_contour`.

## Data shape

- Quotes: `100,000`
- Scenarios per quote: `21`
- Scored scenario rows: `2,100,000`
- Objective: `expected_margin`
- Constraint: `conversion_prediction`
- Ratebook factor groups:
  - `channel_band`
  - `proposer_age_band`
  - `vehicle_age_band`
- Frontier range:
  - `conversion_prediction`: `189.03746032714844` to `99695.75`
- Frontier steps in Haute config: `15`

## Run

Copy this folder into the `price-contour` repo, install/build price-contour as usual, then run:

```bash
python reproduce_ratebook_frontier.py --frontier-steps 15
```

For a quick scaling check:

```bash
python reproduce_ratebook_frontier.py --scale --frontier-steps 15
```

The script prints JSON lines for:

- `build_grid_from_parquet_chunked`
- `align_factors`
- `ratebook_base_solve`
- `ratebook_frontier`

The expected behaviour from Haute's environment was that the base ratebook solve is sub-second, while the 15-point frontier dominates runtime.
