"""Stage API scope keeps saved parents valid across UI changes and refused jobs."""

import pytest

from haute.errors import ConflictError
from haute.schemas import StageJobRequest, TrainRequest
from tests.conftest import make_graph
from tests.test_stage_store import bundle, publish


def request():
    return TrainRequest(
        graph=make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": {
                                "path": "input.parquet",
                                "input_type": "file",
                                "format": "parquet",
                            },
                        },
                    },
                    {
                        "id": "model",
                        "data": {
                            "label": "model",
                            "nodeType": "modelling",
                            "config": {"algorithm": "tboost", "stages": {}},
                        },
                    },
                ],
                "edges": [{"id": "edge", "source": "source", "target": "model"}],
            }
        ),
        node_id="model",
        model="m1",
        stage=StageJobRequest(operation="full"),
    )


def test_scope_ignores_layout_and_drafts_but_rejects_upstream_changes(tmp_path):
    from haute.routes._training_stages import StageJobScope

    body = request()
    scope = StageJobScope.from_request(body)
    first = publish(scope.store, scope.key, tmp_path, result={"graph_digest": scope.graph_digest})[
        "current"
    ]
    body.stage = StageJobRequest(operation="pruned", parent_revision=first["revision"])
    body.graph.nodes[-1].data.config["stages"] = {"pruning": {"max_tables": 2}}
    body.graph.nodes[-1].position["x"] = 300
    changed = StageJobScope.from_request(body)
    changed.validate_parent()
    body.graph.nodes[0].data.config["path"] = "different.parquet"
    with pytest.raises(ConflictError, match="upstream"):
        StageJobScope.from_request(body).validate_parent()


def test_scope_cancel_keeps_parent_and_closes_durable_job(tmp_path):
    from haute.routes._training_stages import StageJobScope

    scope = StageJobScope.from_request(request())
    first = publish(scope.store, scope.key, tmp_path)["current"]
    scope.begin("cancelled")
    scope.finish("cancelled", {"status": "cancelled", "message": "Cancelled"})
    snapshot = scope.store.snapshot(scope.key)
    assert snapshot["current"] == first
    assert snapshot["job"]["status"] == "cancelled"


@pytest.mark.parametrize("automatic", [False, True])
def test_service_publishes_preview_and_cancel_preserves_applied(tmp_path, monkeypatch, automatic):
    import time

    from t_boost import _Booster

    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.routes._training_stages import StageJobScope
    from tests._modelling_route_helpers import _make_modelling_graph
    from tests._training_helpers import join_training_preparation
    from tests.test_staged_tboost import PARAMS
    from tests.test_tboost_family import EVALUATION, frame
    from tests.test_training_worker_protocol import _inline_protocol_runner

    data = tmp_path / "source.parquet"
    frame(1500).write_parquet(data)
    graph = _make_modelling_graph(
        str(data),
        target="claims",
        weight="weight",
        algorithm="tboost",
        params=PARAMS,
        evaluation={**EVALUATION, "test": {"size": 0.2}},
        feature_columns=["region", "age"],
    )
    graph["nodes"][1]["data"]["config"].update(
        {
            "stages": {"path": {"min_rows": 20}},
            "offset": "exposure",
            "loss_function": "Poisson",
        }
    )
    body = TrainRequest(
        graph=make_graph(graph),
        node_id="train",
        model="m1",
        stage=StageJobRequest(operation="full", prepare_next=automatic),
    )
    original_fit = _Booster.fit
    fit_calls = 0

    def first_fit_only(*args, **kwargs):
        nonlocal fit_calls
        fit_calls += 1
        assert fit_calls == 1, "automatic preview refitted the GBM"
        return original_fit(*args, **kwargs)

    if automatic:
        monkeypatch.setattr(_Booster, "fit", first_fit_only)
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)

    def run(body):
        job_id = service.start(body).job_id
        join_training_preparation(job_id)
        deadline = time.monotonic() + 30
        while store.require_job(job_id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        job = store.require_job(job_id)
        assert job["status"] == "completed", (
            job.get("message"),
            job.get("error"),
            job.get("error_detail"),
        )
        # Supervisor cleanup finishes just after the completion claim.
        while job_id in service._stage_scopes and time.monotonic() < deadline:
            time.sleep(0.01)
        return job

    run(body)
    scope = StageJobScope.from_request(body)
    full = scope.store.snapshot(scope.key)["current"]
    assert full["result"]["response"]["staged"]["selected_stage"] == "full"

    def await_preview(stage):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = scope.store.snapshot(scope.key)
            if state["next_preview"] is None and state["job"]["status"] != "running":
                assert (
                    state["preview"]["result"]["response"]["staged"]["prepared_path"]
                    == state["preview"]["result"]["response"]["staged"]["paths"][stage]["id"]
                ), state
                return state
            time.sleep(0.02)
        pytest.fail(f"Automatic {stage} preview did not complete: {state}")

    if automatic:
        await_preview("pruned")
        assert fit_calls == 1
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("post-fit called booster"))
    monkeypatch.setattr(
        service, "_execute_and_sink", lambda *a, **kw: pytest.fail("post-fit executed upstream")
    )
    body.stage = StageJobRequest(operation="pruned", parent_revision=full["revision"])
    if not automatic:
        run(body)
    value = scope.store.snapshot(scope.key)
    assert value["current"] == full
    assert value["preview"]["result"]["response"]["staged"]["selected_stage"] == "pruned"
    assert not list(scope.store.root.glob("*/staging/*"))  # prepared objects disposed

    def apply(value):
        from haute.routes._training_paths import apply_path, inspect_path
        from haute.schemas import StagePathApplyRequest, StagePathReadRequest

        staged = value["preview"]["result"]["response"]["staged"]
        path = next(p for p in staged["paths"].values() if p["id"] == staged["prepared_path"])
        point = next(
            (p for p in reversed(path["points"]) if p["status"] == "changed"), path["points"][0]
        )
        inspect = StagePathReadRequest(
            **body.model_dump(),
            current_revision=value["current"]["revision"],
            artifact_revision=value["preview"]["revision"],
            path_id=path["id"],
            point_id=point["id"],
        )
        inspected = inspect_path(scope, inspect)
        assert scope.store.snapshot(scope.key) == value
        request = StagePathApplyRequest(**inspect.model_dump(), prepare_next=automatic)
        applied = apply_path(scope, request, service.start_preview)["current"]
        assert (
            applied["result"]["response"]["diagnostic_metrics"]
            == inspected.response.diagnostic_metrics
        )
        # The editor adopts only after successful Apply; subsequent requests see its recipe.
        body.graph.nodes[1].data.config["stages"] = applied["result"]["response"]["staged"][
            "configuration"
        ]
        return applied

    applied = apply(value)
    for operation in ("banded", "smoothed"):
        body.stage = StageJobRequest(operation=operation, parent_revision=applied["revision"])
        if automatic:
            candidate = await_preview(operation)
        else:
            run(body)
            candidate = scope.store.snapshot(scope.key)
        assert candidate["current"] == applied
        staged = candidate["preview"]["result"]["response"]["staged"]
        assert staged["prepared_path"] == staged["paths"][operation]["id"]
        applied = apply(candidate)
    assert scope.store.snapshot(scope.key)["next_preview"] is None
    # Finalisation uses the frozen source, then survives losing every transient job.
    monkeypatch.setattr(_Booster, "fit", original_fit)
    body.stage = StageJobRequest(operation="finalise", parent_revision=applied["revision"])
    final_job = run(body)
    final_state = scope.store.snapshot(scope.key)
    assert final_state["current"] == applied
    assert final_state["final"]["result"]["response"]["final_test_metrics"]
    from haute.routes._training_artifacts import require_training_artifacts

    expected_bytes = require_training_artifacts(final_job).model.read_bytes()
    restarted_store = JobStore()
    restarted = TrainService(restarted_store, protocol_runner=_inline_protocol_runner)
    restored = restarted.restore_final_stage(body)
    assert (
        require_training_artifacts(restarted_store.require_job(restored.job_id)).model.read_bytes()
        == expected_bytes
    )
    assert restarted.restore_final_stage(body).job_id == restored.job_id
    # Restoring writes no archive copy: the store keeps the stage state.
    restored_artifacts = require_training_artifacts(restarted_store.require_job(restored.job_id))
    assert restored_artifacts.stage_state is None
    # The final revision's closure keeps no checkpoint parts (nothing resumes from it).
    with scope.store.lease(scope.key, final_state["final"]["revision"]) as final:
        assert not any(name.startswith("fit/") for name in final.manifest.objects)
        assert {"export/model.native", "plan.json", "source.parquet"} <= set(
            final.manifest.objects
        )
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("post-fit called booster"))
    body.stage = StageJobRequest(operation="banded", parent_revision=applied["revision"])
    original_prepare = StageJobScope.prepare

    def cancel_before_completion(self, path, response, **kwargs):
        prepared = original_prepare(self, path, response, **kwargs)
        service.cancel(response.job_id)
        return prepared

    monkeypatch.setattr(StageJobScope, "prepare", cancel_before_completion)
    cancelled_id = service.start(body).job_id
    join_training_preparation(cancelled_id)
    deadline = time.monotonic() + 30
    while cancelled_id in service._stage_scopes and time.monotonic() < deadline:
        time.sleep(0.02)
    value = scope.store.snapshot(scope.key)
    assert store.require_job(cancelled_id)["status"] == "cancelled"
    assert value["current"] == applied
    assert value["preview"] is None
    assert value["job"]["status"] == "cancelled"
    assert not list(scope.store.root.glob("*/staging/*"))  # prepared objects disposed


def test_stage_routes_restore_only_selected_model_and_apply_with_revision_checks(client, tmp_path):
    from haute.modelling._staged_runtime import fit_config_digest
    from haute.modelling._train_config import build_training_job_kwargs
    from haute.routes._training_stages import StageJobScope
    from tests.test_modelling_train_routes import _as_interactive_build

    body = request()
    config = body.graph.nodes[-1].data.config
    config.update(
        {
            "target": "y",
            "feature_columns": ["x"],
            "loss_function": "RMSE",
            "params": {"n_trees": 10},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 1,
                "validation": {"method": "none"},
            },
        }
    )
    kwargs = build_training_job_kwargs(config, data="unused", default_name="model")
    kwargs["purpose"] = "exploration"
    digest = fit_config_digest(kwargs)
    scope = StageJobScope.from_request(body)
    from haute.schemas import TrainResponse

    result = {
        "graph_digest": scope.graph_digest,
        "response": TrainResponse(
            status="started",
            staged={
                "schema_version": 2,
                "selected_stage": "full",
                "evaluation_identity": "a" * 64,
                "fit_config_digest": digest,
                "configuration": {},
                "stages": {"full": {}},
            },
        ).model_dump(mode="json"),
        "config": dict(config),
    }
    current = publish(scope.store, scope.key, tmp_path, result=result, config_digest=digest)[
        "current"
    ]
    value = publish(
        scope.store,
        scope.key,
        tmp_path,
        job="prune",
        operation="pruned",
        parent=current["revision"],
        result=result,
        config_digest=digest,
    )
    _as_interactive_build(config, {"id": "m1", "name": "First"}, {"id": "m2", "name": "Second"})
    payload = body.model_dump(mode="json")
    response = client.post("/api/modelling/stages/read", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["current"]["revision"] == current["revision"]
    other = client.post("/api/modelling/stages/read", json={**payload, "model": "m2"})
    assert other.status_code == 200, other.text
    assert other.json()["current"] is None
    action = {
        **payload,
        "current_revision": current["revision"],
        "preview_revision": value["preview"]["revision"],
    }
    stale = client.post(
        "/api/modelling/stages/apply", json={**action, "current_revision": "0" * 32}
    )
    assert stale.status_code == 409, stale.text
    applied = client.post("/api/modelling/stages/apply", json=action)
    assert applied.status_code == 200, applied.text
    assert applied.json()["current"]["revision"] == value["preview"]["revision"]
    assert applied.json()["preview"] is None
    assert client.post("/api/modelling/stages/discard", json=action).status_code == 409
    checkpoint_request = {
        **payload,
        "action": "save",
        "name": "Baseline",
        "current_revision": value["preview"]["revision"],
    }
    saved = client.post("/api/modelling/stages/checkpoint", json=checkpoint_request)
    assert saved.status_code == 200, saved.text
    checkpoint = saved.json()["checkpoints"][0]
    assert checkpoint["name"] == "Baseline" and "record" not in checkpoint
    config["models"][0]["params"] = {"n_trees": 20}
    restore_request = {
        **checkpoint_request,
        "graph": body.graph.model_dump(mode="json"),
        "action": "restore",
        "checkpoint_id": checkpoint["id"],
    }
    restored = client.post("/api/modelling/stages/checkpoint", json=restore_request)
    assert restored.status_code == 200, restored.text
    assert restored.json()["current"]["result"]["config"]["params"] == {"n_trees": 10}
    config["evaluation"]["seed"] = 99
    refused = client.post(
        "/api/modelling/stages/checkpoint",
        json={**restore_request, "graph": body.graph.model_dump(mode="json")},
    )
    assert refused.status_code == 409, refused.text
    assert "Shared training settings" in refused.text
    deleted = client.post(
        "/api/modelling/stages/checkpoint", json={**restore_request, "action": "delete"}
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["checkpoints"] == []

    malformed = client.post(
        "/api/modelling/train", json={**payload, "stage": {"operation": "banded"}}
    )
    assert malformed.status_code == 422


def test_stage_parent_becomes_stale_when_local_input_bytes_change(tmp_path):
    from haute.routes._training_stages import StageJobScope
    from tests.test_tboost_family import frame

    body = request()
    path = tmp_path / "source.parquet"
    frame(20).write_parquet(path)
    body.graph.nodes[0].data.config.update(
        {"input_type": "file", "format": "parquet", "path": str(path)}
    )
    scope = StageJobScope.from_request(body)
    first = publish(scope.store, scope.key, tmp_path, result={"graph_digest": scope.graph_digest})[
        "current"
    ]
    body.stage = StageJobRequest(operation="pruned", parent_revision=first["revision"])
    frame(21).write_parquet(path)
    with pytest.raises(ConflictError, match="upstream"):
        StageJobScope.from_request(body).validate_parent()


@pytest.mark.parametrize(
    ("enabled", "after", "expected"),
    [
        ({"pruned": True, "banded": True, "smoothed": True}, "full", "pruned"),
        ({"pruned": False, "banded": True, "smoothed": True}, "full", "banded"),
        ({"pruned": True, "banded": False, "smoothed": True}, "pruned", "smoothed"),
        ({"pruned": False, "banded": False, "smoothed": False}, "full", None),
        ({"pruned": True, "banded": True, "smoothed": True}, "smoothed", None),
    ],
)
def test_next_preview_follows_enabled_chain(enabled, after, expected):
    from haute.routes._training_stages import next_preview_stage

    assert next_preview_stage({"enabled": enabled}, after) == expected


def test_followup_uses_frozen_settings_and_reports_failure_without_losing_parent(tmp_path):
    from haute.routes._training_stages import StageJobScope

    body = request()
    body.stage = StageJobRequest(operation="full", prepare_next=True)
    body.graph.nodes[-1].data.config["stages"] = {"pruning": {"max_tables": 4}}
    scope = StageJobScope.from_request(body)
    scope.begin("full")
    source = bundle(tmp_path / "full.bundle", "full")
    prepared = scope.store.prepare(scope.key, source, result={}, config_digest="a" * 64)
    state = scope.store.publish(scope.key, job_id="full", prepared=prepared, next_preview="pruned")
    revision = state["current"]["revision"]
    body.graph.nodes[-1].data.config["stages"]["pruning"]["max_tables"] = 8
    submitted = []

    def refuse(request):
        submitted.append(request)
        raise ConflictError("Another training job is running")

    scope.start_queued_preview(scope.followup_request, refuse, expected_revision="0" * 32)
    assert submitted == []
    scope.start_queued_preview(scope.followup_request, refuse, expected_revision=revision)
    assert submitted[0].graph.nodes[-1].data.config["stages"]["pruning"]["max_tables"] == 4
    assert submitted[0].stage.operation == "pruned"
    assert submitted[0].stage.prepare_next is False
    failed = scope.store.snapshot(scope.key)
    assert failed["current"] == state["current"] and failed["preview"] is None
    assert failed["next_preview"]["status"] == "failed"
    assert "Another training job" in failed["next_preview"]["message"]


@pytest.mark.parametrize("continuation", ["queued", "automatic", "manual", "replaced"])
def test_cancel_completed_parent_stops_only_its_automatic_continuation(tmp_path, continuation):
    import time

    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.routes._training_stages import StageJobScope

    body = request()
    scope = StageJobScope.from_request(body)
    jobs = JobStore()
    service = TrainService(jobs)
    parent_id = jobs.create_job(
        {
            "status": "running",
            "job_type": "training",
            "node_id": body.node_id,
            "model_id": body.model,
            "pipeline_source": str(body.graph.source_file or ""),
            "start_time": time.monotonic(),
        }
    )
    service._lifecycle.transition(parent_id, to="completed", message="Completed")
    scope.store.begin(scope.key, job_id=parent_id, operation="full", expected_revision=None)
    source = bundle(tmp_path / "full.bundle", "parent")
    prepared = scope.store.prepare(
        scope.key,
        source,
        result={
            "response": {"job_id": "another-parent" if continuation == "replaced" else parent_id}
        },
        config_digest="a" * 64,
    )
    state = scope.store.publish(
        scope.key, job_id=parent_id, prepared=prepared, next_preview="pruned"
    )
    parent = state["current"]
    if continuation != "queued":
        child_id = jobs.create_job(
            {"status": "running", "job_type": "training", "start_time": time.monotonic()}
        )
        scope.store.begin(
            scope.key,
            job_id=child_id,
            operation="pruned",
            expected_revision=parent["revision"],
            queued_preview=continuation != "manual",
        )
    assert service.cancel(parent_id)["status"] == "completed"
    assert scope.store.snapshot(scope.key)["current"] == parent
    if continuation == "queued":
        assert scope.store.snapshot(scope.key)["next_preview"] is None
        with pytest.raises(ConflictError, match="no longer queued"):
            scope.store.begin(
                scope.key,
                job_id="late",
                operation="pruned",
                expected_revision=parent["revision"],
                queued_preview=True,
            )
    else:
        assert jobs.require_job(child_id)["status"] == (
            "cancelled" if continuation == "automatic" else "running"
        )
