"""What a configured node does when a pipeline file runs on its own.

Every node type except ``polars`` is *configured*: its settings live in a JSON
sidecar named by the decorator's ``config=`` keyword (or, for Edge Join and
Explore, in the decorator's own keywords), and in a standalone
:meth:`~haute.pipeline.Pipeline.run` / :meth:`~haute.pipeline.Pipeline.score`
the decorator performs the node's work through the same shared helpers canvas
execution uses. The node's function says only what the user added:

- a function whose first positional parameter is ``df`` (for an External File,
  one with the keyword-only parameter ``obj``) is a **hook**, called with the
  frame the work produced;
- any other function is a **declaration**: its body does nothing and it is
  never called; its positional parameters name the node's inputs.

Canvas execution, preview, trace and deploy never run node functions; they
build each node from its parsed configuration. This module is what keeps a
saved file runnable on its own without any of that machinery in the file.
"""

from __future__ import annotations

import dis
import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._registry import MODELLING_NODE_SEMANTICS, NodeInputPolicy
from haute._types import NodeType
from haute.errors import ConfigError, ExecutionError

__all__ = [
    "CODE_NODE_TYPES",
    "SOURCE_NODE_TYPES",
    "STANDALONE_PASSTHROUGH_TYPES",
    "FunctionKind",
    "function_kind",
    "has_empty_body",
    "is_configured",
    "pipeline_directory",
    "run_configured_node",
]

FunctionKind = Literal["transform", "declaration", "hook"]

#: Configured node types whose function may carry the user's code.
CODE_NODE_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.DATA_INPUT,
        NodeType.EXTERNAL_FILE,
        NodeType.RATING_STEP,
        NodeType.MODEL_SCORE,
        NodeType.SCENARIO_EXPANDER,
        NodeType.EXPLORE,
    }
)

#: Configured node types that are sources whatever their function's signature.
SOURCE_NODE_TYPES: frozenset[NodeType] = frozenset(
    {NodeType.API_INPUT, NodeType.DATA_INPUT, NodeType.CONSTANT}
)

#: Configured node types whose standalone work returns one of their inputs.
#: A behavioural node type (one the executor transforms with) must never be
#: listed here: ``validate_registry_complete`` checks it at import.
STANDALONE_PASSTHROUGH_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.DATA_OUTPUT,
        NodeType.EXPLORE,
        NodeType.EXTERNAL_FILE,
        MODELLING_NODE_SEMANTICS.node_type,
        NodeType.OPTIMISER,
    }
)

_BOOKKEEPING_OPNAMES = frozenset({"RESUME", "NOP", "CACHE"})
_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def has_empty_body(fn: Callable[..., Any]) -> bool:
    """Whether *fn*'s body does nothing: ``...``, ``pass`` or a docstring alone.

    Read from the bytecode, which every such body compiles to the same way (an
    immediate ``return None``) on each supported Python version; the source
    need not be available.
    """
    instructions = [
        instruction
        for instruction in dis.get_instructions(fn)
        if instruction.opname not in _BOOKKEEPING_OPNAMES
    ]
    if len(instructions) == 1:
        (only,) = instructions
        return only.opname == "RETURN_CONST" and only.argval is None
    if len(instructions) == 2:
        load, ret = instructions
        return load.opname == "LOAD_CONST" and load.argval is None and ret.opname == "RETURN_VALUE"
    return False


def _parameters(fn: Callable[..., Any]) -> tuple[list[str], set[str]]:
    parameters = [
        parameter
        for parameter in inspect.signature(fn).parameters.values()
        if parameter.name != "self"
    ]
    positional = [parameter.name for parameter in parameters if parameter.kind in _POSITIONAL]
    keyword_only = {
        parameter.name
        for parameter in parameters
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }
    return positional, keyword_only


def is_configured(node_type: NodeType, config: Mapping[str, Any]) -> bool:
    """Whether a registered node has settings for its decorator to act on.

    Edge Join and Explore carry theirs as decorator keywords; every other type
    except ``polars`` names a ``config=`` sidecar. A sidecar type registered
    without one is a plain function the run calls as written — a form the
    static parser rejects, so it never appears in a saved pipeline file.
    """
    if node_type == NodeType.POLARS:
        return False
    return node_type in (NodeType.EDGE_JOIN, NodeType.EXPLORE) or bool(config.get("config"))


def function_kind(
    node_type: NodeType,
    fn: Callable[..., Any],
    node_name: str,
    config: Mapping[str, Any],
) -> FunctionKind:
    """Classify a registered node function, failing loudly on a shape that cannot run."""
    if not is_configured(node_type, config):
        return "transform"
    empty = has_empty_body(fn)
    if node_type not in CODE_NODE_TYPES:
        # Its parameters only name inputs (an input may be called df); the body is all
        # that can be wrong.
        if not empty:
            raise ConfigError(
                f"Node '{node_name}' has a function body, but its decorator performs the "
                f"node's work and never calls it. Its node type ({node_type.value}) carries "
                "no code; replace the body with `...`.",
                node=node_name,
                node_type=node_type.value,
            )
        return "declaration"
    positional, keyword_only = _parameters(fn)
    if node_type == NodeType.EXTERNAL_FILE:
        marker = "obj"
        is_hook = "obj" in keyword_only
        how_to_add_code = (
            "To add code, keep the inputs as parameters, add the keyword-only parameter "
            "obj (the loaded object), start from df = <first input> and return df."
        )
    else:
        marker = "df"
        is_hook = positional[:1] == ["df"]
        how_to_add_code = (
            "To add code, make the first parameter df (the frame this node produced) "
            "and return the result."
        )
    if is_hook:
        if empty:
            raise ConfigError(
                f"Node '{node_name}' takes {marker} but has no code: add the code, or "
                "declare the node with its inputs as parameters and a `...` body.",
                node=node_name,
                node_type=node_type.value,
            )
        return "hook"
    if not empty:
        raise ConfigError(
            f"Node '{node_name}' has a function body, but its decorator performs the node's "
            f"work and never calls it. {how_to_add_code}",
            node=node_name,
            node_type=node_type.value,
        )
    return "declaration"


def pipeline_directory(fn: Callable[..., Any], node_name: str, pipeline_dir: str = ".") -> Path:
    """The directory *fn*'s ``config=`` paths resolve against: its pipeline's.

    That is the directory of the file defining *fn*, climbed by *pipeline_dir*
    when the file is a submodel definition below the pipeline that registers it.
    """
    file = getattr(fn, "__globals__", {}).get("__file__")
    if not isinstance(file, str) or not file:
        raise ConfigError(
            f"Node '{node_name}' reads its settings from a config file relative to the "
            "pipeline file, but its function was not defined in a file. Run the pipeline "
            "from its .py file.",
            node=node_name,
        )
    return (Path(file).resolve().parent / pipeline_dir).resolve()


def _sidecar(config: Mapping[str, Any], node_type: NodeType, node_name: str) -> str:
    path = config.get("config")
    if not isinstance(path, str) or not path:
        raise ConfigError(
            f"Node '{node_name}' needs its config= sidecar path to run: "
            f'@pipeline.{node_type.value}(config="config/...json").',
            node=node_name,
            node_type=node_type.value,
        )
    return path


def _first(frames: Sequence[Any], node_name: str) -> Any:
    if not frames:
        raise ExecutionError(f"Node '{node_name}' received no input frame.", node=node_name)
    return frames[0]


def _run_configured_work(
    node_type: NodeType,
    *,
    name: str,
    config: Mapping[str, Any],
    fn: Callable[..., Any],
    frames: Sequence[Any],
    input_names: Sequence[str],
    pipeline_dir: str,
) -> Any:
    """Perform *node_type*'s configured work on *frames*, as canvas execution does."""
    if node_type == MODELLING_NODE_SEMANTICS.node_type:
        if MODELLING_NODE_SEMANTICS.input_policy is not NodeInputPolicy.FIRST_CONNECTED:
            raise RuntimeError(
                f"Unsupported modelling input policy: {MODELLING_NODE_SEMANTICS.input_policy!r}"
            )
        return _first(frames, name)
    if node_type in (NodeType.DATA_OUTPUT, NodeType.EXPLORE, NodeType.EXTERNAL_FILE):
        return _first(frames, name)
    if node_type == NodeType.EDGE_JOIN:
        from haute._edge_join import execute_edge_join

        if len(frames) != 2:
            raise ExecutionError(
                f"Edge Join '{name}' needs its base and join frames.",
                node=name,
                received=len(frames),
            )
        return execute_edge_join(frames[0], frames[1], dict(config), collect_eager=True)

    path = _sidecar(config, node_type, name)
    base_dir = pipeline_directory(fn, name, pipeline_dir)
    if node_type == NodeType.API_INPUT:
        from haute._node_apply import resolve_api_input_from_config

        return resolve_api_input_from_config(path, base_dir=base_dir)
    if node_type == NodeType.DATA_INPUT:
        from haute._input_providers import resolve_data_input_from_config
        from haute._project import get_project_root

        return resolve_data_input_from_config(
            path, base_dir=base_dir, project_root=get_project_root(base_dir)
        )
    if node_type == NodeType.MODEL_SCORE:
        from haute._model_scorer import score_from_config

        return score_from_config(_first(frames, name), config=path, base_dir=base_dir)
    if node_type == NodeType.BANDING:
        from haute._rating import apply_banding_from_config

        return apply_banding_from_config(_first(frames, name), path, base_dir=base_dir)
    if node_type == NodeType.RATING_STEP:
        from haute._rating import apply_rating_step_from_config

        return apply_rating_step_from_config(_first(frames, name), path, base_dir=base_dir)
    if node_type == NodeType.SCENARIO_EXPANDER:
        from haute._node_apply import expand_scenarios_from_config

        return expand_scenarios_from_config(_first(frames, name), path, base_dir=base_dir)
    if node_type == NodeType.OPTIMISER_APPLY:
        from haute._node_apply import apply_optimiser_apply_from_config

        return apply_optimiser_apply_from_config(
            *frames, config=path, base_dir=base_dir, source_names=list(input_names)
        )
    if node_type == NodeType.OUTPUT:
        from haute._node_apply import assemble_output_from_config

        return assemble_output_from_config(
            *frames, config=path, base_dir=base_dir, source_names=list(input_names)
        )

    from haute._config_io import load_node_config

    settings = load_node_config(path, base_dir=base_dir)
    if node_type == NodeType.CONSTANT:
        from haute._node_apply import constant_frame

        return constant_frame(settings.get("values", []) or [])
    if node_type == NodeType.LIVE_SWITCH:
        from haute._model_scorer import _scenario_ctx
        from haute._node_apply import select_live_switch_input

        names = list(input_names)
        return select_live_switch_input(
            settings.get("input_scenario_map", {}),
            _scenario_ctx.get(),
            dict(zip(names, frames, strict=True)),
            names,
            switch=name,
        )
    if node_type == NodeType.OPTIMISER:
        from haute._config_validation import resolve_optimiser_data_input

        selected = resolve_optimiser_data_input(settings, list(input_names), node_label=name)
        if selected is None:
            return _first(frames, name)
        return frames[list(input_names).index(selected)]
    raise ConfigError(
        f"Node '{name}' has no standalone behaviour for node type {node_type.value!r}.",
        node=name,
        node_type=node_type.value,
    )


def run_configured_node(
    node_type: NodeType,
    kind: FunctionKind,
    *,
    name: str,
    config: Mapping[str, Any],
    fn: Callable[..., Any],
    frames: Sequence[Any],
    pipeline_dir: str = ".",
) -> Any:
    """Run a configured node: its declaration's work, or that work handed to its hook.

    *pipeline_dir* leads from the file defining *fn* to its pipeline's directory,
    where ``config=`` paths resolve (``.`` except in a submodel definition file).
    """
    positional, _keyword_only = _parameters(fn)

    def work() -> Any:
        return _run_configured_work(
            node_type,
            name=name,
            config=config,
            fn=fn,
            frames=frames,
            input_names=positional,
            pipeline_dir=pipeline_dir,
        )

    if kind == "declaration":
        return work()
    if node_type == NodeType.EXTERNAL_FILE:
        from haute._node_apply import load_external_object_from_config

        obj = load_external_object_from_config(
            _sidecar(config, node_type, name),
            base_dir=pipeline_directory(fn, name, pipeline_dir),
        )
        result = fn(*frames, obj=obj)
    else:
        result = fn(work(), *frames[1:])
    if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
        raise ExecutionError(
            f"Node '{name}' must return its frame (end its code with `return df`); "
            f"it returned {type(result).__name__}.",
            node=name,
        )
    return result
