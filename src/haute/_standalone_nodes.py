"""What a configured node does when a pipeline file runs on its own.

Every node type except ``polars`` is *configured*: its settings live in a JSON
sidecar named by the decorator's ``config=`` keyword (or, for Edge Join, in the
decorator's own keywords), and in a standalone
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._node_specs import CODE_NODE_TYPES, node_spec, node_type_table
from haute._types import NodeType
from haute.errors import ConfigError, ExecutionError

__all__ = [
    "FunctionKind",
    "function_kind",
    "has_empty_body",
    "is_configured",
    "pipeline_directory",
    "run_configured_node",
]

FunctionKind = Literal["transform", "declaration", "hook"]

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


#: Whether a node type's decorator acts on its settings whatever the decorator names: an
#: Edge Join's settings are its keywords, and an Explore node's run refuses to go without
#: its ``config=`` sidecar (READ-43). Any other configured type acts once it names one.
_ALWAYS_CONFIGURED: Mapping[NodeType, bool] = node_type_table(
    "always configured",
    {
        NodeType.API_INPUT: False,
        NodeType.WORKBENCH_INPUT: False,
        NodeType.WORKBENCH_OUTPUT: False,
        NodeType.DATA_INPUT: False,
        NodeType.DATA_OUTPUT: False,
        NodeType.POLARS: False,
        NodeType.EDGE_JOIN: True,
        NodeType.MODEL_SCORE: False,
        NodeType.BANDING: False,
        NodeType.RATING_STEP: False,
        NodeType.OUTPUT: False,
        NodeType.EXPLORE: True,
        NodeType.EXTERNAL_FILE: False,
        NodeType.LIVE_SWITCH: False,
        NodeType.MODELLING: False,
        NodeType.OPTIMISER: False,
        NodeType.SCENARIO_EXPANDER: False,
        NodeType.OPTIMISER_APPLY: False,
        NodeType.CONSTANT: False,
        NodeType.SUBMODEL: False,
        NodeType.SUBMODEL_PORT: False,
    },
)


def is_configured(node_type: NodeType, config: Mapping[str, Any]) -> bool:
    """Whether a registered node has settings for its decorator to act on.

    Edge Join carries its settings as decorator keywords, and an Explore node is
    refused a run without its ``config=`` sidecar (READ-43); every other type
    except ``polars`` names a ``config=`` sidecar. A sidecar type registered
    without one is a plain function the run calls as written — a form the
    static parser rejects, so it never appears in a saved pipeline file.
    """
    if node_spec(node_type).code == "transform":
        return False
    return _ALWAYS_CONFIGURED[node_type] or bool(config.get("config"))


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
    if node_spec(node_type).code == "object_hook":
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


def seeded_workbench_input_ports(
    *, name: str, config: Mapping[str, Any], fn: Callable[..., Any], pipeline_dir: str
) -> tuple[str, ...]:
    """The ports of a Workbench Input ``score()`` seeds with frames: its sidecar read, what it
    may not hold refused, and given the tables of its project's form as a run gives them, so
    a seeded one is held to its form as a run's is (specs/workbench)."""
    from haute._node_apply import workbench_input_ports_from_config

    return workbench_input_ports_from_config(
        _sidecar(config, NodeType.WORKBENCH_INPUT, name),
        base_dir=pipeline_directory(fn, name, pipeline_dir),
    )


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


@dataclass(frozen=True)
class _ConfiguredRun:
    """One configured node's standalone run: what its work reads."""

    node_type: NodeType
    name: str
    config: Mapping[str, Any]
    fn: Callable[..., Any]
    frames: Sequence[Any]
    input_names: Sequence[str]
    pipeline_dir: str
    target_ports: Sequence[str | None] | None

    def first(self) -> Any:
        return _first(self.frames, self.name)

    def located(self) -> tuple[str, Path]:
        """Its ``config=`` sidecar path, and the directory the path resolves against."""
        path = _sidecar(self.config, self.node_type, self.name)
        return path, pipeline_directory(self.fn, self.name, self.pipeline_dir)

    def settings(self) -> dict[str, Any]:
        """Its sidecar, loaded and held to its config model."""
        from haute._config_io import load_node_config

        path, base_dir = self.located()
        return load_node_config(path, base_dir=base_dir, node_type=self.node_type)


def _checked_first(run: _ConfiguredRun) -> Any:
    # Its run passes the frame on, but its sidecar is still held to its model,
    # so a missing or malformed one fails the run as it fails the parse.
    run.settings()
    return run.first()


def _edge_join(run: _ConfiguredRun) -> Any:
    from haute._edge_join import execute_edge_join

    if len(run.frames) != 2:
        raise ExecutionError(
            f"Edge Join '{run.name}' needs its base and join frames.",
            node=run.name,
            received=len(run.frames),
        )
    return execute_edge_join(run.frames[0], run.frames[1], dict(run.config), collect_eager=True)


def _workbench_input(run: _ConfiguredRun) -> Any:
    from haute._node_apply import resolve_workbench_input_from_config

    path, base_dir = run.located()
    return resolve_workbench_input_from_config(path, base_dir=base_dir)


def _quote_input(run: _ConfiguredRun) -> Any:
    from haute._node_apply import resolve_api_input_from_config

    path, base_dir = run.located()
    return resolve_api_input_from_config(path, base_dir=base_dir)


def _data_input(run: _ConfiguredRun) -> Any:
    from haute._input_providers import resolve_data_input_from_config
    from haute._project_context import current_project

    path, base_dir = run.located()
    return resolve_data_input_from_config(
        path, base_dir=base_dir, project_root=current_project().root
    )


def _model_score(run: _ConfiguredRun) -> Any:
    from haute._model_scorer import score_from_config

    path, base_dir = run.located()
    return score_from_config(run.first(), config=path, base_dir=base_dir)


def _banding(run: _ConfiguredRun) -> Any:
    from haute._rating import apply_banding_from_config

    path, base_dir = run.located()
    return apply_banding_from_config(run.first(), path, base_dir=base_dir)


def _rating_step(run: _ConfiguredRun) -> Any:
    from haute._rating import apply_rating_step_from_config

    path, base_dir = run.located()
    return apply_rating_step_from_config(run.first(), path, base_dir=base_dir)


def _scenario_expander(run: _ConfiguredRun) -> Any:
    from haute._node_apply import expand_scenarios_from_config

    path, base_dir = run.located()
    return expand_scenarios_from_config(run.first(), path, base_dir=base_dir)


def _optimiser_apply(run: _ConfiguredRun) -> Any:
    from haute._node_apply import apply_optimiser_apply_from_config

    path, base_dir = run.located()
    return apply_optimiser_apply_from_config(
        *run.frames, config=path, base_dir=base_dir, source_names=list(run.input_names)
    )


def _quote_response(run: _ConfiguredRun) -> Any:
    from haute._node_apply import assemble_output_from_config

    path, base_dir = run.located()
    return assemble_output_from_config(
        *run.frames, config=path, base_dir=base_dir, source_names=list(run.input_names)
    )


def _workbench_output(run: _ConfiguredRun) -> Any:
    from haute._node_apply import assemble_workbench_output_from_config

    path, base_dir = run.located()
    return assemble_workbench_output_from_config(
        *run.frames,
        config=path,
        base_dir=base_dir,
        ports=None if run.target_ports is None else list(run.target_ports),
    )


def _constant(run: _ConfiguredRun) -> Any:
    from haute._node_apply import constant_frame

    return constant_frame(run.settings().get("values", []) or [])


def _source_switch(run: _ConfiguredRun) -> Any:
    from haute._model_scorer import _scenario_ctx
    from haute._node_apply import select_live_switch_input

    settings = run.settings()
    names = list(run.input_names)
    return select_live_switch_input(
        settings.get("input_scenario_map", {}),
        _scenario_ctx.get(),
        dict(zip(names, run.frames, strict=True)),
        names,
        switch=run.name,
    )


def _selected_input(run: _ConfiguredRun) -> Any:
    from haute._config_validation import selected_input_index

    index = selected_input_index(
        run.node_type, run.settings(), list(run.input_names), node_label=run.name
    )
    if not index:
        # No input connected, or the first: the first frame, refused when there is none.
        return run.first()
    return run.frames[index]


def _no_standalone_behaviour(run: _ConfiguredRun) -> Any:
    run.settings()
    raise ConfigError(
        f"Node '{run.name}' has no standalone behaviour for node type {run.node_type.value!r}.",
        node=run.name,
        node_type=run.node_type.value,
    )


#: What each node type's decorator does in a standalone run, as canvas execution does:
#: the shared helper its builder also calls.
_STANDALONE_WORK: Mapping[NodeType, Callable[[_ConfiguredRun], Any]] = node_type_table(
    "standalone work",
    {
        NodeType.API_INPUT: _quote_input,
        NodeType.WORKBENCH_INPUT: _workbench_input,
        NodeType.WORKBENCH_OUTPUT: _workbench_output,
        NodeType.DATA_INPUT: _data_input,
        NodeType.DATA_OUTPUT: _checked_first,
        NodeType.POLARS: _no_standalone_behaviour,
        NodeType.EDGE_JOIN: _edge_join,
        NodeType.MODEL_SCORE: _model_score,
        NodeType.BANDING: _banding,
        NodeType.RATING_STEP: _rating_step,
        NodeType.OUTPUT: _quote_response,
        NodeType.EXPLORE: _checked_first,
        NodeType.EXTERNAL_FILE: _checked_first,
        NodeType.LIVE_SWITCH: _source_switch,
        NodeType.MODELLING: _checked_first,
        NodeType.OPTIMISER: _selected_input,
        NodeType.SCENARIO_EXPANDER: _scenario_expander,
        NodeType.OPTIMISER_APPLY: _optimiser_apply,
        NodeType.CONSTANT: _constant,
        NodeType.SUBMODEL: _no_standalone_behaviour,
        NodeType.SUBMODEL_PORT: _no_standalone_behaviour,
    },
)


def _run_configured_work(
    node_type: NodeType,
    *,
    name: str,
    config: Mapping[str, Any],
    fn: Callable[..., Any],
    frames: Sequence[Any],
    input_names: Sequence[str],
    pipeline_dir: str,
    target_ports: Sequence[str | None] | None = None,
) -> Any:
    """Perform *node_type*'s configured work on *frames*, as canvas execution does.

    *target_ports* are the ports *frames* arrive on, aligned with them, when the run
    knows them; a Workbench Output needs them to know which table each frame fills.
    """
    run = _ConfiguredRun(
        node_type, name, config, fn, frames, input_names, pipeline_dir, target_ports
    )
    return _STANDALONE_WORK[node_type](run)


def live_switch_routes(
    *, name: str, config: Mapping[str, Any], fn: Callable[..., Any], pipeline_dir: str = "."
) -> tuple[list[str], dict[str, str]]:
    """A Source Switch's input names, in connection order, and its input-to-source map.

    The names are its function's positional parameters, which its run binds to its
    frames in that order; the map is its sidecar's ``input_scenario_map``.
    """
    from haute._config_io import load_node_config

    settings = load_node_config(
        _sidecar(config, NodeType.LIVE_SWITCH, name),
        base_dir=pipeline_directory(fn, name, pipeline_dir),
        node_type=NodeType.LIVE_SWITCH,
    )
    positional, _keyword_only = _parameters(fn)
    return positional, dict(settings.get("input_scenario_map") or {})


def run_configured_node(
    node_type: NodeType,
    kind: FunctionKind,
    *,
    name: str,
    config: Mapping[str, Any],
    fn: Callable[..., Any],
    frames: Sequence[Any],
    pipeline_dir: str = ".",
    target_ports: Sequence[str | None] | None = None,
) -> Any:
    """Run a configured node: its declaration's work, or that work handed to its hook.

    *pipeline_dir* leads from the file defining *fn* to its pipeline's directory,
    where ``config=`` paths resolve (``.`` except in a submodel definition file).
    *target_ports* are the ports *frames* arrive on, when the caller knows them.
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
            target_ports=target_ports,
        )

    if kind == "declaration":
        return work()
    if node_spec(node_type).code == "object_hook":
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
