"""Ratchets for the one place a failure is put into words (``haute._failure_answers``).

A route, the underwriter app, a job's outcome and a command answer a failure through the
module: nothing else builds an HTTP error, and an edge that catches every exception hands
it to the module or raises it on. Each list below names the only places allowed otherwise,
with why. A new place fails here; one that is removed must leave its list too.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests._source_files import REPO_ROOT, source_files

SOURCE_ROOT = REPO_ROOT / "src" / "haute"

#: The HTTP adapter: the one module that turns an answer into a response.
HTTP_ADAPTERS = frozenset(
    {
        "src/haute/routes/_error_handlers.py",
        "src/haute/routes/_validation_errors.py",
    }
)

#: Responses with an error status built outside the HTTP adapter, by file and scope.
ALLOWED_ERROR_RESPONSES: dict[tuple[str, str], str] = {
    ("src/haute/_local_security.py", "LocalSessionMiddleware.dispatch"): (
        "refuses a request without the session before it is let in, outside the request's "
        "failure middleware by design (specs/server-api, request middleware chain)"
    ),
    ("src/haute/routes/json_cache.py", "_api_input_schema_error_response"): (
        "the JSON cache keeps its type-discriminated 422 body, {detail, type} "
        "(specs/server-api, Error handling)"
    ),
    ("src/haute/_workbench_app.py", "create_app.price"): (
        "the price route refuses a body as the deployed /quote route refuses it, its payload "
        "whole at the top level (specs/workbench)"
    ),
    ("src/haute/routes/pipeline.py", "_pipeline_recovery_error_response"): (
        "a recovery action's structured refusal, a body the editor's recovery panel reads "
        "whole (specs/server-api/node-recovery-actions.md)"
    ),
}

#: The edges: where a failure leaves Haute for someone to read. The routers, the editor's
#: server, the underwriter app, the commands, and the modules that record a job's outcome.
EDGE_GLOBS = (
    "src/haute/routes/[a-z]*.py",
    "src/haute/server.py",
    "src/haute/_workbench_app*.py",
    "src/haute/assistant/_loop.py",
    "src/haute/cli/*.py",
    "src/haute/routes/_background_jobs.py",
    "src/haute/routes/_node_data_service.py",
    "src/haute/routes/_optimiser_service.py",
    "src/haute/routes/_optimiser_worker.py",
    "src/haute/routes/_training_lifecycle.py",
    "src/haute/routes/_training_preparation.py",
    "src/haute/routes/_training_worker.py",
)

#: The module's functions that answer any failure, an incident for one nothing names. A
#: function anywhere in Haute that calls one of them (or calls such a function) hands a
#: failure to the module too: an edge's own adapter, which words what it knows and hands on
#: everything else. A partial classifier answers only what it names, so a catch that calls
#: one and words the rest itself is the fallback this ratchet refuses.
MODULE_FUNCTIONS = frozenset(
    {
        "answer_failure",
        "answer_pipeline_failure",
        "failure_refusal",
        "unexpected_failure",
        "record_incident",
    }
)

#: The module's functions that answer only the failures they name, ``None`` for the rest.
PARTIAL_CLASSIFIERS = (
    "classify_failure",
    "pipeline_failure",
    "node_failed_error",
    "file_answer",
    "git_refusal_answer",
    "database_refusal_answer",
)


def _handing_functions() -> frozenset[str]:
    """The module's functions and every function in Haute that calls one, transitively.

    A name defined more than once (``run``, ``record``) is no one function, so it hands
    nothing on by its name alone.
    """
    calls: dict[str, set[str]] = {}
    defined: Counter[str] = Counter()
    for path in source_files(SOURCE_ROOT):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defined[node.name] += 1
                calls.setdefault(node.name, set()).update(_called_names(node))
    calls = {name: called for name, called in calls.items() if defined[name] == 1}
    handing = set(MODULE_FUNCTIONS)
    while True:
        more = {name for name, called in calls.items() if called & handing} - handing
        if not more:
            return frozenset(handing)
        handing |= more


#: Catches of every exception at an edge that neither hand it to the module nor raise it
#: on, by file and enclosing scope, with how many and why.
ALLOWED_BROAD_CATCHES: dict[tuple[str, str], tuple[int, str]] = {
    # Raised on, after a step of their own.
    ("src/haute/cli/_impact.py", "_impact_databricks"): (
        1,
        "only the SDK's not-found is a first deployment; anything else is raised on",
    ),
    ("src/haute/routes/pipeline.py", "get_first_pipeline._find_first"): (
        1,
        "the other pipelines are tried first; the first failure is raised once they are",
    ),
    ("src/haute/routes/pipeline.py", "get_pipeline._find"): (
        2,
        "the other pipelines are tried first; the first failure is raised once they are",
    ),
    ("src/haute/routes/pipeline.py", "_held_output_write"): (
        1,
        "a staging cleanup that fails is noted on the Write's own failure, which is raised",
    ),
    ("src/haute/routes/_training_lifecycle.py", "TrainService._parent_worker_cleanup.cleanup"): (
        1,
        "each cleanup step runs; their failures are raised together once all have",
    ),
    ("src/haute/routes/_background_jobs.py", "IsolatedJobSupervisor._finish_outcome"): (
        1,
        "a cleanup that fails is added to the job's outcome, which is already worded",
    ),
    # A command's own words for a check that goes on.
    ("src/haute/cli/_helpers.py", "_open_browser"): (
        1,
        "a browser that cannot open is said with the address to open by hand; the server goes on",
    ),
    ("src/haute/cli/_smoke.py", "_smoke_http"): (
        1,
        "a failed health check is the smoke test's own result, said and counted",
    ),
    ("src/haute/cli/_smoke.py", "_smoke_databricks"): (
        1,
        "the endpoint check says Databricks' refusal in its gatekeeper's words",
    ),
    # Work that goes on without what failed, said beside it.
    ("src/haute/routes/pipeline.py", "list_pipelines._parse_one"): (
        1,
        "one unreadable pipeline is listed source_only; opening it answers its failure",
    ),
    ("src/haute/routes/pipeline.py", "_publish_committed"): (
        1,
        "the save has landed; other open pipelines' documents not republished are reloaded",
    ),
    ("src/haute/routes/_training_lifecycle.py", "TrainService._check_gpu_vram_before_launch"): (
        1,
        "training goes ahead, with the warning that the GPU memory check could not run",
    ),
    ("src/haute/routes/mlflow.py", "_model_version_run_params"): (
        1,
        "a version's run parameters are optional to its listing",
    ),
    ("src/haute/routes/mlflow.py", "_run_summaries"): (
        1,
        "a run whose artifacts cannot be listed is counted and the listing goes on",
    ),
    ("src/haute/routes/mlflow.py", "_probe_outcome"): (
        1,
        "the connection test's result is its category; an MLflow error's text can hold credentials",
    ),
    ("src/haute/routes/mlflow.py", "_run_probe_bounded._worker"): (
        1,
        "the probe thread hands what it raised to the caller, which answers it",
    ),
    # The assistant's mechanics, never the turn's answer to the analyst.
    ("src/haute/assistant/_loop.py", "_aclose_quietly"): (
        1,
        "a provider stream's close failing is logged; it must not mask the turn's own outcome",
    ),
    ("src/haute/assistant/_loop.py", "_RunningTool.settle"): (
        1,
        "an interrupted tool's failure is logged and the model is told only that it was "
        "interrupted; the turn's answer is the loop's",
    ),
    # Background tasks nobody waits on.
    ("src/haute/server.py", "_settle_saves"): (
        1,
        "a save record the server's start cannot settle is logged and settled again by the "
        "next save of its pipeline; the server still starts",
    ),
    ("src/haute/server.py", "_watcher_forever"): (
        1,
        "the live-sync watcher restarts after a crash, logged; no request waits on it",
    ),
    ("src/haute/server.py", "_file_watcher._flush_once"): (
        1,
        "a batch that fails is requeued and retried, then isolated (_flush)",
    ),
}


def _scope_names(tree: ast.Module) -> dict[ast.AST, str]:
    """Each node's enclosing ``Class.function`` scope."""
    scopes: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{scope}.{child.name}" if scope else child.name
            scopes[child] = inner
            visit(child, inner)

    visit(tree, "")
    return scopes


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _is_broad(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(
        isinstance(name, ast.Name) and name.id in {"Exception", "BaseException"} for name in names
    )


def _raises_on(handler: ast.ExceptHandler) -> bool:
    """Whether every way out of *handler* raises what it caught again (a bare ``raise``)."""
    last = handler.body[-1]
    return isinstance(last, ast.Raise) and last.exc is None


def error_responses(path: Path) -> Counter[tuple[str, str]]:
    """``HTTPException(...)`` and ``JSONResponse(status_code=...)`` with an error status
    built in *path*, by file and scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = _scope_names(tree)
    found: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "HTTPException":
            found[(_relative(path), scopes.get(node, ""))] += 1
        elif name == "JSONResponse":
            status = next((kw.value for kw in node.keywords if kw.arg == "status_code"), None)
            if status is not None and not (
                isinstance(status, ast.Constant)
                and isinstance(status.value, int)
                and status.value < 400
            ):
                found[(_relative(path), scopes.get(node, ""))] += 1
    return found


def unhanded_broad_catches(
    path: Path, handing: frozenset[str] = MODULE_FUNCTIONS
) -> Counter[tuple[str, str]]:
    """Catches of every exception in *path* that neither hand it to the module (call one of
    *handing*) nor raise it on, by file and scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = _scope_names(tree)
    found: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not _is_broad(node):
            continue
        if _raises_on(node) or _called_names(node) & handing:
            continue
        found[(_relative(path), scopes.get(node, ""))] += 1
    return found


def _edge_files() -> list[Path]:
    files: list[Path] = []
    for pattern in EDGE_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return files


@pytest.mark.meta
def test_only_the_http_adapter_builds_an_error_response() -> None:
    found: Counter[tuple[str, str]] = Counter()
    for path in source_files(SOURCE_ROOT):
        if _relative(path) not in HTTP_ADAPTERS:
            found.update(error_responses(path))
    unlisted = sorted(key for key in found if key not in ALLOWED_ERROR_RESPONSES)
    stale = sorted(key for key in ALLOWED_ERROR_RESPONSES if key not in found)
    assert not unlisted, (
        "Raise a refusal (haute.errors.RequestRefusedError and its kinds) and let "
        "haute._failure_answers answer it, or list the place with its reason:\n"
        + "\n".join(f"  {file} :: {scope}" for file, scope in unlisted)
    )
    assert not stale, f"Listed places that no longer build an error response: {stale}"


@pytest.mark.meta
def test_every_edge_hands_an_unnamed_failure_to_the_module() -> None:
    found: Counter[tuple[str, str]] = Counter()
    handing = _handing_functions()
    for path in _edge_files():
        found.update(unhanded_broad_catches(path, handing))
    wrong = {
        key: count
        for key, count in found.items()
        if ALLOWED_BROAD_CATCHES.get(key, (0, ""))[0] != count
    }
    stale = sorted(key for key in ALLOWED_BROAD_CATCHES if key not in found)
    assert not wrong, (
        "An edge catches every exception without answering it through "
        "haute._failure_answers or raising it on; hand it to the module, or list the "
        "place with its count and reason:\n"
        + "\n".join(
            f"  {file} :: {scope} ({count})" for (file, scope), count in sorted(wrong.items())
        )
    )
    assert not stale, f"Listed catches that are gone: {stale}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from fastapi import HTTPException\n"
            "def route():\n    raise HTTPException(status_code=500, detail='x')\n",
            1,
        ),
        (
            "from starlette.responses import JSONResponse\n"
            "def route():\n    return JSONResponse(status_code=503, content={})\n",
            1,
        ),
        (
            "from starlette.responses import JSONResponse\n"
            "def route():\n    return JSONResponse(status_code=200, content={})\n",
            0,
        ),
    ],
)
def test_the_scan_finds_a_new_error_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, expected: int
) -> None:
    module = tmp_path / "route.py"
    module.write_text(source, encoding="utf-8")
    monkeypatch.setattr(f"{__name__}.REPO_ROOT", tmp_path)

    assert sum(error_responses(module).values()) == expected


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        ("    except Exception:\n        return None\n", 1),
        ("    except Exception as exc:\n        logger.error('x', error=str(exc))\n", 1),
        ("    except Exception:\n        cleanup()\n        raise\n", 0),
        ("    except Exception as exc:\n        raise failure_refusal(exc) from exc\n", 0),
        # A partial classifier answers only what it names: wording the rest locally is the
        # fallback the ratchet refuses.
        *(
            pytest.param(
                "    except Exception as exc:\n"
                f"        answer = {partial}(exc)\n"
                "        if answer is not None:\n"
                "            return answer.message\n"
                "        return 'Build failed.'\n",
                1,
                id=f"{partial}-then-fallback",
            )
            for partial in PARTIAL_CLASSIFIERS
        ),
        ("    except ValueError:\n        return None\n", 0),
    ],
)
def test_the_scan_finds_a_new_unhanded_catch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handler: str, expected: int
) -> None:
    module = tmp_path / "route.py"
    module.write_text(f"def route():\n    try:\n        work()\n{handler}", encoding="utf-8")
    monkeypatch.setattr(f"{__name__}.REPO_ROOT", tmp_path)

    assert sum(unhanded_broad_catches(module).values()) == expected
