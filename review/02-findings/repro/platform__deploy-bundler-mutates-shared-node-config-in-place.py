"""Adversarial repro for claim
`deploy-bundler-mutates-shared-node-config-in-place`.

Claim: collect_artifacts rewrites config["artifact_path"] = basename in
place on the shared node-config dict (shared between full_graph and the
pruned model_copy), making bundling non-idempotent and leaking the
shortened path to other consumers.

This script proves/disproves THREE distinct sub-claims:

  A. (factual core) The mutation leaks to the ORIGINAL full_graph node's
     config dict — full_graph.nodes[i].data.config["artifact_path"] is
     overwritten with the basename. EXPECTED: True (genuine shared state).

  B. (idempotency) Calling collect_artifacts twice on the SAME graph
     object produces a DIFFERENT artifact key / different intermediate
     state depending on call order. The claim's repro_strategy asserts
     this. EXPECTED per claim: differs. We test what actually happens.

  C. (downstream harm) A real consumer that re-derives the artifact key
     from config["artifact_path"] (the deploy scorer's _remap_artifact,
     which keys on f"{nid}__{Path(raw_path).name}") sees a DIFFERENT key
     after the mutation than the bundler produced. EXPECTED per claim:
     mismatch / wrong value. We test what actually happens.

No real MLflow / disk model is used: _download_model_artifact is
monkeypatched to return a tempfile path.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.deploy import _bundler
from haute.deploy._bundler import collect_artifacts
from haute.deploy._pruner import prune_for_deploy
from haute.deploy._scorer import _remap_artifact


def _build_full_graph() -> PipelineGraph:
    """API input -> modelScore(output) graph with a deep artifact_path."""
    api = GraphNode(
        id="src",
        data=NodeData(label="src", nodeType=NodeType.API_INPUT, config={"path": "in.csv"}),
    )
    score = GraphNode(
        id="score",
        data=NodeData(
            label="score",
            nodeType=NodeType.MODEL_SCORE,
            config={
                "sourceType": "run",
                "run_id": "abc123",
                "artifact_path": "models/deep/model.cbm",
                "output": True,  # marks it as the output node
            },
        ),
    )
    from haute._types import GraphEdge

    edge = GraphEdge(id="e1", source="src", target="score")
    return PipelineGraph(nodes=[api, score], edges=[edge], source_file=None)


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _sandbox.set_project_root(tmp_path)

        # Fake the downloaded model file on disk inside the sandbox.
        fake_model = tmp_path / "downloaded_model.cbm"
        fake_model.write_bytes(b"CBM")

        download_calls: list[tuple[str, str]] = []

        def _fake_download(run_id: str, artifact_path: str, pipeline_dir: Path) -> Path:
            download_calls.append((run_id, artifact_path))
            return fake_model

        # Patch the MLflow download + the feature-contract bundling (no contract
        # next to our tempfile, so it is a harmless no-op, but patch defensively).
        orig_download = _bundler._download_model_artifact
        _bundler._download_model_artifact = _fake_download  # type: ignore[assignment]
        try:
            full_graph = _build_full_graph()
            # Sanity: the original config holds the DEEP path before bundling.
            orig_cfg = full_graph.nodes[1].data.config
            assert orig_cfg["artifact_path"] == "models/deep/model.cbm", orig_cfg

            pruned_graph, _kept, _removed = prune_for_deploy(full_graph, "score")

            # Identity check: is the pruned node config the SAME dict object?
            pruned_cfg = pruned_graph.nodes[
                [n.id for n in pruned_graph.nodes].index("score")
            ].data.config
            shared_object = pruned_cfg is orig_cfg
            print(f"[setup] pruned config IS full_graph config object: {shared_object}")

            artifacts1 = collect_artifacts(pruned_graph, ["src"], tmp_path)

            # -------- Sub-claim A: in-memory leak to full_graph --------
            leaked_value = full_graph.nodes[1].data.config["artifact_path"]
            print(f"[A] full_graph config['artifact_path'] after bundling = {leaked_value!r}")
            if leaked_value == "model.cbm":
                print("[A] CONFIRMED: shared-state mutation leaked the basename to full_graph")
            else:
                failures.append(
                    f"[A] expected full_graph config mutated to 'model.cbm', got {leaked_value!r}"
                )

            key1 = next(iter(artifacts1))
            print(f"[A] bundler artifact key (call 1)          = {key1!r}")

            # -------- Sub-claim B: idempotency / call-order dependence --------
            # Second call on the SAME (already-mutated) graph object.
            artifacts2 = collect_artifacts(pruned_graph, ["src"], tmp_path)
            key2 = next(iter(artifacts2))
            value_after_second = full_graph.nodes[1].data.config["artifact_path"]
            print(f"[B] bundler artifact key (call 2)          = {key2!r}")
            print(f"[B] config['artifact_path'] after call 2   = {value_after_second!r}")
            if key1 == key2 and value_after_second == "model.cbm":
                print(
                    "[B] REFUTES non-idempotency: key + config value are STABLE across calls "
                    "(Path('model.cbm').name == 'model.cbm')"
                )
                idempotent = True
            else:
                print("[B] CONFIRMS non-idempotency: state differs by call count")
                idempotent = False

            # -------- Sub-claim C: downstream consumer wrong value --------
            # 1) Manifest path: pruned_graph serialized AFTER mutation, then the
            #    runtime reloads fresh and keys with _remap_artifact.
            manifest_graph = PipelineGraph.model_validate(pruned_graph.model_dump())
            manifest_cfg = manifest_graph.nodes[
                [n.id for n in manifest_graph.nodes].index("score")
            ].data.config
            # The remap dict is what the bundler produced (keyed by basename).
            remap = {key1: str(fake_model)}
            scorer_resolved = _remap_artifact("score", manifest_cfg, remap, "artifact_path")
            print(f"[C-manifest] manifest config['artifact_path'] = {manifest_cfg['artifact_path']!r}")
            print(f"[C-manifest] _remap_artifact resolved          = {scorer_resolved!r}")
            if scorer_resolved == str(fake_model):
                print(
                    "[C-manifest] REFUTES harm: scorer resolves the bundled model correctly "
                    "from the mutated (basename) manifest config"
                )
                manifest_ok = True
            else:
                manifest_ok = False
                failures.append(
                    f"[C-manifest] scorer failed to resolve model: got {scorer_resolved!r}, "
                    f"expected {str(fake_model)!r}"
                )

            # 2) The 'pristine deep path' consumer the claim worries about:
            #    would a scorer keyed on the ORIGINAL deep path differ?
            #    _remap_artifact keys on Path(raw_path).name, so both the deep
            #    path and the basename collapse to the same key.
            key_from_deep = f"score__{Path('models/deep/model.cbm').name}"
            key_from_base = f"score__{Path('model.cbm').name}"
            print(f"[C-key] key from DEEP path  = {key_from_deep!r}")
            print(f"[C-key] key from BASE path  = {key_from_base!r}")
            keys_collapse = key_from_deep == key_from_base == key1
            if keys_collapse:
                print(
                    "[C-key] REFUTES harm: deep-path and basename produce the SAME artifact key "
                    "(Path(...).name is basename-invariant)"
                )
            else:
                failures.append(
                    f"[C-key] keys diverge: deep={key_from_deep!r} base={key_from_base!r} "
                    f"bundler={key1!r}"
                )

        finally:
            _bundler._download_model_artifact = orig_download  # type: ignore[assignment]

    # ---- Verdict ----
    print("\n==== VERDICT ====")
    print(f"A (in-memory leak to full_graph): {'TRUE (mutation leaks)' if leaked_value == 'model.cbm' else 'FALSE'}")
    print(f"B (claimed non-idempotency):      {'REFUTED (idempotent)' if idempotent else 'CONFIRMED'}")
    print(f"C (claimed downstream wrong value): {'REFUTED (no wrong value)' if (manifest_ok and keys_collapse) else 'CONFIRMED'}")

    if failures:
        print("\nUNEXPECTED FAILURES (these would indicate a real harm):")
        for f in failures:
            print("  " + f)
        return 1

    print(
        "\nThe FACTUAL core (shared-state mutation) reproduces, but every claimed "
        "DOWNSTREAM HARM (non-idempotent key, wrong value in a re-resolving consumer) "
        "does NOT materialize: Path(...).name is basename-invariant and resolve_config "
        "re-parses from disk."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
