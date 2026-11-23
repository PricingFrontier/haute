"""Diagnostics stored once: hydrate(split(x)) is x for every real stage result form."""

import json
from dataclasses import fields

import pytest

from haute.errors import HauteValidationError
from haute.modelling._stage_diagnostics import (
    StoredNames,
    closure,
    hydrate,
    same_staged,
    split,
)
from haute.modelling._stage_views import compact_response, compact_result
from haute.modelling._staged_runtime import open_stage_bundle, run_stage_job
from tests.test_staged_runtime import build

DENSE = {"tboost_tables", "loss_history", "validation_loss_history"}


def _plain(value):
    return json.loads(json.dumps(value))


def _results(tmp_path, validation, monkeypatch):
    """Real results: point results, an Apply and a final result as artifact and public forms."""
    from haute.modelling._stage_paths import materialize_path_point, read_path_result
    from haute.routes._training_worker import _training_response_payload

    # Thin response histories below the fixture's round count, so a response's thinned
    # top-level history differs from its snapshots' complete histories.
    monkeypatch.setenv("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", "8")
    _, full = build(tmp_path, validation)
    settings = full.staged["configuration"]
    _, pruning = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages=settings,
        prepare_path=True,
        output_dir=str(tmp_path / "prune"),
    )
    path = pruning.staged["paths"]["pruned"]
    artifacts, public = [], []
    with open_stage_bundle(pruning.stage_checkpoint_path) as bundle:
        for point in path["points"]:
            result = read_path_result(bundle, path["id"], point["id"])
            artifacts.append({f.name: getattr(result, f.name) for f in fields(result)})
        applied, chosen = materialize_path_point(
            bundle, path["id"], path["selected_point"], tmp_path / "apply"
        )
    _, final = run_stage_job(
        applied, operation="finalise", stages=settings, output_dir=str(tmp_path / "final")
    )
    for item in (chosen, final):
        artifacts.append({f.name: getattr(item, f.name) for f in fields(item)})
        response = _training_response_payload(
            item, job_id="job", model_path="model", evaluation=item.evaluation, tuning=item.tuning
        )
        public.append(response)
        public.append({"response": response, "graph_digest": "g", "config": {}})
    return [_plain(value) for value in artifacts], [_plain(value) for value in public]


class _Store(dict):
    def __init__(self):
        super().__init__()
        self.puts = 0
        self.fetched: list[str] = []

    def put(self, name, payload):
        self.puts += 1
        assert self.setdefault(name, payload) == payload  # content addressing

    def get(self, name):
        self.fetched.append(name)
        return json.loads(self[name])


def _inline_dense(value):
    """Dense fields that are inline (not references) in a mapping."""
    return [k for k in DENSE if value.get(k) and "$object" not in value[k]]


@pytest.mark.parametrize("validation", [None, {"method": "cross_validation", "fold_count": 3}])
def test_split_results_hydrate_exactly_and_store_each_object_once(
    tmp_path, monkeypatch, validation
):
    artifacts, public = _results(tmp_path, validation, monkeypatch)
    store = _Store()
    for value in (*artifacts, *public):
        envelope = split(value, store.put)
        body = envelope.get("response") or envelope
        assert not _inline_dense(body)
        assert hydrate(envelope, store.get) == value
        store.fetched.clear()
        expected = compact_result(value) if "response" in value else compact_response(value)
        assert hydrate(envelope, store.get, compact=True) == expected
        assert not any(name.startswith("table/") for name in store.fetched)
        assert closure(envelope, store.get) <= set(store)
    # Persisted snapshots hold references only; dense content lives in its own objects.
    for name, payload in store.items():
        if name.startswith("snapshot/"):
            assert not _inline_dense(json.loads(payload)), name
    # Ancestor snapshots, banks and the GBM history are stored once across all results.
    assert len(store) < store.puts / 2
    # The thinned response history and the complete snapshot history stay distinct objects.
    response = public[0]
    selected = response["staged"]["stages"][response["staged"]["selected_stage"]]
    assert len(response["loss_history"]) < len(selected["loss_history"])


def test_a_split_reuses_the_references_of_hydrated_objects_it_holds(tmp_path, monkeypatch):
    """A result that shares hydrated diagnostics references them as stored, without
    encoding or putting them again; the envelope equals the one a full split gives."""
    artifacts, _ = _results(tmp_path, None, monkeypatch)
    store = _Store()
    envelope = split(artifacts[-1], store.put)
    names = StoredNames()
    hydrated = hydrate(envelope, store.get, names=names)
    # A new result that reuses every hydrated snapshot and dense value by identity.
    reused = {**hydrated, "metrics": {"changed": 1.0}}
    puts = store.puts
    with monkeypatch.context() as patch:
        import haute.modelling._stage_diagnostics as diagnostics

        encoded = []
        original = diagnostics.canonical_json
        patch.setattr(diagnostics, "canonical_json", lambda v: encoded.append(v) or original(v))
        fast = split(reused, store.put, names=names)
    assert fast == split(reused, _Store().put)
    assert encoded == [] and store.puts == puts
    assert hydrate(fast, store.get) == reused
    # A copy is a different object: it is encoded again, to the same references.
    copied = json.loads(json.dumps(reused))
    assert split(copied, store.put, names=names) == fast


def test_artifact_metadata_matches_its_public_response_and_detects_changes(tmp_path, monkeypatch):
    artifacts, public = _results(tmp_path, None, monkeypatch)
    store = _Store()
    for artifact, response in ((artifacts[-2], public[0]), (artifacts[-1], public[2])):
        envelope = split({"staged": artifact["staged"]}, store.put)["staged"]
        assert same_staged(envelope, response["staged"], store.get)
        changed = json.loads(json.dumps(response["staged"]))
        stage = changed["selected_stage"]
        changed["stages"][stage]["metrics"] = {"poisson_deviance": 0.0}
        assert not same_staged(envelope, changed, store.get)


def test_result_data_resembling_references_is_left_alone(tmp_path, monkeypatch):
    artifacts, _ = _results(tmp_path, None, monkeypatch)
    value = {
        **artifacts[0],
        "config": {"monotone_constraints": {"$object": 1, "summary": -1}},
        "feature_importance": [{"feature": "summary", "$object": "history/" + "0" * 64}],
    }
    store = _Store()
    assert hydrate(split(value, store.put), store.get) == value


@pytest.mark.parametrize(
    "damage",
    [
        lambda ref: {"$object": 123, "summary": ref["summary"]},
        lambda ref: {"$object": "history/" + "a" * 64, "summary": ref["summary"]},
        lambda ref: {"$object": "table/not-a-digest", "summary": ref["summary"]},
        lambda ref: {"$object": ref["$object"]},
        lambda ref: {**ref, "summary": {"tables": "x", "factored": 0}},
        lambda ref: {**ref, "summary": {"tables": [{}], "factored": 0}},
        lambda ref: {**ref, "summary": {"tables": [], "factored": -1}},
        lambda ref: {
            **ref,
            "summary": {
                "tables": [{**ref["summary"]["tables"][0], "shape_digest": "short"}],
                "factored": 0,
            },
        },
        lambda ref: {
            **ref,
            "summary": {"tables": [{**ref["summary"]["tables"][0], "order": 0}], "factored": 0},
        },
    ],
)
def test_malformed_references_are_refused_even_by_compact_reads(tmp_path, monkeypatch, damage):
    artifacts, _ = _results(tmp_path, None, monkeypatch)
    store = _Store()
    envelope = split(artifacts[0], store.put)
    envelope["tboost_tables"] = damage(envelope["tboost_tables"])
    for compact in (False, True):
        with pytest.raises(HauteValidationError, match="table"):
            hydrate(envelope, store.get, compact=compact)


def test_compact_reads_take_the_banks_summary_over_any_stored_one(tmp_path, monkeypatch):
    """As `compact_response` derives it from the bank, at the top level and in snapshots."""
    artifacts, _ = _results(tmp_path, None, monkeypatch)
    value = json.loads(json.dumps(artifacts[0]))
    value["table_summary"] = None
    for snapshot in value["staged"]["stages"].values():
        if snapshot.get("tboost_tables"):
            snapshot["table_summary"] = {"tables": [], "factored": 0}  # stale
    store = _Store()
    compact = hydrate(split(value, store.put), store.get, compact=True)
    assert compact == compact_response(value)
    assert compact["table_summary"] is not None
