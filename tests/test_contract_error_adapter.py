from __future__ import annotations

import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._output_assembler import OutputNestingKeyError
from haute.errors import (
    ContractMismatchError,
    GroupByExecutionUnsupportedError,
    LiveSwitchScenarioError,
    RatingExtremaUndefinedError,
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
            GroupByExecutionUnsupportedError(
                "group-by needs a full materialisation boundary",
                node_id="group",
                operator="groupBy",
                profile="training_prep",
                reason_code="profile_requires_bounded_execution",
                remediation="run under an admitted eager profile",
                estimated_peak_bytes=1_024,
                headroom_bytes=512,
            ),
            {
                "error_code": "group_by_execution_unsupported",
                "message": "group-by needs a full materialisation boundary",
                "node_id": "group",
                "operator": "groupBy",
                "profile": "training_prep",
                "reason_code": "profile_requires_bounded_execution",
                "remediation": "run under an admitted eager profile",
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


def test_shared_contract_error_adapter_rejects_unversioned_errors() -> None:
    with pytest.raises(TypeError, match="not a public contract error"):
        contract_error_payload(ContractMismatchError("ordinary mismatch"))
