"""The node vocabulary exposed to the pricing assistant.

The catalog deliberately keeps its mechanical facts derived from the same
registries that validate and save a pipeline.  The only hand-authored part is
the short usage note for each node type.  That gives the model useful authoring
guidance without creating a second source of truth for node names, config keys,
decorators, sidecar folders, or singleton rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE, VALID_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.routes._save_pipeline import _SINGLETON_NODE_TYPES


@dataclass(frozen=True, slots=True)
class NodeCatalogEntry:
    """Facts and authoring guidance for one :class:`~haute._types.NodeType`."""

    node_type: NodeType
    decorator: str | None
    config_keys: tuple[str, ...]
    config_folder: str | None
    singleton: bool
    usage_note: str

    @property
    def config_shapes(self) -> tuple[tuple[str, str], ...]:
        """Return the TypedDict field shapes behind the config allowlist."""

        config_type = _TYPED_DICT_BY_NODE_TYPE.get(self.node_type)
        if config_type is None:
            return ()
        return tuple(
            (key, str(shape).replace("typing.", ""))
            for key, shape in config_type.__annotations__.items()
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable, JSON-shaped representation used by tools."""

        return {
            "node_type": self.node_type.value,
            "decorator": self.decorator,
            "config_keys": list(self.config_keys),
            "config_shapes": [{"key": key, "shape": shape} for key, shape in self.config_shapes],
            "config_folder": self.config_folder,
            "singleton": self.singleton,
            "usage_note": self.usage_note,
        }


# The save service is the authority for singleton policy.  Keep this derived
# rather than repeating the node list here: a new singleton must be visible to
# both save validation and the assistant catalog in the same change.
_SINGLETON_TYPES = frozenset(node_type for node_type, _label in _SINGLETON_NODE_TYPES)


# Usage notes are the catalog's intentionally hand-authored knowledge.  Every
# current NodeType is listed explicitly so adding a NodeType without adding a
# corresponding note leaves the catalog incomplete and fails at import time.
_USAGE_NOTES: dict[NodeType, str] = {
    NodeType.API_INPUT: (
        "Declare the request contract and its input tables; use this as the "
        "pipeline's external quote boundary."
    ),
    NodeType.DATA_INPUT: (
        "Read a file, database, lakehouse, Databricks table, or inline records through "
        "an explicit provider and format; use a snapshot for remote or eager-only inputs."
    ),
    NodeType.DATA_OUTPUT: (
        "Write or sink a Polars frame to a file or database target; keep it at "
        "a deliberate branch endpoint rather than in the scoring path."
    ),
    NodeType.POLARS: (
        "Apply a Polars transform to connected inputs; keep reusable logic in "
        "code and use inputMapping when named inputs need explicit binding."
    ),
    NodeType.EDGE_JOIN: (
        "Join the base input with a connected input; specify the join keys and "
        "join type explicitly, especially when the two key names differ."
    ),
    NodeType.MODEL_SCORE: (
        "Score rows with a saved model selected by run or registered-model "
        "metadata; configure the feature contract and prediction output deliberately."
    ),
    NodeType.BANDING: (
        "Turn continuous or discrete factor values into named bands; define an "
        "output column and an explicit default for values outside the rules."
    ),
    NodeType.RATING_STEP: (
        "Look up rating factors from one or more tables and combine their "
        "outputs with the chosen operation; make table factors and miss policy explicit."
    ),
    NodeType.OUTPUT: (
        "Assemble the top-level JSON response from selected upstream columns "
        "with an explicit outputMapping."
    ),
    NodeType.EXPLORE: (
        "Request exploratory summaries or apply an exploration code block; "
        "treat it as an analysis branch, not as a required pricing stage."
    ),
    NodeType.EXTERNAL_FILE: (
        "Load a serialized external object such as a model or lookup artifact; "
        "provide the file type and any type-specific model class."
    ),
    NodeType.LIVE_SWITCH: (
        "Route one of several connected frames by scenario; use at most one "
        "switch per pipeline and name the scenario map unambiguously."
    ),
    NodeType.MODELLING: (
        "Train a model from the connected frame; configure target, algorithm, "
        "task, features, and split settings, and keep training outside the live quote path."
    ),
    NodeType.OPTIMISER: (
        "Search for better factor or quote values under an objective and "
        "constraints; select the mode and wire the required scored/factor inputs explicitly."
    ),
    NodeType.SCENARIO_EXPANDER: (
        "Expand each quote into deterministic scenario values for optimisation "
        "or analysis; set the source column, range, and number of steps together."
    ),
    NodeType.OPTIMISER_APPLY: (
        "Apply a saved optimisation artifact to an upstream frame; identify "
        "the artifact source and preserve the version/output-column conventions."
    ),
    NodeType.CONSTANT: (
        "Create a one-row frame of named literal values for defaults or lookup "
        "inputs; use values entries with stable names rather than hidden literals in code."
    ),
    NodeType.SUBMODEL: (
        "Reference a separate pipeline module as a top-level graph boundary; "
        "connect only its declared input and output ports, never its internal nodes."
    ),
    NodeType.SUBMODEL_PORT: (
        "Use only for the structural ports of a submodel boundary; it has no "
        "user config or decorator and must not be edited as an ordinary transform."
    ),
}


def _make_entry(node_type: NodeType) -> NodeCatalogEntry:
    """Build one entry from the canonical registries and the local usage note."""

    return NodeCatalogEntry(
        node_type=node_type,
        decorator=NODE_TYPE_TO_DECORATOR.get(node_type),
        config_keys=tuple(sorted(VALID_KEYS.get(node_type, frozenset()))),
        config_folder=NODE_TYPE_TO_FOLDER.get(node_type),
        singleton=node_type in _SINGLETON_TYPES,
        usage_note=_USAGE_NOTES[node_type],
    )


# Iterate over the enum, rather than the hand-authored notes, so the enum's
# order is the catalog's order and an omitted note cannot silently hide a new
# node type.  The explicit membership check lets the completeness validator
# report the missing type with its normal diagnostic instead of using a broad
# fallback note.
NODE_CATALOG: dict[NodeType, NodeCatalogEntry] = {
    node_type: _make_entry(node_type) for node_type in NodeType if node_type in _USAGE_NOTES
}


def validate_catalog_complete() -> None:
    """Assert that every canonical node type has a complete catalog entry.

    This mirrors ``haute._registry.validate_registry_complete``: a missing
    entry is a release-time programming error, not a condition for the model
    to recover from.  Mechanical fields are checked too, so a hand-edited
    catalog entry cannot quietly disagree with save/config behaviour.
    """

    canonical_types = frozenset(NodeType)
    catalog_types = frozenset(NODE_CATALOG)
    missing = [node_type for node_type in NodeType if node_type not in catalog_types]
    unexpected = [node_type for node_type in NODE_CATALOG if node_type not in canonical_types]

    invalid_entries: list[str] = []
    for node_type in NodeType:
        entry = NODE_CATALOG.get(node_type)
        if entry is None:
            continue
        if entry.node_type is not node_type:
            invalid_entries.append(f"{node_type.value}: entry.node_type={entry.node_type!r}")
        if entry.decorator != NODE_TYPE_TO_DECORATOR.get(node_type):
            invalid_entries.append(f"{node_type.value}: decorator")
        expected_keys = tuple(sorted(VALID_KEYS.get(node_type, frozenset())))
        if entry.config_keys != expected_keys:
            invalid_entries.append(f"{node_type.value}: config_keys")
        if entry.config_folder != NODE_TYPE_TO_FOLDER.get(node_type):
            invalid_entries.append(f"{node_type.value}: config_folder")
        if entry.singleton != (node_type in _SINGLETON_TYPES):
            invalid_entries.append(f"{node_type.value}: singleton")
        if not entry.usage_note.strip():
            invalid_entries.append(f"{node_type.value}: usage_note")

    if missing or unexpected or invalid_entries:
        raise RuntimeError(
            "NODE_CATALOG is incomplete or disagrees with canonical registries — "
            "every NodeType needs matching assistant metadata.\n"
            f"  Missing: {[node_type.value for node_type in missing]}\n"
            f"  Unexpected: {[str(node_type) for node_type in unexpected]}\n"
            f"  Invalid: {invalid_entries}"
        )


def render_catalog() -> str:
    """Render the catalog section in a stable, model-readable Markdown form."""

    lines = [
        "## Haute node catalog",
        "Use only these canonical node types and config keys when authoring a graph.",
    ]
    for node_type in NodeType:
        entry = NODE_CATALOG[node_type]
        lines.extend(
            (
                f"### `{node_type.value}`",
                (
                    f"- Decorator: `{entry.decorator}`"
                    if entry.decorator
                    else "- Decorator: structural-only"
                ),
                (
                    "- Config keys: " + ", ".join(f"`{key}`" for key in entry.config_keys)
                    if entry.config_keys
                    else "- Config keys: none"
                ),
                (
                    "- Config shapes: "
                    + ", ".join(f"`{key}`: `{shape}`" for key, shape in entry.config_shapes)
                    if entry.config_shapes
                    else "- Config shapes: none"
                ),
                (
                    f"- Sidecar folder: `config/{entry.config_folder}/`"
                    if entry.config_folder
                    else "- Sidecar folder: none"
                ),
                f"- Singleton: {'yes' if entry.singleton else 'no'}",
                f"- Usage: {entry.usage_note}",
                "",
            )
        )
    return "\n".join(lines).rstrip()


# Fail during import if a new node type is not represented here.  This is
# intentionally eager: an incomplete catalog must not reach a configured model.
validate_catalog_complete()


__all__ = [
    "NODE_CATALOG",
    "NodeCatalogEntry",
    "render_catalog",
    "validate_catalog_complete",
]
