from __future__ import annotations

import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._output_assembler import OutputNestingKeyError
from haute.errors import (
    ChunkMemoryRiskError,
    ContractMismatchError,
    ContractResolutionError,
    GroupByExecutionUnsupportedError,
    InputPreparationError,
    LiveSwitchScenarioError,
    PreambleError,
    RatingExtremaUndefinedError,
    RatingFactorDtypeContractError,
    RatingFactorMissingError,
    TraceCorrelationUnsupportedError,
)
from haute.routes._contract_errors import (
    CONTRACT_ERROR_HTTP_STATUS,
    CONTRACT_ERROR_TERMINAL_REASON,
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_job_fields,
    contract_error_payload,
    contract_error_terminal_reason,
)


def _public_error_cases() -> list[tuple[BaseException, dict[str, object]]]:
    return [
        (
            ApiInputSchemaError("API Input has no v2 schema (tables[])"),
            {
                "error_code": "api_input_schema_invalid",
                "message": "API Input has no v2 schema (tables[])",
            },
        ),
        (
            PreambleError("preamble failed", source_line=7),
            {"error_code": "preamble_failed", "message": "preamble failed", "source_line": 7},
        ),
        (
            ContractResolutionError(
                "Unable to resolve the node column contract.",
                node_id="score",
                node_type="modelScore",
                failure_kind="artifact_store",
            ),
            {
                "error_code": "contract_resolution_failed",
                "message": "Unable to resolve the node column contract.",
                "node_id": "score",
                "node_type": "modelScore",
                "failure_kind": "artifact_store",
            },
        ),
        (
            ChunkMemoryRiskError(
                "One estimated target row exceeds the configured chunk byte budget.",
                target_node_id="output",
                estimated_target_row_bytes=2_048,
                target_chunk_bytes=1_024,
            ),
            {
                "error_code": "chunk_memory_risk",
                "message": ("One estimated target row exceeds the configured chunk byte budget."),
                "target_node_id": "output",
                "reason_code": "single_row_exceeds_budget",
                "estimated_target_row_bytes": 2_048,
                "estimated_minimum_chunk_bytes": 2_048,
                "row_expansion_factor": 1,
                "target_chunk_bytes": 1_024,
            },
        ),
        (
            GroupByExecutionUnsupportedError(
                "group-by needs a full materialisation boundary",
                node_id="group",
                operator="groupBy",
                profile="training_prep",
                reason_code="materialisation_exceeds_headroom",
                remediation="increase memory headroom or narrow the input",
                estimated_peak_bytes=1_024,
                headroom_bytes=512,
            ),
            {
                "error_code": "group_by_execution_unsupported",
                "message": "group-by needs a full materialisation boundary",
                "node_id": "group",
                "operator": "groupBy",
                "profile": "training_prep",
                "reason_code": "materialisation_exceeds_headroom",
                "remediation": "increase memory headroom or narrow the input",
                "estimated_peak_bytes": 1_024,
                "headroom_bytes": 512,
            },
        ),
        (
            TraceCorrelationUnsupportedError(
                "trace keys cannot be compared",
                node_id="target",
                key_columns=["opaque"],
                dtypes=["Object"],
                reason_code="unsupported_dtype",
            ),
            {
                "error_code": "trace_correlation_unsupported",
                "message": "trace keys cannot be compared",
                "node_id": "target",
                "key_columns": ("opaque",),
                "dtypes": ("Object",),
                "reason_code": "unsupported_dtype",
            },
        ),
        (
            RatingExtremaUndefinedError(
                "all rating values are null",
                output_column="premium",
                operation="max",
            ),
            {
                "error_code": "rating_extrema_undefined",
                "message": "all rating values are null",
                "output_column": "premium",
                "operation": "max",
            },
        ),
        (
            RatingFactorMissingError(
                "rating factor is absent",
                table="territory",
                factor="region",
            ),
            {
                "error_code": "rating_factor_missing",
                "message": "rating factor is absent",
                "table": "territory",
                "factor": "region",
            },
        ),
        (
            RatingFactorDtypeContractError(
                "saved rating factor dtype no longer matches the input",
                table="territory",
                factor="region",
                saved_dtype={"kind": "String"},
                input_dtype={"kind": "Categorical"},
            ),
            {
                "error_code": "rating_factor_dtype_contract",
                "message": "saved rating factor dtype no longer matches the input",
                "table": "territory",
                "factor": "region",
                "saved_dtype": {"kind": "String"},
                "input_dtype": {"kind": "Categorical"},
            },
        ),
        (
            LiveSwitchScenarioError(
                "scenario has no live-switch mapping",
                switch="source",
                scenario="stress",
                available_mappings=["test", "live"],
            ),
            {
                "error_code": "live_switch_scenario_missing",
                "message": "scenario has no live-switch mapping",
                "switch": "source",
                "scenario": "stress",
                "available_mappings": ("live", "test"),
            },
        ),
        (
            OutputNestingKeyError(
                "nesting key is null",
                frame="children",
                output_path="$[:].id",
                key="id",
            ),
            {
                "error_code": "output_nesting_key_null",
                "message": "nesting key is null",
                "frame": "children",
                "output_path": "$[:].id",
                "key": "id",
            },
        ),
    ]


@pytest.mark.parametrize(("exc", "expected"), _public_error_cases())
def test_shared_contract_error_adapter_preserves_sync_and_background_payloads(
    exc: BaseException,
    expected: dict[str, object],
) -> None:
    assert isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES)
    assert contract_error_payload(exc) == expected

    http_exc = contract_error_http_exception(exc)
    assert http_exc.status_code == CONTRACT_ERROR_HTTP_STATUS == 422
    assert http_exc.detail == expected

    fields = contract_error_job_fields(exc)
    assert CONTRACT_ERROR_TERMINAL_REASON == "contract_error"
    assert fields == {
        "error": str(exc),
        "error_detail": expected,
        "error_code": expected["error_code"],
        "http_status_code": 422,
    }


def _input_preparation_error(reason_code: str) -> InputPreparationError:
    return InputPreparationError(
        "Preparing this Data Input's snapshot failed.",
        node_id="input",
        identity_digest="a" * 64,
        build_class="bounded",
        reason_code=reason_code,
        remediation="Build the snapshot and try again.",
    )


def test_input_preparation_error_is_a_public_contract_error() -> None:
    exc = _input_preparation_error("build_failed")
    assert isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES)
    payload = contract_error_payload(exc)
    assert payload == {
        "error_code": "input_preparation_failed",
        "message": "Preparing this Data Input's snapshot failed.",
        "node_id": "input",
        "identity_digest": "a" * 64,
        "build_class": "bounded",
        "reason_code": "build_failed",
        "remediation": "Build the snapshot and try again.",
    }
    assert contract_error_http_exception(exc).status_code == 422
    assert contract_error_terminal_reason(exc) == CONTRACT_ERROR_TERMINAL_REASON
    assert contract_error_job_fields(exc)["http_status_code"] == 422


def test_a_memory_limited_preparation_records_the_memory_limited_terminal_state() -> None:
    exc = _input_preparation_error("memory_limited")
    # A synchronous route still answers 422 with the contract payload.
    assert contract_error_http_exception(exc).status_code == 422
    assert contract_error_terminal_reason(exc) == "memory_limited"
    fields = contract_error_job_fields(exc)
    assert fields["error_code"] == "memory_limit"
    assert fields["http_status_code"] == 507
    assert fields["error_detail"] == contract_error_payload(exc)


def test_input_preparation_error_rejects_an_unknown_reason_code() -> None:
    with pytest.raises(ValueError, match="unknown input preparation reason code"):
        _input_preparation_error("nope")


def test_shared_contract_error_adapter_rejects_unversioned_errors() -> None:
    with pytest.raises(TypeError, match="not a public contract error"):
        contract_error_payload(ContractMismatchError("ordinary mismatch"))
