"""Opt-in phase timing for the audit server: wall-clock spans of the parent's job handoff.

Each wrapped call appends one JSON line ``{name, start, end, seconds, thread}`` (epoch
seconds) to ``phase-timing.jsonl`` in the audit output directory. Only the audit server
installs these wrappers; product code is unchanged.
"""

import functools
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# (module or class path, attribute). Functions imported by name are patched in the module
# that calls them, because that is the binding the call site resolves.
TARGETS: tuple[tuple[str, str], ...] = (
    ("haute.routes._training_lifecycle", "_validate_training_artifacts"),
    ("haute.routes._training_lifecycle", "_assert_json_finite"),
    ("haute.routes._training_lifecycle", "capture_provenance"),
    ("haute.routes._training_lifecycle:TrainService", "_launch_training_protocol"),
    ("haute.routes._training_lifecycle:TrainService", "_prepare_and_launch_training"),
    ("haute.routes._training_lifecycle:TrainService", "_execute_and_sink"),
    ("haute.routes._training_lifecycle:TrainService", "_finish_stage_job"),
    ("haute.routes._training_lifecycle:TrainService", "_release_superseded_training_artifacts"),
    ("haute.routes._training_lifecycle:TrainService", "start_preview"),
    ("haute.routes._training_stages:StageJobScope", "parent_reference"),
    ("haute.routes._training_stages:StageJobScope", "prepare"),
    ("haute.routes._training_stages:StageJobScope", "publish"),
    ("haute.routes._training_stages:StageJobScope", "finish"),
    ("haute.routes._training_stages:StageJobScope", "start_queued_preview"),
    ("haute.modelling._stage_store:StageStore", "prepare"),
    ("haute.modelling._stage_store:StageStore", "publish"),
    ("haute.modelling._stage_store:StageStore", "_write"),
    ("haute.modelling._stage_store:StageStore", "_hydrate"),
    ("haute.modelling._stage_store:StageStore", "_collect"),
    ("haute.modelling._stage_store:StageStore", "apply_path"),
    ("haute.modelling._stage_store:StageStore", "cleanup"),
    ("haute.modelling._stage_objects:StageArchive", "stage_into"),
    ("haute.modelling._stage_objects:StageObjects", "install"),
)
# Spans inside the spawned stage worker: reading its parent revision and packing its own.
WORKER_TARGETS: tuple[tuple[str, str], ...] = (
    ("haute.modelling._staged_runtime", "extract_archive"),
    ("haute.modelling._staged_runtime", "_pack"),
    ("haute.modelling._stage_paths", "_pack"),
    ("haute.modelling._staged_runtime:StageBundle", "_load_session"),
)


def record(output: Path, name: str, start: float, end: float) -> None:
    line = json.dumps(
        {
            "name": name,
            "start": start,
            "end": end,
            "seconds": end - start,
            "thread": threading.current_thread().name,
            "pid": os.getpid(),
        }
    )
    with _LOCK, (output / "phase-timing.jsonl").open("a") as handle:
        handle.write(line + "\n")


def _timed(function: Callable[..., Any], name: str, output: Path) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        try:
            return function(*args, **kwargs)
        finally:
            record(output, name, start, time.time())

    return wrapped


def install(output: Path, targets: tuple[tuple[str, str], ...] = TARGETS) -> None:
    import importlib

    for location, attribute in targets:
        module_name, _, class_name = location.partition(":")
        owner: Any = importlib.import_module(module_name)
        if class_name:
            owner = getattr(owner, class_name)
        original = getattr(owner, attribute)
        label = f"{class_name + '.' if class_name else ''}{attribute}"
        setattr(owner, attribute, _timed(original, label, output))
