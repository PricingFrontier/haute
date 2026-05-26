"""Bundle 6 sub-task C — trust-model contract for stale-config cleanup.

The save service deletes config sidecars that the pipeline previously
owned but no longer needs.  Per the interop investigation
(``notes-haute/git-history/_INTEROP_AND_FILE_OWNERSHIP.md`` item 4 of the
Bundle 6 recommendations), "previously owned" means **referenced by the
on-disk pipeline file's parsed graph**.  Any config file outside that
set is not haute's to delete — it may have been hand-added by the user,
by another tool, or by a prior haute version we don't recognise.

The trust-model statement that pins this contract lives in
``notes-haute/security/SECURITY.md`` §3 "Stable-layer file ownership".

Before this fix, ``SavePipelineService`` was constructed fresh per save
request (``routes/pipeline.py::save_pipeline``).  ``_prev_config_files``
was a per-instance attribute, so on every save it started as ``None`` —
which triggered the full-scan fallback at ``_save_pipeline.py``
lines 474-495.  That fallback walked every ``config/<type>/`` folder and
unlinked any JSON not in the current save's set, regardless of who put
it there.

After this fix:

  - At the start of ``save()``, the service computes
    ``_prev_config_files`` by parsing the on-disk pipeline file and
    collecting the config files that graph references.  If the file
    doesn't exist or can't be parsed, the result is an empty mapping
    — the safe answer when we genuinely don't know what we own.
  - ``_remove_stale_config_files`` uses that pre-computed prev to drive
    a diff-based cleanup (``stale = prev - current``).  The full-scan
    fallback is gone.
  - Unknown config files (hand-added, from another tool, from an older
    haute version) are never in ``prev``, so they're never in
    ``stale``, so they're preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.schemas import SavePipelineRequest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Clean temp project root with a minimal pipeline shell."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("main")\n'
    )
    return tmp_path


def _make_scenario_expander_node(label: str) -> GraphNode:
    """An expander node writes its config to ``config/expander/<label>.json``."""
    return GraphNode(
        id=label,
        data=NodeData(
            label=label,
            nodeType=NodeType.SCENARIO_EXPANDER,
            config={
                "scenario_expander": True,
                "quote_id": "policy_id",
                "column_name": "scenario_value",
                "min_value": 0.8,
                "max_value": 1.2,
                "steps": 11,
            },
        ),
        position={"x": 0.0, "y": 0.0},
    )


def _make_request(nodes: list[GraphNode], source: str = "main.py") -> SavePipelineRequest:
    return SavePipelineRequest(
        name="main",
        description="",
        graph=PipelineGraph(nodes=nodes, edges=[]),
        preamble="",
        source_file=source,
    )


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestSaveDoesNotDeleteUnknownConfigs:
    """Pin the trust-model contract: haute only deletes config files it
    previously wrote (as evidenced by the on-disk pipeline graph that
    referenced them)."""

    def test_first_save_preserves_orphan_in_a_managed_folder(
        self, project_root: Path
    ) -> None:
        """A user (or external tool) hand-added a config file in a haute-
        managed folder before the first save of this pipeline.  The save
        must preserve it: haute never wrote it, so it's not haute's to
        delete.

        Before the fix, this file would be deleted by the full-scan
        fallback at ``_save_pipeline.py:474-495`` because
        ``_prev_config_files is None`` on the fresh save instance.
        """
        from haute.routes._save_pipeline import SavePipelineService

        # Pre-existing orphan in an expander folder, not from any graph
        # haute has ever written.
        config_dir = project_root / "config" / "expander"
        config_dir.mkdir(parents=True)
        orphan = config_dir / "manual_orphan.json"
        orphan.write_text(json.dumps({"manual": True, "preserved": "yes"}))

        svc = SavePipelineService(project_root)
        req = _make_request([_make_scenario_expander_node("new_node")])
        svc.save(req)

        assert orphan.exists(), (
            "Trust-model violation: manual config file in a managed "
            "folder was deleted on first save. The save service "
            "should only delete files referenced by the on-disk "
            "pipeline graph's previous state — manual_orphan.json "
            "was never written by haute."
        )

    def test_orphan_preserved_when_alongside_legitimate_haute_configs(
        self, project_root: Path
    ) -> None:
        """A multi-pipeline / mixed-tool setup: haute owns some configs
        in the expander folder, and there's a manual orphan alongside
        them.  Save must delete neither — the orphan is unknown,
        the legitimate ones are still in the graph."""
        from haute.routes._save_pipeline import SavePipelineService

        # First save: haute writes config/expander/node_a.json.
        svc = SavePipelineService(project_root)
        svc.save(_make_request([_make_scenario_expander_node("node_a")]))

        # User hand-adds an orphan in the same folder.
        orphan = project_root / "config" / "expander" / "manual_orphan.json"
        orphan.write_text(json.dumps({"manual": True}))

        # Second save: still has node_a, no other changes.
        svc2 = SavePipelineService(project_root)
        svc2.save(_make_request([_make_scenario_expander_node("node_a")]))

        node_a_config = project_root / "config" / "expander" / "node_a.json"
        assert node_a_config.exists(), "Legitimate haute config was deleted"
        assert orphan.exists(), (
            "Trust-model violation: manual orphan in the same folder as "
            "haute-managed configs was deleted on a subsequent save. "
            "Only the on-disk graph's referenced configs may be deleted."
        )

    def test_diff_based_cleanup_still_removes_legitimately_orphaned_node_config(
        self, project_root: Path
    ) -> None:
        """The trust-model contract preserves UNKNOWN files; it must
        still delete files haute previously wrote that the current graph
        no longer references.  This is the happy-path stale cleanup."""
        from haute.routes._save_pipeline import SavePipelineService

        # First save: graph has node_a + node_b → two configs written.
        svc1 = SavePipelineService(project_root)
        svc1.save(_make_request([
            _make_scenario_expander_node("node_a"),
            _make_scenario_expander_node("node_b"),
        ]))

        node_a_config = project_root / "config" / "expander" / "node_a.json"
        node_b_config = project_root / "config" / "expander" / "node_b.json"
        assert node_a_config.exists() and node_b_config.exists()

        # Second save: graph drops node_b.  The on-disk pipeline (from
        # save #1) parses to include both → prev = {node_a, node_b}.
        # current = {node_a}.  stale = {node_b}.  Delete node_b only.
        svc2 = SavePipelineService(project_root)
        svc2.save(_make_request([_make_scenario_expander_node("node_a")]))

        assert node_a_config.exists(), "Active node's config was wrongly deleted"
        assert not node_b_config.exists(), (
            "Stale config cleanup is broken: a node removed from the "
            "graph between saves should have its config deleted "
            "(it was haute-written; haute knows it's no longer needed)."
        )

    def test_unparseable_pipeline_results_in_no_deletes(
        self, project_root: Path
    ) -> None:
        """If the on-disk pipeline file can't be parsed (mid-edit
        corruption, encoding issue, etc.), the save can't compute a
        baseline of "what haute previously owned" — so nothing should
        be deleted.  Safest behaviour: preserve everything, log a
        warning, let the save's regenerated .py replace the unparseable
        version."""
        from haute.routes._save_pipeline import SavePipelineService

        # First write some legitimate configs via a normal save.
        svc1 = SavePipelineService(project_root)
        svc1.save(_make_request([_make_scenario_expander_node("node_a")]))

        # Corrupt the on-disk .py.
        py_path = project_root / "main.py"
        py_path.write_text("this is not valid python at all \n@@@@@")

        # Add another orphan to verify it's preserved through the
        # corruption-recovery save path.
        manual_orphan = project_root / "config" / "expander" / "manual.json"
        manual_orphan.write_text(json.dumps({"manual": True}))

        # Second save: graph is just node_a.  Prev parses to {}, so no
        # diff-deletes fire; existing orphan + the original node_a
        # config both survive (node_a is rewritten by the save).
        svc2 = SavePipelineService(project_root)
        svc2.save(_make_request([_make_scenario_expander_node("node_a")]))

        node_a_config = project_root / "config" / "expander" / "node_a.json"
        assert node_a_config.exists(), "Active node's config not present"
        assert manual_orphan.exists(), (
            "Trust-model violation: when the on-disk pipeline is "
            "unparseable, the safe answer is 'preserve everything'. "
            "Deleting an orphan in that state would be acting on no "
            "evidence of ownership."
        )
