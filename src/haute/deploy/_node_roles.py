"""What a deployment reads off a node's type (specs/deploy, "Node roles").

:data:`DEPLOY_ARTIFACTS` names, for every node type, what a node of it reads from
disk that a deployment must carry for it: the bundler collects it, the scorer
serves the node from the bundled copy, and the request-input readers know a
Data Input by it. It lists every node type, so a new type fails at import until
deploy says what it carries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from haute._node_specs import node_spec, node_type_table
from haute._node_type import NodeType

#: What a node reads that a deployment carries for it: a Data Input's ``dataset``
#: (its retained snapshot, or its direct Parquet file), a Load File's ``file``, a
#: Model Scoring node's ``model`` (with the feature contract it scores under), and an
#: Apply Optimisation's ``optimiser_result`` (bundled when its source is a file;
#: one from MLflow is loaded when the container runs).
DeployArtifact = Literal["dataset", "file", "model", "optimiser_result"]

DEPLOY_ARTIFACTS: Mapping[NodeType, DeployArtifact | None] = node_type_table(
    "deploy artifacts",
    {
        NodeType.API_INPUT: None,
        NodeType.WORKBENCH_INPUT: None,
        NodeType.WORKBENCH_OUTPUT: None,
        NodeType.DATA_INPUT: "dataset",
        NodeType.DATA_OUTPUT: None,
        NodeType.POLARS: None,
        NodeType.EDGE_JOIN: None,
        NodeType.MODEL_SCORE: "model",
        NodeType.BANDING: None,
        NodeType.RATING_STEP: None,
        NodeType.OUTPUT: None,
        NodeType.EXPLORE: None,
        NodeType.EXTERNAL_FILE: "file",
        NodeType.LIVE_SWITCH: None,
        NodeType.MODELLING: None,
        NodeType.OPTIMISER: None,
        NodeType.SCENARIO_EXPANDER: None,
        NodeType.OPTIMISER_APPLY: "optimiser_result",
        NodeType.CONSTANT: None,
        NodeType.SUBMODEL: None,
        NodeType.SUBMODEL_PORT: None,
    },
)


def is_workbench_input(node_type: NodeType | str) -> bool:
    """Whether *node_type* is the Workbench Input: the request input given the form's tables."""
    spec = node_spec(node_type)
    return spec.request_input and spec.workbench


def is_quote_input(node_type: NodeType | str) -> bool:
    """Whether *node_type* is the Quote Input: the request input the editor authors."""
    spec = node_spec(node_type)
    return spec.request_input and not spec.workbench
