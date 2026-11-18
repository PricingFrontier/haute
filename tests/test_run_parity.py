"""Every way of running a pipeline gives one answer (EXE-10).

A pipeline is run six ways (specs/execution-engine/high-level.md, "Ways of running a
pipeline"): the editor's preview, a trace, ``haute run``, the file imported and run as
Python, the deploy's dry run and the deployed service. This module runs every shipped
example, and a generated pipeline for each node type and each Columns setting, every way
that answers at its target, with the same inputs, target, source and memory profile (in
this process, with no memory cap). It requires one answer from each: the same rows, as
the service renders them, and the same columns in the same order and the same types
wherever a way reports them; or the same refusal, in the words the case names.

A difference the specification allows has its own case (``MEANT``), which shows it and
names the specification's sentence; a sentence the specification does not hold fails.
A difference another roadmap package owns is a strict expected failure naming it, so the
package's delivery turns it green. A node type or a way without a row here fails
(``test_every_node_type_has_a_case``, ``test_the_ways_are_the_specification_s``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._node_specs import node_type_table
from haute._types import NodeType
from tests._project import use_project

REPO = Path(__file__).resolve().parents[1]
SHIPPED_EXAMPLES = REPO / "src" / "haute" / "assistant" / "assets" / "examples"
EXECUTION_SPEC = REPO / "specs" / "execution-engine" / "high-level.md"

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


class Way(StrEnum):
    """A way of running a pipeline, as the specification's table names it."""

    PREVIEW = "preview"
    TRACE = "trace"
    HAUTE_RUN = "haute run"
    PYTHON = "plain Python"
    DRY_RUN = "dry run"
    SERVICE = "service"


#: The ways that answer at any node, and the ways that answer only at a deployment's
#: response node under the live source (``MEANT["deployment answers"]``).
FRAME_WAYS = (Way.PREVIEW, Way.TRACE, Way.HAUTE_RUN, Way.PYTHON)
DEPLOY_WAYS = (Way.DRY_RUN, Way.SERVICE)


@dataclass(frozen=True)
class Meant:
    """A difference between ways the specification allows: the specification's sentence
    that allows it, and the words a way refusing for it uses."""

    sentence: str
    words: str = ""


MEANT: Mapping[str, Meant] = {
    "deployment answers": Meant("A deployment answers at its response node, under the live source"),
    "row limit": Meant("A preview shows the first rows of the answer, up to its row limit"),
    "no request input": Meant(
        "A pipeline with more than one source and no request input is not deployed",
        "none is a request input",
    ),
    "workbench": Meant(
        "A workbench project is released as the underwriter app, which prices its quotes, "
        "and `haute deploy` refuses it",
        "this project prices in the workbench",
    ),
    "one row per quote": Meant(
        "A request of several quotes is answered with one row per quote, in request order; "
        "a quote the pipeline answers with other than one row is answered in a request of "
        "its own",
        "A request of several quotes is answered with one row per quote",
    ),
    "unmeasured boundary": Meant(
        "work whose memory nobody has measured is admitted, with a warning, under a "
        "worker's memory cap and refused without one",
        "cross_join_unmeasured",
    ),
    "workbench trace": Meant(
        "A trace of a Workbench Output explains a cell from the frame that fills its table"
    ),
    "no contracts": Meant(
        "The file run as Python checks no node's column contract: a node reading a column "
        "its input lacks refuses it in its own words",
        "does not provide",
    ),
    "not admitted": Meant(
        "The file run as Python runs in the analyst's own process, which Haute neither caps "
        "nor admits, so it runs work an admitted way refuses for its memory"
    ),
}


# ---------------------------------------------------------------------------
# One answer
# ---------------------------------------------------------------------------


def _pruned(value: Any) -> Any:
    """*value* as a response document holds it: null fields and empty collections left
    out, as ``render_output_document`` leaves them out of a frame's rows."""
    if isinstance(value, dict):
        kept = {key: _pruned(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if item not in (None, [], {})}
    if isinstance(value, list):
        return [_pruned(item) for item in value]
    return value


def _row_text(row: Mapping[str, Any]) -> str:
    from haute.deploy._response_json import response_json

    return response_json(_pruned(dict(row))).decode("utf-8")


@dataclass(frozen=True)
class Answer:
    """What one way answers at a target: its rows, as the service renders them, with the
    columns and types it reports (``None`` where it reports none), or its refusal."""

    rows: tuple[str, ...] = ()
    columns: tuple[str, ...] | None = None
    dtypes: tuple[str, ...] | None = None
    refusal: str | None = None
    #: Whether the refusal is an exception the way did not answer: never a refusal a case
    #: names, however its words read.
    internal: bool = False

    @classmethod
    def of_frame(cls, frame: pl.DataFrame) -> Answer:
        from haute._output_assembler import render_output_document

        return cls(
            rows=tuple(_row_text(row) for row in render_output_document(frame)),
            columns=tuple(frame.columns),
            dtypes=tuple(str(dtype) for dtype in frame.dtypes),
        )

    @classmethod
    def of_rows(
        cls,
        rows: Sequence[Mapping[str, Any]],
        columns: Sequence[str] | None = None,
        dtypes: Sequence[str] | None = None,
    ) -> Answer:
        return cls(
            rows=tuple(_row_text(row) for row in rows),
            columns=None if columns is None else tuple(columns),
            dtypes=None if dtypes is None else tuple(dtypes),
        )

    @classmethod
    def refused(cls, words: object) -> Answer:
        return cls(refusal=str(words))

    @classmethod
    def raised(cls, exc: Exception) -> Answer:
        return cls(refusal=f"{type(exc).__name__}: {exc}", internal=True)

    def show(self) -> str:
        if self.refusal is not None:
            raised = "raised and " if self.internal else ""
            return f"{raised}refused: {self.refusal[:600]}"
        described = [f"{len(self.rows)} rows"]
        if self.columns is not None:
            described.append(f"columns {list(self.columns)}")
        if self.dtypes is not None:
            described.append(f"types {list(self.dtypes)}")
        return "; ".join(described) + "".join(f"\n      {row}" for row in self.rows[:6])


def _disagreements(answers: Mapping[Way, Answer], refusal: str | None) -> list[str]:
    """How *answers* fail to be one answer: every way the same rows (and columns and types
    where both report them), or, when the case names *refusal*, every way refusing in it."""
    problems: list[str] = []
    if refusal is not None:
        for way, answer in answers.items():
            if answer.refusal is None or answer.internal or refusal not in answer.refusal:
                problems.append(f"{way} does not refuse in the words {refusal!r}")
        return problems
    reference_way, reference = next(iter(answers.items()))
    for way, answer in answers.items():
        if answer.refusal is not None:
            problems.append(f"{way} refuses")
            continue
        if reference.refusal is not None:
            continue
        if answer.rows != reference.rows:
            problems.append(f"{way}'s rows differ from {reference_way}'s")
        for aspect in ("columns", "dtypes"):
            mine, theirs = getattr(answer, aspect), getattr(reference, aspect)
            if mine is not None and theirs is not None and mine != theirs:
                problems.append(f"{way}'s {aspect} differ from {reference_way}'s")
    return problems


def _shown(answers: Mapping[Way, Answer]) -> str:
    return "\n".join(f"    {way}: {answer.show()}" for way, answer in answers.items())


def _owned(
    package: str,
    answers: Mapping[Way, Answer],
    differing: Mapping[Way, Callable[[Answer], bool]],
    refusal: str | None = None,
) -> None:
    """The difference *package* owns, held by its strict expected failure.

    *differing* names each way the owned difference sets apart, with what that way answers
    because of it. When every such way answers so, and every other way gives one answer
    (none raising), this raises the ``AssertionError`` the expected failure takes. Anything
    else (another difference, an exception no check expects, an owned way answering
    otherwise) fails the test outright, never taken for the owned difference; and one answer
    from every way passes, so the strict mark fails until it is removed.
    """
    rest = {way: answer for way, answer in answers.items() if way not in differing}
    present = bool(differing) and all(check(answers[way]) for way, check in differing.items())
    if present:
        problems = _disagreements(rest, refusal)
        if not problems:
            raise AssertionError(f"{package}, as its entry describes:\n{_shown(answers)}")
        pytest.fail(
            f"{package}'s difference, and another: " + "; ".join(problems) + "\n" + _shown(answers)
        )
    problems = _disagreements(answers, refusal)
    if problems:
        pytest.fail(f"not {package}'s difference: " + "; ".join(problems) + "\n" + _shown(answers))


def _refuses(answer: Answer, words: str) -> bool:
    """Whether *answer* is a refusal holding *words*, as the way answered it."""
    return answer.refusal is not None and not answer.internal and words in answer.refusal


def _assert_one_answer(answers: Mapping[Way, Answer], refusal: str | None = None) -> None:
    problems = _disagreements(answers, refusal)
    assert not problems, "; ".join(problems) + "\n" + _shown(answers)


# ---------------------------------------------------------------------------
# A project, and running it each way
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A node a case reads its answer at, under a source."""

    node: str
    source: str = "live"
    #: The words every way refuses in, for a target that is refused.
    refusal: str | None = None
    #: A file whose response or single leaf is *node*, for plain Python; ``None`` when
    #: *node* is the project's response node.
    leaf: str | None = None


@dataclass
class Project:
    """A pipeline project on disk, the quotes its deployment is sent, and its targets."""

    root: Path
    pipeline: Path
    targets: list[Target]
    #: The quotes the dry run and the service are sent, as JSON; ``None`` when the
    #: pipeline is not deployed.
    request: list[dict[str, Any]] | None = None
    #: The response node, which a deployment answers at.
    response: str = "quote_response"


def _short_name(tmp_path: Path, name: str) -> str:
    """A short folder name for the project of *name* beside *tmp_path*: snapshot
    generations add long content-addressed components, and the project's path must stay
    short enough for Windows' legacy path limit."""
    return "rp-" + hashlib.sha256(f"{tmp_path}:{name}".encode()).hexdigest()[:10]


def _fresh(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()


def _finish_project(root: Path) -> None:
    """Make *root* a project as a checkout is: its haute.toml names the test quotes'
    folder, and it is a git repository."""
    toml = root / "haute.toml"
    text = toml.read_text(encoding="utf-8") if toml.exists() else ""
    if "[project]" not in text:
        text = '[project]\npipeline = "main.py"\n' + text
    if "[test_quotes]" not in text:
        text += '\n[test_quotes]\ndir = "tests/quotes"\n'
    toml.write_text(text, encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    use_project(root)


def _graph(project: Project) -> Any:
    """The pipeline as the editor previews it: parsed, flattened, and its workbench nodes
    given its form's tables."""
    from haute._workbench_nodes import form_owner, with_form_tables
    from haute.graph_utils import flatten_graph
    from haute.routes._helpers import parse_pipeline_to_graph

    graph = flatten_graph(parse_pipeline_to_graph(project.pipeline))
    if form_owner(graph) is not None:
        graph = with_form_tables(graph, project_root=project.root)
    return graph


def _prepare_inputs(project: Project) -> None:
    """Build each Data Input's snapshot, so every way reads the same rows.

    A preview builds a snapshot it needs; plain Python and the dry run read one already
    built (``test_a_clean_checkout_runs_every_way`` holds that difference).
    """
    from haute._execution_context import ExecutionProfile
    from haute._input_providers import build_input_snapshot
    from haute._polars_io_registry import data_input_is_direct
    from haute._source_cache import SourceCacheStore

    store = SourceCacheStore(project.root)
    for node in _graph(project).nodes:
        if node.data.nodeType != NodeType.DATA_INPUT:
            continue
        config = {key: value for key, value in node.data.config.items() if key != "code"}
        if data_input_is_direct(config):
            continue
        path = config.get("path")
        if isinstance(path, str) and not Path(path).is_absolute():
            config["path"] = str((project.root / path).resolve())
        build_input_snapshot(
            config, store=store, base_dir=project.root, profile=ExecutionProfile.PREVIEW_EAGER
        )


def _preview(project: Project, target: Target, row_limit: int | None = None) -> Answer:
    from haute.errors import HauteError
    from haute.executor import execute_graph

    try:
        result = execute_graph(
            _graph(project), target_node_id=target.node, source=target.source, row_limit=row_limit
        )[target.node]
    except (HauteError, ValueError) as exc:
        return Answer.refused(exc)
    if result.status != "ok":
        return Answer.refused(result.error)
    return Answer.of_rows(
        result.preview,
        [column.name for column in result.columns],
        [column.dtype for column in result.columns],
    )


def _trace(project: Project, target: Target, rows: int) -> Answer:
    from haute.errors import HauteError
    from haute.trace import execute_trace

    graph = _graph(project)
    traced: list[dict[str, Any]] = []
    try:
        for row_index in range(max(rows, 1)):
            trace = execute_trace(
                graph, row_index=row_index, target_node_id=target.node, source=target.source
            )
            if rows:
                traced.append(dict(trace.output_value))
    except (HauteError, ValueError) as exc:
        return Answer.refused(exc)
    return Answer.of_rows(traced, list(traced[0]) if traced else None)


def _haute_run(project: Project, target: Target, capsys: pytest.CaptureFixture[str]) -> Answer:
    from haute.cli._run import RunConfig, run_frames

    capsys.readouterr()
    try:
        frames = run_frames(
            RunConfig(pipeline_file=project.pipeline, node=target.node, source=target.source)
        )
    except SystemExit:
        return Answer.refused(capsys.readouterr().err)
    ((_label, frame),) = frames
    return Answer.of_frame(frame)


@contextlib.contextmanager
def _imported(path: Path) -> Iterator[dict[str, Any]]:
    """The module *path* defines, imported as ``python <path>`` imports it, and the modules
    it imported from its folder forgotten afterwards."""
    before = set(sys.modules)
    sys.path.insert(0, str(path.parent))
    try:
        yield runpy.run_path(str(path), run_name=f"haute_parity_{path.stem}")
    finally:
        sys.path.remove(str(path.parent))
        for name in set(sys.modules) - before:
            module_file = getattr(sys.modules[name], "__file__", None) or ""
            if module_file and Path(module_file).resolve().is_relative_to(path.parent):
                del sys.modules[name]


def _python(project: Project, target: Target) -> Answer:
    """``pipeline.run()``, and, at a deployment's response, ``pipeline.score()`` of the
    quotes as the service frames them: one answer, or the two shown."""
    from haute.errors import HauteError

    path = project.pipeline if target.leaf is None else project.root / target.leaf
    refused = (HauteError, ValueError, pl.exceptions.PolarsError)
    with _imported(path) as namespace:
        pipeline = namespace["pipeline"]
        try:
            ran = Answer.of_frame(pipeline.run(source=target.source))
        except refused as exc:
            ran = Answer.refused(exc)
        scored_frame = _scored_request(project) if target.leaf is None else None
        if scored_frame is None or target.source != "live":
            return ran
        try:
            scored = Answer.of_frame(pipeline.score(scored_frame))
        except refused as exc:
            scored = Answer.refused(exc)
    if scored != ran:
        return Answer.refused(
            f"run() and score() differ:\n  run: {ran.show()}\n  score: {scored.show()}"
        )
    return ran


def _scored_request(project: Project) -> pl.DataFrame | None:
    """The quotes as the service frames them, which ``pipeline.score()`` seeds its one input
    with: a Quote Input whose one table is the request's root, or a Data Input the
    deployment reads its requests by; ``None`` for any other pipeline."""
    from haute.cli._helpers import EXPECTED_FAILURES
    from haute.deploy._request_frame import frame_rows, request_frame_schema

    if not project.request:
        return None
    try:
        resolved = _resolved(project)
    except EXPECTED_FAILURES:
        # A pipeline its deployment refuses: run() alone answers.
        return None
    try:
        (input_id,) = resolved.input_node_ids
        node = _graph(project).node_map[input_id]
        tables = node.data.config.get("tables") or []
        one_root_table = len(tables) == 1 and tables[0].get("path") == "$[:]"
        if node.data.nodeType == NodeType.API_INPUT and not one_root_table:
            return None
        if node.data.nodeType not in (NodeType.API_INPUT, NodeType.DATA_INPUT):
            return None
        schema = request_frame_schema(resolved.input_schema)
        return frame_rows(project.request, schema, omittable=frozenset(resolved.omittable_inputs))
    finally:
        resolved.close()


def _test_quotes(project: Project) -> Path:
    assert project.request is not None
    path = project.root / "tests" / "quotes" / "parity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"input": row} for row in project.request]), encoding="utf-8")
    return path


def _resolved(project: Project) -> Any:
    from haute.deploy._config import DeployConfig, resolve_config

    return resolve_config(DeployConfig.from_toml(project.root / "haute.toml"))


def _dry_run(project: Project, capsys: pytest.CaptureFixture[str]) -> Answer:
    """``haute deploy --dry-run`` on the test quotes, and, when it passes, the answers
    it priced them to."""
    from haute.cli._deploy import DeployCliConfig, handle_deploy
    from haute.deploy._validators import price_test_quote_file

    quotes = _test_quotes(project)
    capsys.readouterr()
    try:
        handle_deploy(
            DeployCliConfig(pipeline_file=None, model_name=None, endpoint_suffix=None, dry_run=True)
        )
    except SystemExit:
        return Answer.refused(capsys.readouterr().err)
    resolved = _resolved(project)
    try:
        answers = price_test_quote_file(resolved, quotes)
    finally:
        resolved.close()
    return Answer.of_frame(pl.concat(answers, how="vertical"))


def _served_app(project: Project, resolved: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib.util

    from fastapi.testclient import TestClient

    from haute.deploy._container import _generate_app_source, write_bundle

    build = project.root / "build"
    shutil.rmtree(build, ignore_errors=True)
    write_bundle(resolved, build)
    app_path = build / "app.py"
    app_path.write_text(_generate_app_source("parity", 8080), encoding="utf-8")
    module_name = f"_haute_parity_app_{project.root.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    async def in_process(function: Any, *args: Any, config: Any) -> Any:
        del config
        return function(*args)

    # The batch worker runs in this process, as its spawn would run it.
    monkeypatch.setattr(module, "run_isolated_worker_async", in_process)
    return TestClient(module.app)


def _service(project: Project, monkeypatch: pytest.MonkeyPatch, *, batched: bool = False) -> Answer:
    """The served app sent the quotes as the dry run sends them (one request, or one a
    quote), or, *batched*, as one request whatever the deployment's record."""
    from haute.cli._helpers import EXPECTED_FAILURES
    from haute.deploy._validators import _case_per_request

    assert project.request is not None
    try:
        resolved = _resolved(project)
    except EXPECTED_FAILURES as exc:
        return Answer.refused(exc)
    try:
        client = _served_app(project, resolved, monkeypatch)
        alone = _case_per_request(resolved) and not batched
    finally:
        resolved.close()
    requests = [[row] for row in project.request] if alone else [project.request]
    rows: list[dict[str, Any]] = []
    for request in requests:
        response = client.post("/quote", json=request[0] if len(request) == 1 else request)
        if response.status_code != 200:
            return Answer.refused(response.text)
        rows.extend(response.json()["rows"])
    return Answer.of_rows(rows)


def _answers(
    project: Project,
    target: Target,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    without: frozenset[Way] = frozenset(),
) -> dict[Way, Answer]:
    """*target*'s answer from each way that answers at it (``MEANT["deployment answers"]``),
    but the ways *without*."""
    deployed = (
        project.request is not None and target.node == project.response and target.source == "live"
    )
    preview = _answered(lambda: _preview(project, target))
    runs: dict[Way, Callable[[], Answer]] = {
        Way.PREVIEW: lambda: preview,
        Way.TRACE: lambda: _trace(project, target, len(preview.rows)),
        Way.HAUTE_RUN: lambda: _haute_run(project, target, capsys),
        Way.PYTHON: lambda: _python(project, target),
    }
    if deployed:
        runs[Way.DRY_RUN] = lambda: _dry_run(project, capsys)
        runs[Way.SERVICE] = lambda: _service(project, monkeypatch)
    return {way: _answered(run) for way, run in runs.items() if way not in without}


def _answered(run: Callable[[], Answer]) -> Answer:
    """*run*'s answer, or the exception it raised as an internal error."""
    try:
        return run()
    except Exception as exc:
        return Answer.raised(exc)


def _assert_every_target(
    project: Project, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = []
    for target in project.targets:
        answers = _answers(project, target, capsys, monkeypatch)
        problems = _disagreements(answers, target.refusal)
        if problems:
            failures.append(
                f"at {target.node} under {target.source}: {'; '.join(problems)}\n" + _shown(answers)
            )
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# The shipped examples
# ---------------------------------------------------------------------------


def _example_project(tmp_path: Path, name: str) -> Project:
    from haute.assistant._assets import materialize_example_bundle
    from haute.deploy._pruner import find_output_node

    root = tmp_path.parent / _short_name(tmp_path, name)

    _fresh(root)
    if name == "reference":
        shutil.copytree(REPO / "examples" / "reference", root / "examples" / "reference")
        (root / "haute.toml").write_text(
            '[project]\npipeline = "examples/reference/main.py"\n', encoding="utf-8"
        )
        pipeline = root / "examples" / "reference" / "main.py"
    else:
        root.rmdir()
        materialize_example_bundle(name, root)
        pipeline = root / "pipeline.py"
    _finish_project(root)
    project = Project(root=root, pipeline=pipeline, targets=[])
    graph = _graph(project)
    project.response = find_output_node(graph)
    project.targets = [Target(project.response)]
    if name in _EXAMPLES_REFUSED:
        # Nothing to prepare or send: the pipeline is refused before it reads a quote.
        project.request = []
        return project
    _prepare_inputs(project)
    project.request = _request_rows(project, graph)
    return project


def _request_rows(project: Project, graph: Any) -> list[dict[str, Any]] | None:
    """The quotes a deployment of *project* is sent: its request input's sample, or the
    rows of the one source it reads; ``None`` when it has neither."""
    from haute._node_specs import node_spec
    from haute.cli._helpers import EXPECTED_FAILURES
    from haute.executor import execute_graph

    try:
        resolved = _resolved(project)
    except EXPECTED_FAILURES:
        # Not deployed (``MEANT["no request input"]``): the quotes are never read.
        return []
    try:
        (input_id,) = resolved.input_node_ids
    finally:
        resolved.close()
    node = graph.node_map[input_id]
    if node_spec(node.data.nodeType).request_input:
        raw = json.loads((project.root / node.data.config["path"]).read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else [raw]
    result = execute_graph(graph, target_node_id=input_id, source="live")[input_id]
    return list(result.preview)


#: The examples whose ways differ for a reason another package owns.
_EXAMPLES_OWNED_ELSEWHERE: Mapping[str, Any] = {
    "model_lifecycle": pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="MLR-04: a Model Scoring run that does not exist is refused in other words "
        "by each way",
    ),
}

#: The words every way refuses an example in: one whose model was never trained, refused
#: naming the run its Model Scoring names.
_EXAMPLES_REFUSED: Mapping[str, str] = {"model_lifecycle": "replace-with-training-run-id"}


#: The examples a deployment refuses, by the meant difference that allows it.
_EXAMPLES_NOT_DEPLOYED: Mapping[str, str] = {"reference_join": "no request input"}


def _examples() -> list[Any]:
    names = ["reference", *sorted(p.name for p in SHIPPED_EXAMPLES.iterdir() if p.is_dir())]
    return [
        pytest.param(
            name,
            id=name,
            marks=[_EXAMPLES_OWNED_ELSEWHERE[name]] if name in _EXAMPLES_OWNED_ELSEWHERE else [],
        )
        for name in names
    ]


@pytest.mark.parametrize("name", _examples())
def test_a_shipped_example_gives_one_answer_every_way(
    tmp_path: Path,
    name: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _example_project(tmp_path, name)
    (target,) = project.targets
    answers = _answers(project, target, capsys, monkeypatch)
    if name in _EXAMPLES_NOT_DEPLOYED:
        meant = MEANT[_EXAMPLES_NOT_DEPLOYED[name]]
        for way in DEPLOY_WAYS:
            refused = answers.pop(way)
            assert refused.refusal is not None and meant.words in refused.refusal, (
                f"{way}: {refused.show()}"
            )
    words = _EXAMPLES_REFUSED.get(name)
    if name in _EXAMPLES_OWNED_ELSEWHERE:
        assert words is not None

        def raised(answer: Answer) -> bool:
            # The missing run raised as MLflow's own exception, naming it: the command line's
            # edge turns the same exception into its unrecognised-failure sentence.
            refusal = answer.refusal or ""
            return answer.internal and refusal.startswith("MlflowException: ") and words in refusal

        differing = {way: raised for way, answer in answers.items() if not _refuses(answer, words)}
        _owned("MLR-04", answers, differing, words)
        return
    _assert_one_answer(answers, refusal=words)


# ---------------------------------------------------------------------------
# Generated pipelines: each node type, each Columns setting
# ---------------------------------------------------------------------------

_QUOTES = [
    {"quote_id": "q1", "age": 30, "region": "N", "exposure": 1.0},
    {"quote_id": "q2", "age": 45, "region": "S", "exposure": 0.5},
]
_QUOTE_COLUMNS = ["quote_id", "age", "region", "exposure"]
_QUOTE_TYPES = {"quote_id": "str", "age": "int", "region": "str", "exposure": "float"}


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(root: Path, rel: str, value: Any) -> None:
    _write(root, rel, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _quote_input_config(columns: Sequence[str] = _QUOTE_COLUMNS) -> dict[str, Any]:
    return {
        "path": "data/quotes.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "row_id_column": "quote_id",
                "columns": [
                    {
                        "name": name,
                        "path": f"$[:].{name}",
                        "type": _QUOTE_TYPES[name],
                        "status": "Confirmed",
                        "selected": name in columns,
                        "levels": None,
                    }
                    for name in _QUOTE_COLUMNS
                ],
            }
        ],
    }


def _output_config(source: str, columns: Sequence[str]) -> dict[str, Any]:
    return {
        "output_mapping": [
            {
                "source_port": source,
                "source_column": column,
                "output_path": f"$[:].{column}",
                "enabled": True,
            }
            for column in columns
        ]
    }


@dataclass(frozen=True)
class _Def:
    """One node of a generated pipeline file: its definition, and the inputs it is connected
    to as ``(source, source_port, target_port)``."""

    name: str
    text: str
    inputs: tuple[tuple[str, str | None, str | None], ...] = ()


_QUOTE_INPUT = _Def(
    "quote_input", '@pipeline.api_input(config="config/quote_input.json")\ndef quote_input(): ...'
)


def _from_quotes(target_port: str | None = None) -> tuple[str, str | None, str | None]:
    return ("quote_input", "quotes", target_port)


def _pipeline_text(defs: Sequence[_Def], upto: str | None = None) -> str:
    """The pipeline file holding *defs*, or only *upto* and the nodes it reads."""
    by_name = {d.name: d for d in defs}
    kept: set[str] = set()
    pending = [upto] if upto is not None else list(by_name)
    while pending:
        name = pending.pop()
        if name not in kept:
            kept.add(name)
            pending.extend(source for source, _sp, _tp in by_name[name].inputs)
    parts = ['import polars as pl\n\nimport haute\n\npipeline = haute.Pipeline("parity")\n']
    connects: list[str] = []
    for d in defs:
        if d.name not in kept:
            continue
        parts.append(d.text + "\n")
        for source, source_port, target_port in d.inputs:
            ports = "".join(
                f', {key}="{value}"'
                for key, value in (("source_port", source_port), ("target_port", target_port))
                if value
            )
            connects.append(f'pipeline.connect("{source}", "{d.name}"{ports})')
    return "\n\n".join(parts) + "\n\n" + "\n".join(connects) + "\n"


@dataclass(frozen=True)
class Columns:
    """A node's Columns settings, as its settings hold them."""

    name: str
    selected: tuple[str, ...] = ()
    renames: Mapping[str, str] = field(default_factory=dict)

    def settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        if self.selected:
            settings["selected_columns"] = list(self.selected)
        if self.renames:
            settings["column_renames"] = dict(self.renames)
        return settings

    def keywords(self) -> str:
        """The settings as decorator keywords: a Transform's and an Edge Join's."""
        return "".join(f", {key}={value!r}" for key, value in self.settings().items())

    def shaped(self, columns: Sequence[str]) -> list[str]:
        """*columns* as these settings leave them: selected, in the selection's order,
        then renamed."""
        kept = [c for c in self.selected if c in columns] if self.selected else list(columns)
        return [self.renames.get(c, c) for c in kept]


def _stale_words(node: str) -> str:
    """The one sentence every way refuses a selection none of whose columns exist in."""
    return (
        f"Node {node!r} keeps columns its input no longer has any of: 'gone'. "
        "Choose its columns again."
    )


EVERY_COLUMN = Columns("every column")


def _columns_settings(chosen: str, other: str = "quote_id") -> list[Columns]:
    """Each Columns setting: every column, two columns in another order than the frame's,
    the two with one renamed, and a selection whose every column is gone."""
    return [
        EVERY_COLUMN,
        Columns("chosen", (chosen, other)),
        Columns("chosen and renamed", (chosen, other), {chosen: f"{chosen}_out"}),
        Columns("stale", ("gone",)),
    ]


@dataclass(frozen=True)
class Written:
    """What a generated case writes: its nodes, the node under test's columns before its
    own Columns settings, and, when the response does not read the node under test, the
    node it reads and that node's columns given the node under test's."""

    defs: list[_Def]
    columns: list[str]
    response_reads: tuple[str, Callable[[list[str]], list[str]]] | None = None
    #: The quotes its deployment is sent; the generated quotes when ``None``.
    request: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class Generated:
    """A generated pipeline: the node under test, its type and its Columns settings."""

    node_type: NodeType
    node: str
    write: Callable[[Path, Columns], Written]
    columns: Columns = EVERY_COLUMN
    #: Another source the node is read under, beside ``live``.
    other_source: str | None = None
    #: Other nodes read, each from its own leaf file, beside the node under test.
    also: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.node_type.value}: {self.columns.name}"


def _generated_project(tmp_path: Path, case: Generated) -> Project:
    root = tmp_path.parent / _short_name(tmp_path, case.name)
    _fresh(root)
    _write_json(root, "data/quotes.json", _QUOTES)
    _write_json(root, "config/quote_input.json", _quote_input_config())
    written = case.write(root, case.columns)
    stale = case.columns.name == "stale"
    shaped = written.columns if stale else case.columns.shaped(written.columns)
    reads, columns = case.node, shaped
    if written.response_reads is not None:
        reads, after = written.response_reads
        columns = after(shaped)
    _write_json(root, "config/output.json", _output_config(reads, columns))
    defs = [
        *written.defs,
        _Def(
            "quote_response",
            f'@pipeline.output(config="config/output.json")\ndef quote_response({reads}): ...',
            ((reads, None, None),),
        ),
    ]
    _write(root, "main.py", _pipeline_text(defs))
    _write(root, "leaf.py", _pipeline_text(defs, upto=case.node))
    for node in case.also:
        _write(root, f"leaf_{node}.py", _pipeline_text(defs, upto=node))
    _finish_project(root)
    refusal = _stale_words(case.node) if stale else None
    sources = ["live", *([case.other_source] if case.other_source else [])]
    targets = [Target(case.node, source, refusal, "leaf.py") for source in sources]
    targets.extend(Target(node, leaf=f"leaf_{node}.py") for node in case.also)
    targets.append(Target("quote_response", refusal=refusal))
    project = Project(
        root=root,
        pipeline=root / "main.py",
        targets=targets,
        request=written.request if written.request is not None else _QUOTES,
    )
    _prepare_inputs(project)
    return project


def _on_quotes(name: str, decorator: str, created: list[str]) -> Written:
    """A node reading the quotes, declared by *decorator*, adding *created*."""
    return Written(
        [_QUOTE_INPUT, _Def(name, f"{decorator}\ndef {name}(quotes): ...", (_from_quotes(),))],
        [*_QUOTE_COLUMNS, *created],
    )


def _transform(root: Path, columns: Columns) -> Written:
    return Written(
        [
            _QUOTE_INPUT,
            _Def(
                "calc",
                f"@pipeline.polars({columns.keywords().lstrip(', ')})\n"
                "def calc(quotes: pl.LazyFrame) -> pl.LazyFrame:\n"
                '    return quotes.with_columns(doubled=pl.col("age") * 2)',
                (_from_quotes(),),
            ),
        ],
        [*_QUOTE_COLUMNS, "doubled"],
    )


def _banding(root: Path, columns: Columns) -> Written:
    factor = {
        "banding": "breakpoints",
        "column": "age",
        "output_column": "age_band",
        "rules": [{"upper": 40, "label": "young"}, {"label": "older"}],
    }
    _write_json(root, "config/banding/banded.json", {"factors": [factor], **columns.settings()})
    return _on_quotes(
        "banded", '@pipeline.banding(config="config/banding/banded.json")', ["age_band"]
    )


def _rating_step(root: Path, columns: Columns) -> Written:
    table = {
        "factors": ["region"],
        "output_column": "region_factor",
        "default_value": 1.0,
        "rows": [["N", 1.2], ["S", 0.9]],
    }
    _write_json(root, "config/rating_step/rated.json", {"tables": [table], **columns.settings()})
    return _on_quotes(
        "rated", '@pipeline.rating_step(config="config/rating_step/rated.json")', ["region_factor"]
    )


def _write_rows(
    root: Path, name: str = "rows", rows: Sequence[Mapping[str, Any]] = _QUOTES
) -> None:
    (root / "data").mkdir(exist_ok=True)
    pl.DataFrame(
        [{**row, "loading": 1.0 + index / 10} for index, row in enumerate(rows)]
    ).write_parquet(root / "data" / f"{name}.parquet")


def _data_input(root: Path, columns: Columns) -> Written:
    _write_rows(root)
    _write_json(
        root,
        "config/data_input/rows.json",
        {
            "input_type": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "data/rows.parquet",
            **columns.settings(),
        },
    )
    return Written(
        [
            _Def(
                "rows",
                '@pipeline.data_input(config="config/data_input/rows.json")\ndef rows(): ...',
            )
        ],
        [*_QUOTE_COLUMNS, "loading"],
        request=pl.read_parquet(root / "data" / "rows.parquet").to_dicts(),
    )


def _regions(root: Path) -> _Def:
    (root / "data").mkdir(exist_ok=True)
    pl.DataFrame({"region": ["N", "S"], "region_name": ["North", "South"]}).write_parquet(
        root / "data" / "regions.parquet"
    )
    _write_json(
        root,
        "config/data_input/regions.json",
        {"input_type": "file", "format": "parquet", "mode": "scan", "path": "data/regions.parquet"},
    )
    return _Def(
        "regions",
        '@pipeline.data_input(config="config/data_input/regions.json")\ndef regions(): ...',
    )


def _edge_join(root: Path, columns: Columns) -> Written:
    return Written(
        [
            _QUOTE_INPUT,
            _regions(root),
            _Def(
                "joined",
                '@pipeline.edge_join(how="left", left_on=["region"], right_on=["region"], '
                f'validate="m:1", maintain_order="left"{columns.keywords()})\n'
                "def joined(quotes, regions): ...",
                (_from_quotes("base"), ("regions", None, "join")),
            ),
        ],
        [*_QUOTE_COLUMNS, "region_name"],
    )


def _data_output(root: Path, columns: Columns) -> Written:
    _write_json(
        root,
        "config/data_output/written.json",
        {
            "output_type": "file",
            "format": "parquet",
            "path": "out/written.parquet",
            **columns.settings(),
        },
    )
    return _on_quotes(
        "written", '@pipeline.data_output(config="config/data_output/written.json")', []
    )


def _external_file(root: Path, columns: Columns) -> Written:
    _write(root, "data/loading.json", '{"loading": 1.25}\n')
    _write_json(
        root,
        "config/external_file/loaded.json",
        {"path": "data/loading.json", "file_type": "json", **columns.settings()},
    )
    return Written(
        [
            _QUOTE_INPUT,
            _Def(
                "loaded",
                '@pipeline.external_file(config="config/external_file/loaded.json")\n'
                "def loaded(quotes: pl.LazyFrame, *, obj) -> pl.LazyFrame:\n"
                "    df = quotes\n"
                '    df = df.with_columns(loading=pl.lit(obj["loading"]))\n'
                "    return df",
                (_from_quotes(),),
            ),
        ],
        [*_QUOTE_COLUMNS, "loading"],
    )


def _model_file(root: Path, *, offset: str | None = None) -> str:
    """A small CatBoost model of the quotes' age, with its feature contract; its path."""
    from haute.modelling._algorithms import CatBoostAlgorithm
    from haute.modelling._feature_contract import build_contract, save_contract
    from haute.modelling._training_job import model_contract_filename

    ages = [float(age) for age in range(20, 60)]
    frame = pl.DataFrame(
        {"age": ages, "exposure": [1.0] * len(ages), "y": [age / 20 for age in ages]}
    )
    algorithm = CatBoostAlgorithm()
    fit = algorithm.fit(
        frame,
        ["age"],
        [],
        "y",
        None,
        {"iterations": 5, "depth": 2, "verbose": 0, "loss_function": "Poisson"},
        "regression",
        offset=offset,
    )
    path = root / "models" / "model.cbm"
    path.parent.mkdir(parents=True, exist_ok=True)
    algorithm.save(fit.model, path)
    save_contract(
        build_contract(
            features=["age"],
            feature_types={"age": "Int64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
            offset_column=offset,
            offset_link=None if offset is None else "log",
            offset_type=None if offset is None else "Float64",
        ),
        path.with_name(model_contract_filename(path.stem)),
    )
    return "models/model.cbm"


def _model_score(root: Path, columns: Columns) -> Written:
    _write_json(
        root,
        "config/model_score/scored.json",
        {
            "source_type": "file",
            "model_path": _model_file(root),
            "task": "regression",
            "output_column": "prediction",
            **columns.settings(),
        },
    )
    return _on_quotes(
        "scored", '@pipeline.model_score(config="config/model_score/scored.json")', ["prediction"]
    )


def _constant(root: Path, columns: Columns) -> Written:
    _write_json(
        root,
        "config/constant/rates.json",
        {"values": {"rate": 1.5, "fee": 10.0}, **columns.settings()},
    )
    return Written(
        [
            _QUOTE_INPUT,
            _Def(
                "rates", '@pipeline.constant(config="config/constant/rates.json")\ndef rates(): ...'
            ),
            _Def(
                "priced",
                '@pipeline.edge_join(how="cross", maintain_order="left")\n'
                "def priced(quotes, rates): ...",
                (_from_quotes("base"), ("rates", None, "join")),
            ),
        ],
        ["rate", "fee"],
        response_reads=("priced", lambda shaped: [*_QUOTE_COLUMNS, *shaped]),
    )


#: A Data Input whose name is spelled with a letter outside ASCII, as a parameter names it.
_NON_ASCII = "données"


def _source_switch(root: Path, columns: Columns) -> Written:
    from haute._graph_utils import _sanitize_func_name

    batch = [{**quote, "quote_id": f"b{index}"} for index, quote in enumerate(_QUOTES)]
    _write_rows(root, "batch", batch)
    _write_json(
        root,
        f"config/data_input/{_NON_ASCII}.json",
        {"input_type": "file", "format": "parquet", "mode": "scan", "path": "data/batch.parquet"},
    )
    # The quotes carry no loading; the batch's is selected away.
    _write_json(
        root,
        "config/source_switch/choose.json",
        {
            "input_scenario_map": {"quotes": "live", _sanitize_func_name(_NON_ASCII): "batch"},
            **columns.settings(),
        },
    )
    return Written(
        [
            _QUOTE_INPUT,
            _Def(
                _NON_ASCII,
                f'@pipeline.data_input(config="config/data_input/{_NON_ASCII}.json")\n'
                f"def {_NON_ASCII}(): ...",
            ),
            _Def(
                "choose",
                '@pipeline.live_switch(config="config/source_switch/choose.json")\n'
                f"def choose(quotes, {_NON_ASCII}): ...",
                (_from_quotes(), (_NON_ASCII, None, None)),
            ),
        ],
        list(_QUOTE_COLUMNS),
    )


def _scenario_expander(root: Path, columns: Columns) -> Written:
    _write_json(
        root,
        "config/scenario_expander/expanded.json",
        {
            "quote_id": "quote_id",
            "column_name": "scenario_value",
            "min_value": 0.8,
            "max_value": 1.2,
            "step_count": 3,
            "step_column": "scenario_index",
            **columns.settings(),
        },
    )
    return _on_quotes(
        "expanded",
        '@pipeline.scenario_expander(config="config/scenario_expander/expanded.json")',
        ["scenario_value", "scenario_index"],
    )


def _optimiser_apply(root: Path, columns: Columns) -> Written:
    _write_json(
        root,
        "data/ratebook.json",
        {
            "version": "parity-v1",
            "created_at": "2026-11-18T00:00:00Z",
            "mode": "ratebook",
            "lambdas": {},
            "objective": "premium",
            "constraints": {},
            "factor_tables": {
                "region": [
                    {"__factor_group__": "N", "optimal_scenario_value": 1.1},
                    {"__factor_group__": "S", "optimal_scenario_value": 0.95},
                ]
            },
            "combined_factor_bounds": {"min": 0.9, "max": 1.1},
            "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        },
    )
    _write_json(
        root,
        "config/optimiser_apply/applied.json",
        {
            "source_type": "file",
            "artifact_path": "data/ratebook.json",
            "version_column": "ratebook_version",
            "optimised_value_column": "selected_factor",
            "optimiser_mode": "ratebook",
            "ratebook_input": "quotes",
            **columns.settings(),
        },
    )
    return _on_quotes(
        "applied",
        '@pipeline.optimiser_apply(config="config/optimiser_apply/applied.json")',
        ["selected_factor", "ratebook_version"],
    )


def _optimised(root: Path, columns: Columns) -> Written:
    """Scenarios expanded from the quotes, with an online Optimisation beside the response
    reading them: in a run it passes on the frame it optimises."""
    written = _scenario_expander(root, columns)
    _write_json(
        root,
        "config/optimiser/optimise.json",
        {
            "mode": "online",
            "objective": "age",
            "constraints": {"exposure": {"min": 0.1}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "max_iter": 5,
            "tolerance": 0.001,
        },
    )
    optimise = _Def(
        "optimise",
        '@pipeline.optimiser(config="config/optimiser/optimise.json")\ndef optimise(expanded): ...',
        (("expanded", None, None),),
    )
    return Written([*written.defs, optimise], written.columns)


def _side_branches(root: Path, columns: Columns) -> Written:
    """A Transform on the quotes, with an Explore, a Model Training and an Optimiser
    beside it reading the same rows: none feeds the response, and every way leaves them
    out of its answer."""
    _write_json(root, "config/explore/looked.json", {})
    _write_json(
        root,
        "config/modelling/trained.json",
        {
            "name": "parity",
            "target": "age",
            "feature_columns": ["exposure"],
            "algorithm": "catboost",
            "task": "regression",
            "loss_function": "RMSE",
            "params": {"iterations": 2, "depth": 2, "verbose": False},
            "metrics": ["rmse"],
        },
    )
    written = _transform(root, columns)
    return Written(
        [
            *written.defs,
            _Def(
                "looked",
                '@pipeline.explore(config="config/explore/looked.json")\ndef looked(quotes): ...',
                (_from_quotes(),),
            ),
            _Def(
                "trained",
                '@pipeline.modelling(config="config/modelling/trained.json")\n'
                "def trained(quotes): ...",
                (_from_quotes(),),
            ),
        ],
        written.columns,
    )


#: The generated pipeline of each node type with a Columns tab: the node under test, what
#: writes it, the column its selection chooses and the column chosen beside it.
_COLUMNS_TAB_CASES: Mapping[NodeType, tuple[str, Callable[[Path, Columns], Written], str, str]] = {
    NodeType.POLARS: ("calc", _transform, "doubled", "quote_id"),
    NodeType.BANDING: ("banded", _banding, "age_band", "quote_id"),
    NodeType.RATING_STEP: ("rated", _rating_step, "region_factor", "quote_id"),
    NodeType.DATA_INPUT: ("rows", _data_input, "loading", "quote_id"),
    NodeType.EDGE_JOIN: ("joined", _edge_join, "region_name", "quote_id"),
    NodeType.DATA_OUTPUT: ("written", _data_output, "exposure", "quote_id"),
    NodeType.EXTERNAL_FILE: ("loaded", _external_file, "loading", "quote_id"),
    NodeType.MODEL_SCORE: ("scored", _model_score, "prediction", "quote_id"),
    NodeType.CONSTANT: ("rates", _constant, "fee", "rate"),
    NodeType.LIVE_SWITCH: ("choose", _source_switch, "exposure", "quote_id"),
    NodeType.SCENARIO_EXPANDER: ("expanded", _scenario_expander, "scenario_value", "quote_id"),
    NodeType.OPTIMISER_APPLY: ("applied", _optimiser_apply, "selected_factor", "quote_id"),
}

#: Another source a node type's generated pipeline is read under.
_OTHER_SOURCE: Mapping[NodeType, str] = {NodeType.LIVE_SWITCH: "batch"}


def _generated_cases() -> list[Any]:
    cases = [
        Generated(node_type, node, write, columns, _OTHER_SOURCE.get(node_type))
        for node_type, (node, write, chosen, other) in _COLUMNS_TAB_CASES.items()
        for columns in _columns_settings(chosen, other)
    ]
    cases.append(Generated(NodeType.EXPLORE, "calc", _side_branches, also=("looked", "trained")))
    cases.append(Generated(NodeType.OPTIMISER, "expanded", _optimised, also=("optimise",)))
    return [pytest.param(case, id=case.name) for case in cases]


@pytest.mark.parametrize("case", _generated_cases())
def test_a_generated_pipeline_gives_one_answer_every_way(
    tmp_path: Path,
    case: Generated,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_every_target(_generated_project(tmp_path, case), capsys, monkeypatch)


# ---------------------------------------------------------------------------
# The differences the specification allows
# ---------------------------------------------------------------------------


def _spec_text() -> str:
    return " ".join(EXECUTION_SPEC.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("name", list(MEANT))
def test_a_meant_difference_is_the_specification_s(name: str) -> None:
    assert " ".join(MEANT[name].sentence.split()) in _spec_text(), (
        f"The specification does not allow the difference {name!r}: no sentence "
        f"{MEANT[name].sentence!r} in {EXECUTION_SPEC.name}."
    )


def test_a_preview_shows_the_first_rows_up_to_its_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "row limit" in MEANT
    project = _generated_project(tmp_path, Generated(NodeType.POLARS, "calc", _transform))
    target = Target("quote_response")
    answer = _answers(project, target, capsys, monkeypatch)[Way.HAUTE_RUN]

    limited = _preview(project, target, row_limit=1)

    assert len(answer.rows) == 2
    assert limited.rows == answer.rows[:1]
    assert limited.columns == answer.columns


def test_a_quote_answered_with_several_rows_is_answered_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    meant = MEANT["one row per quote"]
    project = _generated_project(
        tmp_path, Generated(NodeType.SCENARIO_EXPANDER, "expanded", _scenario_expander)
    )

    alone = _service(project, monkeypatch)
    batch = _service(project, monkeypatch, batched=True)

    assert len(alone.rows) == len(_QUOTES) * 3, alone.show()
    assert batch.refusal is not None and meant.words in batch.refusal, batch.show()


def test_a_workbench_project_is_released_as_its_app(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _workbench_project(tmp_path)
    (target,) = project.targets
    answers = _answers(project, target, capsys, monkeypatch, without=frozenset({Way.SERVICE}))
    meant = MEANT["workbench"]
    refused = answers.pop(Way.DRY_RUN)
    assert refused.refusal is not None and meant.words in refused.refusal, refused.show()
    # A trace explains the cell from the frame filling the table: its values, and others.
    traced = json.loads(answers.pop(Way.TRACE).rows[0])
    assert "workbench trace" in MEANT
    answers.pop(Way.PYTHON)
    _assert_one_answer(answers)
    (table,) = (json.loads(row) for row in answers[Way.PREVIEW].rows)
    assert {key: traced[key] for key in ("premium", "state")} == {"premium": 100.0, "state": "NY"}
    assert {key: table[key] for key in ("premium", "state")} == {"premium": 100.0, "state": "NY"}


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="WB-76: the walker prices a workbench's sample under no quote identity, where "
    "pipeline.run() gives it the stand-in",
)
def test_a_workbench_sample_is_priced_under_one_identity_every_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _workbench_project(tmp_path)
    (target,) = project.targets
    preview = _preview(project, target)
    # Run as Python, the pipeline answers with its response document, a table a column.
    document = _python(project, target)

    tables = [
        json.dumps(json.loads(row)["pricing"], separators=(",", ":")) for row in document.rows
    ]
    answers = {Way.PREVIEW: preview, Way.PYTHON: Answer(rows=tuple(tables))}
    identity = ("quote_reference", "quote_option")

    def without_identity(answer: Answer) -> list[dict[str, Any]]:
        rows = [json.loads(row) for row in answer.rows]
        return [{key: value for key, value in row.items() if key not in identity} for row in rows]

    def stands_in(answer: Answer) -> bool:
        # The file run as Python gives the stand-in; the walker leaves the identity out (null)
        # and agrees with it on every other value.
        rows = [json.loads(row) for row in answer.rows]
        walked = [json.loads(row) for row in preview.rows]
        return (
            answer.refusal is None
            and preview.refusal is None
            and all(
                (row.get("quote_reference"), row.get("quote_option")) == ("PREVIEW", 1)
                for row in rows
            )
            and not any(key in row for row in walked for key in identity)
            and without_identity(answer) == without_identity(preview)
        )

    _owned("WB-76", answers, {Way.PYTHON: stands_in})


def _workbench_project(tmp_path: Path) -> Project:
    """A workbench project: a Workbench Input's quote priced by a Transform into a
    Workbench Output, its form beside the pipeline, as the editor saves them."""
    from haute._types import GraphEdge, PipelineGraph
    from haute.codegen import graph_to_code_multi
    from haute.graph_utils import GraphNode, NodeData
    from haute.routes._save_pipeline import SavePipelineService
    from tests._workbench_helpers import form_table, without_form_tables, workbench_project

    def node(nid: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
        return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))

    graph = PipelineGraph(
        nodes=[
            node("workbench_input", NodeType.WORKBENCH_INPUT, {}),
            node(
                "priced",
                NodeType.POLARS,
                {"code": "df = policy_input.with_columns(premium=pl.col('limit') / 10)"},
            ),
            node("workbench_output", NodeType.WORKBENCH_OUTPUT, {}),
        ],
        edges=[
            GraphEdge(
                id="e1", source="workbench_input", target="priced", sourceHandle="policy_input"
            ),
            GraphEdge(
                id="e2", source="priced", target="workbench_output", targetHandle="pricing_output"
            ),
        ],
    )
    root = tmp_path.parent / _short_name(tmp_path, "workbench")
    _fresh(root)
    saved = without_form_tables(graph)
    files = {
        **graph_to_code_multi(saved, pipeline_name="main"),
        **SavePipelineService._collect_node_configs_recursive(saved),
    }
    for relative, content in files.items():
        _write(root, relative, content)
    workbench_project(
        root,
        [
            form_table("policy", ("state", "str"), ("limit", "int")),
            form_table("pricing", outputs=[("premium", "float"), ("state", "str")]),
        ],
        {"policy": [{"state": "NY", "limit": "1,000"}]},
    )
    _finish_project(root)
    return Project(
        root=root,
        pipeline=root / "main.py",
        targets=[Target("workbench_output")],
        request=[{"policy": {"state": "NY", "limit": 1000}}],
        response="workbench_output",
    )


def _cross_join(root: Path, columns: Columns) -> Written:
    """Two many-row inputs crossed in a Transform's code."""
    return Written(
        [
            _QUOTE_INPUT,
            _regions(root),
            _Def(
                "crossed",
                "@pipeline.polars\n"
                "def crossed(quotes: pl.LazyFrame, regions: pl.LazyFrame) -> pl.LazyFrame:\n"
                '    return quotes.join(regions, how="cross", suffix="_r", '
                'maintain_order="left_right")',
                (_from_quotes(), ("regions", None, None)),
            ),
        ],
        [*_QUOTE_COLUMNS, "region_r", "region_name"],
    )


def test_an_unmeasured_boundary_is_refused_without_a_cap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._native_memory_limit import native_memory_backend_scope

    meant = MEANT["unmeasured boundary"]
    project = _generated_project(tmp_path, Generated(NodeType.POLARS, "crossed", _cross_join))
    target = Target("quote_response")
    answers = _answers(project, target, capsys, monkeypatch)
    # The editor previews in a worker under a native memory cap.
    with native_memory_backend_scope("rlimit"):
        capped = _preview(project, target)

    ran = answers.pop(Way.PYTHON)
    for way, refused in answers.items():
        assert refused.refusal is not None and meant.words in refused.refusal, (
            f"{way}: {refused.show()}"
        )
    assert "not admitted" in MEANT
    assert capped.refusal is None, capped.show()
    assert capped.rows == ran.rows
    assert len(ran.rows) == len(_QUOTES) * 2


# ---------------------------------------------------------------------------
# The request a deployment reads
# ---------------------------------------------------------------------------


def _deploy_answers(
    project: Project, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> dict[Way, Answer]:
    return {
        Way.DRY_RUN: _dry_run(project, capsys),
        Way.SERVICE: _service(project, monkeypatch),
    }


def _flat_quotes(root: Path) -> None:
    """A flat Quote Input: its sample a Parquet file, which a request is framed by."""
    pl.DataFrame(_QUOTES).write_parquet(root / "data" / "quotes.parquet")
    config = _quote_input_config()
    config["path"] = "data/quotes.parquet"
    _write_json(root, "config/quote_input.json", config)


def _flat_transform(root: Path, columns: Columns) -> Written:
    _flat_quotes(root)
    return _transform(root, columns)


def test_the_dry_run_refuses_a_value_its_column_does_not_hold_as_the_service_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _generated_project(tmp_path, Generated(NodeType.POLARS, "calc", _flat_transform))
    project.request = [{**_QUOTES[0], "age": 30.9}]

    _assert_one_answer(
        _deploy_answers(project, capsys, monkeypatch),
        refusal="Quote 0's 'age' is 30.9, which its Int64 column does not hold.",
    )


def _exposure_model(root: Path, columns: Columns) -> Written:
    _flat_quotes(root)
    _write_json(
        root,
        "config/model_score/scored.json",
        {
            "source_type": "file",
            "model_path": _model_file(root, offset="exposure"),
            "task": "regression",
            "output_column": "prediction",
        },
    )
    return _on_quotes(
        "scored", '@pipeline.model_score(config="config/model_score/scored.json")', ["prediction"]
    )


def test_the_dry_run_prices_a_quote_that_leaves_out_its_exposure_as_the_service_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _generated_project(
        tmp_path, Generated(NodeType.MODEL_SCORE, "scored", _exposure_model)
    )
    resolved = _resolved(project)
    try:
        assert resolved.omittable_inputs == ["exposure"]
    finally:
        resolved.close()
    project.request = [{key: value for key, value in _QUOTES[0].items() if key != "exposure"}]

    answers = _deploy_answers(project, capsys, monkeypatch)

    _assert_one_answer(answers)
    # Priced for a full term: as the quote holding an exposure of 1.
    project.request = [{**project.request[0], "exposure": 1.0}]
    full_term = _service(project, monkeypatch)
    (left_out,), (one,) = answers[Way.SERVICE].rows, full_term.rows
    assert json.loads(left_out)["prediction"] == json.loads(one)["prediction"]


def _reads_dropped(root: Path, columns: Columns) -> Written:
    written = _banding(root, Columns("chosen", ("age_band", "quote_id")))
    # The response reads a column the Banding's selection drops.
    return Written(written.defs, written.columns, ("banded", lambda shaped: [*shaped, "age"]))


def test_a_stale_selection_inside_a_submodel_is_refused_in_one_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _example_project(tmp_path, "reusable_submodel")
    root = tmp_path.parent / _short_name(tmp_path, "reusable_submodel")
    module = root / "modules" / "reusable_enrichment.py"
    source = module.read_text(encoding="utf-8")
    assert source.count("@submodel.polars\n") == 1
    module.write_text(
        source.replace("@submodel.polars\n", '@submodel.polars(selected_columns=["gone"])\n'),
        encoding="utf-8",
    )
    (target,) = project.targets

    answers = _answers(project, target, capsys, monkeypatch)

    _assert_one_answer(answers, refusal=_stale_words("enriched in enrichment"))


def test_a_column_a_selection_drops_is_refused_by_the_node_reading_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _generated_project(tmp_path, Generated(NodeType.BANDING, "banded", _reads_dropped))
    answers = _answers(project, Target("quote_response"), capsys, monkeypatch)
    meant = MEANT["no contracts"]

    ran = answers.pop(Way.PYTHON)
    assert ran.refusal is not None and meant.words in ran.refusal, ran.show()
    _assert_one_answer(
        answers,
        refusal="'quote_response' needs the columns 'age', 'exposure' and 'region', which are "
        "not in its input.",
    )


# ---------------------------------------------------------------------------
# Differences other packages own
# ---------------------------------------------------------------------------


def _constant_cross_join(root: Path, columns: Columns) -> Written:
    """The Constant page's example: a Transform crossing the quotes with a Constant."""
    _write_json(root, "config/constant/rates.json", {"values": {"rate": 1.5}})
    return Written(
        [
            _QUOTE_INPUT,
            _Def(
                "rates", '@pipeline.constant(config="config/constant/rates.json")\ndef rates(): ...'
            ),
            _Def(
                "priced",
                "@pipeline.polars\n"
                "def priced(quotes: pl.LazyFrame, rates: pl.LazyFrame) -> pl.LazyFrame:\n"
                '    return quotes.join(rates, how="cross", maintain_order="left_right")',
                (_from_quotes(), ("rates", None, None)),
            ),
        ],
        [*_QUOTE_COLUMNS, "rate"],
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="EXE-05: a cross join with a one-row Constant is refused without a cap",
)
def test_a_transform_crossing_a_constant_answers_every_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _generated_project(
        tmp_path, Generated(NodeType.POLARS, "priced", _constant_cross_join)
    )

    answers = _answers(project, Target("quote_response"), capsys, monkeypatch)
    admitted = {
        way: lambda answer: _refuses(answer, "cross_join_unmeasured")
        for way in answers
        if way is not Way.PYTHON
    }
    _owned("EXE-05", answers, admitted)


def _csv_quotes(root: Path, columns: Columns) -> Written:
    """A Quote Input whose sample is a CSV file, every column's type declared in its table."""
    pl.DataFrame(_QUOTES).write_csv(root / "data" / "quotes.csv")
    config = _quote_input_config()
    config["path"] = "data/quotes.csv"
    _write_json(root, "config/quote_input.json", config)
    return _transform(root, columns)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="IOL-03: a bounded run reads a Quote Input's CSV without the types its table declares",
)
def test_a_quote_input_on_a_csv_answers_every_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _generated_project(tmp_path, Generated(NodeType.API_INPUT, "calc", _csv_quotes))
    target = Target("calc", leaf="leaf.py")

    answers = {
        Way.PREVIEW: _preview(project, target),
        Way.HAUTE_RUN: _haute_run(project, target, capsys),
        Way.PYTHON: _python(project, target),
    }
    _owned(
        "IOL-03",
        answers,
        {Way.HAUTE_RUN: lambda answer: _refuses(answer, "CSV sources require declared dtypes")},
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DPL-05: plain Python and the dry run read a Data Input only from a snapshot a "
    "preview or haute run built",
)
def test_a_clean_checkout_runs_every_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.assistant._assets import materialize_example_bundle

    root = tmp_path.parent / _short_name(tmp_path, "clean")

    _fresh(root)
    root.rmdir()
    materialize_example_bundle("minimal_batch", root)
    _finish_project(root)
    project = Project(
        root=root,
        pipeline=root / "pipeline.py",
        targets=[Target("quote_response")],
        request=[{"fixture_value": 10}, {"fixture_value": 20}],
    )
    # Nothing has built the input's snapshot: run as a clean checkout runs.
    ran = _python(project, project.targets[0])
    dry = _dry_run(project, capsys)
    preview = _preview(project, project.targets[0])

    answers = {Way.PREVIEW: preview, Way.PYTHON: ran, Way.DRY_RUN: dry}

    def unbuilt(answer: Answer) -> bool:
        return _refuses(answer, "input_snapshot_missing")

    _owned("DPL-05", answers, {Way.PYTHON: unbuilt, Way.DRY_RUN: unbuilt})


#: The node types a test above runs, by the case that holds it.
_NODE_TYPE_CASES: Mapping[NodeType, tuple[str, ...]] = node_type_table(
    "run parity cases",
    {
        NodeType.API_INPUT: ("polars: every column", "minimal_live_quote"),
        NodeType.WORKBENCH_INPUT: ("workbench",),
        NodeType.WORKBENCH_OUTPUT: ("workbench",),
        NodeType.DATA_INPUT: ("dataInput: every column", "reference"),
        NodeType.DATA_OUTPUT: ("dataOutput: every column",),
        NodeType.POLARS: ("polars: every column",),
        NodeType.EDGE_JOIN: ("edgeJoin: every column", "reference_join"),
        NodeType.MODEL_SCORE: ("modelScore: every column",),
        NodeType.BANDING: ("banding: every column", "discrete_banding"),
        NodeType.RATING_STEP: ("ratingStep: every column", "rating_step"),
        NodeType.OUTPUT: ("polars: every column",),
        NodeType.EXPLORE: ("explore: every column",),
        NodeType.EXTERNAL_FILE: ("externalFile: every column",),
        NodeType.LIVE_SWITCH: ("liveSwitch: every column", "live_batch_parity"),
        NodeType.MODELLING: ("explore: every column",),
        NodeType.OPTIMISER: ("optimiser: every column",),
        NodeType.SCENARIO_EXPANDER: ("scenarioExpander: every column",),
        NodeType.OPTIMISER_APPLY: ("optimiserApply: every column", "ratebook_optimisation_apply"),
        NodeType.CONSTANT: ("constant: every column",),
        NodeType.SUBMODEL: ("reusable_submodel",),
        NodeType.SUBMODEL_PORT: ("reusable_submodel",),
    },
)


def _node_types(pipeline: Path, targets: Sequence[str]) -> set[NodeType]:
    """The node types the ways run reading *targets* of *pipeline*: each target and the
    nodes it reads, a submodel occurrence's definition included."""
    from haute.routes._helpers import parse_pipeline_to_graph

    graph = parse_pipeline_to_graph(pipeline)
    parents: dict[str, set[str]] = {}
    for edge in graph.edges:
        parents.setdefault(edge.target, set()).add(edge.source)
    read: set[str] = set()
    pending = list(targets)
    while pending:
        node = pending.pop()
        if node not in read:
            read.add(node)
            pending.extend(parents.get(node, ()))
    nodes = [graph.node_map[node] for node in read]
    types = {NodeType(node.data.nodeType) for node in nodes}
    if NodeType.SUBMODEL in types:
        # A Port has no decorator: no file holds one, and an occurrence runs its ports.
        types.add(NodeType.SUBMODEL_PORT)
        types.update(
            NodeType(node.data.nodeType)
            for definition in (graph.submodels or {}).values()
            for node in definition.graph.nodes
        )
    return types


def _case_node_types(tmp_path: Path, name: str) -> set[NodeType]:
    """The node types the case *name* runs at its targets, read from its pipeline as
    written."""
    from haute.assistant._assets import materialize_example_bundle

    (tmp_path / "e").mkdir(parents=True)

    generated = {param.id: param.values[0] for param in _generated_cases()}
    if name in generated:
        project = _generated_project(tmp_path / "g", generated[name])
    elif name == "workbench":
        project = _workbench_project(tmp_path / "w")
    elif name == "reference":
        project = Project(
            REPO, REPO / "examples" / "reference" / "main.py", [Target("quote_response")]
        )
    else:
        root = tmp_path / "e" / name
        materialize_example_bundle(name, root)
        use_project(root)
        project = Project(root, root / "pipeline.py", [Target("quote_response")])
    return _node_types(project.pipeline, [target.node for target in project.targets])


def test_every_node_type_has_a_case(tmp_path: Path) -> None:
    names = {param.id for param in [*_examples(), *_generated_cases()]} | {"workbench"}
    unknown = {
        node_type.value: [case for case in cases if case not in names]
        for node_type, cases in _NODE_TYPE_CASES.items()
        if not cases or any(case not in names for case in cases)
    }
    assert not unknown, f"node types without a case, or naming none: {unknown}"
    named = {case for cases in _NODE_TYPE_CASES.values() for case in cases}
    held = {
        case: _case_node_types(tmp_path / f"c{index}", case)
        for index, case in enumerate(sorted(named))
    }
    absent = {
        node_type.value: [case for case in cases if node_type not in held[case]]
        for node_type, cases in _NODE_TYPE_CASES.items()
        if any(node_type not in held[case] for case in cases)
    }
    assert not absent, f"cases naming a node type no target of theirs reads: {absent}"


def test_the_ways_are_the_specification_s() -> None:
    sections = EXECUTION_SPEC.read_text(encoding="utf-8").split("### Ways of running a pipeline")
    assert len(sections) == 2, "The specification has no section 'Ways of running a pipeline'."
    section = re.split(r"\n#{2,3} ", sections[1], maxsplit=1)[0]
    named = re.findall(r"^\| \*\*(.+?)\*\* \|", section, re.MULTILINE)
    assert named == [way.value for way in Way]
