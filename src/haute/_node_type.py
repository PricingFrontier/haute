"""The node-type enum, alone in a standard-library leaf.

``haute._node_specs`` imports it without the cycle through ``haute._types``,
which imports ``haute._graph_utils`` before it could define the enum.
``haute._types`` re-exports it, so ``from haute._types import NodeType`` stays
the usual import.
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    """Canonical node-type identifiers shared with the React Flow frontend.

    Inherits from ``StrEnum`` so ``NodeType.API_INPUT == "apiInput"`` is ``True``
    and JSON serialization produces the plain string value.
    """

    API_INPUT = "apiInput"
    WORKBENCH_INPUT = "workbenchInput"
    WORKBENCH_OUTPUT = "workbenchOutput"
    DATA_INPUT = "dataInput"
    DATA_OUTPUT = "dataOutput"
    POLARS = "polars"
    EDGE_JOIN = "edgeJoin"
    MODEL_SCORE = "modelScore"
    BANDING = "banding"
    RATING_STEP = "ratingStep"
    OUTPUT = "output"
    EXPLORE = "explore"
    EXTERNAL_FILE = "externalFile"
    LIVE_SWITCH = "liveSwitch"
    MODELLING = "modelling"
    OPTIMISER = "optimiser"
    SCENARIO_EXPANDER = "scenarioExpander"
    OPTIMISER_APPLY = "optimiserApply"
    CONSTANT = "constant"
    SUBMODEL = "submodel"
    SUBMODEL_PORT = "submodelPort"
