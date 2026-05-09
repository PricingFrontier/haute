from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

import haute
from haute import _contracts
from haute._contracts import Contract, get_column_contract
from haute._json_safe import row_to_json_safe, rows_to_json_safe, to_json_safe
from haute._registry import NodeRegistryEntry
from haute._types import NodeType


class TestJsonSafe:
    def test_to_json_safe_normalizes_non_finite_numbers(self) -> None:
        assert to_json_safe(math.nan) is None
        assert to_json_safe(math.inf) is None
        assert to_json_safe(-math.inf) is None

    def test_to_json_safe_preserves_scalar_and_time_values(self) -> None:
        assert to_json_safe("premium") == "premium"
        assert to_json_safe(12) == 12
        assert to_json_safe(True) is True
        assert to_json_safe(1.5) == 1.5
        assert to_json_safe(date(2026, 4, 23)) == "2026-04-23"
        assert to_json_safe(time(10, 5, 3)) == "10:05:03"
        assert to_json_safe(datetime(2026, 4, 23, 10, 5, 3)) == "2026-04-23T10:05:03"
        assert to_json_safe(timedelta(minutes=5, seconds=2)) == "0:05:02"

    def test_to_json_safe_recurses_through_nested_structures(self) -> None:
        payload = {
            7: [1.0, math.nan, ("ok", datetime(2026, 4, 23, 9, 0, 0))],
            "obj": SimpleNamespace(label="quoted"),
        }

        assert to_json_safe(payload) == {
            "7": [1.0, None, ["ok", "2026-04-23T09:00:00"]],
            "obj": "namespace(label='quoted')",
        }

    def test_row_helpers_apply_json_safe_conversion(self) -> None:
        row = {"quote_id": 42, "score": math.nan, "ran_at": datetime(2026, 4, 23, 12, 0, 0)}
        assert row_to_json_safe(row) == {
            "quote_id": 42,
            "score": None,
            "ran_at": "2026-04-23T12:00:00",
        }
        assert rows_to_json_safe([row, {"flag": True, "delta": timedelta(seconds=30)}]) == [
            {
                "quote_id": 42,
                "score": None,
                "ran_at": "2026-04-23T12:00:00",
            },
            {
                "flag": True,
                "delta": "0:00:30",
            },
        ]


class TestContracts:
    def test_contract_round_trips_between_tuple_and_dataclass(self) -> None:
        contract = Contract.from_tuple(({"premium"}, {"base_rate", "age"}))

        assert contract == Contract(
            inputs=frozenset({"base_rate", "age"}),
            outputs=frozenset({"premium"}),
        )
        assert contract.to_tuple() == ({"premium"}, {"base_rate", "age"})

    def test_contract_from_user_declared_accepts_multiple_shapes(self) -> None:
        from_dict = Contract.from_user_declared({"inputs": ["a"], "outputs": ("b",)})
        from_parent_dict = Contract.from_user_declared(
            {
                "inputs": ["key", "premium"],
                "outputs": [],
                "inputs_by_parent": {"left": ["key"], "right": ["key", "premium"]},
            }
        )
        from_tuple = Contract.from_user_declared((["feature"], None))
        from_object = Contract.from_user_declared(
            SimpleNamespace(inputs=("x", "y"), outputs=["score"])
        )
        from_opaque = Contract.from_user_declared("opaque")

        assert from_dict == Contract(inputs=frozenset({"a"}), outputs=frozenset({"b"}))
        assert from_parent_dict == Contract(
            inputs=frozenset({"key", "premium"}),
            outputs=frozenset(),
            inputs_by_parent={
                "left": frozenset({"key"}),
                "right": frozenset({"key", "premium"}),
            },
        )
        assert from_tuple == Contract(inputs=frozenset({"feature"}), outputs=None)
        assert from_object == Contract(
            inputs=frozenset({"x", "y"}),
            outputs=frozenset({"score"}),
        )
        assert from_opaque == Contract.opaque()
        assert Contract.from_user_declared(None) is None

    def test_contract_from_user_declared_rejects_invalid_shapes(self) -> None:
        with pytest.raises(ValueError, match="unknown string"):
            Contract.from_user_declared("strict")

        with pytest.raises(ValueError, match="expected both 'inputs' and 'outputs'"):
            Contract.from_user_declared({"inputs": ["a"]})

        with pytest.raises(ValueError, match="must be strings"):
            Contract.from_user_declared({"inputs": [1], "outputs": []})

        with pytest.raises(ValueError, match="must be iterable"):
            Contract.from_user_declared({"inputs": "age", "outputs": []})

        with pytest.raises(ValueError, match="unknown key"):
            Contract.from_user_declared({"inputs": [], "outputs": [], "typo": []})

        with pytest.raises(ValueError, match="inputs_by_parent"):
            Contract.from_user_declared(
                {"inputs": [], "outputs": [], "inputs_by_parent": ["left"]}
            )

        with pytest.raises(ValueError, match="unsupported type"):
            Contract.from_user_declared(123)

    def test_get_column_contract_returns_registered_contract(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_contracts, "ensure_registry_ready", lambda: None)
        monkeypatch.setattr(
            _contracts,
            "NODE_REGISTRY",
            {
                NodeType.POLARS: NodeRegistryEntry(
                    column_contract=lambda config: (
                        {"premium"} if config.get("emit") else set(),
                        {"base_rate"},
                    )
                )
            },
        )

        assert get_column_contract(NodeType.POLARS, {"emit": True}) == (
            {"premium"},
            {"base_rate"},
        )

    def test_get_column_contract_fails_loudly_when_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_contracts, "ensure_registry_ready", lambda: None)
        monkeypatch.setattr(_contracts, "NODE_REGISTRY", {})

        with pytest.raises(KeyError, match="no column contract registered"):
            get_column_contract(NodeType.POLARS, {})


class TestPackageInit:
    def test_dir_and_getattr_expose_lazy_exports(self) -> None:
        exported = dir(haute)
        assert "Pipeline" in exported
        assert "Submodel" in exported
        assert haute.Pipeline.__name__ == "Pipeline"
        assert haute.Submodel.__name__ == "Submodel"

    def test_getattr_raises_clean_attribute_error_for_unknown_name(self) -> None:
        with pytest.raises(AttributeError, match="does_not_exist"):
            getattr(haute, "does_not_exist")

    def test_version_falls_back_in_editable_dev_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import importlib.metadata

        monkeypatch.setattr(
            importlib.metadata,
            "version",
            lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()),
        )

        reloaded = importlib.reload(haute)
        try:
            assert reloaded.__version__ == "0.0.0-dev"
        finally:
            importlib.reload(reloaded)
