"""Reproduction probe for V022.

Claim: `_gen_model_score` omits `categorical_levels` and `feature_contract_path`
from the emitted decorator, so on a parse -> re-save round-trip the sidecar
loses both runtime-affecting keys, silently dropping categorical-domain /
feature-contract validation in `score_from_config`.

This probe performs the FULL round trip using the real public API:

    graph -> graph_to_code (emit .py)
          -> collect_node_configs (FIRST sidecar, written to a tempdir)
          -> parse_pipeline_source(code, _base_dir=tmp)  (PARSE)
          -> collect_node_configs(parsed)  (SECOND/re-saved sidecar)

and asserts on the SPECIFIC VALUES of categorical_levels / feature_contract_path
in the re-saved sidecar.  If the bug were real, both keys would be absent after
the round trip.  If they survive, the claim is refuted.

Isolation: all disk I/O is under tempfile; no rating/, src/, tests/, or real
project files are read or written.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from haute._config_io import collect_node_configs
from haute.codegen import graph_to_code
from haute.graph_utils import PipelineGraph
from haute.parser import parse_pipeline_source

CATEGORICAL_LEVELS = {"region": ["north", "south", None], "band": ["A", "B"]}
FEATURE_CONTRACT_PATH = "deploy/feature_contract.json"


def _build_graph() -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "score",
                    "data": {
                        "label": "scorer",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": "abc123",
                            "artifact_path": "model.cbm",
                            "task": "regression",
                            "output_column": "prediction",
                            "categorical_levels": CATEGORICAL_LEVELS,
                            "feature_contract_path": FEATURE_CONTRACT_PATH,
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e_source_score", "source": "source", "target": "score"}
            ],
        }
    )


def _scorer_sidecar(graph: PipelineGraph) -> dict:
    """Return the parsed model-score sidecar JSON for the scorer node."""
    configs = collect_node_configs(graph)
    rel = "config/model_scoring/scorer.json"
    assert rel in configs, f"expected {rel} in {sorted(configs)}"
    return json.loads(configs[rel])


def main() -> None:
    graph = _build_graph()

    # --- codegen -> emitted .py ------------------------------------------
    code = graph_to_code(graph)
    # Sanity: the decorator is post-processed to a config= path reference
    # (the inline kwargs from _gen_model_score never reach the final file).
    assert 'config="config/model_scoring/scorer.json"' in code, code

    # --- FIRST save: write the sidecar collect_node_configs derives ------
    first_sidecar = _scorer_sidecar(graph)
    print("FIRST sidecar keys:", sorted(first_sidecar))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for rel_path, content in collect_node_configs(graph).items():
            f = tmp / rel_path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")

        # --- PARSE the emitted .py back into a graph ---------------------
        parsed = parse_pipeline_source(code, _base_dir=tmp)
        node_map = {n.data.label: n for n in parsed.nodes}
        assert "scorer" in node_map, sorted(node_map)
        parsed_cfg = node_map["scorer"].data.config
        print("PARSED node config keys:", sorted(parsed_cfg))

        # --- SECOND save: re-derive the sidecar from the parsed graph ----
        second_sidecar = _scorer_sidecar(parsed)
        print("SECOND (re-saved) sidecar keys:", sorted(second_sidecar))

    # ---------------------------------------------------------------------
    # The bug claim: after the round trip both keys are GONE from the
    # re-saved sidecar, so score_from_config(cfg.get(...)) reads None.
    # Assert on the SPECIFIC VALUES they must retain to keep validation.
    # ---------------------------------------------------------------------
    cl = second_sidecar.get("categorical_levels")
    fcp = second_sidecar.get("feature_contract_path")

    print("round-trip categorical_levels:", cl)
    print("round-trip feature_contract_path:", fcp)

    assert cl == CATEGORICAL_LEVELS, (
        f"categorical_levels LOST/CHANGED on round trip: "
        f"expected {CATEGORICAL_LEVELS!r}, got {cl!r}"
    )
    assert fcp == FEATURE_CONTRACT_PATH, (
        f"feature_contract_path LOST/CHANGED on round trip: "
        f"expected {FEATURE_CONTRACT_PATH!r}, got {fcp!r}"
    )

    print(
        "REFUTED: both categorical_levels and feature_contract_path SURVIVE "
        "the parse->re-save round trip (sourced from the sidecar, not the "
        "decorator kwargs)."
    )


if __name__ == "__main__":
    main()
