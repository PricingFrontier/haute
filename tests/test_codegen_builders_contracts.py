"""Focused contracts for codegen builder edge cases not covered elsewhere."""

from __future__ import annotations

from haute._codegen_builders import _gen_live_switch, _gen_model_score
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_node as _n


def _live_switch_node(input_scenario_map: dict[str, str]):
    return _n(
        {
            "id": "switch",
            "data": {
                "label": "Switch",
                "nodeType": "liveSwitch",
                "config": {
                    "input_scenario_map": input_scenario_map,
                    "inputs": list(input_scenario_map),
                },
            },
        }
    )


def test_live_switch_prefers_mapped_live_input_over_source_order() -> None:
    node = _live_switch_node({"live_src": "live", "batch_src": "test_batch"})

    code = _gen_live_switch(node, ["batch_src", "live_src"])

    assert "return live_src" in code
    _compile_node_code(code)


def test_live_switch_falls_back_to_first_param_when_live_mapping_is_missing() -> None:
    node = _live_switch_node({"missing_live_src": "live", "batch_src": "test_batch"})

    code = _gen_live_switch(node, ["batch_src", "shadow_src"])

    assert "return batch_src" in code
    _compile_node_code(code)


def test_model_score_registered_source_emits_registered_model_kwargs() -> None:
    node = _n(
        {
            "id": "score",
            "data": {
                "label": "Score",
                "nodeType": "modelScore",
                "config": {
                    "sourceType": "registered",
                    "registered_model": "catalog.schema.pricing_model",
                    "version": "7",
                    "task": "classification",
                    "output_column": "score",
                },
            },
        }
    )

    code = _gen_model_score(node, ["features"])

    assert 'source_type="registered"' in code
    assert "registered_model='catalog.schema.pricing_model'" in code
    assert "version='7'" in code
    assert "run_id=" not in code
    assert "artifact_path=" not in code
    _compile_node_code(code)
