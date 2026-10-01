"""Shared assistant application-service contracts (ASSIST-A05)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from haute.assistant._ops import AssistantOperationError, PlanStore
from haute.assistant._wire_ops import OpValidationError
from haute.schemas import AssistantChangeRecord

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


def _free_code_steps(source: str, code: str) -> list[dict[str, str]]:
    """A Transform's steps: start from *source*, then one free-code card."""

    return [
        {"id": "start", "kind": "source", "input": source},
        {"id": "logic", "kind": "free_code", "code": code},
    ]


def _service(project_root: Path, *, published: list[dict] | None = None):
    from haute.assistant._application import PipelineApplicationService

    def publish(source_file, change):
        if published is not None:
            published.append({"source_file": source_file, "change": change})
        return "f" * 64

    return PipelineApplicationService(
        project_root=project_root,
        pipeline_root=project_root,
        mutations_readiness=lambda _root: (True, None),
        publish_document_update=publish,
    )


class TestDryRun:
    @pytest.mark.parametrize(
        ("operations", "tier", "egress"),
        [
            pytest.param(
                [{"op": "delete_node", "node": "quotes"}],
                "structural",
                "none",
                id="nothing-resolved",
            ),
            pytest.param(
                [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "doubled",
                        "ref": "d",
                        "config": {
                            "steps": _free_code_steps(
                                "quotes", "# Double x\ndf = df.with_columns(y=pl.col('x') * 2)"
                            )
                        },
                    },
                    {"op": "add_edge", "source": "quotes", "target": "$d"},
                ],
                "schema",
                "schema-resolution",
                id="node-code-ran",
            ),
        ],
    )
    def test_plan_egress_says_whether_node_code_ran_over_data(
        self, project_root: Path, operations: list[dict[str, object]], tier: str, egress: str
    ):
        plan = _service(project_root).dry_run("main.py", operations, summary="Test plan.").plan

        assert (plan.verification_tier, plan.egress) == (tier, egress)
        assert plan.as_dict()["egress"] == egress

    def test_injected_empty_plan_store_remains_the_service_authority(self, project_root: Path):
        from haute.assistant._application import PipelineApplicationService

        shared_store = PlanStore()
        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda _source, _change: "f" * 64,
            plan_store=shared_store,
        )

        plan = service.dry_run(
            "main.py",
            [{"op": "delete_node", "node": "quotes"}],
            summary="Test plan.",
        ).plan

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
            summary="Test plan.",
        ).plan

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
                summary="Test plan.",
            ).plan

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
                summary="Test plan.",
            ).plan

        assert (project_root / "main.py").read_bytes() == before
        assert len(service.plan_store) == 0

    def test_an_occurrence_named_like_its_inner_node_fails_at_dry_run(self, tmp_path: Path):
        """Codegen would refuse this graph at apply, so dry-run refuses it first."""

        from fastapi import HTTPException

        from haute.assistant._assets import materialize_example_bundle

        materialize_example_bundle("reusable_submodel", tmp_path / "b")
        source = tmp_path / "b" / "pipeline.py"
        text = source.read_text(encoding="utf-8")
        colliding = text.replace('"enrichment"', '"enriched"')
        assert colliding != text
        source.write_bytes(colliding.encode("utf-8"))
        service = _service(tmp_path / "b")

        with pytest.raises(HTTPException, match="sanitize to the same Python function name"):
            service.dry_run(
                "pipeline.py", [{"op": "update_preamble", "preamble": None}], summary="Test plan."
            ).plan

        assert source.read_bytes() == colliding.encode("utf-8")
        assert len(service.plan_store) == 0

    def test_an_edge_out_of_model_training_is_rejected_at_dry_run(self, tmp_path: Path):
        from fastapi import HTTPException

        from haute.assistant._assets import materialize_example_bundle

        materialize_example_bundle("model_lifecycle", tmp_path / "b")
        service = _service(tmp_path / "b")

        with pytest.raises(HTTPException, match="'train'.*has no output") as raised:
            service.dry_run(
                "pipeline.py",
                [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "after",
                        "config": {"steps": _free_code_steps("train", "df = df")},
                        "ref": "a",
                    },
                    {"op": "add_edge", "source": "train", "target": "$a"},
                ],
                summary="Test plan.",
            ).plan

        assert raised.value.status_code == 400
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
                        "steps": _free_code_steps(
                            "quotes", 'df = df.group_by("x").agg(pl.len().alias("n"))'
                        ),
                    },
                    "ref": "t",
                },
                {"op": "add_edge", "source": "quotes", "target": "$t"},
            ],
            summary="Test plan.",
        ).plan

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
                    "config": {
                        "steps": _free_code_steps(
                            "shared", 'df = df.with_columns(y=pl.col("x") * 2)'
                        )
                    },
                    "ref": "d",
                },
                {"op": "add_edge", "source": "shared", "target": "$d"},
            ],
            summary="Test plan.",
        ).plan

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
            summary="Test plan.",
        ).plan

        assert plan.verification_tier == "structural"
        assert plan.verification_evidence == ()
        # Every target was excused, but resolving them still ran node code.
        assert plan.egress == "schema-resolution"
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
                summary="Test plan.",
            ).plan

        assert excinfo.value.code == "schema_unresolvable"
        # The failure is kept for the tool boundary to render under the
        # egress policy; the message itself names only the node.
        assert str(excinfo.value) == "Schema validation failed for node 'broken'."
        assert "still_absent" in str(excinfo.value.failure)

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
            summary="Test plan.",
        ).plan
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
            summary="Test plan.",
        ).plan

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
            summary="Test plan.",
        ).plan

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
        assert published == [{"source_file": "main.py", "change": result.change}]

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

        def fail_once(source_file, after, receipt):
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise RuntimeError("simulated pre-commit failure")
            return original_commit(source_file, after, receipt)

        monkeypatch.setattr(service, "_commit", fail_once)
        operations = [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}]
        first = service.dry_run("main.py", operations, summary="Test plan.").plan

        with pytest.raises(RuntimeError, match="simulated pre-commit failure"):
            await service.apply("main.py", first.plan_hash)

        with pytest.raises(AssistantOperationError) as direct_retry:
            await service.apply("main.py", first.plan_hash)
        assert direct_retry.value.code == "plan_aborted"
        assert "def quotes(" in (project_root / "main.py").read_text(encoding="utf-8")

        revalidated = service.dry_run("main.py", operations, summary="Test plan.").plan
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
            summary="Test plan.",
        ).plan
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
            publish_document_update=lambda _source, _change: "f" * 64,
            project_sources=lambda _source: (evidence,),
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
            summary="Test plan.",
        ).plan
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
            summary="Test plan.",
        ).plan

        result = await service.apply("main.py", plan.plan_hash)

        assert result.actual_diff.nodes_removed == ("quotes",)

    async def test_mutation_readiness_is_rechecked_at_apply(self, project_root: Path):
        from haute.assistant._application import PipelineApplicationService

        service = PipelineApplicationService(
            project_root=project_root,
            pipeline_root=project_root,
            mutations_readiness=lambda _root: (False, "working branch is not ready"),
            publish_document_update=lambda _source, _change: "f" * 64,
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
            summary="Test plan.",
        ).plan

        with pytest.raises(AssistantOperationError) as exc:
            await service.apply("main.py", plan.plan_hash)
        assert exc.value.code == "authority_denied"
        assert "renamed" not in (project_root / "main.py").read_text(encoding="utf-8")

    async def test_committed_verification_failure_is_published_and_never_retried(
        self, project_root: Path
    ):
        from haute._pipeline_recovery import load_pipeline_editor_document
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
            publish_document_update=lambda source, change: (
                published.append({"source": source, "change": change}) or "f" * 64
            ),
            parse_graph=parser,
        )
        plan = service.dry_run(
            "main.py",
            [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
            summary="Test plan.",
        ).plan

        with pytest.raises(CommittedVerificationError) as exc:
            await service.apply("main.py", plan.plan_hash)

        assert exc.value.result["verification_status"] == "failed"
        assert exc.value.result["graph_fingerprint"] == "f" * 64
        assert "def renamed(" in (project_root / "main.py").read_text(encoding="utf-8")
        # The committed save keeps a change record, so the analyst can undo it: the
        # plan's change, the save's commit and the revision the save produced.
        change = AssistantChangeRecord.model_validate(exc.value.result["change"])
        assert change.id == plan.plan_hash
        assert [(node.id, node.change, node.renamed_from) for node in change.changes.nodes] == [
            ("renamed", "renamed", "quotes")
        ]
        assert (
            change.revision
            == load_pipeline_editor_document(
                project_root / "main.py", project_root=project_root
            ).source_revision
        )
        assert published == [{"source": "main.py", "change": change}]
        with pytest.raises(AssistantOperationError) as second:
            await service.apply("main.py", plan.plan_hash)
        assert second.value.code == "plan_already_applied"


#: A four-node build: a Transform, a Banding, a Rating Step and a Transform.
_FOUR_NODE_BATCH: list[dict[str, object]] = [
    {
        "op": "add_node",
        "node_type": "polars",
        "name": "features",
        "ref": "f",
        "config": {
            "steps": _free_code_steps(
                "quotes", "# Derive driver age\ndf = df.with_columns(driver_age=pl.col('x') * 20)"
            )
        },
    },
    {
        "op": "add_node",
        "node_type": "banding",
        "name": "bands",
        "ref": "b",
        "config": {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "driver_age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"boundary": "24", "label": "17-24"},
                        {"boundary": "", "label": "25+"},
                    ],
                    "default": None,
                }
            ]
        },
    },
    {
        "op": "add_node",
        "node_type": "ratingStep",
        "name": "rated",
        "ref": "r",
        "config": {
            "tables": [
                {
                    "factors": ["age_band"],
                    "outputColumn": "age_relativity",
                    "entries": [
                        {"age_band": "17-24", "value": 1.8},
                        {"age_band": "25+", "value": 1.05},
                    ],
                    "defaultValue": 1.0,
                }
            ],
            "combinedOutputs": [],
            "steps": [],
        },
    },
    {
        "op": "add_node",
        "node_type": "polars",
        "name": "priced",
        "ref": "p",
        "config": {
            "steps": _free_code_steps(
                "rated", "# Price it\ndf = df.with_columns(premium=pl.col('age_relativity') * 350)"
            )
        },
    },
    {"op": "add_edge", "source": "quotes", "target": "$f"},
    {"op": "add_edge", "source": "$f", "target": "$b"},
    {"op": "add_edge", "source": "$b", "target": "$r"},
    {"op": "add_edge", "source": "$r", "target": "$p"},
]
#: Configuration values and code text the four-node batch writes.
_BATCH_VALUES = (
    "driver_age",
    "age_band",
    "17-24",
    "25+",
    "age_relativity",
    "1.8",
    "1.05",
    "350",
    "breakpoints",
    "with_columns",
    "pl.col",
    "Derive driver age",
    "Price it",
)
_SUMMARY = "Band driver age, rate it and price the quote."


def _provider_payload(name: str, result: Mapping[str, object]) -> str:
    """The tool result exactly as the provider receives it."""

    from haute.assistant._tools import _bounded_tool_result

    return json.dumps(_bounded_tool_result(name, result), separators=(",", ":"))


class TestChangeCard:
    """After every apply the analyst sees a value-free card of what was saved."""

    async def test_a_four_node_build_is_carded_without_values_in_a_small_payload(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._application as application_module
        from haute.routes._save_pipeline import SavePipelineService

        # A ledger-captured save, so the payloads carry a commit and its parent.
        commit, parent = "a" * 40, "b" * 40
        monkeypatch.setattr(
            SavePipelineService, "_capture_save_in_ledger", lambda *_args: (commit, False)
        )
        monkeypatch.setattr(
            application_module, "commit_parent", lambda sha, _cwd: parent if sha == commit else None
        )
        service = _service(project_root)
        dry_run = service.dry_run(
            "main.py",
            _FOUR_NODE_BATCH,
            summary=_SUMMARY,
            assumptions=["Drivers under 25 are the young band."],
        )
        result = await service.apply("main.py", dry_run.plan.plan_hash)

        change = result.change
        assert change.summary == _SUMMARY
        assert change.assumptions == ["Drivers under 25 are the young band."]
        assert [
            (node.id, node.type, node.change, node.steps, node.fields)
            for node in change.changes.nodes
        ] == [
            ("features", "Polars", "added", ["source", "free_code"], []),
            ("bands", "Banding", "added", None, []),
            ("rated", "Rating Step", "added", [], []),
            ("priced", "Polars", "added", ["source", "free_code"], []),
        ]
        assert [(edge.source, edge.target) for edge in change.changes.edges_added] == [
            ("bands", "rated"),
            ("features", "bands"),
            ("quotes", "features"),
            ("rated", "priced"),
        ]
        assert change.changes.edges_removed == []
        assert (change.git_sha, change.parent_sha) == (commit, parent)

        record = change.model_dump_json()
        for value in _BATCH_VALUES:
            assert value not in record, value
        assert '"code"' not in record and '"intent"' not in record

        dry_run_payload = _provider_payload("dry_run_graph_edits", dry_run.as_dict())
        apply_payload = _provider_payload("apply_graph_plan", result.as_dict())
        assert len(dry_run_payload.encode()) < 1024, dry_run_payload
        assert len(apply_payload.encode()) < 1024, apply_payload
        for payload in (dry_run_payload, apply_payload):
            assert "normalized_operations" not in payload
            assert "expected_diff" not in payload and "actual_diff" not in payload
            for value in _BATCH_VALUES:
                assert value not in payload, value
        assert json.loads(dry_run_payload)["operations"] == len(_FOUR_NODE_BATCH)
        assert AssistantChangeRecord.model_validate(json.loads(apply_payload)["change"]) == change

    async def test_step_edits_renames_and_field_changes_read_in_words(self, project_root: Path):
        service = _service(project_root)
        build = service.dry_run("main.py", _FOUR_NODE_BATCH, summary=_SUMMARY)
        await service.apply("main.py", build.plan.plan_hash)

        edit = service.dry_run(
            "main.py",
            [
                {
                    "op": "edit_steps",
                    "node": "features",
                    "edits": [
                        {
                            "insert_after": "logic",
                            "step": {
                                "kind": "free_code",
                                "code": "# Flag young\ndf = df.with_columns(young=pl.col('x') < 2)",
                            },
                        }
                    ],
                },
                {"op": "rename_node", "node": "priced", "new_name": "premium"},
                {
                    "op": "update_node",
                    "node": "bands",
                    "config": {
                        "factors": [
                            {
                                "banding": "breakpoints",
                                "column": "driver_age",
                                "outputColumn": "age_band",
                                "rules": [
                                    {"boundary": "21", "label": "17-21"},
                                    {"boundary": "", "label": "22+"},
                                ],
                                "default": None,
                            }
                        ],
                        "selected_columns": [],
                    },
                },
            ],
            summary="Flag young drivers, rename the pricing node and rebalance the bands.",
        )
        result = await service.apply("main.py", edit.plan.plan_hash)

        chips = {node.id: node for node in result.change.changes.nodes}
        assert chips["features"].change == "changed"
        assert chips["features"].steps == ["source", "free_code", "free_code"]
        assert chips["features"].steps_changed == 1
        assert chips["premium"].change == "renamed"
        assert chips["premium"].renamed_from == "priced"
        assert chips["bands"].change == "changed"
        assert chips["bands"].fields == ["factors", "selected columns"]
        assert "17-21" not in result.change.model_dump_json()

    def test_an_identical_dry_run_replaces_the_receipt_of_an_unapplied_plan(
        self, project_root: Path
    ):
        service = _service(project_root)
        operations = [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}]
        first = service.dry_run("main.py", operations, summary="Rename quotes.")
        second = service.dry_run(
            "main.py",
            operations,
            summary="Rename the quotes node.",
            assumptions=["Nothing reads it."],
        )

        assert first.plan.plan_hash == second.plan.plan_hash
        receipt = service.plan_store.receipt(second.plan.plan_hash)
        assert (receipt.summary, receipt.assumptions) == (
            "Rename the quotes node.",
            ("Nothing reads it.",),
        )

    @pytest.mark.parametrize(
        ("summary", "assumptions"),
        [
            pytest.param("", (), id="empty-summary"),
            pytest.param("x" * 161, (), id="long-summary"),
            pytest.param("Rename quotes.", ("",), id="empty-assumption"),
            pytest.param("Rename quotes.", ("a",) * 6, id="too-many-assumptions"),
        ],
    )
    def test_a_receipt_outside_its_bounds_is_refused(
        self, project_root: Path, summary: str, assumptions: tuple[str, ...]
    ):
        with pytest.raises(AssistantOperationError) as caught:
            _service(project_root).dry_run(
                "main.py",
                [{"op": "rename_node", "node": "quotes", "new_name": "renamed"}],
                summary=summary,
                assumptions=assumptions,
            )
        assert caught.value.code == "invalid_request"


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
                    "steps": _free_code_steps(
                        "quotes", "df = df.with_columns(pl.col('x').alias('x_copy'))"
                    ),
                },
            },
            {"op": "add_edge", "source": "quotes", "target": "$derive"},
        ],
        summary="Test plan.",
    ).plan

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
                        "steps": _free_code_steps(
                            "quotes",
                            "df = df.with_columns(pl.lit(True).alias('flag')).fill_null({'x': 0})",
                        ),
                    },
                },
                {"op": "add_edge", "source": "quotes", "target": "$bad"},
            ],
            summary="Test plan.",
        ).plan

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
            summary="Test plan.",
        ).plan

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
        evidence, _inferred = _resolve_target_evidence(_PreparedGraph.build(graph), "out")

        assert evidence["kind"] == "node_schema_resolved"
        assert evidence["node"] == "out"
        assert evidence["shape"] == "frame"
        assert evidence["column_count"] == 2


# ---------------------------------------------------------------------------
# Stepped-node write contract (ASSIST-01)
# ---------------------------------------------------------------------------

#: Each palette-default stepped node on a valid base, fed by the quotes Data Input.
_STEPPED_BASES: dict[str, tuple[str, dict]] = {
    "quotes": ("dataInput", {"path": "quotes.parquet"}),
    "t": ("polars", {}),
    "rated": (
        "ratingStep",
        {
            "tables": [
                {
                    "factors": ["region"],
                    "outputColumn": "rate",
                    "defaultValue": "1.0",
                    "entries": [{"region": "north", "value": "1.25"}],
                }
            ]
        },
    ),
    "expanded": (
        "scenarioExpander",
        {
            "quote_id": "quote_id",
            "column_name": "scenario_value",
            "min_value": 0.9,
            "max_value": 1.1,
            "stepCount": 3,
            "step_column": "scenario_index",
        },
    ),
    "explored": ("explore", {}),
    "loaded": ("externalFile", {"path": "lookup.json", "fileType": "json"}),
    "scored": (
        "modelScore",
        {
            "sourceType": "run",
            "run_id": "abc123",
            "artifact_path": "model.cbm",
            "task": "regression",
            "output_column": "prediction",
        },
    ),
}
_FRAME_START = ("quotes", "rated", "expanded", "explored", "loaded", "scored")
_FREE_CODE = "# Flag each row\ndf = df.with_columns(flag=pl.lit(1))"


def _palette_config(node_type: str, base: dict) -> dict:
    from haute._config_io import palette_default_config
    from haute._types import NodeType

    defaults = palette_default_config(NodeType(node_type))
    if base.get("sourceType") == "run":
        defaults = {"steps": defaults["steps"]}
    return {**defaults, **base}


@pytest.fixture()
def stepped_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A saved pipeline holding every stepped type at its palette default."""

    import json

    import polars as pl

    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._sandbox import set_project_root
    from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
    from haute.routes._save_pipeline import SavePipelineService

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)  # restored by the autouse _restore_project_root
    pl.DataFrame(
        {"quote_id": ["q1", "q2"], "region": ["north", "south"], "x": [1.0, 2.0]}
    ).write_parquet(tmp_path / "quotes.parquet")
    (tmp_path / "lookup.json").write_text(json.dumps({"north": 1}), encoding="utf-8")
    (tmp_path / "main.py").write_text(
        'import haute\n\npipeline = haute.Pipeline("main", description="stepped")\n',
        encoding="utf-8",
    )
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id=node_id,
                type=node_type,
                data=NodeData(
                    label=node_id,
                    nodeType=NodeType(node_type),
                    config=_palette_config(node_type, base),
                ),
            )
            for node_id, (node_type, base) in _STEPPED_BASES.items()
        ],
        edges=[
            GraphEdge(id=f"quotes->{node_id}", source="quotes", target=node_id)
            for node_id in _STEPPED_BASES
            if node_id != "quotes"
        ],
    )
    SavePipelineService(project_root=tmp_path, pipeline_root=tmp_path).save_graph_transactionally(
        graph=graph,
        name="main",
        description="stepped",
        preamble=None,
        source_file="main.py",
        base_revision=load_pipeline_editor_document(
            tmp_path / "main.py", project_root=tmp_path
        ).source_revision,
    )
    return tmp_path


@pytest.fixture()
def scorable_model():
    """Score ``x`` with a stub model wherever the engine loads the run's model."""

    from unittest.mock import MagicMock, patch

    import numpy as np

    from haute._mlflow_io import ScoringModel

    model = MagicMock()
    model.feature_names_ = ["x"]
    model.predict.return_value = np.array([1.0, 2.0])
    model.get_cat_feature_indices.return_value = []
    del model.predict_proba
    stub = ScoringModel(
        model=model, feature_names=["x"], cat_feature_names=frozenset(), flavor="catboost"
    )
    with patch("haute._mlflow_io.load_mlflow_model", return_value=stub):
        yield stub


def _project_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".json"}
    }


def _reparsed_config(root: Path, node_id: str) -> dict:
    from haute.routes._helpers import parse_pipeline_to_graph

    graph = parse_pipeline_to_graph(root / "main.py")
    return next(node for node in graph.nodes if node.id == node_id).data.config


@pytest.mark.usefixtures("scorable_model")
class TestSteppedWrites:
    @pytest.mark.parametrize("node", list(_STEPPED_BASES))
    def test_the_authored_config_projection_survives_save_and_reparse(
        self, stepped_project: Path, node: str
    ):
        """Pins the `node_config` projection against the real save for every
        code-carrying palette type: derived and editor-only fields stay out of
        it, so an untouched save never fails its own verification."""

        from haute._types import NodeData, NodeType
        from haute.assistant._ops import _authored_config_projection

        node_type, base = _STEPPED_BASES[node]
        planned = NodeData(
            label=node, nodeType=NodeType(node_type), config=_palette_config(node_type, base)
        ).config
        reparsed = _reparsed_config(stepped_project, node)

        assert (
            _authored_config_projection(NodeType(node_type), reparsed)
            == _authored_config_projection(NodeType(node_type), planned)
            == {"steps": []}
        )

    @pytest.mark.parametrize("node", list(_STEPPED_BASES))
    @pytest.mark.parametrize("with_steps_removed", [False, True])
    def test_a_code_write_to_a_palette_default_stepped_node_is_refused(
        self, stepped_project: Path, node: str, with_steps_removed: bool
    ):
        service = _service(stepped_project)
        before = _project_files(stepped_project)
        config: dict[str, object] = {"code": "df = df.head(1)"}
        if with_steps_removed:
            config["steps"] = None

        with pytest.raises(OpValidationError) as excinfo:
            service.dry_run(
                "main.py",
                [{"op": "update_node", "node": node, "config": config}],
                summary="Test plan.",
            ).plan

        assert '{"id": "logic", "kind": "free_code", "code": "..."}' in str(excinfo.value)
        assert ('"kind": "source"' in str(excinfo.value)) == (node == "t")
        assert _project_files(stepped_project) == before
        assert len(service.plan_store) == 0

    @pytest.mark.parametrize("node", ["t", *_FRAME_START])
    async def test_a_free_code_write_applies_and_the_saved_body_holds_it(
        self, stepped_project: Path, node: str
    ):
        logic = {"id": "logic", "kind": "free_code", "code": _FREE_CODE}
        steps = (
            [{"id": "start", "kind": "source", "input": "quotes"}, logic]
            if node == "t"
            else [logic]
        )
        service = _service(stepped_project)
        plan = service.dry_run(
            "main.py",
            [{"op": "update_node", "node": node, "config": {"steps": steps}}],
            summary="Test plan.",
        ).plan

        result = await service.apply("main.py", plan.plan_hash)

        assert result.verification_tier == "schema"
        saved_source = (stepped_project / "main.py").read_text(encoding="utf-8")
        assert "df = df.with_columns(flag=pl.lit(1))" in saved_source
        saved = _reparsed_config(stepped_project, node)
        assert saved["steps"] == steps
        assert "_steps_discarded" not in saved and "_steps_error" not in saved

    @pytest.mark.parametrize("node", list(_STEPPED_BASES))
    async def test_the_advertised_new_logic_applies_verbatim(
        self, stepped_project: Path, node: str
    ):
        """The step list a stepped descriptor advertises, spelled the same way
        in the system prompt, applies unchanged but for its edge placeholder."""

        import json

        from haute.assistant._catalog import EDGE_NAME_PLACEHOLDER, capability_manifest
        from haute.assistant._loop import build_system_prompt

        node_type, _base = _STEPPED_BASES[node]
        descriptor = next(item for item in capability_manifest().nodes if item.id == node_type)
        advertised = descriptor.as_dict()["step_authoring"]["new_logic"]
        prompt = build_system_prompt(source_file="main.py")
        assert f"`{json.dumps(advertised)}`" in prompt
        steps = json.loads(json.dumps(advertised).replace(EDGE_NAME_PLACEHOLDER, "quotes"))
        service = _service(stepped_project)

        plan = service.dry_run(
            "main.py",
            [{"op": "update_node", "node": node, "config": {"steps": steps}}],
            summary="Test plan.",
        ).plan
        await service.apply("main.py", plan.plan_hash)

        saved = _reparsed_config(stepped_project, node)
        assert saved["steps"] == steps
        assert "_steps_discarded" not in saved and "_steps_error" not in saved

    async def test_a_data_input_added_with_a_path_saves_the_palette_input_type(
        self, stepped_project: Path
    ):
        service = _service(stepped_project)
        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "dataInput",
                    "name": "claims",
                    "ref": "claims",
                    "config": {"path": "quotes.parquet"},
                },
                {"op": "add_node", "node_type": "explore", "name": "look", "ref": "look"},
                {"op": "add_edge", "source": "$claims", "target": "$look"},
            ],
            summary="Test plan.",
        ).plan

        await service.apply("main.py", plan.plan_hash)

        saved = _reparsed_config(stepped_project, "claims")
        assert saved["inputType"] == "file"
        assert saved["steps"] == []

    async def test_a_saved_config_that_misses_its_digest_is_a_verification_failure(
        self, stepped_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._application import CommittedVerificationError

        service = _service(stepped_project)
        steps = [{"id": "logic", "kind": "free_code", "code": _FREE_CODE}]
        plan = service.dry_run(
            "main.py",
            [{"op": "update_node", "node": "rated", "config": {"steps": steps}}],
            summary="Test plan.",
        ).plan
        original_commit = service._commit

        def commit_other_steps(source_file, after, receipt):
            node = next(item for item in after.nodes if item.id == "rated")
            other = [{"id": "logic", "kind": "free_code", "code": "df = df.head(1)"}]
            after.nodes[after.nodes.index(node)] = node.with_config(
                {**node.data.config, "steps": other}
            )
            return original_commit(source_file, after, receipt)

        monkeypatch.setattr(service, "_commit", commit_other_steps)

        with pytest.raises(CommittedVerificationError) as excinfo:
            await service.apply("main.py", plan.plan_hash)

        assert excinfo.value.result["verification_error_code"] == "postcondition_failed"

    def test_steps_a_save_would_discard_fail_the_dry_run(
        self, stepped_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._application as application_module

        generate = application_module.graph_to_code_multi

        def hand_edited_body(*args, **kwargs):
            files = generate(*args, **kwargs)
            return {
                path: source.replace("flag=pl.lit(1)", "flag=pl.lit(2)")
                for path, source in files.items()
            }

        monkeypatch.setattr(application_module, "graph_to_code_multi", hand_edited_body)
        service = _service(stepped_project)
        before = _project_files(stepped_project)
        steps = [{"id": "logic", "kind": "free_code", "code": _FREE_CODE}]

        with pytest.raises(AssistantOperationError) as excinfo:
            service.dry_run(
                "main.py",
                [{"op": "update_node", "node": "rated", "config": {"steps": steps}}],
                summary="Test plan.",
            ).plan

        assert excinfo.value.code == "op_not_applied"
        assert "'rated'" in str(excinfo.value)
        assert _project_files(stepped_project) == before
        assert len(service.plan_store) == 0


MODELLING_PIPELINE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="modelling readiness fixture")


@pipeline.polars
def policies() -> pl.LazyFrame:
    return pl.LazyFrame({"exposure": [1.0, 0.5], "age": [30.0, 40.0], "claims": [0, 1]})


@pipeline.modelling(config="config/model_training/train.json")
def train(policies): ...
"""

_VALID_GLM = {
    "target": "claims",
    "algorithm": "glm",
    "family": "poisson",
    "link": "log",
    "offset": "exposure",
    "terms": {"age": {"type": "linear"}},
    "evaluation": {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    },
}


@pytest.fixture()
def modelling_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A saved pipeline whose modelling node is the editor's unfinished ``{}``."""

    from haute._sandbox import set_project_root

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)  # restored by the autouse _restore_project_root
    (tmp_path / "config" / "model_training").mkdir(parents=True)
    (tmp_path / "config" / "model_training" / "train.json").write_text("{}", encoding="utf-8")
    (tmp_path / "main.py").write_text(MODELLING_PIPELINE, encoding="utf-8")
    return tmp_path


class TestWrittenNodesAreReady:
    """Malformed values fail save validation; an assistant-written Modelling or
    Load File node must also be complete and match its input and file."""

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            pytest.param(
                {**_VALID_GLM, "algorithm": "GLM", "family": "Poisson"},
                "Unknown algorithm 'GLM'",
                id="algorithm-case",
            ),
            pytest.param(
                {**_VALID_GLM, "family": "poison"}, "Unknown GLM family 'poison'", id="family"
            ),
            pytest.param(
                {"target": "claims", "algorithm": "gbm", "loss_function": "Poisson"},
                "Unknown algorithm 'gbm'",
                id="algorithm",
            ),
            pytest.param(
                {
                    "target": "claims",
                    "algorithm": "catboost",
                    "loss_function": "Poisson",
                    "feature_columns": ["age", "claims"],
                },
                "Target column 'claims' is also listed in feature_columns",
                id="target-is-a-feature",
            ),
        ],
    )
    def test_a_malformed_value_fails_save_validation_at_dry_run(
        self, modelling_project: Path, config: dict, message: str
    ):
        from fastapi import HTTPException

        service = _service(modelling_project)

        with pytest.raises(HTTPException) as raised:
            service.dry_run(
                "main.py",
                [{"op": "update_node", "node": "train", "config": config}],
                summary="Test plan.",
            ).plan

        assert raised.value.status_code == 400
        assert message in raised.value.detail
        assert len(service.plan_store) == 0

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            pytest.param(
                {**_VALID_GLM, "offset": "exposure_years"},
                "Training input is missing required column(s): ['exposure_years']",
                id="offset-column-missing",
            ),
            pytest.param(
                {**_VALID_GLM, "family": None}, "GLM config has no family", id="glm-no-family"
            ),
            pytest.param(
                {**_VALID_GLM, "target": None}, "Modelling config has no target", id="no-target"
            ),
            pytest.param(
                {**_VALID_GLM, "evaluation": None},
                "Modelling config has no evaluation object",
                id="no-evaluation",
            ),
        ],
    )
    def test_an_unready_modelling_node_fails_the_dry_run(
        self, modelling_project: Path, config: dict, message: str
    ):
        service = _service(modelling_project)

        with pytest.raises(AssistantOperationError) as raised:
            service.dry_run(
                "main.py",
                [{"op": "update_node", "node": "train", "config": config}],
                summary="Test plan.",
            ).plan

        assert raised.value.code == "node_not_ready"
        assert "'train'" in str(raised.value)
        assert message in str(raised.value)
        assert len(service.plan_store) == 0

    def test_a_modelling_node_beyond_the_diff_limit_is_still_proved_ready(
        self, modelling_project: Path
    ):
        import json

        fillers = [f"a{index:02d}" for index in range(50)]
        (modelling_project / "main.py").write_text(
            MODELLING_PIPELINE
            + "".join(
                f"\n\n@pipeline.polars\ndef {name}() -> pl.LazyFrame:\n"
                f"    return pl.LazyFrame({{'x': [1]}})\n"
                for name in fillers
            ),
            encoding="utf-8",
        )
        (modelling_project / "config" / "model_training" / "train.json").write_text(
            json.dumps(_VALID_GLM), encoding="utf-8"
        )
        service = _service(modelling_project)
        operations = [
            *({"op": "update_node", "node": name, "config": {}} for name in fillers),
            {"op": "update_node", "node": "train", "config": {"target": None}},
        ]

        with pytest.raises(AssistantOperationError) as raised:
            service.dry_run("main.py", operations, summary="Test plan.").plan

        assert raised.value.code == "node_not_ready"
        assert "'train'" in str(raised.value)
        assert len(service.plan_store) == 0

    def test_a_caller_declares_at_most_the_declared_postcondition_cap(
        self, modelling_project: Path
    ):
        from haute.assistant._wire_ops import MAX_DECLARED_POSTCONDITIONS

        service = _service(modelling_project)
        declared = [{"kind": "node_exists", "node": "train"}] * (MAX_DECLARED_POSTCONDITIONS + 1)

        with pytest.raises(AssistantOperationError) as raised:
            service.dry_run(
                "main.py",
                [{"op": "update_node", "node": "train", "config": _VALID_GLM}],
                postconditions=declared,
                summary="Test plan.",
            ).plan

        assert raised.value.code == "invalid_plan"
        assert f"at most {MAX_DECLARED_POSTCONDITIONS} postconditions" in str(raised.value)
        assert len(service.plan_store) == 0

    def test_a_load_file_whose_path_does_not_exist_fails_the_dry_run(self, modelling_project: Path):
        service = _service(modelling_project)

        with pytest.raises(AssistantOperationError) as raised:
            service.dry_run(
                "main.py",
                [
                    {
                        "op": "add_node",
                        "node_type": "externalFile",
                        "name": "lookup",
                        "ref": "l",
                        "config": {"path": "missing.json", "fileType": "json"},
                    },
                    {"op": "add_edge", "source": "policies", "target": "$l"},
                ],
                summary="Test plan.",
            ).plan

        assert raised.value.code == "node_not_ready"
        assert "'lookup'" in str(raised.value)
        assert "does not exist" in str(raised.value)

    def test_a_load_file_that_does_not_load_as_its_type_fails_the_dry_run(
        self, modelling_project: Path
    ):
        (modelling_project / "lookup.json").write_text("{not json", encoding="utf-8")
        service = _service(modelling_project)

        with pytest.raises(AssistantOperationError) as raised:
            service.dry_run(
                "main.py",
                [
                    {
                        "op": "add_node",
                        "node_type": "externalFile",
                        "name": "lookup",
                        "ref": "l",
                        "config": {"path": "lookup.json", "fileType": "json"},
                    },
                    {"op": "add_edge", "source": "policies", "target": "$l"},
                ],
                summary="Test plan.",
            ).plan

        assert raised.value.code == "node_not_ready"
        assert "JSONDecodeError" in str(raised.value)
        assert "not json" not in str(raised.value)

    async def test_the_valid_glm_applies(self, modelling_project: Path):
        service = _service(modelling_project)

        plan = service.dry_run(
            "main.py",
            [{"op": "update_node", "node": "train", "config": _VALID_GLM}],
            summary="Test plan.",
        ).plan
        await service.apply("main.py", plan.plan_hash)

        saved = _reparsed_config(modelling_project, "train")
        assert {key: saved.get(key) for key in _VALID_GLM} == _VALID_GLM

    async def test_an_edit_beside_an_empty_modelling_node_applies(self, modelling_project: Path):
        service = _service(modelling_project)

        plan = service.dry_run(
            "main.py",
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "doubled",
                    "ref": "d",
                    "config": {
                        "steps": _free_code_steps(
                            "policies", "# Double age\ndf = df.with_columns(age2=pl.col('age') * 2)"
                        )
                    },
                },
                {"op": "add_edge", "source": "policies", "target": "$d"},
            ],
            summary="Test plan.",
        ).plan
        await service.apply("main.py", plan.plan_hash)

        assert _reparsed_config(modelling_project, "train") == {}


@pytest.fixture()
def csv_project(project_root: Path) -> Path:
    from haute._sandbox import set_project_root

    set_project_root(project_root)  # restored by the autouse _restore_project_root
    (project_root / "claims.csv").write_text("id;amount\n1;10\n2;20\n", encoding="utf-8")
    return project_root


def _add_input_and_transform(config: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "op": "add_node",
            "node_type": "dataInput",
            "name": "claims",
            "ref": "c",
            "config": config,
        },
        {
            "op": "add_node",
            "node_type": "polars",
            "name": "doubled",
            "ref": "d",
            "config": {
                "steps": _free_code_steps(
                    "claims", "# Double amount\ndf = df.with_columns(y=pl.col('amount') * 2)"
                )
            },
        },
        {"op": "add_edge", "source": "$c", "target": "$d"},
    ]


class TestInferredInputSchemas:
    """A new local file input resolves its schema from the file at dry-run."""

    _CSV = {
        "inputType": "file",
        "format": "csv",
        "path": "claims.csv",
        "arguments": {"separator": ";", "schema_overrides": {"id": "str"}},
    }

    async def test_a_new_csv_input_and_its_transform_apply_at_the_inferred_tier(
        self, csv_project: Path
    ):
        import json
        from hashlib import sha256

        from haute._input_providers import source_cache_identity
        from haute._polars_io_registry import INFERRED_SCHEMA_ROWS
        from haute._source_cache import SourceCacheStore

        service = _service(csv_project)
        plan = service.dry_run(
            "main.py", _add_input_and_transform(self._CSV), summary="Test plan."
        ).plan

        # The separator splits two columns and the override keeps `id` a string.
        columns = [
            {"name": "id", "dtype": "String"},
            {"name": "amount", "dtype": "Int64"},
            {"name": "y", "dtype": "Int64"},
        ]
        digest = sha256(
            json.dumps({"columns": columns}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert plan.verification_tier == "schema"
        assert [dict(item) for item in plan.verification_evidence] == [
            {
                "kind": "node_schema_resolved",
                "node": "doubled",
                "shape": "frame",
                "column_count": 3,
                "schema_sha256": digest,
            },
            {
                "kind": "input_schema_inferred",
                "node": "claims",
                "tier": "inferred",
                "format": "csv",
                "inference_rows": INFERRED_SCHEMA_ROWS,
            },
        ]

        result = await service.apply("main.py", plan.plan_hash)

        assert result.verification_evidence[-len(plan.verification_evidence) :] == (
            plan.verification_evidence
        )
        identity = source_cache_identity(self._CSV, base_dir=csv_project)
        assert SourceCacheStore(csv_project).status(identity).state == "missing"


def _quote_table(label: str, path: str, columns: dict[str, str]) -> dict[str, object]:
    return {
        "path": path,
        "label": label,
        "emit": True,
        "row_id_column": None,
        "columns": [
            {
                "name": name,
                "path": f"{path}.{name}",
                "type": dtype,
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            }
            for name, dtype in columns.items()
        ],
    }


_QUOTE_INPUT = {
    "path": "quote.json",
    "tables": [
        _quote_table("policy", "$[:]", {"quote_id": "str", "age": "int"}),
        _quote_table("drivers", "$[:].drivers[:]", {"driver_id": "str", "main": "bool"}),
    ],
}


def _add_quote_input_and_transform(config: dict[str, object]) -> list[dict[str, object]]:
    return [
        {"op": "add_node", "node_type": "apiInput", "name": "quote", "ref": "q", "config": config},
        {
            "op": "add_node",
            "node_type": "polars",
            "name": "aged",
            "ref": "a",
            "config": {
                "steps": _free_code_steps(
                    "policy", "# Age next year\ndf = df.with_columns(next_age=pl.col('age') + 1)"
                )
            },
        },
        {"op": "add_edge", "source": "$q", "target": "$a", "source_handle": "policy"},
    ]


class TestDeclaredQuoteInputSchemas:
    """A new Quote Input resolves each table's schema from its declared contract."""

    async def test_a_new_quote_input_and_a_transform_apply_at_the_declared_tier(
        self, project_root: Path
    ):
        from haute._json_shred._snapshots import api_input_snapshot_source
        from haute._source_cache import SourceCacheStore

        service = _service(project_root)
        plan = service.dry_run(
            "main.py", _add_quote_input_and_transform(_QUOTE_INPUT), summary="Test plan."
        ).plan

        assert plan.verification_tier == "schema"
        declared = [
            dict(item)
            for item in plan.verification_evidence
            if item["kind"] == "input_schema_declared"
        ]
        assert declared == [
            {
                "kind": "input_schema_declared",
                "node": "quote",
                "table": "policy",
                "tier": "declared",
                "column_count": 2,
            },
        ]

        result = await service.apply("main.py", plan.plan_hash)

        assert result.verification_evidence[-len(plan.verification_evidence) :] == (
            plan.verification_evidence
        )
        store = SourceCacheStore(project_root)
        source = api_input_snapshot_source(_QUOTE_INPUT, project_root / "quote.json")
        assert {store.status(table.identity).state for table in source.tables} == {"missing"}

    def test_a_column_without_a_declared_type_is_refused_by_the_contract_validator(
        self, project_root: Path
    ):
        from haute._api_input_schema import ApiInputSchemaError
        from haute.assistant._application import SchemaUnresolvableError

        untyped = _quote_table("policy", "$[:]", {"quote_id": "str", "age": "int"})
        del untyped["columns"][1]["type"]  # type: ignore[index]
        config = {"path": "quote.json", "tables": [untyped]}

        with pytest.raises(SchemaUnresolvableError) as refused:
            _service(project_root).dry_run(
                "main.py", _add_quote_input_and_transform(config), summary="Test plan."
            ).plan

        # A preview meets the same refusal, so the remedy is the column's type.
        assert isinstance(refused.value.failure, ApiInputSchemaError)
        assert "column=age" in str(refused.value.failure)

    def test_a_table_labelled_like_an_existing_node_is_refused_at_dry_run(self, project_root: Path):
        """A parameter named `quotes` would bind both the frame and the node `quotes`.

        Dry-run passed and apply then failed reparsing its own file, so save
        validation, which dry-run runs, refuses it before anything is written.
        """
        from fastapi import HTTPException

        config = {
            "path": "quote.json",
            "tables": [_quote_table("quotes", "$[:]", {"quote_id": "str", "age": "int"})],
        }
        ops = _add_quote_input_and_transform(config)
        ops[1]["config"]["steps"][0]["input"] = "quotes"  # type: ignore[index]
        ops[2]["source_handle"] = "quotes"
        source = project_root / "main.py"
        service = _service(project_root)

        with pytest.raises(HTTPException) as raised:
            service.dry_run("main.py", ops, summary="Test plan.").plan

        assert raised.value.status_code == 400
        detail = str(raised.value.detail)
        assert "table 'quotes'" in detail
        assert "Quote Input 'quote'" in detail
        assert "node 'quotes'" in detail
        assert source.read_text(encoding="utf-8") == PIPELINE_SOURCE
        assert len(service.plan_store) == 0
