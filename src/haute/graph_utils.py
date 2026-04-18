"""Backward-compatible re-export facade.

All symbols that were previously defined in ``graph_utils.py`` are now
split across focused modules but re-exported here so existing imports
like ``from haute.graph_utils import GraphNode`` continue to work.

Modules:
    _types.py        — Pydantic models, TypedDicts, NodeType, config-key tuples
    _graph_utils.py  — pure-function graph helpers (sanitize, mappings)
    _topo.py         — topo_sort_ids, ancestors
    _cache.py        — graph_fingerprint
    _io.py           — read_source, load_external_object
    _flatten.py      — flatten_graph
    _execute_lazy.py — _prepare_graph, _execute_lazy
"""

from haute._cache import graph_fingerprint as graph_fingerprint
from haute._execute_lazy import EagerResult as EagerResult
from haute._execute_lazy import _execute_eager_core as _execute_eager_core
from haute._execute_lazy import _execute_lazy as _execute_lazy
from haute._execute_lazy import _prepare_graph as _prepare_graph
from haute._execute_lazy import (
    _prune_live_switch_edges as _prune_live_switch_edges,
)
from haute._flatten import flatten_graph as flatten_graph
from haute._graph_utils import _resolve_sink_path as _resolve_sink_path
from haute._graph_utils import _sanitize_func_name as _sanitize_func_name
from haute._graph_utils import build_instance_mapping as build_instance_mapping
from haute._graph_utils import resolve_orig_source_names as resolve_orig_source_names
from haute._io import _object_cache as _object_cache
from haute._io import load_external_object as load_external_object
from haute._io import read_source as read_source
from haute._mlflow_io import ScoringModel as ScoringModel
from haute._mlflow_io import load_local_model as load_local_model
from haute._mlflow_io import load_mlflow_model as load_mlflow_model
from haute._model_scorer import score_from_config as score_from_config
from haute._optimiser_io import load_mlflow_optimiser_artifact as load_mlflow_optimiser_artifact
from haute._optimiser_io import load_optimiser_artifact as load_optimiser_artifact
from haute._topo import CycleError as CycleError
from haute._topo import ancestors as ancestors
from haute._topo import topo_sort_ids as topo_sort_ids
from haute._types import DECORATOR_TO_NODE_TYPE as DECORATOR_TO_NODE_TYPE
from haute._types import MODEL_SCORE_CONFIG_KEYS as MODEL_SCORE_CONFIG_KEYS
from haute._types import MODELLING_CONFIG_KEYS as MODELLING_CONFIG_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR as NODE_TYPE_TO_DECORATOR
from haute._types import OPTIMISER_APPLY_CONFIG_KEYS as OPTIMISER_APPLY_CONFIG_KEYS
from haute._types import OPTIMISER_CONFIG_KEYS as OPTIMISER_CONFIG_KEYS
from haute._types import SCENARIO_EXPANDER_CONFIG_KEYS as SCENARIO_EXPANDER_CONFIG_KEYS
from haute._types import GraphEdge as GraphEdge
from haute._types import GraphNode as GraphNode
from haute._types import HauteError as HauteError
from haute._types import NodeData as NodeData
from haute._types import NodeType as NodeType
from haute._types import PipelineGraph as PipelineGraph
from haute._types import _Frame as _Frame
