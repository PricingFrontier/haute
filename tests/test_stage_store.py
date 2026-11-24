"""Durable stage publication cannot lose a valid parent or apply a stale preview."""

import json
import uuid

import pytest

import haute.modelling._stage_objects as objects_module
from haute.errors import ConflictError, HauteValidationError
from haute.modelling._stage_objects import canonical_json, write_bundle
from haute.modelling._stage_store import StageStore

HISTORY = [{"iteration": float(i), "train_deviance": 1.0 / i} for i in range(1, 6)]


def bundle(path, label):
    """A minimal portable stage archive; the store never interprets its members."""
    write_bundle(
        path,
        {"plan.json": canonical_json({"label": label})},
        revision=uuid.uuid4().hex,
        result={},
        metadata={"label": label},
    )
    return path


def label(revision):
    """The label of a leased revision, read through its verified objects."""
    return json.loads(revision.read_bytes(revision.manifest.objects["plan.json"]))["label"]


def publish(
    store,
    key,
    tmp_path,
    *,
    job="fit",
    operation="full",
    parent=None,
    result=None,
    config_digest="a" * 64,
):
    store.begin(key, job_id=job, operation=operation, expected_revision=parent)
    source = bundle(tmp_path / f"{job}.bundle", f"complete artifact {job}")
    prepared = store.prepare(
        key,
        source,
        result={"stage": operation} if result is None else result,
        config_digest=config_digest,
    )
    return store.publish(
        key, job_id=job, prepared=prepared, preview=operation in {"pruned", "banded", "smoothed"}
    )


def dense_result():
    """A store result with dense diagnostics, so its revision references shared objects."""
    return {
        "response": {"job_id": "fit", "loss_history": HISTORY, "metrics": {"gini": 0.2}},
        "graph_digest": "c" * 64,
    }


def revision_file(store, key, revision):
    return store._objects(key).revision_path(revision)


def object_file(store, key, revision, name):
    manifest = store._objects(key).read_revision(revision)
    return store._objects(key).object_path(manifest.objects[name].sha256)


def pointer_revision(store, key):
    """The current revision, read from the pointer alone (no revision is hydrated)."""
    return json.loads((store._directory(key) / "manifest.json").read_bytes())["current"]["revision"]


def files(store, key, kind):
    directory = store._directory(key) / kind
    return set(directory.iterdir()) if directory.exists() else set()


def test_compact_stage_read_preserves_decisions_without_loading_dense_results(
    tmp_path, monkeypatch
):
    store = StageStore(tmp_path)
    key = store.key("pipeline.py", "node", "model")
    tables = {
        "tables": [
            {
                "term": "x:y",
                "features": ["x", "y"],
                "order": 2,
                "scores": [[float(i) for i in range(1000)] for _ in range(5)],
                "axes": [],
            }
        ],
        "factored": [],
    }
    response = {
        "diagnostic_metrics": {"gini": 0.2},
        "ave_per_feature": [{"feature": "x"}],
        "tboost_tables": tables,
        "staged": {"stages": {"full": {"tboost_tables": tables}}},
    }
    value = publish(store, key, tmp_path, result={"response": response, "graph_digest": "c" * 64})
    revision = value["current"]["revision"]
    table = store._objects(key).read_revision(revision).result["response"]["tboost_tables"]
    table_digest = store._objects(key).read_revision(revision).objects[table["$object"]].sha256
    read = objects_module.StageObjects.read_bytes

    def refuse_tables(self, ref):
        assert ref.sha256 != table_digest, "Compact read decoded the dense tables"
        return read(self, ref)

    with monkeypatch.context() as patch:
        patch.setattr(objects_module.StageObjects, "read_bytes", refuse_tables)
        compact = store.snapshot(key, compact=True)["current"]["result"]["response"]
    assert compact["diagnostic_metrics"] == response["diagnostic_metrics"]
    assert compact["ave_per_feature"] == response["ave_per_feature"]
    assert compact["tboost_tables"] is None
    assert compact["table_summary"]["tables"][0]["cells"] == 5000
    assert len(json.dumps(compact)) < 2000
    assert store.snapshot(key)["current"]["result"]["response"] == response
    # The bank is stored once, although the response and its snapshot both carry it.
    assert len(files(store, key, "objects")) == 4  # plan, table, snapshot and compact view
    with store.lease(key, revision) as pinned:
        store.remove(key)
        assert label(pinned) == "complete artifact fit"
    store.cleanup(key)
    assert not files(store, key, "objects") and not files(store, key, "revisions")


@pytest.mark.parametrize("current", ["panels", "full"])
def test_a_narrow_read_hydrates_only_the_current_record(tmp_path, monkeypatch, current):
    store = StageStore(tmp_path)
    key = store.key("pipeline.py", "node", "model")
    tables = {"tables": [{"term": "x", "features": ["x"], "order": 1, "scores": [1.0], "axes": []}]}
    tables["factored"] = []
    response = {
        "tboost_tables": tables,
        "shap_curves": [{"feature": "x"}],
        "pdp_data": [{"feature": "x"}],
        "double_lift": [{"decile": 1}],
        "staged": {"stages": {"full": {"tboost_tables": tables}}},
    }
    result = {"response": response, "graph_digest": "c" * 64}
    full = publish(store, key, tmp_path, result=result)["current"]["revision"]
    publish(store, key, tmp_path, job="prune", operation="pruned", parent=full, result=result)
    table = store._objects(key).read_revision(full).result["response"]["tboost_tables"]
    table_digest = store._objects(key).read_revision(full).objects[table["$object"]].sha256
    read = objects_module.StageObjects.read_bytes

    def refuse_tables(self, ref):
        assert ref.sha256 != table_digest, "A panels read decoded the dense tables"
        return read(self, ref)

    with monkeypatch.context() as patch:
        if current == "panels":
            patch.setattr(objects_module.StageObjects, "read_bytes", refuse_tables)
        state = store.recover(key, current=current)
    read_current = state["current"]["result"]["response"]
    if current == "full":
        assert read_current == response
    else:
        # The compact view with the result panels' diagnostics, and no tables.
        assert read_current["diagnostics_view"] == "panels"
        assert read_current["tboost_tables"] is None
        assert read_current["shap_curves"] == response["shap_curves"]
        assert read_current["pdp_data"] == response["pdp_data"]
        assert read_current["double_lift"] == response["double_lift"]
    # Every other record stays compact, whichever way the current one is read.
    preview = state["preview"]["result"]["response"]
    assert preview["diagnostics_view"] == "compact"
    assert preview["tboost_tables"] is None and preview["shap_curves"] == []


def test_preview_apply_and_discard_survive_restart(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("pipeline.py", "node", "model")
    first = publish(store, key, tmp_path)["current"]
    preview = publish(
        store, key, tmp_path, job="preview", operation="pruned", parent=first["revision"]
    )
    restarted = StageStore(tmp_path)
    assert restarted.snapshot(key) == preview
    assert preview["current"] == first
    applied = restarted.apply(
        key, expected_revision=first["revision"], preview_revision=preview["preview"]["revision"]
    )
    assert applied["current"] == preview["preview"]
    assert applied["preview"] is None
    again = publish(
        restarted,
        key,
        tmp_path,
        job="another",
        operation="pruned",
        parent=applied["current"]["revision"],
    )
    discarded = restarted.discard(key, preview_revision=again["preview"]["revision"])
    assert discarded["current"] == applied["current"]
    assert discarded["preview"] is None


def test_stale_and_cancelled_completions_cannot_publish(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    with pytest.raises(ConflictError):
        store.begin(key, job_id="stale", operation="banded", expected_revision="wrong")
    store.begin(key, job_id="cancelled", operation="banded", expected_revision=first["revision"])
    prepared = store.prepare(
        key, bundle(tmp_path / "candidate", "candidate"), result={}, config_digest="a" * 64
    )
    store.finish(key, job_id="cancelled", status="cancelled", message="Cancelled")
    with pytest.raises(ConflictError):
        store.publish(key, job_id="cancelled", prepared=prepared)
    assert store.snapshot(key)["current"] == first
    assert not files(store, key, "staging")
    with pytest.raises(ConflictError):
        store.apply(key, expected_revision="wrong", preview_revision="wrong")
    # The refused revision was written ahead of the pointer; collection removes it.
    store.cleanup(key)
    assert not revision_file(store, key, prepared.revision).exists()


def test_failed_manifest_write_preserves_current(tmp_path, monkeypatch):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    store.begin(key, job_id="next", operation="banded", expected_revision=first["revision"])
    prepared = store.prepare(
        key, bundle(tmp_path / "candidate", "candidate"), result={}, config_digest="a" * 64
    )

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError, match="disk full"):
        store.publish(key, job_id="next", prepared=prepared, preview=True)
    assert StageStore(tmp_path).snapshot(key)["current"] == first


@pytest.mark.parametrize("fault", ["revision_written", "objects_installed"])
def test_a_crashed_publication_leaves_current_and_its_leftovers_are_collected(
    tmp_path, monkeypatch, fault
):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path, result=dense_result())["current"]
    before = files(store, key, "objects")
    store.begin(key, job_id="next", operation="banded", expected_revision=first["revision"])
    prepared = store.prepare(
        key,
        bundle(tmp_path / "candidate", "candidate"),
        result={**dense_result(), "response": {"loss_history": HISTORY[:2]}},
        config_digest="a" * 64,
    )

    def crash(name):
        if name == fault:
            raise KeyboardInterrupt  # the process dies here

    with monkeypatch.context() as patch:
        patch.setattr(objects_module, "_fault_point", crash)
        with pytest.raises(KeyboardInterrupt):
            store.publish(key, job_id="next", prepared=prepared, preview=True)
    store.finish(key, job_id="next", status="interrupted", message="Interrupted")
    store.cleanup(key)
    assert StageStore(tmp_path).snapshot(key)["current"] == first
    assert files(store, key, "objects") == before
    assert not revision_file(store, key, prepared.revision).exists()
    assert not files(store, key, "staging")


def test_collection_follows_pins_and_verifies_every_read(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    with store.lease(key, first["revision"]) as pinned:
        assert label(pinned) == "complete artifact fit"
        publish(store, key, tmp_path, job="new-fit")
        store.cleanup(key)
        assert label(pinned) == "complete artifact fit"
    store.cleanup(key)
    assert not revision_file(store, key, first["revision"]).exists()
    current = store.snapshot(key)["current"]
    object_file(store, key, current["revision"], "plan.json").write_bytes(b"corrupt")
    with store.lease(key, current["revision"]) as pinned:
        with pytest.raises(HauteValidationError, match="checksum|size"):
            label(pinned)


def test_a_discarded_preview_stays_readable_while_pinned(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    preview = publish(
        store, key, tmp_path, job="preview", operation="pruned", parent=first["revision"]
    )["preview"]
    with store.lease(key, preview["revision"]) as pinned:
        store.discard(key, preview_revision=preview["revision"])
        store.cleanup(key)
        assert label(pinned) == "complete artifact preview"
    store.cleanup(key)
    assert not revision_file(store, key, preview["revision"]).exists()


def test_recovery_preserves_current_and_does_not_interrupt_live_owner(tmp_path, monkeypatch):
    import haute.modelling._stage_store as stage_store

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    store.begin(key, job_id="lost", operation="banded", expected_revision=first["revision"])
    assert store.recover(key)["job"]["status"] == "running"

    monkeypatch.setattr(stage_store, "process_is_alive", lambda pid: False)
    recovered = StageStore(tmp_path).recover(key)
    assert recovered["job"]["status"] == "interrupted"
    assert recovered["current"] == first


def test_corrupt_preview_cannot_replace_valid_current(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    state = publish(
        store, key, tmp_path, job="preview", operation="pruned", parent=first["revision"]
    )
    path = revision_file(store, key, state["preview"]["revision"])
    path.write_bytes(path.read_bytes().replace(b'"label"', b'"lebal"'))
    with pytest.raises(HauteValidationError, match="checksum|size"):
        store.apply(
            key, expected_revision=first["revision"], preview_revision=state["preview"]["revision"]
        )
    assert pointer_revision(store, key) == first["revision"]


@pytest.mark.parametrize("target", ["object", "revision"])
def test_store_symlinks_cannot_be_followed(tmp_path, target):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    path = (
        object_file(store, key, first["revision"], "plan.json")
        if target == "object"
        else revision_file(store, key, first["revision"])
    )
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(HauteValidationError, match="regular file"):
        with store.lease(key, first["revision"]) as pinned:
            label(pinned)
    assert outside.exists()


def test_readers_never_block_publication(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        with store.lease(key, first["revision"]) as pinned:
            writer = pool.submit(publish, store, key, tmp_path, job="new-build")
            assert writer.result(timeout=5)["current"]["revision"] != first["revision"]
            assert label(pinned) == "complete artifact fit"
    with pytest.raises(ConflictError):
        with store.lease(key, first["revision"]):
            pytest.fail("leased a replaced revision")


def test_remove_detaches_all_revisions_but_respects_a_reader_pin(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    with store.lease(key, first["revision"]) as pinned:
        store.remove(key)
        assert label(pinned) == "complete artifact fit"
        assert store.snapshot(key)["current"] is None
    store.cleanup(key)
    assert not files(store, key, "revisions") and not files(store, key, "objects")
    store.begin(key, job_id="new", operation="full", expected_revision=None)
    with pytest.raises(ConflictError, match="running"):
        store.remove(key)


@pytest.mark.parametrize("level", [".haute", ".haute/staged-training", "key"])
def test_store_rejects_symlinked_ancestors_before_writing(tmp_path, level):
    store = StageStore(tmp_path / "project")
    key = store.key("p", "n", "m")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = store.root / key if level == "key" else tmp_path / "project" / level
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HauteValidationError, match="symlink"):
        store.begin(key, job_id="fit", operation="full", expected_revision=None)
    assert not list(outside.iterdir())


def test_reading_an_unbuilt_model_stores_nothing(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    assert store.recover(key, compact=True)["current"] is None
    assert store.snapshot(key)["current"] is None
    store.cleanup(key)
    assert {path.name for path in store.root.iterdir()} == {key}
    assert {path.name for path in store._directory(key).iterdir()} <= {"manifest.lock"}


@pytest.mark.parametrize("older", [2, 3])
def test_an_older_store_reads_as_empty_until_a_full_build_reclaims_it(tmp_path, older):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    directory = store._directory(key)
    directory.mkdir(parents=True)
    old = "a" * 32
    legacy = [directory / f"{old}.hstage", directory / f"{old}.result.json"]
    for path in legacy:
        path.write_bytes(b"old format")
    (directory / "manifest.json").write_text(json.dumps({"schema_version": older, "current": {}}))
    assert store.snapshot(key)["current"] is None
    assert all(path.exists() for path in legacy)  # a read interprets and removes nothing
    publish(store, key, tmp_path)
    assert not any(path.exists() for path in legacy)
    assert json.loads((directory / "manifest.json").read_bytes())["schema_version"] == 4


@pytest.mark.parametrize("operation", ["banded", "smoothed"])
def test_every_postfit_stage_is_reversible(tmp_path, operation):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    store.begin(key, job_id="candidate", operation=operation, expected_revision=first["revision"])
    prepared = store.prepare(
        key, bundle(tmp_path / "candidate.bundle", "candidate"), result={}, config_digest="a" * 64
    )
    state = store.publish(key, job_id="candidate", prepared=prepared, preview=True)
    assert state["current"] == first
    restarted = StageStore(tmp_path)
    assert (
        restarted.apply(
            key, expected_revision=first["revision"], preview_revision=state["preview"]["revision"]
        )["current"]
        == state["preview"]
    )


def test_named_checkpoint_retains_restores_and_releases_revision(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    state = store.checkpoint(
        key, action="save", expected_revision=first["revision"], name="Baseline"
    )
    saved = state["checkpoints"][0]
    second = publish(store, key, tmp_path, job="replacement")["current"]
    store.cleanup(key)
    restarted = StageStore(tmp_path)
    with restarted.lease(key, first["revision"]) as pinned:
        assert label(pinned) == "complete artifact fit"
    with pytest.raises(ConflictError):
        restarted.checkpoint(
            key, action="restore", expected_revision=first["revision"], checkpoint_id=saved["id"]
        )
    restored = restarted.checkpoint(
        key, action="restore", expected_revision=second["revision"], checkpoint_id=saved["id"]
    )
    assert restored["current"] == first
    assert restored["preview"] is None and restored["final"] is None
    publish(store, key, tmp_path, job="third")
    current = store.snapshot(key)["current"]
    store.checkpoint(
        key, action="delete", expected_revision=current["revision"], checkpoint_id=saved["id"]
    )
    store.cleanup(key)
    assert not revision_file(store, key, first["revision"]).exists()


def test_checkpoint_refuses_active_jobs_and_corrupt_restore(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    state = store.checkpoint(
        key, action="save", expected_revision=first["revision"], name="Original"
    )
    saved = state["checkpoints"][0]
    store.begin(key, job_id="busy", operation="full", expected_revision=None)
    with pytest.raises(ConflictError):
        store.checkpoint(
            key, action="delete", expected_revision=first["revision"], checkpoint_id=saved["id"]
        )
    store.finish(key, job_id="busy", status="cancelled", message="Cancelled")
    second = publish(store, key, tmp_path, job="next")["current"]
    path = revision_file(store, key, first["revision"])
    path.write_bytes(path.read_bytes().replace(b'"label"', b'"lebal"'))
    with pytest.raises(HauteValidationError, match="checksum|size"):
        store.checkpoint(
            key, action="restore", expected_revision=second["revision"], checkpoint_id=saved["id"]
        )
    assert pointer_revision(store, key) == second["revision"]


def test_queued_preview_is_atomic_with_parent_and_recovers_without_training(tmp_path, monkeypatch):
    import haute.modelling._stage_store as module

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    store.begin(key, job_id="full", operation="full", expected_revision=None)
    prepared = store.prepare(
        key, bundle(tmp_path / "full.bundle", "full"), result={}, config_digest="a" * 64
    )
    state = store.publish(key, job_id="full", prepared=prepared, next_preview="pruned")
    revision = state["current"]["revision"]
    assert state["next_preview"]["parent_revision"] == revision
    assert state["next_preview"]["status"] == "queued"
    monkeypatch.setattr(module, "process_is_alive", lambda pid: False)
    restarted = StageStore(tmp_path).recover(key)
    assert restarted["next_preview"]["status"] == "interrupted"
    assert restarted["current"] == state["current"]
    store.begin(key, job_id="retry", operation="pruned", expected_revision=revision)
    assert store.snapshot(key)["next_preview"] is None


def test_automatic_preview_claim_requires_the_matching_queue(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    current = publish(store, key, tmp_path)["current"]
    with pytest.raises(ConflictError, match="no longer queued"):
        store.begin(
            key,
            job_id="unexpected",
            operation="pruned",
            expected_revision=current["revision"],
            queued_preview=True,
        )
    assert store.snapshot(key)["job"]["id"] == "fit"


def test_automatic_job_marker_survives_reload_and_manual_rebuild_replaces_preview(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    store.begin(key, job_id="full", operation="full", expected_revision=None)
    source = bundle(tmp_path / "full.bundle", "full")
    prepared = store.prepare(key, source, result={}, config_digest="a" * 64)
    state = store.publish(key, job_id="full", prepared=prepared, next_preview="pruned")
    revision = state["current"]["revision"]
    store.begin(
        key, job_id="auto", operation="pruned", expected_revision=revision, queued_preview=True
    )
    assert StageStore(tmp_path).snapshot(key)["job"]["automatic"] is True
    candidate = store.prepare(key, source, result={}, config_digest="a" * 64)
    store.publish(key, job_id="auto", prepared=candidate, preview=True)
    rebuilt = publish(store, key, tmp_path, job="rebuild")
    assert rebuilt["job"]["automatic"] is False
    assert rebuilt["preview"] is None
    assert rebuilt["current"]["revision"] != revision


def test_path_apply_cas_publishes_only_the_selected_revision(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    preview = publish(
        store, key, tmp_path, job="path", operation="banded", parent=first["revision"]
    )["preview"]
    source = bundle(tmp_path / "chosen", "exact selected candidate")
    prepared = store.prepare(key, source, result={"point": "point-2"}, config_digest="a" * 64)
    applied = store.apply_path(
        key,
        expected_revision=first["revision"],
        artifact_revision=preview["revision"],
        prepared=prepared,
        next_preview="smoothed",
    )
    assert applied["current"]["revision"] == prepared.revision
    assert applied["current"]["result"] == {"point": "point-2"}
    assert applied["current"]["build_id"] == first["build_id"]
    assert applied["preview"] is None
    assert applied["next_preview"]["parent_revision"] == prepared.revision
    with store.lease(key, prepared.revision) as pinned:
        assert label(pinned) == "exact selected candidate"
    stale = store.prepare(key, source, result={"point": "point-1"}, config_digest="a" * 64)
    with pytest.raises(ConflictError):
        store.apply_path(
            key,
            expected_revision=first["revision"],
            artifact_revision=prepared.revision,
            prepared=stale,
        )
    assert store.snapshot(key) == applied
    assert not files(store, key, "staging")


def test_path_preparation_is_owned_against_cleanup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    current = publish(store, key, tmp_path)["current"]
    source = bundle(tmp_path / "point.bundle", "exact candidate")
    prepared = store.prepare(key, source, result={}, config_digest="a" * 64)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(StageStore(tmp_path).cleanup, key).result(timeout=5)
    assert all(item.path.exists() for item in prepared.staged)
    applied = store.apply_path(
        key,
        expected_revision=current["revision"],
        artifact_revision=current["revision"],
        prepared=prepared,
    )
    with store.lease(key, applied["current"]["revision"]) as pinned:
        assert label(pinned) == "exact candidate"
    abandoned = store.prepare(key, source, result={}, config_digest="a" * 64)
    store.discard_prepared(abandoned)
    assert not any(item.path.exists() for item in abandoned.staged)
    assert not files(store, key, "staging")


def test_large_dense_results_publish_and_checkpoint_without_growing_manifests(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    source = bundle(tmp_path / "bundle", "stage bundle")
    history = [{"iteration": float(i), "train_deviance": 1.0 / i} for i in range(1, 300_001)]
    result = {"response": {"loss_history": history}, "graph_digest": "c" * 64}
    store.begin(key, job_id="full", operation="full", expected_revision=None)
    first = store.prepare(key, source, result=result, config_digest="a" * 64)
    store.publish(key, job_id="full", prepared=first)
    store.begin(key, job_id="band", operation="banded", expected_revision=first.revision)
    preview = store.prepare(key, source, result=result, config_digest="a" * 64)
    store.publish(key, job_id="band", prepared=preview, preview=True)
    restarted = StageStore(tmp_path)
    state = restarted.snapshot(key)
    assert state["schema_version"] == 1
    assert state["current"]["result"] == state["preview"]["result"] == result
    applied = restarted.apply(
        key, expected_revision=first.revision, preview_revision=preview.revision
    )
    saved = restarted.checkpoint(
        key, action="save", expected_revision=preview.revision, name="Banding"
    )
    assert saved["checkpoints"][0]["record"] == applied["current"]
    pointer = store._directory(key) / "manifest.json"
    assert pointer.stat().st_size < 8192
    raw = json.loads(pointer.read_bytes())
    assert raw["schema_version"] == 4
    assert "result" not in raw["current"]
    assert revision_file(store, key, preview.revision).stat().st_size < 8192
    # Both revisions share one history object and one compact view.
    assert len(files(store, key, "objects")) == 3


def test_an_oversized_result_envelope_is_refused_before_anything_is_published(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    publish(store, key, tmp_path)
    before = store.snapshot(key)
    with pytest.raises(HauteValidationError, match="size limit"):
        store.prepare(
            key,
            bundle(tmp_path / "candidate", "candidate"),
            result={"data": "x" * (objects_module.REVISION_LIMIT + 1)},
            config_digest="a" * 64,
        )
    assert store.snapshot(key) == before
    assert not files(store, key, "staging")


@pytest.mark.parametrize(
    "fault",
    [
        "revision-missing",
        "revision-digest",
        "revision-size",
        "revision-symlink",
        "revision-json",
        "revision-nonfinite",
        "object-missing",
        "object-digest",
        "object-symlink",
        "object-hardlink",
        "pointer-json",
        "pointer-record",
    ],
)
@pytest.mark.parametrize("compact", [False, True])
def test_corrupt_revisions_and_objects_are_refused(tmp_path, fault, compact):
    import hashlib
    import os

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path, result=dense_result())["current"]
    pointer = store._directory(key) / "manifest.json"
    raw = json.loads(pointer.read_bytes())
    kind, _, damage = fault.partition("-")
    manifest = store._objects(key).read_revision(first["revision"])
    path = {
        "revision": revision_file(store, key, first["revision"]),
        # A compact read decodes only the revision's view; a full read, its diagnostics.
        "object": store._objects(key).object_path(
            manifest.objects["view"].sha256
            if compact
            else next(
                ref.sha256 for name, ref in manifest.objects.items() if name.startswith("history")
            )
        ),
        "pointer": pointer,
    }[kind]
    if damage == "missing":
        path.unlink()
    elif damage == "digest" and kind == "revision":
        raw["current"]["sha256"] = "0" * 64
    elif damage == "digest":
        path.write_bytes(path.read_bytes().replace(b"iteration", b"iterathon"))
    elif damage == "size":
        raw["current"]["size"] += 1
    elif damage in {"symlink", "hardlink"}:
        outside = tmp_path / "outside.json"
        path.rename(outside)
        path.symlink_to(outside) if damage == "symlink" else os.link(outside, path)
    elif damage == "record":
        raw["current"]["result"] = {}
    elif kind == "pointer":
        pointer.write_text("{bad")
    else:
        path.write_text('{"x":NaN}' if damage == "nonfinite" else "{bad")
        raw["current"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        raw["current"]["size"] = path.stat().st_size
    if kind != "pointer" or damage == "record":
        pointer.write_text(json.dumps(raw))
    if compact and kind == "revision" and damage not in {"digest", "size"}:
        # A compact status read verifies only the view, by the pointer's digest. A damaged
        # revision manifest is refused by every full read, lease and collection.
        assert StageStore(tmp_path).snapshot(key, compact=True)["current"]["result"]
        compact = False
    if compact and kind == "revision":
        compact = False  # the record's manifest digest and size are checked on full reads
    with pytest.raises(HauteValidationError):
        StageStore(tmp_path).snapshot(key, compact=compact)


def test_cleanup_removes_orphans_and_dead_staging_but_keeps_live_staging(tmp_path):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    current = publish(store, key, tmp_path)["current"]
    objects = store._objects(key)
    orphan_object = objects.objects / ("f" * 64)
    orphan_revision = objects.revisions / f"{'e' * 32}.json"
    dead_staging = objects.staging / f"{'d' * 12}-{'c' * 32}.tmp"
    for path in (orphan_object, orphan_revision, dead_staging):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"left behind")
    live = objects.stage_bytes(b"in flight")
    store.cleanup(key)
    assert not orphan_object.exists() and not orphan_revision.exists()
    assert not dead_staging.exists()
    assert live.path.exists()  # this process is alive and owns it
    live.path.unlink()
    assert store.snapshot(key)["current"] == current


@pytest.mark.parametrize("operation", ["publish", "apply_path"])
def test_result_hydration_failure_cannot_commit_or_delete_the_applied_model(
    tmp_path, monkeypatch, operation
):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    first = publish(store, key, tmp_path)["current"]
    if operation == "publish":
        store.begin(
            key, job_id="candidate", operation="banded", expected_revision=first["revision"]
        )
    prepared = store.prepare(
        key,
        bundle(tmp_path / "candidate.bundle", "new candidate"),
        result={"point": "new"},
        config_digest="a" * 64,
    )
    pointer = store._directory(key) / "manifest.json"
    before = pointer.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("result read failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_hydrate", fail)
        with pytest.raises(OSError, match="result read failed"):
            if operation == "publish":
                store.publish(key, job_id="candidate", prepared=prepared, preview=True)
            else:
                store.apply_path(
                    key,
                    expected_revision=first["revision"],
                    artifact_revision=first["revision"],
                    prepared=prepared,
                )
    assert pointer.read_bytes() == before
    assert StageStore(tmp_path).snapshot(key)["current"] == first
    with store.lease(key, first["revision"]) as pinned:
        assert label(pinned) == "complete artifact fit"
    store.cleanup(key)
    assert not revision_file(store, key, prepared.revision).exists()


@pytest.mark.parametrize("parent_id", ["full", ""])
def test_cancel_followup_uses_metadata_without_decoding_diagnostics(
    tmp_path, monkeypatch, parent_id
):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    store.begin(key, job_id="full", operation="full", expected_revision=None)
    prepared = store.prepare(
        key,
        bundle(tmp_path / "bundle", "full"),
        result={"response": {"job_id": parent_id}},
        config_digest="a" * 64,
    )
    store.publish(key, job_id="full", prepared=prepared, next_preview="pruned")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            store, "_load_result", lambda *args, **kwargs: pytest.fail("decoded diagnostics")
        )
        assert store.cancel_followup(key, parent_job_id=parent_id) is None
    assert store.snapshot(key)["next_preview"] is None


def test_scopes_share_one_process_token(tmp_path):
    store = StageStore(tmp_path)
    keys = [store.key("p", "n", model) for model in ("a", "b")]
    for key in keys:
        publish(store, key, tmp_path, job=f"fit-{key[:6]}")
    with store.lease(keys[0], store.snapshot(keys[0])["current"]["revision"]):
        with store.lease(keys[1], store.snapshot(keys[1])["current"]["revision"]):
            assert len(list((store.root / "processes").glob("*.lock"))) == 1


def test_compact_learning_history_stays_bounded_and_keeps_bag_endpoints() -> None:
    from haute.modelling._stage_views import compact_response

    history = [
        {
            "iteration": float(i),
            "train_deviance": 1.0 / i,
            "bag_1_iteration": float(min(i, 750)),
            "bag_1_train_deviance": 1.0 / min(i, 750),
        }
        for i in range(1, 1001)
    ]
    source = {"loss_history": history, "staged": {"stages": {"full": {"loss_history": history}}}}
    result = compact_response(source)
    for rows in (result["loss_history"], result["staged"]["stages"]["full"]["loss_history"]):
        assert len(rows) <= 200
        assert rows[0] == history[0]
        assert rows[-1] == history[-1]
    assert len(source["loss_history"]) == 1000


@pytest.mark.parametrize("failure", ["reclaim_legacy", "collect"])
def test_housekeeping_failures_after_commit_never_fail_the_publication(
    tmp_path, monkeypatch, failure
):
    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")

    def fail(*args, **kwargs):
        raise OSError("housekeeping failed")

    monkeypatch.setattr(objects_module.StageObjects, failure, fail)
    published = publish(store, key, tmp_path)["current"]
    assert pointer_revision(store, key) == published["revision"]
    assert store.snapshot(key)["current"] == published
    monkeypatch.undo()
    store.cleanup(key)  # a later collection succeeds
    assert store.snapshot(key)["current"] == published


def test_pin_release_failures_after_commit_never_fail_the_publication(tmp_path, monkeypatch):
    from pathlib import Path

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    unlink = Path.unlink

    def refuse(path, *args, **kwargs):
        if path.parent.name == "leases":  # the publication's own pin, released after commit
            raise PermissionError("in use")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    published = publish(store, key, tmp_path)["current"]
    assert pointer_revision(store, key) == published["revision"]
    monkeypatch.undo()
    store.cleanup(key)
    assert store.snapshot(key)["current"] == published


def test_apply_publishes_a_revision_from_its_parents_objects_without_an_archive(tmp_path):
    from haute.modelling._stage_objects import ObjectRef

    store = StageStore(tmp_path)
    key = store.key("p", "n", "m")
    current = publish(store, key, tmp_path)["current"]
    parent = store._objects(key).read_revision(current["revision"])
    prepared = store.prepare_members(
        key,
        {"plan.json": parent.objects["plan.json"], "snapshot/" + "a" * 64: b'{"new":1}'},
        metadata={"label": "applied"},
        result={"point": "point-2"},
        config_digest="a" * 64,
        parent_revision=current["revision"],
    )
    # Only the new JSON object, the result's diagnostics and the compact view are staged.
    assert {item.sha256 for item in prepared.staged} <= {
        ref.sha256 for name, ref in prepared.manifest.objects.items() if name != "plan.json"
    }
    applied = store.apply_path(
        key,
        expected_revision=current["revision"],
        artifact_revision=current["revision"],
        prepared=prepared,
    )
    with store.lease(key, applied["current"]["revision"]) as pinned:
        assert label(pinned) == "complete artifact fit"  # the inherited plan object
    with pytest.raises(HauteValidationError, match="does not hold"):
        store.prepare_members(
            key,
            {"plan.json": ObjectRef("b" * 64, 2, "json")},
            metadata={},
            result={},
            config_digest="a" * 64,
            parent_revision=applied["current"]["revision"],
        )
    assert not files(store, key, "staging")
