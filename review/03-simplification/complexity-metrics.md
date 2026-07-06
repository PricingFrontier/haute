# Complexity metrics - src/haute (Phase: simplification)

> AST-measured. Cyclomatic = 1 + decision points (McCabe proxy). Nesting = max depth of control structures.
> A high number is a LEAD to investigate, not proof of a problem - some complexity is irreducible.

## Summary

- Functions analysed: **2242**
- Length > 100 lines: **92**  | > 75 lines: 155
- Cyclomatic > 20: **66**  | > 15: 129
- Max nesting > 5: **24**

## Top 30 by length (lines)

| function | file | line | len | cx | nest | params |
|---|---|---:|---:|---:|---:|---:|
| `_execute_lazy` | _execute_lazy.py | 642 | 785 | 141 | 7 | 12 |
| `_execute_eager_core` | _execute_lazy.py | 1553 | 495 | 98 | 6 | 12 |
| `enrich_steps` | _trace_enrichment.py | 1363 | 476 | 104 | 11 | 7 |
| `execute_graph` | executor.py | 805 | 452 | 62 | 4 | 10 |
| `score_graph_lazy` | deploy/_scorer.py | 423 | 419 | 46 | 3 | 7 |
| `graph_to_code_multi` | codegen.py | 886 | 378 | 71 | 3 | 6 |
| `execute_trace` | trace.py | 310 | 300 | 26 | 4 | 9 |
| `_run_streaming_frontier_auto_range_job` | routes/_optimiser_service.py | 3660 | 290 | 24 | 5 | 8 |
| `_intercept` | deploy/_scorer.py | 469 | 265 | 27 | 3 | 4 |
| `log_experiment` | modelling/_mlflow_log.py | 153 | 243 | 36 | 4 | 9 |
| `_run_solve_setup_and_launch` | routes/_optimiser_service.py | 2665 | 242 | 20 | 4 | 9 |
| `iter_chunked_frames` | chunking.py | 841 | 232 | 38 | 4 | 1 |
| `_run_frontier_auto_range_job` | routes/_optimiser_service.py | 3429 | 230 | 15 | 2 | 12 |
| `_execute_and_sink` | routes/_train_service.py | 796 | 227 | 30 | 3 | 9 |
| `_launch_background` | routes/_train_service.py | 1024 | 227 | 16 | 2 | 9 |
| `_compute_metrics` | modelling/_training_job.py | 1068 | 222 | 28 | 2 | 7 |
| `_launch_background` | routes/_optimiser_service.py | 4824 | 222 | 30 | 4 | 6 |
| `compute_prepared_plan` | projection.py | 2138 | 219 | 37 | 6 | 5 |
| `_call` | _expression_parser.py | 1706 | 216 | 85 | 7 | 2 |
| `preview_node` | routes/pipeline.py | 496 | 216 | 38 | 2 | 1 |
| `azure_devops_yml` | _scaffold.py | 689 | 210 | 1 | 0 | 1 |
| `_execute_pipeline` | routes/_optimiser_service.py | 4137 | 208 | 26 | 6 | 7 |
| `handle_init` | cli/_init_cmd.py | 324 | 207 | 21 | 3 | 1 |
| `_generate_app_source` | deploy/_container.py | 241 | 204 | 1 | 0 | 2 |
| `_finalize_solve_result` | routes/_optimiser_service.py | 2122 | 196 | 22 | 4 | 13 |
| `execute_sink` | executor.py | 1409 | 191 | 21 | 3 | 6 |
| `load_mlflow_model` | _mlflow_io.py | 850 | 189 | 14 | 6 | 7 |
| `run` | modelling/_training_job.py | 372 | 187 | 19 | 4 | 5 |
| `_file_watcher` | server.py | 514 | 184 | 38 | 4 | 0 |
| `generate_model_card` | modelling/_model_card.py | 62 | 179 | 34 | 2 | 5 |

## Top 30 by cyclomatic complexity

| function | file | line | len | cx | nest | params |
|---|---|---:|---:|---:|---:|---:|
| `_execute_lazy` | _execute_lazy.py | 642 | 785 | 141 | 7 | 12 |
| `enrich_steps` | _trace_enrichment.py | 1363 | 476 | 104 | 11 | 7 |
| `_execute_eager_core` | _execute_lazy.py | 1553 | 495 | 98 | 6 | 12 |
| `_call` | _expression_parser.py | 1706 | 216 | 85 | 7 | 2 |
| `graph_to_code_multi` | codegen.py | 886 | 378 | 71 | 3 | 6 |
| `execute_graph` | executor.py | 805 | 452 | 62 | 4 | 10 |
| `_build_node_config` | _config_builder.py | 74 | 160 | 56 | 17 | 4 |
| `score_graph_lazy` | deploy/_scorer.py | 423 | 419 | 46 | 3 | 7 |
| `iter_chunked_frames` | chunking.py | 841 | 232 | 38 | 4 | 1 |
| `fit` | modelling/_algorithms.py | 451 | 137 | 38 | 5 | 14 |
| `preview_node` | routes/pipeline.py | 496 | 216 | 38 | 2 | 1 |
| `_file_watcher` | server.py | 514 | 184 | 38 | 4 | 0 |
| `compute_prepared_plan` | projection.py | 2138 | 219 | 37 | 6 | 5 |
| `log_experiment` | modelling/_mlflow_log.py | 153 | 243 | 36 | 4 | 9 |
| `_apply_rating_table` | _rating.py | 486 | 150 | 34 | 1 | 3 |
| `generate_model_card` | modelling/_model_card.py | 62 | 179 | 34 | 2 | 5 |
| `_build_input_sources` | _trace_enrichment.py | 1034 | 160 | 33 | 6 | 8 |
| `_build_data_quality_summary` | routes/_explore_service.py | 222 | 100 | 33 | 1 | 2 |
| `_parse_expression_impl` | _expression_parser.py | 1006 | 163 | 32 | 8 | 2 |
| `render_loss_curve_svg` | modelling/_charts.py | 282 | 144 | 32 | 2 | 2 |
| `_prune_to_column_relevance` | trace.py | 840 | 91 | 32 | 5 | 4 |
| `enrich_banding` | _trace_enrichment.py | 492 | 154 | 31 | 7 | 4 |
| `normalise_categorical_levels` | modelling/_feature_contract.py | 292 | 95 | 31 | 3 | 4 |
| `_substitute_names_in_ast` | _expression_parser.py | 1203 | 84 | 30 | 3 | 2 |
| `build_instance_mapping` | _graph_utils.py | 128 | 69 | 30 | 3 | 3 |
| `handle_deploy` | cli/_deploy.py | 71 | 149 | 30 | 3 | 1 |
| `_launch_background` | routes/_optimiser_service.py | 4824 | 222 | 30 | 4 | 6 |
| `_execute_and_sink` | routes/_train_service.py | 796 | 227 | 30 | 3 | 9 |
| `_build_lazy_node` | _execute_lazy.py | 1110 | 140 | 29 | 5 | 1 |
| `validate_v2_schema` | _api_input_schema.py | 263 | 140 | 28 | 4 | 1 |

## Top 15 by nesting depth

| function | file | line | len | cx | nest | params |
|---|---|---:|---:|---:|---:|---:|
| `_build_node_config` | _config_builder.py | 74 | 160 | 56 | 17 | 4 |
| `enrich_steps` | _trace_enrichment.py | 1363 | 476 | 104 | 11 | 7 |
| `_row_local_expr_is_supported` | chunking.py | 1278 | 51 | 17 | 9 | 3 |
| `_parse_expression_impl` | _expression_parser.py | 1006 | 163 | 32 | 8 | 2 |
| `_build_rename_chain` | _trace_enrichment.py | 1290 | 71 | 20 | 8 | 5 |
| `fetch_and_cache` | _databricks_io.py | 266 | 164 | 18 | 7 | 5 |
| `_execute_lazy` | _execute_lazy.py | 642 | 785 | 141 | 7 | 12 |
| `_call` | _expression_parser.py | 1706 | 216 | 85 | 7 | 2 |
| `infer_v2_schema_from_data` | _json_shred.py | 1108 | 125 | 22 | 7 | 2 |
| `_walk` | _json_shred.py | 1145 | 39 | 15 | 7 | 2 |
| `enrich_banding` | _trace_enrichment.py | 492 | 154 | 31 | 7 | 4 |
| `_ordered_expression_demands` | projection.py | 1078 | 93 | 27 | 7 | 2 |
| `_attach_code_from_body` | _config_builder.py | 236 | 21 | 13 | 6 | 4 |
| `_execute_eager_core` | _execute_lazy.py | 1553 | 495 | 98 | 6 | 12 |
| `load_mlflow_model` | _mlflow_io.py | 850 | 189 | 14 | 6 | 7 |

## Top 20 files by LOC

| file | loc |
|---|---:|
| routes/_optimiser_service.py | 5046 |
| projection.py | 2506 |
| _expression_parser.py | 2232 |
| _execute_lazy.py | 2048 |
| _trace_enrichment.py | 1839 |
| routes/optimiser.py | 1803 |
| _builders.py | 1734 |
| executor.py | 1600 |
| chunking.py | 1592 |
| modelling/_training_job.py | 1579 |
| _model_scorer.py | 1425 |
| schemas.py | 1347 |
| codegen.py | 1264 |
| _json_shred.py | 1252 |
| routes/_train_service.py | 1251 |
| _scaffold.py | 1242 |
| _mlflow_io.py | 1203 |
| _codegen_builders.py | 1158 |
| _code_extraction.py | 1052 |
| _ram_estimate.py | 999 |
