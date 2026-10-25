"""Tests for haute.modelling._feature_contract — train→deploy feature contract.

Written before implementation (TDD). All tests will fail with
``ModuleNotFoundError`` until:

* F1 delivers ``haute.errors.FeatureMismatchError``
* F5 delivers ``haute.modelling._feature_contract``

Principles these tests enforce:

* ``FeatureContract`` is frozen — mutation raises.
* ``contract_hash`` is deterministic and order-independent, and changes
  whenever any field of the contract changes.
* ``save_contract`` produces human-readable JSON (indent=2, sorted keys).
* ``load_contract`` fails loudly on drift — missing fields, unknown
  fields, and wrong types each raise.
* ``assert_contracts_match`` raises a ``FeatureMismatchError`` whose
  message names *which* field disagreed, so a deploy-time mismatch
  points the operator at the actual drift.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import polars as pl
import pytest

from haute.errors import FeatureMismatchError
from haute.modelling._feature_contract import (
    CONTRACT_VERSION,
    UNSEEN_CATEGORICAL_VALUES_CODE,
    FeatureContract,
    UnseenCategoricalValues,
    assert_contracts_match,
    build_contract,
    count_unseen_categorical_values,
    load_contract,
    merge_categorical_level_declarations,
    merge_unseen_categorical_values,
    observed_training_levels,
    report_unseen_categorical_values,
    save_contract,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _basic_kwargs() -> dict:
    """Canonical contract inputs used across most tests."""
    return dict(
        features=["age", "region", "vehicle_value"],
        feature_types={"age": "Int64", "region": "String", "vehicle_value": "Float64"},
        categorical_features=["region"],
        target_name="ClaimCount",
        target_type="Int64",
        task="regression",
    )


@pytest.fixture()
def basic_contract() -> FeatureContract:
    return build_contract(**_basic_kwargs())


# ---------------------------------------------------------------------------
# 1. Build contract basic
# ---------------------------------------------------------------------------


class TestBuildContract:
    def test_build_contract_basic(self) -> None:
        """build_contract populates every field from its arguments."""
        kwargs = _basic_kwargs()
        contract = build_contract(**kwargs)

        assert isinstance(contract, FeatureContract)
        assert contract.features == kwargs["features"]
        assert contract.feature_types == kwargs["feature_types"]
        assert contract.categorical_features == kwargs["categorical_features"]
        assert contract.categorical_levels == {}
        assert contract.target_name == kwargs["target_name"]
        assert contract.target_type == kwargs["target_type"]
        assert contract.task == kwargs["task"]
        assert isinstance(contract.contract_hash, str)
        assert contract.contract_hash  # non-empty

    def test_build_contract_records_declared_categorical_levels(self) -> None:
        kwargs = _basic_kwargs()
        kwargs["categorical_levels"] = {"region": ["north", "south"]}

        contract = build_contract(**kwargs)

        assert contract.categorical_levels == {"region": ["north", "south"]}

    def test_categorical_levels_are_deterministically_ordered(self) -> None:
        kwargs = _basic_kwargs()
        kwargs["categorical_levels"] = {"region": ["south", "north", None]}

        contract = build_contract(**kwargs)

        assert contract.categorical_levels == {"region": ["north", "south", None]}

    def test_categorical_levels_reject_unordered_iterables(self) -> None:
        kwargs = _basic_kwargs()
        kwargs["categorical_levels"] = {"region": {"north", "south"}}

        with pytest.raises(FeatureMismatchError, match="must be lists"):
            build_contract(**kwargs)


# ---------------------------------------------------------------------------
# 2–4. Hash determinism, sensitivity, order-independence
# ---------------------------------------------------------------------------


class TestUnseenCategoricalValues:
    def test_counts_values_and_nulls_the_training_rows_never_held(self) -> None:
        frame = pl.DataFrame({"region": ["north", "east", "east", "west", None]})

        for scored in (frame, frame.lazy()):
            assert count_unseen_categorical_values(scored, {"region": ["north"]}) == {
                "region": UnseenCategoricalValues("region", {"east": 2, "west": 1}, 1)
            }

    def test_a_null_the_training_rows_held_is_not_counted(self) -> None:
        frame = pl.DataFrame({"region": ["north", None]})

        assert count_unseen_categorical_values(frame, {"region": ["north", None]}) == {}

    def test_never_raises_and_skips_a_column_the_frame_lacks(self) -> None:
        frame = pl.DataFrame({"other": ["north"]})

        assert count_unseen_categorical_values(frame, {"region": ["north"]}) == {}

    def test_an_integer_category_is_compared_as_strings(self) -> None:
        training = pl.DataFrame({"area": [1, 2, None], "region": ["", "north", "north"]})
        levels = observed_training_levels(training, ["area", "region"])

        assert levels == {"area": ["1", "2", None], "region": ["", "north"]}
        scored = pl.DataFrame({"area": [2, 3], "region": ["", None]})
        assert count_unseen_categorical_values(scored, levels) == {
            "area": UnseenCategoricalValues("area", {"3": 1}, 0),
            "region": UnseenCategoricalValues("region", {}, 1),
        }

    def test_chunk_counts_merge(self) -> None:
        first = {"region": UnseenCategoricalValues("region", {"east": 1}, 1)}
        second = {
            "region": UnseenCategoricalValues("region", {"east": 2, "west": 1}, 0),
            "make": UnseenCategoricalValues("make", {"Zed": 4}, 0),
        }

        merged = merge_unseen_categorical_values(first, second)
        assert merged["region"] == UnseenCategoricalValues("region", {"east": 3, "west": 1}, 1)
        assert merged["make"].rows == 4

    def test_the_reason_reads_in_the_singular_and_names_nulls_alone(self) -> None:
        from haute.modelling._feature_contract import unseen_categorical_reason

        one = UnseenCategoricalValues("region", {"mars": 1}, 0)
        nulls = UnseenCategoricalValues("region", {}, 1)

        assert unseen_categorical_reason(one, "Deployed model") == (
            "Deployed model: 'region' has 1 value not in the training data, in 1 row ('mars')."
        )
        assert unseen_categorical_reason(nulls, "Deployed model") == (
            "Deployed model: 'region' has 1 missing value, though the training data had none."
        )

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (["region"], "must be a mapping"),
            ({"": ["north"]}, "column names must be non-empty strings"),
            ({"postcode": ["north"]}, "outside the model features"),
            ({"region": "north"}, "values must be lists"),
            ({"region": [3]}, "must be strings or null"),
            ({"region": ["north", "north"]}, "duplicate level"),
        ],
    )
    def test_malformed_training_levels_are_refused(self, raw: object, message: str) -> None:
        from haute.modelling._feature_contract import normalise_training_levels

        with pytest.raises(FeatureMismatchError, match=message):
            normalise_training_levels(raw, features=["age", "region"])  # type: ignore[arg-type]

    def test_the_report_logs_and_records_an_execution_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        import haute._execution_context as execution_context

        recorded: list[tuple[str, str | None, str | None]] = []

        class _Context:
            def record_execution_warning(
                self, code: str, *, node_id: str | None = None, reason: str | None = None
            ) -> None:
                recorded.append((code, node_id, reason))

        monkeypatch.setattr(execution_context, "current_execution_context", _Context)
        values = {f"v{index}": 1 for index in range(7)}
        unseen = {"make": UnseenCategoricalValues("make", values, 2)}
        with capture_logs() as logs:
            report_unseen_categorical_values(unseen, context="Model Score", node_id="score")

        [entry] = [log for log in logs if log["event"] == UNSEEN_CATEGORICAL_VALUES_CODE]
        assert (entry["feature"], entry["values"], entry["rows"], entry["null_rows"]) == (
            "make",
            7,
            7,
            2,
        )
        assert entry["examples"] == ["v0", "v1", "v2", "v3", "v4"]
        assert recorded == [
            (
                UNSEEN_CATEGORICAL_VALUES_CODE,
                "score",
                "Model Score: 'make' has 7 values not in the training data, in 7 rows "
                "('v0', 'v1', 'v2', 'v3', 'v4', ...) and 2 missing values, though the "
                "training data had none.",
            )
        ]


class TestMergeCategoricalLevelDeclarations:
    def test_merges_matching_declarations(self) -> None:
        merged = merge_categorical_level_declarations(
            [
                ("source", {"region": ["north", "south"]}),
                ("modelScore", {"region": ["north", "south"], "channel": ["web"]}),
            ]
        )

        assert merged == {
            "region": ["north", "south"],
            "channel": ["web"],
        }

    def test_rejects_conflicting_declarations(self) -> None:
        with pytest.raises(FeatureMismatchError, match="Conflicting") as exc_info:
            merge_categorical_level_declarations(
                [
                    ("source", {"region": ["north", "south"]}),
                    ("modelScore", {"region": ["north", "east"]}),
                ]
            )

        assert exc_info.value.context["column"] == "region"
        assert exc_info.value.context["source_node"] == "modelScore"


class TestContractHash:
    def test_hash_is_deterministic(self) -> None:
        """Two contracts built from identical inputs share a hash."""
        a = build_contract(**_basic_kwargs())
        b = build_contract(**_basic_kwargs())
        assert a.contract_hash == b.contract_hash

    @pytest.mark.parametrize(
        "mutation",
        [
            # Change features list (add a feature)
            {
                "features": ["age", "region", "vehicle_value", "postcode"],
                "feature_types": {
                    "age": "Int64",
                    "region": "String",
                    "vehicle_value": "Float64",
                    "postcode": "String",
                },
            },
            # Change feature types (age Int64 -> Float64)
            {"feature_types": {"age": "Float64", "region": "String", "vehicle_value": "Float64"}},
            # Change categorical set
            {"categorical_features": ["region", "age"]},
            # Change categorical value-domain
            {"categorical_levels": {"region": ["north", "east"]}},
            # Change the training levels
            {"training_levels": {"region": ["north"]}},
            # Change target name
            {"target_name": "ClaimAmount"},
            # Change target type
            {"target_type": "Float64"},
            # Change task
            {"task": "classification"},
        ],
    )
    def test_hash_changes_when_any_field_changes(self, mutation: dict) -> None:
        """Any structural change to the contract produces a new hash."""
        base = build_contract(**_basic_kwargs())

        mutated_kwargs = _basic_kwargs()
        mutated_kwargs.update(mutation)
        mutated = build_contract(**mutated_kwargs)

        assert base.contract_hash != mutated.contract_hash, (
            f"Expected hash to change after mutation {mutation}, "
            f"but both were {base.contract_hash!r}"
        )

    def test_hash_ignores_dict_key_insertion_order(self) -> None:
        """Two dicts with identical content but different insertion order
        must produce the same hash — we don't want serialization quirks
        to perturb the contract.
        """
        kwargs_a = _basic_kwargs()
        kwargs_a["feature_types"] = {
            "age": "Int64",
            "region": "String",
            "vehicle_value": "Float64",
        }

        kwargs_b = _basic_kwargs()
        kwargs_b["feature_types"] = {
            "vehicle_value": "Float64",
            "region": "String",
            "age": "Int64",
        }

        a = build_contract(**kwargs_a)
        b = build_contract(**kwargs_b)
        assert a.contract_hash == b.contract_hash

    def test_categorical_level_reorder_preserves_hash(self) -> None:
        kwargs_a = _basic_kwargs()
        kwargs_a["categorical_levels"] = {"region": ["north", "south"]}
        kwargs_b = _basic_kwargs()
        kwargs_b["categorical_levels"] = {"region": ["south", "north"]}

        assert build_contract(**kwargs_a).contract_hash == build_contract(**kwargs_b).contract_hash


# ---------------------------------------------------------------------------
# 5–6. save / load round-trip, JSON formatting
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_and_load_round_trip(
        self, tmp_path: Path, basic_contract: FeatureContract
    ) -> None:
        """Save then load returns a FeatureContract equal to the original."""
        path = tmp_path / "feature_contract.json"
        save_contract(basic_contract, path)

        assert path.exists()

        loaded = load_contract(path)
        assert loaded == basic_contract
        assert loaded.contract_hash == basic_contract.contract_hash

    def test_training_levels_round_trip_and_are_never_compared_with_live_data(
        self, tmp_path: Path
    ) -> None:
        trained = build_contract(
            **_basic_kwargs(), training_levels={"region": ["south", "", None, "north"]}
        )
        path = tmp_path / "feature_contract.json"
        save_contract(trained, path)

        assert load_contract(path).training_levels == {"region": ["", "north", "south", None]}
        assert_contracts_match(trained, build_contract(**_basic_kwargs()))

    def test_saved_json_is_human_readable(
        self, tmp_path: Path, basic_contract: FeatureContract
    ) -> None:
        """Written contract is pretty-printed (indent=2) with sorted keys."""
        path = tmp_path / "feature_contract.json"
        save_contract(basic_contract, path)

        text = path.read_text(encoding="utf-8")

        # Pretty-printed with indent=2 → contains newline + 2 spaces.
        assert "\n  " in text, f"Expected indent=2 formatting, got:\n{text}"

        # Must round-trip through json.loads — i.e. valid JSON.
        parsed = json.loads(text)
        assert isinstance(parsed, dict)

        # Top-level keys sorted alphabetically.
        top_keys = list(parsed.keys())
        assert top_keys == sorted(top_keys), f"Top-level keys must be sorted, got {top_keys}"

    def test_save_is_deterministic(self, tmp_path: Path, basic_contract: FeatureContract) -> None:
        """Writing the same contract twice produces identical bytes —
        this is required for content-hashing the artifact downstream.
        """
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        save_contract(basic_contract, path_a)
        save_contract(basic_contract, path_b)

        assert path_a.read_bytes() == path_b.read_bytes()


# ---------------------------------------------------------------------------
# 7–9. Loader rejects malformed JSON
# ---------------------------------------------------------------------------


class TestLoadRejectsMalformed:
    def _valid_payload(self) -> dict:
        c = build_contract(**_basic_kwargs())
        return {
            "features": c.features,
            "feature_types": c.feature_types,
            "categorical_features": c.categorical_features,
            "categorical_levels": c.categorical_levels,
            "training_levels": c.training_levels,
            "target_name": c.target_name,
            "target_type": c.target_type,
            "task": c.task,
            "contract_hash": c.contract_hash,
            "offset_column": c.offset_column,
            "offset_link": c.offset_link,
            "contract_version": CONTRACT_VERSION,
            "model": None,
        }

    def test_load_rejects_missing_required_field(self, tmp_path: Path) -> None:
        """Dropping ``features`` from the JSON causes load to raise."""
        payload = self._valid_payload()
        del payload["features"]
        path = tmp_path / "c.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        # The error must name the missing field — otherwise the failure
        # is cryptic and the operator can't fix it.
        assert "features" in str(exc_info.value)

    def test_load_rejects_unknown_top_level_field(self, tmp_path: Path) -> None:
        """Extra top-level keys indicate contract drift — never ignore."""
        payload = self._valid_payload()
        payload["wibble"] = 1
        path = tmp_path / "c.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        assert "wibble" in str(exc_info.value)

    def test_load_rejects_wrong_type(self, tmp_path: Path) -> None:
        """``features`` must be a list; a string is a schema violation."""
        payload = self._valid_payload()
        payload["features"] = "not a list"
        path = tmp_path / "c.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        assert "features" in str(exc_info.value)

    def test_load_rejects_missing_file(self, tmp_path: Path) -> None:
        """A path that doesn't exist raises — no silent empty contract."""
        with pytest.raises(FileNotFoundError):
            load_contract(tmp_path / "does_not_exist.json")


# ---------------------------------------------------------------------------
# 10–14. assert_contracts_match behaviour
# ---------------------------------------------------------------------------


class TestAssertContractsMatch:
    def test_identical_contracts_do_not_raise(self, basic_contract: FeatureContract) -> None:
        """Two equal contracts must pass silently."""
        other = build_contract(**_basic_kwargs())
        # Both call directions should succeed.
        assert_contracts_match(basic_contract, other)
        assert_contracts_match(other, basic_contract)

    def test_feature_order_difference_raises(self) -> None:
        """Same features, different order, is a mismatch — feature order
        is load-bearing for CatBoost pools and for positional scoring.
        """
        expected = build_contract(**_basic_kwargs())

        reordered_kwargs = _basic_kwargs()
        reordered_kwargs["features"] = ["region", "age", "vehicle_value"]
        actual = build_contract(**reordered_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        message = str(exc_info.value)
        # The message must at least reference that features/order differ.
        assert "feature" in message.lower() or "order" in message.lower()

    def test_feature_type_difference_raises(self) -> None:
        """age: Int64 on the expected side, Float64 on the actual side
        must raise and name the offending feature.
        """
        expected = build_contract(**_basic_kwargs())

        actual_kwargs = _basic_kwargs()
        actual_kwargs["feature_types"] = {
            "age": "Float64",  # was Int64
            "region": "String",
            "vehicle_value": "Float64",
        }
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        message = str(exc_info.value)
        assert "age" in message, (
            f"Diff message must name the mismatched feature 'age'; got: {message}"
        )

    def test_categorical_set_difference_raises(self) -> None:
        """Changing the categorical set without changing any other field
        must still raise — categoricals affect encoding at scoring time.
        """
        expected = build_contract(**_basic_kwargs())

        actual_kwargs = _basic_kwargs()
        actual_kwargs["categorical_features"] = ["region", "age"]
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        message = str(exc_info.value).lower()
        assert "categorical" in message or "age" in message

    def test_categorical_level_difference_raises(self) -> None:
        expected = build_contract(
            **{
                **_basic_kwargs(),
                "categorical_levels": {"region": ["north", "south"]},
            }
        )
        actual = build_contract(
            **{
                **_basic_kwargs(),
                "categorical_levels": {"region": ["north", "east"]},
            }
        )

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)

        assert exc_info.value.context["field"] == "categorical_levels"
        assert "region" in str(exc_info.value)

    def test_task_difference_raises(self) -> None:
        """regression vs classification is a structural mismatch —
        the model-family assumptions differ.
        """
        expected = build_contract(**_basic_kwargs())

        actual_kwargs = _basic_kwargs()
        actual_kwargs["task"] = "classification"
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        message = str(exc_info.value).lower()
        assert "task" in message or "classification" in message or "regression" in message

    def test_target_name_difference_raises(self) -> None:
        """ClaimCount vs ClaimAmount must raise — the model was trained
        against a different target.
        """
        expected = build_contract(**_basic_kwargs())

        actual_kwargs = _basic_kwargs()
        actual_kwargs["target_name"] = "ClaimAmount"
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        message = str(exc_info.value)
        assert "target" in message.lower() or "ClaimAmount" in message or "ClaimCount" in message


# ---------------------------------------------------------------------------
# 15. FeatureContract is frozen
# ---------------------------------------------------------------------------


class TestFrozenContract:
    def test_cannot_mutate_scalar_field(self, basic_contract: FeatureContract) -> None:
        """Reassigning a scalar field raises FrozenInstanceError."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            basic_contract.target_name = "something_else"  # type: ignore[misc]

    def test_cannot_mutate_hash(self, basic_contract: FeatureContract) -> None:
        """contract_hash is set once at construction and can't be replaced."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            basic_contract.contract_hash = "tampered"  # type: ignore[misc]

    def test_cannot_assign_new_attribute(self, basic_contract: FeatureContract) -> None:
        """Frozen dataclasses also reject brand-new attribute assignment."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            basic_contract.surprise = "value"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 16. Tamper-evidence (verify_hash) + structured context kwargs
# ---------------------------------------------------------------------------


class TestTamperEvidence:
    def test_load_detects_tampered_json(self, tmp_path: Path) -> None:
        """A contract file with stale contract_hash after hand-editing must
        raise on load by default — the whole point of the artifact.
        """
        from haute.modelling._feature_contract import save_contract

        contract = build_contract(**_basic_kwargs())
        path = tmp_path / "tampered.json"
        save_contract(contract, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["features"] = ["xxx_tampered"] + raw["features"]
        path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        err = exc_info.value
        assert "hash" in str(err).lower()
        assert err.context.get("path") == str(path)
        assert err.context.get("expected_hash")
        assert err.context.get("actual_hash")

    def test_load_verify_hash_false_skips_check(self, tmp_path: Path) -> None:
        """verify_hash=False is the documented opt-out for rehydrating a
        contract that was deliberately modified in memory.
        """
        from haute.modelling._feature_contract import save_contract

        contract = build_contract(**_basic_kwargs())
        path = tmp_path / "tampered.json"
        save_contract(contract, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["features"] = ["xxx_tampered"] + raw["features"]
        path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

        loaded = load_contract(path, verify_hash=False)
        assert "xxx_tampered" in loaded.features


def _raw_from_contract(contract: FeatureContract) -> dict:
    return {
        "features": contract.features,
        "feature_types": contract.feature_types,
        "categorical_features": contract.categorical_features,
        "categorical_levels": contract.categorical_levels,
        "training_levels": contract.training_levels,
        "target_name": contract.target_name,
        "target_type": contract.target_type,
        "task": contract.task,
        "contract_hash": contract.contract_hash,
        "offset_column": contract.offset_column,
        "offset_link": contract.offset_link,
        "contract_version": CONTRACT_VERSION,
        "model": None,
    }


class TestStructuredContext:
    def test_missing_field_error_has_context(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        raw = _raw_from_contract(build_contract(**_basic_kwargs()))
        raw.pop("target_name")
        path.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        err = exc_info.value
        assert err.context.get("path") == str(path)
        assert "target_name" in err.context.get("missing", [])

    def test_unknown_field_error_has_context(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        raw = _raw_from_contract(build_contract(**_basic_kwargs()))
        raw["surprise"] = 1
        path.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path)
        assert "surprise" in exc_info.value.context.get("unknown", [])

    def test_wrong_type_error_has_context(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        raw = _raw_from_contract(build_contract(**_basic_kwargs()))
        raw["features"] = "not_a_list"
        path.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(FeatureMismatchError) as exc_info:
            load_contract(path, verify_hash=False)
        ctx = exc_info.value.context
        assert ctx.get("field") == "features"
        assert ctx.get("expected_type") == "list"
        assert ctx.get("actual_type") == "str"

    def test_assert_mismatch_has_context(self) -> None:
        expected = build_contract(**_basic_kwargs())
        actual_kwargs = _basic_kwargs()
        actual_kwargs["target_type"] = "Float64"
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        ctx = exc_info.value.context
        assert ctx.get("field") == "target_type"
        assert ctx.get("expected") == "Int64"
        assert ctx.get("actual") == "Float64"

    def test_load_rejects_malformed_categorical_levels(self, tmp_path: Path) -> None:
        contract = build_contract(**_basic_kwargs())
        raw = _raw_from_contract(contract)
        raw["categorical_levels"] = {"region": ["north", "north"]}
        path = tmp_path / "bad_levels.json"
        path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

        with pytest.raises(FeatureMismatchError, match="duplicate"):
            load_contract(path, verify_hash=False)


# ---------------------------------------------------------------------------
# 17. Additional coverage gaps (target_type diff, pure-reorder hash)
# ---------------------------------------------------------------------------


class TestAssertContractsMatchTargetType:
    def test_target_type_difference_raises(self) -> None:
        expected = build_contract(**_basic_kwargs())
        actual_kwargs = _basic_kwargs()
        actual_kwargs["target_type"] = "Float64"
        actual = build_contract(**actual_kwargs)

        with pytest.raises(FeatureMismatchError) as exc_info:
            assert_contracts_match(expected, actual)
        assert "target_type" in str(exc_info.value)


class TestHashPureReorder:
    def test_feature_pure_reorder_changes_hash(self) -> None:
        """Pure reorder of features (no add/remove) must change the hash —
        feature order is part of the training contract.
        """
        a = build_contract(**_basic_kwargs())
        kwargs_b = _basic_kwargs()
        kwargs_b["features"] = list(reversed(kwargs_b["features"]))
        b = build_contract(**kwargs_b)
        assert a.contract_hash != b.contract_hash

    def test_categorical_pure_reorder_changes_hash(self) -> None:
        kwargs_a = _basic_kwargs()
        kwargs_a["features"] = ["age", "region", "vehicle_class", "vehicle_value"]
        kwargs_a["feature_types"] = {
            "age": "Int64",
            "region": "String",
            "vehicle_class": "String",
            "vehicle_value": "Float64",
        }
        kwargs_a["categorical_features"] = ["region", "vehicle_class"]
        a = build_contract(**kwargs_a)

        kwargs_b = dict(kwargs_a)
        kwargs_b["categorical_features"] = ["vehicle_class", "region"]
        b = build_contract(**kwargs_b)
        assert a.contract_hash != b.contract_hash


# ---------------------------------------------------------------------------
# 18. Contract filename constant
# ---------------------------------------------------------------------------


class TestContractFilename:
    def test_filename_constant_exported(self) -> None:
        from haute.modelling._feature_contract import CONTRACT_FILENAME

        assert CONTRACT_FILENAME == "feature_contract.json"
