"""Shared assistant application-service contracts (ASSIST-A05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.assistant._ops import AssistantOperationError, PlanStore
from haute.assistant._wire_ops import OpValidationError

PIPELINE_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="application fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2]})
"""


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    return tmp_path


def _service(project_root: Path, *, published: list[dict] | None = None):
    from haute.assistant._application import PipelineApplicationService

    def publish(source_file):
        if published is not None:
            published.append({"source_file": source_file})
        return "f" * 64

    return PipelineApplicationService(
        project_root=project_root,
        pipeline_root=project_root,
        mutations_readiness=lambda _root: (True, None),
        publish_document_update=publish,
    )


class TestDryRun:
    def test_injected_empty_plan_store_remains_the_service_authority(self, project_root: Path):
        from haute.assistant._application import PipelineApplicationService

        shared_store = PlanStore()
        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda _source: "f" * 64,
            plan_store=shared_store,
        )

        plan = service.dry_run(
            "main.py",
            [{"op": "delete_node", "node": "quotes"}],
        )

        assert service.plan_store is shared_store
        assert shared_store.get(plan.plan_hash) == plan

    def test_dry_run_is_no_write_and_records_exact_plan(self, project_root: Path):
        service = _service(project_root)
        before = (project_root / "main.py").read_bytes()

        plan = service.dry_run(
            "main.py",
            [
                {"op": "add_node", "node_type": "banding", "name": "Age band", "ref": "b"},
                {"op": "add_edge", "source": "quotes", "target": "$b"},
            ],
        )

        assert (project_root / "main.py").read_bytes() == before
        assert plan.base_revision
        assert plan.plan_hash
        assert plan.diff.nodes_added == ("Age_band",)
        assert service.plan_store.get(plan.plan_hash) == plan

    def test_real_save_validation_runs_before_a_plan_is_recorded(self, project_root: Path):
        service = _service(project_root)

        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: testing validation rejection, not specific type
            service.dry_run(
                "main.py",
                [
                    {"op": "add_node", "node_type": "apiInput", "name": "one"},
                    {"op": "add_node", "node_type": "apiInput", "name": "two"},
                ],
            )

        assert len(service.plan_store) == 0

    @pytest.mark.parametrize(
        ("join_config", "message"),
        [
            (
                {
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["x"],
                },
                "unknown config key",
            ),
            (
                {
                    "baseInput": "quotes",
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["x"],
                    "leftOn": ["x"],
                    "rightOn": ["x"],
                },
                "unknown config key",
            ),
        ],
    )
    def test_dry_run_rejects_edge_join_config_that_save_cannot_commit(
        self,
        project_root: Path,
        join_config: dict[str, object],
        message: str,
    ):
        service = _service(project_root)
        before = (project_root / "main.py").read_bytes()

        with pytest.raises(OpValidationError, match=message):
            service.dry_run(
                "main.py",
                [
                    {
                        "op": "add_node",
                        "node_type": "constant",
                        "name": "lookup",
                        "config": {"values": [{"name": "x", "value": 1}]},
                        "ref": "lookup",
                    },
                    {
                        "op": "add_node",
                        "node_type": "edgeJoin",
                        "name": "joined",
                        "config": join_config,
                        "ref": "joined",
                    },
                    {
                        "op": "add_edge",
                        "source": "quotes",
                        "target": "$joined",
                        "target_handle": "base",
                    },
                    {
                        "op": "add_edge",
                        "source": "$lookup",
                        "target": "$joined",
                        "target_handle": "join",
                    },
                ],
            )

        assert (project_root / "main.py").read_bytes() == before
        assert len(service.plan_store) == 0


SHARED_SOURCE_PIPELINE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="schema validation scope fixture")


@pipeline.polars
def shared() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2]})


@pipeline.polars
def broken(shared: pl.LazyFrame) -> pl.LazyFrame:
    return shared.select("absent_column")
"""


@pytest.fixture()
def shared_source_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A saved pipeline that already contains one unresolvable node."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(SHARED_SOURCE_PIPELINE, encoding="utf-8")
    return tmp_path


class TestSchemaValidationScope:
    """Which nodes a plan is answerable for, and which it merely passes near.

    Seeding an edge's *source* pulled every other branch of a shared input into
    validation, so an unrelated pre-existing defect blocked and misattributed
    every edit that touched that input.
    """

    def test_group_by_edit_validates_at_schema_tier(self, project_root: Path):
        """Schema resolution collects nothing, so an authored aggregation is
        verifiable rather than categorically unresolvable."""

        service = _service(project_root)

        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "totals",
                    "config": {
                        "code": 'df = quotes.group_by("x").agg(pl.len().alias("n"))\n',
                    },
                    "ref": "t",
                },
                {"op": "add_edge", "source": "quotes", "target": "$t"},
            ],
        )

        assert plan.verification_tier == "schema"
        assert [item["node"] for item in plan.verification_evidence] == ["totals"]

    def test_new_branch_off_a_shared_input_ignores_the_input_s_other_branches(
        self, shared_source_project: Path
    ):
        service = _service(shared_source_project)

        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "doubled",
                    "config": {"code": 'df = shared.with_columns(y=pl.col("x") * 2)\n'},
                    "ref": "d",
                },
                {"op": "add_edge", "source": "shared", "target": "$d"},
            ],
        )

        assert plan.verification_tier == "schema"
        assert [item["node"] for item in plan.verification_evidence] == ["doubled"]
        assert plan.validation_warnings == ()

    def test_untouched_collateral_that_already_failed_becomes_a_warning(
        self, shared_source_project: Path
    ):
        """Changing `shared` legitimately reaches its whole downstream cone,
        which contains a node that was already broken. The plan did not cause
        that, so it is reported and excluded from evidence rather than
        rejecting the edit."""

        service = _service(shared_source_project)

        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "update_node",
                    "node": "shared",
                    "config": {"code": 'df = pl.LazyFrame({"x": [1, 2, 3]})\n'},
                }
            ],
        )

        assert plan.verification_tier == "structural"
        assert plan.verification_evidence == ()
        assert any(
            warning.startswith("pre_existing_schema_failure:broken")
            for warning in plan.validation_warnings
        ), plan.validation_warnings

    def test_a_node_this_plan_changed_is_never_excused(self, shared_source_project: Path):
        """The plan owns every node it authors. An empty node fails on the
        saved pipeline by construction, so excusing changed nodes would
        silently accept exactly the broken code the analyst asked for."""

        service = _service(shared_source_project)

        with pytest.raises(AssistantOperationError) as excinfo:
            service.dry_run(
                "main.py",
                [
                    {
                        "op": "update_node",
                        "node": "broken",
                        "config": {"code": 'df = shared.select("still_absent")\n'},
                    }
                ],
            )

        assert excinfo.value.code == "schema_unresolvable"
        assert "still_absent" in str(excinfo.value)

    async def test_pre_existing_warning_is_hash_stable_through_apply(
        self, shared_source_project: Path
    ):
        """Validation warnings are hashed into the plan authority and `apply`
        recomputes them, so the warning must be deterministic. Carrying the
        engine's message would embed estimated row counts and scan byte sizes
        and turn an ordinary apply into a spurious `invalid_plan`."""

        service = _service(shared_source_project)
        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "update_node",
                    "node": "shared",
                    "config": {"code": 'df = pl.LazyFrame({"x": [1, 2, 3]})\n'},
                }
            ],
        )
        assert plan.validation_warnings == ("pre_existing_schema_failure:broken",)

        result = await service.apply("main.py", plan.plan_hash)

        assert result.plan_hash == plan.plan_hash
        assert result.verification_tier == "structural"


class TestApply:
    async def test_dry_run_and_apply_replay_share_the_verified_plan_builder(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._application as application_module

        built = []
        original = application_module.build_verified_plan

        def track_build(*args, **kwargs):
            verified = original(*args, **kwargs)
            built.append(verified)
            return verified

        monkeypatch.setattr(application_module, "build_verified_plan", track_build)
        service = _service(project_root)
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
        )

        await service.apply("main.py", plan.plan_hash)

        assert len(built) == 2
        assert built[0].plan == plan
        assert built[1].plan == plan
        assert built[0].result_graph == built[1].result_graph

    async def test_low_risk_apply_is_exact_single_use_and_truthfully_verified(
        self, project_root: Path
    ):
        published: list[dict] = []
        service = _service(project_root, published=published)
        plan = service.dry_run(
            "main.py",
            [
                {"op": "add_node", "node_type": "banding", "name": "Age band", "ref": "b"},
                {"op": "add_edge", "source": "quotes", "target": "$b"},
            ],
        )

        result = await service.apply("main.py", plan.plan_hash)

        saved = (project_root / "main.py").read_text(encoding="utf-8")
        assert "def Age_band(" in saved
        assert result.plan_hash == plan.plan_hash
        assert result.base_revision == plan.base_revision
        assert result.result_revision != result.base_revision
        assert result.expected_diff == result.actual_diff
        assert result.verification_tier == "schema"
        assert result.verification_evidence
        assert result.graph_fingerprint == "f" * 64
        assert published == [{"source_file": "main.py"}]

        with pytest.raises(AssistantOperationError) as exc:
            await service.apply("main.py", plan.plan_hash)
        assert exc.value.code == "plan_already_applied"

    async def test_precommit_failure_requires_fresh_dry_run_before_retry(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        service = _service(project_root)
        original_commit = service._commit
        commit_attempts = 0

        def fail_once(source_file, after):
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise RuntimeError("simulated pre-commit failure")
            return original_commit(source_file, after)

        monkeypatch.setattr(service, "_commit", fail_once)
        operations = [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}]
        first = service.dry_run("main.py", operations)

        with pytest.raises(RuntimeError, match="simulated pre-commit failure"):
            await service.apply("main.py", first.plan_hash)

        with pytest.raises(AssistantOperationError) as direct_retry:
            await service.apply("main.py", first.plan_hash)
        assert direct_retry.value.code == "plan_aborted"
        assert "def quotes(" in (project_root / "main.py").read_text(encoding="utf-8")

        revalidated = service.dry_run("main.py", operations)
        assert revalidated.plan_hash == first.plan_hash

        result = await service.apply("main.py", revalidated.plan_hash)

        assert commit_attempts == 2
        assert result.plan_hash == first.plan_hash
        assert "def renamed(" in (project_root / "main.py").read_text(encoding="utf-8")

    async def test_stale_revision_is_rejected_before_write_or_publish(self, project_root: Path):
        published: list[dict] = []
        service = _service(project_root, published=published)
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
        )
        changed = PIPELINE_SOURCE + "\n# changed outside the assistant\n"
        (project_root / "main.py").write_text(changed, encoding="utf-8")

        with pytest.raises(AssistantOperationError) as exc:
            await service.apply("main.py", plan.plan_hash)
        assert exc.value.code == "stale_revision"
        assert (project_root / "main.py").read_text(encoding="utf-8") == changed
        assert published == []

    async def test_retrieved_project_source_is_rechecked_from_the_stored_plan_manifest(
        self, project_root: Path
    ):
        from haute.assistant._application import PipelineApplicationService

        evidence = project_root / "docs.md"
        evidence.write_text("Sensitivity: internal\nterritory definition", encoding="utf-8")
        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda _source: "f" * 64,
            project_sources=lambda _source: (evidence,),
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
        )
        assert "content:docs.md" in dict(plan.source_manifest)

        evidence.write_text("Sensitivity: internal\nchanged definition", encoding="utf-8")
        with pytest.raises(AssistantOperationError) as exc:
            await service.apply("main.py", plan.plan_hash)

        assert exc.value.code == "stale_project_evidence"
        assert "def quotes(" in (project_root / "main.py").read_text(encoding="utf-8")

    async def test_destructive_graph_authoring_applies_without_confirmation(
        self, project_root: Path
    ):
        service = _service(project_root)
        plan = service.dry_run(
            "main.py",
            [{"op": "delete_node", "node": "quotes"}],
        )

        result = await service.apply("main.py", plan.plan_hash)

        assert result.actual_diff.nodes_removed == ("quotes",)

    async def test_mutation_readiness_is_rechecked_at_apply(self, project_root: Path):
        from haute.assistant._application import PipelineApplicationService

        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (False, "working branch is not ready"),
            publish_document_update=lambda _source: "f" * 64,
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
        )

        with pytest.raises(AssistantOperationError) as exc:
            await service.apply("main.py", plan.plan_hash)
        assert exc.value.code == "authority_denied"
        assert "renamed" not in (project_root / "main.py").read_text(encoding="utf-8")

    async def test_committed_verification_failure_is_published_and_never_retried(
        self, project_root: Path
    ):
        from haute.assistant._application import CommittedVerificationError
        from haute.routes._helpers import parse_pipeline_to_graph

        published: list[dict] = []
        parse_count = 0

        def parser(path: Path):
            nonlocal parse_count
            parse_count += 1
            if parse_count >= 3:
                raise RuntimeError("reparse failed after commit")
            return parse_pipeline_to_graph(path)

        from haute.assistant._application import PipelineApplicationService

        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda source: published.append({"source": source}) or "f" * 64,
            parse_graph=parser,
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
        )

        with pytest.raises(CommittedVerificationError) as exc:
            await service.apply("main.py", plan.plan_hash)

        assert exc.value.result["verification_status"] == "failed"
        assert exc.value.result["graph_fingerprint"] == "f" * 64
        assert "def renamed(" in (project_root / "main.py").read_text(encoding="utf-8")
        assert published == [{"source": "main.py"}]
        with pytest.raises(AssistantOperationError) as second:
            await service.apply("main.py", plan.plan_hash)
        assert second.value.code == "plan_already_applied"


def test_save_service_exposes_the_same_no_write_validation_used_by_save(project_root: Path):
    from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
    from haute.routes._save_pipeline import SavePipelineService

    graph = PipelineGraph(
        nodes=[
            GraphNode(id="one", data=NodeData(label="One", nodeType=NodeType.API_INPUT)),
            GraphNode(id="two", data=NodeData(label="Two", nodeType=NodeType.API_INPUT)),
        ],
        source_file="main.py",
    )
    service = SavePipelineService(project_root, project_root)

    with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: testing validation rejection, not specific type
        service.validate_graph(graph, source_file="main.py")


def test_dry_run_binds_schema_evidence_into_executable_plan(
    project_root: Path,
) -> None:
    service = _service(project_root)

    plan = service.dry_run(
        "main.py",
        [
            {
                "op": "add_node",
                "node_type": "polars",
                "name": "derive_x",
                "ref": "derive",
                "config": {
                    "code": "df = quotes.with_columns(pl.col('x').alias('x_copy'))",
                },
            },
            {"op": "add_edge", "source": "quotes", "target": "$derive"},
        ],
    )

    assert plan.verification_tier == "schema"
    assert plan.verification_evidence
    evidence = plan.verification_evidence[0]
    assert evidence["kind"] == "node_schema_resolved"
    assert evidence["node"] == "derive_x"
    assert evidence["column_count"] == 2
    assert len(evidence["schema_sha256"]) == 64


def test_dry_run_rejects_trace_regression_polars_plan_before_storing(
    project_root: Path,
) -> None:
    service = _service(project_root)

    with pytest.raises(AssistantOperationError) as exc_info:
        service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "bad_fill",
                    "ref": "bad",
                    "config": {
                        "code": (
                            "df = quotes.with_columns(pl.lit(True).alias('flag'))"
                            ".fill_null({'x': 0})"
                        ),
                    },
                },
                {"op": "add_edge", "source": "quotes", "target": "$bad"},
            ],
        )

    assert exc_info.value.code == "schema_unresolvable"
    assert "bad_fill" in str(exc_info.value)
    assert len(service.plan_store) == 0


def test_dry_run_rejects_invalid_banding_semantics_before_storing(
    project_root: Path,
) -> None:
    service = _service(project_root)

    with pytest.raises(Exception, match="banding.*age"):
        service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "banding",
                    "name": "age_band",
                    "ref": "band",
                    "config": {
                        "factors": [
                            {
                                "banding": "age",
                                "column": "x",
                                "outputColumn": "age_band",
                                "rules": [{"key": "0-10", "value": 10}],
                            }
                        ],
                    },
                },
                {"op": "add_edge", "source": "quotes", "target": "$band"},
            ],
        )

    assert len(service.plan_store) == 0


class TestOutputTargetEvidence:
    def test_output_terminal_evidence_resolves_without_collecting(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """EXEC-P08: ``_resolve_target_evidence`` declares ``schema_only=True``.

        An OUTPUT terminal used to assemble its whole document at build time, so
        the declaration was false for exactly the graphs it mattered for. The
        declaration now reaches the OUTPUT builder, which describes the document
        from its mapping and its source schemas instead of assembling it.
        """
        import polars as pl

        from haute.assistant._application import _PreparedGraph, _resolve_target_evidence
        from tests.conftest import make_edge, make_graph, make_output_config

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = pl.LazyFrame({'quote_id': ['q1'], 'premium': [1.5]})"
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(
                                ["quote_id", "premium"], source_port="quotes"
                            ),
                        },
                    },
                ],
                "edges": [make_edge("quotes", "out").model_dump()],
            }
        )

        def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("target evidence must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        evidence = _resolve_target_evidence(_PreparedGraph.build(graph), "out")

        assert evidence["kind"] == "node_schema_resolved"
        assert evidence["node"] == "out"
        assert evidence["shape"] == "frame"
        assert evidence["column_count"] == 2
