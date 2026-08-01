"""Load the pricing assistant's packaged authoring knowledge.

The exemplar files are package data, not importable pipeline modules.  Keeping
them as text is important: importing an exemplar would execute user-shaped
pipeline code and would make the examples depend on the project's optional
runtime environment.  The parser is used only when an example is requested,
so the graph returned to the assistant is produced by the same code path as a
saved pipeline.
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from hashlib import sha256
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from haute.assistant._render import render_pipeline_graph

if TYPE_CHECKING:
    from haute._types import PipelineGraph

_ASSET_PACKAGE = "haute.assistant"
_ASSET_DIR = "assets"
_EXAMPLES_DIR = "examples"
_GUIDE_NAME = "authoring_guide.md"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ASSERTION_TIERS = frozenset({"fast", "negative", "ordinary"})
_REVIEW_CLASSES = frozenset({"engineering", "pricing"})
_ASSERTION_CHECKS = frozenset(
    {
        "adversarial_rejection",
        "deployment_preflight",
        "dry_run",
        "execute",
        "model_scoring",
        "optimisation",
        "optimiser_apply",
        "parse",
        "trace",
        "training",
    }
)
_ORDINARY_CHECKS = frozenset(
    {
        "deployment_preflight",
        "model_scoring",
        "optimisation",
        "optimiser_apply",
        "training",
    }
)
_CHECK_RESOURCE_ROLES = {
    "adversarial_rejection": "negative_cases",
    "deployment_preflight": "deployment_config",
    "dry_run": "plan_request",
    "model_scoring": "model_scoring_config",
    "optimisation": "optimiser_config",
    "optimiser_apply": "optimiser_apply_config",
    "trace": "trace_expectation",
    "training": "model_config",
}
_RESOURCE_ROLES = frozenset(
    {
        "banding_config",
        "boundary_cases",
        "deployment_config",
        "expected_graph",
        "expected_schema",
        "golden_output",
        "golden_request",
        "input_config",
        "model_config",
        "model_scoring_config",
        "negative_cases",
        "optimiser_apply_config",
        "optimiser_artifact",
        "optimiser_config",
        "output_config",
        "paired_prompts",
        "pipeline_source",
        "plan_request",
        "project_documentation",
        "project_configuration",
        "rating_config",
        "reference_config",
        "request_config",
        "scenario_config",
        "semantic_assertions",
        "submodel_source",
        "synthetic_data",
        "synthetic_reference_data",
        "synthetic_request",
        "trace_expectation",
    }
)
_REQUIRED_RESOURCE_ROLES = frozenset(
    {
        "boundary_cases",
        "expected_graph",
        "expected_schema",
        "golden_output",
        "golden_request",
        "paired_prompts",
        "pipeline_source",
        "project_configuration",
        "semantic_assertions",
    }
)


def _asset_root() -> Traversable:
    """Return the resource directory containing the assistant assets."""

    return resources.files(_ASSET_PACKAGE).joinpath(_ASSET_DIR)


def _examples_root() -> Traversable:
    """Return the resource directory containing exemplar pipeline sources."""

    return _asset_root().joinpath(_EXAMPLES_DIR)


@cache
def _example_resources() -> tuple[tuple[str, Traversable], ...]:
    """Return all exemplar resources in stable, source-file order."""

    examples_root = _examples_root()
    legacy_examples = tuple(
        sorted(
            (
                Path(resource.name).stem,
                resource,
            )
            for resource in examples_root.iterdir()
            if resource.is_file() and resource.name.endswith(".py")
        )
    )
    bundles = tuple(
        (bundle.name, bundle.joinpath("pipeline.py"))
        for bundle in examples_root.iterdir()
        if bundle.is_dir() and bundle.joinpath("manifest.json").is_file()
    )
    examples = tuple(sorted((*legacy_examples, *bundles)))
    if not examples:
        raise RuntimeError("No assistant exemplar pipeline assets were found.")
    return examples


def _read_resource(resource: Traversable) -> str:
    """Read a UTF-8 package resource as text."""

    return resource.read_text(encoding="utf-8")


def _materialize_resource_tree(resource: Traversable, destination: Path) -> None:
    """Copy a Traversable tree so parser-relative sidecars stay available."""

    destination.mkdir(parents=True, exist_ok=True)
    for child in resource.iterdir():
        target = destination / child.name
        if child.is_dir():
            _materialize_resource_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _module_notes(source: str, *, resource_name: str) -> str:
    """Extract and validate an exemplar's complete module docstring."""

    try:
        tree = ast.parse(source, filename=resource_name)
    except SyntaxError as exc:
        raise RuntimeError(f"Assistant exemplar {resource_name!r} is not valid Python.") from exc

    notes = ast.get_docstring(tree)
    if notes is None or not notes.strip():
        raise RuntimeError(f"Assistant exemplar {resource_name!r} must have a module docstring.")
    if not notes.splitlines()[0].strip():
        raise RuntimeError(
            f"Assistant exemplar {resource_name!r} must start its module docstring with a summary."
        )
    return notes


def _resource_for_name(name: str) -> Traversable | None:
    """Find an exemplar by its filename stem."""

    return dict(_example_resources()).get(name)


def _bundle_root(name: str) -> Traversable | None:
    candidate = _examples_root().joinpath(name)
    return (
        candidate if candidate.is_dir() and candidate.joinpath("manifest.json").is_file() else None
    )


def _safe_relative_path(path: object) -> str:
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")):
        raise RuntimeError("Bundle resource paths must be non-empty relative paths.")
    parts = Path(path).parts
    if any(part in {"", ".", ".."} for part in parts) or Path(path).drive:
        raise RuntimeError(f"Unsafe bundle resource path: {path!r}")
    return path.replace("\\", "/")


def _read_bundle_manifest(bundle: Traversable) -> dict[str, object]:
    try:
        manifest = json.loads(bundle.joinpath("manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid assistant example bundle {bundle.name!r}.") from exc
    required = {
        "schema_version",
        "id",
        "version",
        "summary",
        "source",
        "assertion_tier",
        "review_class",
        "resources",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest["schema_version"] != 1
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid manifest schema."
        )
    if (
        manifest["id"] != bundle.name
        or not isinstance(manifest["version"], str)
        or not manifest["version"].strip()
        or not isinstance(manifest["summary"], str)
        or not manifest["summary"].strip()
        or manifest["assertion_tier"] not in _ASSERTION_TIERS
        or manifest["review_class"] not in _REVIEW_CLASSES
        or not isinstance(manifest["resources"], list)
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid identity or inventory."
        )
    source = _safe_relative_path(manifest["source"])
    paths: set[str] = set()
    roles: list[str] = []
    for item in manifest["resources"]:
        if not isinstance(item, dict) or set(item) != {"path", "role", "sha256"}:
            raise RuntimeError(
                f"Assistant example bundle {bundle.name!r} has an invalid resource entry."
            )
        path = _safe_relative_path(item["path"])
        role = item["role"]
        if not isinstance(role, str) or role not in _RESOURCE_ROLES:
            raise RuntimeError(
                f"Assistant example bundle {bundle.name!r} has an unknown resource role."
            )
        if (
            path in paths
            or not isinstance(item["sha256"], str)
            or _SHA256_RE.fullmatch(item["sha256"]) is None
        ):
            raise RuntimeError(
                f"Assistant example bundle {bundle.name!r} has an invalid resource inventory."
            )
        paths.add(path)
        roles.append(role)
    missing_roles = sorted(_REQUIRED_RESOURCE_ROLES.difference(roles))
    if missing_roles:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} is missing required resource roles: "
            + ", ".join(missing_roles)
        )
    if not {"synthetic_data", "synthetic_request"}.intersection(roles):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} is missing a synthetic input resource."
        )
    if manifest["assertion_tier"] == "negative" and "negative_cases" not in roles:
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} is missing negative cases.")
    if source not in paths:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} does not inventory its source."
        )
    source_item = next(item for item in manifest["resources"] if item["path"] == source)
    if source_item["role"] != "pipeline_source":
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} source must have pipeline_source role."
        )
    return manifest


def example_bundle_manifests() -> tuple[dict[str, object], ...]:
    """Load validated, content-addressed manifests in stable bundle-ID order."""
    manifests = []
    for bundle in sorted(
        (item for item in _examples_root().iterdir() if item.is_dir()), key=lambda item: item.name
    ):
        if bundle.joinpath("manifest.json").is_file():
            manifests.append(_read_bundle_manifest(bundle))
    return tuple(manifests)


def _validate_bundle(bundle: Traversable, manifest: dict[str, object]) -> None:
    resources = manifest["resources"]
    assert isinstance(resources, list)
    for item in resources:
        assert isinstance(item, dict)
        path = _safe_relative_path(item["path"])
        resource = bundle.joinpath(*path.split("/"))
        if not resource.is_file():
            raise RuntimeError(f"Assistant example bundle {bundle.name!r} is missing {path!r}.")
        actual = sha256(resource.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise RuntimeError(
                f"Assistant example bundle {bundle.name!r} digest mismatch for {path!r}."
            )
    declared = {"manifest.json"} | {str(item["path"]) for item in resources}
    actual_paths = set(_resource_paths(bundle))
    if undeclared := sorted(actual_paths - declared):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has undeclared resources: "
            + ", ".join(undeclared)
        )


def _resource_paths(root: Traversable, prefix: str = "") -> tuple[str, ...]:
    """Return a Traversable-compatible recursive file inventory."""
    paths: list[str] = []
    for child in root.iterdir():
        path = f"{prefix}{child.name}"
        if child.is_file():
            paths.append(path)
        elif child.is_dir():
            paths.extend(_resource_paths(child, prefix=f"{path}/"))
    return tuple(paths)


def _read_bundle_json(bundle: Traversable, path: str) -> dict[str, object]:
    raw = json.loads(bundle.joinpath(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} resource {path!r} must be an object."
        )
    return raw


def _read_bundle_json_value(bundle: Traversable, path: str) -> object:
    try:
        return json.loads(bundle.joinpath(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has invalid JSON in {path!r}."
        ) from exc


def _validate_teaching_resources(bundle: Traversable) -> None:
    request = _read_bundle_json(bundle, "golden_request.json")
    output = _read_bundle_json(bundle, "golden_output.json")
    prompts = _read_bundle_json(bundle, "prompts.json")
    boundary_cases = _read_bundle_json_value(bundle, "boundary_cases.json")
    if not request or not output:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} needs non-empty golden fixtures."
        )
    if set(prompts) != {"user", "assistant"} or not all(
        isinstance(value, str) and value.strip() for value in prompts.values()
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} has invalid paired prompts.")
    if (
        not isinstance(boundary_cases, list)
        or not boundary_cases
        or not all(
            isinstance(item, dict)
            and set(item) == {"case", "expectation"}
            and all(isinstance(value, str) and value.strip() for value in item.values())
            for item in boundary_cases
        )
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} has invalid boundary cases.")


def _validate_negative_cases(bundle: Traversable) -> None:
    cases = _read_bundle_json_value(bundle, "negative_cases.json")
    if (
        not isinstance(cases, list)
        or not cases
        or not all(
            isinstance(case, dict)
            and set(case) == {"id", "kind", "input", "expected"}
            and isinstance(case["id"], str)
            and bool(case["id"])
            and case["kind"] in {"dry_run", "project_knowledge"}
            and isinstance(case["input"], dict)
            and isinstance(case["expected"], dict)
            and bool(case["expected"])
            for case in cases
        )
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} has invalid negative cases.")


def _validate_assertions(
    bundle: Traversable,
    manifest: dict[str, object],
) -> dict[str, object]:
    assertions = _read_bundle_json(bundle, "assertions.json")
    allowed_keys = {"target", "required_columns", "row_count", "checks"}
    if set(assertions) not in (
        allowed_keys - {"row_count"},
        allowed_keys,
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid assertion schema."
        )
    target = assertions.get("target")
    columns = assertions.get("required_columns")
    checks = assertions.get("checks")
    row_count = assertions.get("row_count")
    if (
        not isinstance(target, str)
        or not target
        or not isinstance(columns, list)
        or not columns
        or not all(isinstance(column, str) and column for column in columns)
        or not isinstance(checks, list)
        or not checks
        or len(checks) != len(set(checks))
        or not all(isinstance(check, str) and check in _ASSERTION_CHECKS for check in checks)
        or "parse" not in checks
        or (
            "row_count" in assertions
            and (not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0)
        )
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has invalid assertion values."
        )
    tier = manifest["assertion_tier"]
    if (
        (tier == "fast" and "execute" not in checks)
        or (tier == "ordinary" and not _ORDINARY_CHECKS.intersection(checks))
        or (tier == "negative" and "adversarial_rejection" not in checks)
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} assertions do not match its tier."
        )
    if tier == "negative":
        _validate_negative_cases(bundle)
    resources = manifest.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has no assertion resource inventory."
        )
    declared_roles = {
        item.get("role")
        for item in resources
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    missing_evidence_roles = sorted(
        {
            role
            for check in checks
            if (role := _CHECK_RESOURCE_ROLES.get(check)) is not None and role not in declared_roles
        }
    )
    if missing_evidence_roles:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} assertions lack evidence resources: "
            + ", ".join(missing_evidence_roles)
        )
    return assertions


def _validate_bundle_expectations(
    bundle: Traversable,
    graph: dict[str, object],
    manifest: dict[str, object],
) -> None:
    expected_graph = _read_bundle_json(bundle, "expected_graph.json")
    expected_schema = _read_bundle_json(bundle, "expected_schema.json")
    assertions = _validate_assertions(bundle, manifest)
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid rendered graph."
        )
    node_types = {node.get("type") for node in nodes if isinstance(node, dict)}
    expected_node_types = expected_graph.get("node_types")
    if not isinstance(expected_node_types, list) or not all(
        isinstance(node_type, str) for node_type in expected_node_types
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has invalid expected node types."
        )
    if not set(expected_node_types).issubset(node_types):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} graph does not meet its expected node types."
        )
    if expected_graph.get("edge_count") != len(edges):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} graph does not meet its expected edge count."
        )
    if expected_schema.get("target") != assertions.get("target"):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} schema/assertion targets differ."
        )
    expected_columns = expected_schema.get("required_columns")
    assertion_columns = assertions.get("required_columns")
    if (
        not isinstance(expected_columns, list)
        or not expected_columns
        or not all(isinstance(column, str) and column for column in expected_columns)
        or expected_columns != assertion_columns
    ):
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} schema/assertion columns differ."
        )
    _validate_teaching_resources(bundle)


def _execute_fast_bundle(bundle: Traversable, manifest: dict[str, object]) -> None:
    """Run a tiny materialized batch fixture through the production eager executor."""
    from haute._execution_context import ExecutionProfile
    from haute._input_providers import build_input_snapshot
    from haute._sandbox import _get_project_root, set_project_root
    from haute._source_cache import SourceCacheStore
    from haute.execution import invalidate_dataframe_execution_cache
    from haute.executor import _preview_cache, execute_graph
    from haute.graph_utils import flatten_graph
    from haute.routes._helpers import parse_pipeline_to_graph

    assertions = _read_bundle_json(bundle, "assertions.json")
    golden = _read_bundle_json(bundle, "golden_output.json")
    with TemporaryDirectory(prefix="haute-assistant-example-fast-") as temp_dir:
        destination = Path(temp_dir) / bundle.name
        _materialize_resource_tree(bundle, destination)
        graph = flatten_graph(parse_pipeline_to_graph(destination / str(manifest["source"])))
        original_root = _get_project_root()
        try:
            # Bundles are independent installed projects. Process-wide preview
            # and dataframe caches must not carry a same-shaped prior bundle's
            # materialized frames across that project boundary.
            _preview_cache.clear()
            invalidate_dataframe_execution_cache()
            set_project_root(destination)
            store = SourceCacheStore(destination)
            for node in graph.nodes:
                if node.data.nodeType.value != "dataInput":
                    continue
                config = node.data.config
                # Parser-side source helpers are deliberately present in the
                # teaching source for authoring realism.  The production data
                # input builder already resolves this canonical config; do not
                # execute the helper body as user code in this bounded smoke.
                config.pop("code", None)
                path = config.get("path")
                if isinstance(path, str):
                    config["path"] = str((destination / path).resolve())
                build_input_snapshot(
                    config,
                    store=store,
                    base_dir=destination,
                    profile=ExecutionProfile.PREVIEW_EAGER,
                )
            target = assertions["target"]
            if not isinstance(target, str):
                raise RuntimeError(
                    f"Assistant example bundle {bundle.name!r} has an invalid assertion target."
                )
            result = execute_graph(graph, target_node_id=target, row_limit=10)[target]
            if result.status != "ok":
                raise RuntimeError(
                    f"Assistant example bundle {bundle.name!r} fast execution failed: "
                    f"{result.error}"
                )
            required_columns = assertions.get("required_columns", [])
            column_names = {column.name for column in result.columns}
            if not isinstance(required_columns, list) or not set(required_columns).issubset(
                column_names
            ):
                raise RuntimeError(
                    f"Assistant example bundle {bundle.name!r} fast schema assertion failed."
                )
            if "row_count" in assertions and result.row_count != assertions["row_count"]:
                raise RuntimeError(
                    f"Assistant example bundle {bundle.name!r} fast row-count assertion failed."
                )
            if "row_count" in golden and result.row_count != golden["row_count"]:
                raise RuntimeError(
                    f"Assistant example bundle {bundle.name!r} golden row count differs."
                )
            if "columns" in golden:
                expected_columns = golden["columns"]
                if (
                    not isinstance(expected_columns, list)
                    or not set(expected_columns) <= column_names
                ):
                    raise RuntimeError(
                        f"Assistant example bundle {bundle.name!r} golden columns differ."
                    )
            preview = result.preview or []
            for column, expected_values in golden.items():
                if column in {"row_count", "columns"}:
                    continue
                actual_values = [
                    row[column] for row in preview if isinstance(row, dict) and column in row
                ]
                if actual_values != expected_values:
                    raise RuntimeError(
                        f"Assistant example bundle {bundle.name!r} golden values differ "
                        f"for {column!r}: expected {expected_values!r}, "
                        f"got {actual_values!r}."
                    )
            checks = assertions.get("checks")
            assert isinstance(checks, list)
            if "trace" in checks:
                _verify_fast_trace(bundle, graph)
            if "dry_run" in checks:
                _verify_fast_dry_run(bundle, destination)
        finally:
            _preview_cache.clear()
            invalidate_dataframe_execution_cache()
            set_project_root(original_root)


def _verify_fast_trace(
    bundle: Traversable,
    graph: PipelineGraph,
) -> None:
    """Execute and compare a bundle's closed row-trace expectation."""

    from haute.trace import execute_trace

    expected = _read_bundle_json(bundle, "trace_expected.json")
    if set(expected) != {
        "target",
        "row_index",
        "column",
        "required_node_ids",
        "expected_value",
    }:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid trace expectation."
        )
    target = expected["target"]
    row_index = expected["row_index"]
    column = expected["column"]
    required_node_ids = expected["required_node_ids"]
    if (
        not isinstance(target, str)
        or not target
        or not isinstance(row_index, int)
        or isinstance(row_index, bool)
        or row_index < 0
        or not isinstance(column, str)
        or not column
        or not isinstance(required_node_ids, list)
        or not required_node_ids
        or not all(isinstance(node_id, str) and node_id for node_id in required_node_ids)
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} has invalid trace values.")
    trace = execute_trace(
        graph,
        row_index=row_index,
        target_node_id=target,
        column=column,
    )
    if trace.output_value != expected["expected_value"]:
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} trace output differs.")
    step_ids = {step.node_id for step in trace.steps}
    if not set(required_node_ids).issubset(step_ids):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} trace steps differ.")


def _verify_fast_dry_run(bundle: Traversable, destination: Path) -> None:
    """Execute a no-write graph plan and compare its semantic evidence."""

    from haute.assistant._application import PipelineApplicationService

    request = _read_bundle_json(bundle, "dry_run.json")
    if set(request) != {
        "operations",
        "expected_nodes_removed",
        "expected_nodes_added",
    }:
        raise RuntimeError(
            f"Assistant example bundle {bundle.name!r} has an invalid dry-run expectation."
        )
    operations = request["operations"]
    removed = request["expected_nodes_removed"]
    added = request["expected_nodes_added"]
    if (
        not isinstance(operations, list)
        or not operations
        or not isinstance(removed, list)
        or not all(isinstance(node_id, str) for node_id in removed)
        or not isinstance(added, list)
        or not all(isinstance(node_id, str) for node_id in added)
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} has invalid dry-run values.")
    source = destination / "pipeline.py"
    before = source.read_bytes()
    service = PipelineApplicationService(
        project_root=destination,
        pipeline_root=destination,
        mutations_readiness=lambda _root: (True, None),
        publish_graph_update=lambda _source, _graph: "f" * 64,
    )
    plan = service.dry_run("pipeline.py", operations)
    if (
        source.read_bytes() != before
        or list(plan.diff.nodes_removed) != removed
        or list(plan.diff.nodes_added) != added
    ):
        raise RuntimeError(f"Assistant example bundle {bundle.name!r} dry-run evidence differs.")


def validate_example_bundles(*, execute_fast: bool = False) -> tuple[dict[str, object], ...]:
    """Verify all bundle inventories and parse each source without importing it."""
    report = []
    for manifest in example_bundle_manifests():
        bundle = _bundle_root(str(manifest["id"]))
        assert bundle is not None
        _validate_bundle(bundle, manifest)
        result = _load_bundle(bundle, manifest)
        graph = result["graph"]
        assert isinstance(graph, dict)
        _validate_bundle_expectations(bundle, graph, manifest)
        executed = execute_fast and manifest["assertion_tier"] == "fast"
        if executed:
            _execute_fast_bundle(bundle, manifest)
        report.append(
            {
                "id": manifest["id"],
                "validated": True,
                "parsed": bool(result["graph"]),
                "executed": executed,
            }
        )
    return tuple(report)


def materialize_example_bundle(name: str, destination: Path) -> dict[str, object]:
    """Copy one validated bundle to an empty destination for specialist checks.

    The copy is intentionally explicit instead of exposing the installed
    package-resource path: wheels may be zip-backed, and the example's
    project-relative sidecars must behave exactly as they do after a user
    copies the bundle into a project.
    """

    bundle = _bundle_root(name)
    if bundle is None:
        error = _unknown_example_error(name)["error"]
        assert isinstance(error, dict)
        message = error.get("message")
        assert isinstance(message, str)
        raise ValueError(message)
    manifest = _read_bundle_manifest(bundle)
    _validate_bundle(bundle, manifest)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Assistant example destination is not empty: {destination}")
    _materialize_resource_tree(bundle, destination)
    return manifest


def _example_attribution(
    *,
    name: str,
    version: str,
    summary: str,
    assertion_tier: str,
    review_class: str,
) -> dict[str, str]:
    """Return the bounded provenance needed to interpret one teaching graph."""

    return {
        "id": name,
        "version": version,
        "summary": summary,
        "assertion_tier": assertion_tier,
        "review_class": review_class,
    }


def _load_bundle(bundle: Traversable, manifest: dict[str, object]) -> dict[str, object]:
    source_path = _safe_relative_path(manifest["source"])
    source = _read_resource(bundle.joinpath(*source_path.split("/")))
    notes = _module_notes(source, resource_name=source_path)
    from haute.routes._helpers import parse_pipeline_to_graph

    with TemporaryDirectory(prefix="haute-assistant-example-") as temp_dir:
        destination = Path(temp_dir) / bundle.name
        _materialize_resource_tree(bundle, destination)
        graph = parse_pipeline_to_graph(destination / source_path)

    return {
        "name": manifest["id"],
        "attribution": _example_attribution(
            name=str(manifest["id"]),
            version=str(manifest["version"]),
            summary=str(manifest["summary"]),
            assertion_tier=str(manifest["assertion_tier"]),
            review_class=str(manifest["review_class"]),
        ),
        "narrative": notes,
        "graph": render_pipeline_graph(graph),
    }


@cache
def authoring_guide() -> str:
    """Return the packaged Haute authoring guide.

    A missing or empty guide is a packaging defect and therefore raises a
    clear error instead of silently weakening every assistant turn.
    """

    resource = _asset_root().joinpath(_GUIDE_NAME)
    try:
        guide = _read_resource(resource)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"Assistant authoring guide is missing: {_GUIDE_NAME}") from exc
    if not guide.strip():
        raise RuntimeError(f"Assistant authoring guide is empty: {_GUIDE_NAME}")
    return guide


@cache
def example_index() -> list[tuple[str, str]]:
    """Return ``(example_name, one_line_summary)`` pairs for all exemplars."""

    index: list[tuple[str, str]] = []
    for name, resource in _example_resources():
        notes = _module_notes(_read_resource(resource), resource_name=resource.name)
        index.append((name, notes.splitlines()[0].strip()))
    return index


def _unknown_example_error(name: str) -> dict[str, object]:
    """Build the structured error passed back to the model for an unknown name."""

    valid_names = [example_name for example_name, _summary in example_index()]
    message = f"Unknown assistant example {name!r}. Choose one of: {', '.join(valid_names)}."
    return {
        "error": {
            "code": "unknown_example",
            "message": message,
            "name": name,
            "valid_names": valid_names,
        }
    }


def load_example(name: str) -> dict[str, object]:
    """Return an exemplar's notes and parser-produced graph rendering.

    Exemplars are parsed as source files and never imported. The complete
    example resource tree is materialised together so parser-relative config
    sidecars work for both filesystem and zip-backed package importers.
    """

    bundle = _bundle_root(name)
    if bundle is not None:
        manifest = _read_bundle_manifest(bundle)
        _validate_bundle(bundle, manifest)
        return _load_bundle(bundle, manifest)
    resource = _resource_for_name(name)
    if resource is None:
        return _unknown_example_error(name)

    source = _read_resource(resource)
    notes = _module_notes(source, resource_name=resource.name)

    from haute.routes._helpers import parse_pipeline_to_graph

    with TemporaryDirectory(prefix="haute-assistant-example-") as temp_dir:
        examples_path = Path(temp_dir) / _EXAMPLES_DIR
        _materialize_resource_tree(_examples_root(), examples_path)
        graph = parse_pipeline_to_graph(examples_path / resource.name)

    return {
        "name": name,
        "attribution": _example_attribution(
            name=name,
            version="legacy",
            summary=notes.splitlines()[0].strip(),
            assertion_tier="ordinary",
            review_class="engineering",
        ),
        "narrative": notes,
        "graph": render_pipeline_graph(graph),
    }


__all__ = [
    "authoring_guide",
    "example_bundle_manifests",
    "example_index",
    "load_example",
    "materialize_example_bundle",
    "validate_example_bundles",
]
