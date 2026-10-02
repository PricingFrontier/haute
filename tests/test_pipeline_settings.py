"""The pipeline settings file (specs/execution-engine/low-level.md, "Pipeline settings").

``.haute/pipeline-settings.json`` holds the settings the Pipeline settings pane
shows. Every key is optional and automatic when absent; an invalid file fails
every read loudly; the pane writes the same file a person may edit by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute import _pipeline_settings
from haute._execution_admission import (
    create_admitted_execution_context,
    execution_budget_for_profile,
)
from haute._execution_context import ExecutionProfile
from haute._pipeline_settings import (
    SETTINGS_PATH,
    PipelineSettings,
    PipelineSettingsError,
    automatic_pipeline_settings,
    follow_chunk_rows,
    read_pipeline_settings,
    settings_file,
    stop_following_chunk_rows,
    update_pipeline_settings,
)
from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size

GIB = 1024**3


def _write(root: Path, text: str) -> Path:
    path = settings_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root the routes and every consumer read the settings of."""
    monkeypatch.chdir(haute_scratch)
    return haute_scratch


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_a_missing_file_is_all_automatic(tmp_path: Path) -> None:
    settings = read_pipeline_settings(tmp_path)

    assert settings == PipelineSettings()
    assert settings.caching_enabled is True
    assert settings.streaming_chunk_size == 500_000
    assert settings.pipeline_time_limit_seconds == 30 * 60
    assert settings.modelling_time_limit_seconds == 60 * 60
    assert settings.optimisation_time_limit_seconds is None
    assert settings.cache_size_bytes is None
    assert settings.preview_memory_bytes is None
    assert settings.kept_free_bytes is None


def test_a_written_file_lists_only_set_keys_in_order_and_round_trips(tmp_path: Path) -> None:
    update_pipeline_settings(
        tmp_path,
        {
            "pipeline_time_limit_minutes": 45,
            "caching": False,
            "preview_memory_gb": 8.5,
            "chunk_rows": 250_000,
            "kept_free_gb": 0,
        },
    )

    assert settings_file(tmp_path).read_text(encoding="utf-8") == (
        "{\n"
        '  "chunk_rows": 250000,\n'
        '  "caching": false,\n'
        '  "preview_memory_gb": 8.5,\n'
        '  "kept_free_gb": 0,\n'
        '  "pipeline_time_limit_minutes": 45\n'
        "}\n"
    )
    settings = read_pipeline_settings(tmp_path)
    assert settings.caching_enabled is False
    assert settings.streaming_chunk_size == 250_000
    assert settings.preview_memory_bytes == round(8.5 * GIB)
    assert settings.kept_free_bytes == 0
    assert settings.pipeline_time_limit_seconds == 45 * 60
    assert settings.modelling_time_limit_seconds == 60 * 60


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("chunk_rows", True),
        ("chunk_rows", 0),
        ("chunk_rows", 10_000_001),
        ("chunk_rows", 2.5),
        ("chunk_rows", 500_000.0),
        ("chunk_rows", "500000"),
        ("caching", "false"),
        ("caching", 0),
        ("cache_size_gb", 0),
        ("cache_size_gb", -1),
        ("cache_size_gb", True),
        ("cache_size_gb", "20"),
        ("preview_memory_gb", 0),
        ("preview_memory_gb", -0.5),
        ("preview_memory_gb", False),
        ("preview_memory_gb", 1024 * 1024 + 1),
        ("kept_free_gb", -1),
        ("kept_free_gb", True),
        ("pipeline_time_limit_minutes", 0),
        ("pipeline_time_limit_minutes", -5),
        ("pipeline_time_limit_minutes", 10_081),
        ("modelling_time_limit_minutes", True),
        ("optimisation_time_limit_minutes", "30"),
    ],
)
def test_each_key_refuses_its_invalid_values(tmp_path: Path, key: str, value: object) -> None:
    _write(tmp_path, json.dumps({key: value}))

    with pytest.raises(PipelineSettingsError) as caught:
        read_pipeline_settings(tmp_path)

    message = str(caught.value)
    assert message.startswith(SETTINGS_PATH)
    assert key in message


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ('{"chunk_row": 1000}', "'chunk_row' is not a pipeline setting"),
        ("[1, 2]", "must hold one JSON object"),
        ('"caching"', "must hold one JSON object"),
        ('{"caching": false', "is not valid JSON"),
        ("", "is not valid JSON"),
        ('{"preview_memory_gb": NaN}', "NaN is not a number"),
        ('{"caching": true, "caching": false}', "'caching' is set twice"),
    ],
)
def test_a_malformed_file_is_refused_with_a_message_naming_it(
    tmp_path: Path, text: str, names: str
) -> None:
    _write(tmp_path, text)

    with pytest.raises(PipelineSettingsError) as caught:
        read_pipeline_settings(tmp_path)

    assert str(caught.value).startswith(SETTINGS_PATH)
    assert names in str(caught.value)


def test_a_file_saved_with_a_byte_order_mark_reads(tmp_path: Path) -> None:
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\xef\xbb\xbf{"caching": false}')

    assert read_pipeline_settings(tmp_path).caching_enabled is False


def test_an_update_merges_into_the_keys_already_set(tmp_path: Path) -> None:
    update_pipeline_settings(tmp_path, {"preview_memory_gb": 6})
    update_pipeline_settings(tmp_path, {"modelling_time_limit_minutes": 90})

    settings = read_pipeline_settings(tmp_path)
    assert settings.preview_memory_gb == 6
    assert settings.modelling_time_limit_minutes == 90


def test_none_restores_automatic_and_removes_the_key(tmp_path: Path) -> None:
    update_pipeline_settings(tmp_path, {"preview_memory_gb": 6, "caching": False})
    update_pipeline_settings(tmp_path, {"preview_memory_gb": None})

    assert json.loads(settings_file(tmp_path).read_text(encoding="utf-8")) == {"caching": False}
    update_pipeline_settings(tmp_path, {"caching": None})
    assert settings_file(tmp_path).read_text(encoding="utf-8") == "{}\n"


def test_an_invalid_file_refuses_an_update_and_is_left_as_it_is(tmp_path: Path) -> None:
    path = _write(tmp_path, '{"preview_memory_gb": 0}')

    with pytest.raises(PipelineSettingsError, match="preview_memory_gb"):
        update_pipeline_settings(tmp_path, {"caching": False})

    assert path.read_text(encoding="utf-8") == '{"preview_memory_gb": 0}'


def test_an_invalid_change_writes_nothing(tmp_path: Path) -> None:
    update_pipeline_settings(tmp_path, {"caching": False})
    before = settings_file(tmp_path).read_text(encoding="utf-8")

    with pytest.raises(PipelineSettingsError, match="pipeline_time_limit_minutes"):
        update_pipeline_settings(tmp_path, {"pipeline_time_limit_minutes": 0})
    with pytest.raises(PipelineSettingsError, match="'chunk_row' is not a pipeline setting"):
        update_pipeline_settings(tmp_path, {"chunk_row": 1000})

    assert settings_file(tmp_path).read_text(encoding="utf-8") == before


def test_a_hand_edit_is_read_on_the_next_read(tmp_path: Path) -> None:
    _write(tmp_path, '{"caching": false}')
    assert read_pipeline_settings(tmp_path).caching_enabled is False

    _write(tmp_path, '{"caching": true, "chunk_rows": 1000}')

    settings = read_pipeline_settings(tmp_path)
    assert settings.caching_enabled is True
    assert settings.streaming_chunk_size == 1000


def test_an_unchanged_file_is_not_parsed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, '{"caching": false}')
    parses: list[Path] = []
    original = _pipeline_settings._parse

    def counting_parse(path: Path) -> PipelineSettings:
        parses.append(path)
        return original(path)

    monkeypatch.setattr(_pipeline_settings, "_parse", counting_parse)

    first = read_pipeline_settings(tmp_path)
    second = read_pipeline_settings(tmp_path)

    assert first == second
    assert len(parses) == 1


# ---------------------------------------------------------------------------
# Chunk rows follow the file in the server process
# ---------------------------------------------------------------------------


def test_the_server_applies_the_files_chunk_rows_and_follows_a_hand_edit(tmp_path: Path) -> None:
    _write(tmp_path, '{"chunk_rows": 123000}')
    set_streaming_chunk_size(500_000)
    try:
        follow_chunk_rows(tmp_path)
        assert current_streaming_chunk_size() == 123_000

        _write(tmp_path, '{"chunk_rows": 45000, "caching": true}')
        read_pipeline_settings(tmp_path)
        assert current_streaming_chunk_size() == 45_000

        settings_file(tmp_path).unlink()
        read_pipeline_settings(tmp_path)
        assert current_streaming_chunk_size() == 500_000
    finally:
        stop_following_chunk_rows()


def test_reading_another_projects_settings_never_changes_the_chunk_size(tmp_path: Path) -> None:
    followed, other = tmp_path / "followed", tmp_path / "other"
    followed.mkdir()
    _write(other, '{"chunk_rows": 2000}')
    set_streaming_chunk_size(300_000)
    try:
        follow_chunk_rows(followed)
        assert current_streaming_chunk_size() == 500_000
        set_streaming_chunk_size(300_000)

        read_pipeline_settings(other)

        assert current_streaming_chunk_size() == 300_000
    finally:
        stop_following_chunk_rows()


def test_without_following_a_read_never_changes_the_chunk_size(tmp_path: Path) -> None:
    _write(tmp_path, '{"chunk_rows": 2000}')
    set_streaming_chunk_size(300_000)

    read_pipeline_settings(tmp_path)

    assert current_streaming_chunk_size() == 300_000


# ---------------------------------------------------------------------------
# Automatic figures
# ---------------------------------------------------------------------------


def test_the_automatic_figures_are_what_each_consumer_would_use_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _execution_admission

    monkeypatch.delenv("HAUTE_EXECUTION_MEMORY_POLICY", raising=False)
    monkeypatch.setattr(_execution_admission, "available_ram_bytes", lambda: 20 * GIB)

    automatic = automatic_pipeline_settings(tmp_path)

    assert automatic.chunk_rows == 500_000
    assert automatic.caching is True
    assert 0 < automatic.cache_size_gb <= 20
    assert automatic.kept_free_gb == 2
    # 70% of the 18 GiB above the kept-free memory.
    assert automatic.preview_memory_gb == pytest.approx(18 * 0.7, abs=1e-6)
    assert automatic.pipeline_time_limit_minutes == 30
    assert automatic.modelling_time_limit_minutes == 60
    assert automatic.optimisation_time_limit_minutes is None

    update_pipeline_settings(tmp_path, {"kept_free_gb": 8})
    # The preview figure follows the kept-free override; the reserve's own
    # automatic figure is still 2.
    automatic = automatic_pipeline_settings(tmp_path)
    assert automatic.preview_memory_gb == pytest.approx(12 * 0.7, abs=1e-6)
    assert automatic.kept_free_gb == 2


def test_the_automatic_preview_figure_is_the_fixed_default_under_a_fixed_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _execution_admission

    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "fixed")
    monkeypatch.setattr(_execution_admission, "available_ram_bytes", lambda: 64 * GIB)

    assert automatic_pipeline_settings(tmp_path).preview_memory_gb == 4


# ---------------------------------------------------------------------------
# Consumers read the file when their work starts
# ---------------------------------------------------------------------------


def test_every_preview_is_admitted_with_the_one_preview_budget(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _execution_admission

    monkeypatch.delenv("HAUTE_EXECUTION_MEMORY_POLICY", raising=False)
    monkeypatch.setattr(_execution_admission, "available_ram_bytes", lambda: 20 * GIB)
    # A cache build's variable never sizes a preview any more, whatever it captures.
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "256")

    automatic = create_admitted_execution_context(
        operation="pipeline_preview", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        assert automatic.admission is not None
        assert automatic.admission.budget_policy == "adaptive_local"
        assert automatic.memory_limit_bytes == (18 * GIB) * 7_000 // 10_000
    finally:
        automatic.release_admission()

    update_pipeline_settings(project, {"preview_memory_gb": 1.5})
    configured = create_admitted_execution_context(
        operation="pipeline_preview", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        assert configured.memory_limit_bytes == round(1.5 * GIB)
        assert configured.admission is not None
        assert configured.admission.budget_policy == "pipeline_settings"
        assert configured.admission.config_key == "preview_memory_gb"
    finally:
        configured.release_admission()


def test_the_kept_free_memory_sets_the_reserve_exactly(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _execution_admission

    monkeypatch.delenv("HAUTE_EXECUTION_MEMORY_POLICY", raising=False)
    monkeypatch.delenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.delenv("HAUTE_EXPLORE_MEMORY_LIMIT_BYTES", raising=False)
    monkeypatch.setattr(_execution_admission, "available_ram_bytes", lambda: 10 * GIB)
    # Automatically the reserve is 2 GiB, or half of what is available.
    budget = execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)
    assert budget.os_reserve_bytes == 2 * GIB

    # Set, it is used exactly, even beyond half of what is available.
    update_pipeline_settings(project, {"kept_free_gb": 7})
    budget = execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)
    assert budget.os_reserve_bytes == 7 * GIB
    assert budget.memory_limit_bytes == 3 * GIB
    update_pipeline_settings(project, {"kept_free_gb": 0})
    budget = execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)
    assert budget.os_reserve_bytes == 0
    assert budget.memory_limit_bytes == (10 * GIB) * 7_000 // 10_000


def test_an_invalid_file_refuses_admission(project: Path) -> None:
    _write(project, '{"kept_free_gb": -1}')

    with pytest.raises(PipelineSettingsError, match="kept_free_gb"):
        create_admitted_execution_context(
            operation="pipeline_preview", profile=ExecutionProfile.PREVIEW_EAGER
        )


def test_the_pipeline_time_limit_reaches_every_pipeline_consumer(project: Path) -> None:
    from haute._input_preparation import _build_timeout_seconds
    from haute.routes.input_cache import _build_timeout
    from haute.routes.optimiser import _estimate_timeout
    from haute.routes.output_assemble import _dry_run_timeout
    from haute.routes.pipeline import _pipeline_time_limit

    consumers = (
        _pipeline_time_limit,
        _dry_run_timeout,
        _build_timeout,
        _build_timeout_seconds,
        _estimate_timeout,
    )
    assert [consumer() for consumer in consumers] == [30 * 60.0] * len(consumers)

    update_pipeline_settings(project, {"pipeline_time_limit_minutes": 2.5})

    assert [consumer() for consumer in consumers] == [150.0] * len(consumers)


def test_the_modelling_time_limit_is_a_training_jobs_default(project: Path) -> None:
    from haute.routes._training_lifecycle import _default_train_timeout

    assert _default_train_timeout() == 60 * 60
    update_pipeline_settings(project, {"modelling_time_limit_minutes": 90})
    assert _default_train_timeout() == 90 * 60


def test_the_optimisation_time_limit_is_none_until_set_and_a_config_timeout_wins(
    project: Path,
) -> None:
    from haute.routes._optimiser_service import (
        _auto_range_timeout_from_config,
        _solve_timeout_from_config,
    )

    assert _solve_timeout_from_config({}) is None
    assert _auto_range_timeout_from_config({}) is None

    update_pipeline_settings(project, {"optimisation_time_limit_minutes": 0.5})

    assert _solve_timeout_from_config({}) == 30
    assert _auto_range_timeout_from_config({}) == 30
    assert _solve_timeout_from_config({"timeout": 7}) == 7
    assert _auto_range_timeout_from_config({"auto_range_timeout": 9}) == 9


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_get_returns_the_settings_the_automatic_figures_and_the_path(project: Path, client) -> None:
    update_pipeline_settings(project, {"caching": False, "preview_memory_gb": 6})

    response = client.get("/api/pipeline-settings")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == SETTINGS_PATH
    assert body["settings"] == {
        "chunk_rows": None,
        "caching": False,
        "cache_size_gb": None,
        "preview_memory_gb": 6.0,
        "kept_free_gb": None,
        "pipeline_time_limit_minutes": None,
        "modelling_time_limit_minutes": None,
        "optimisation_time_limit_minutes": None,
    }
    automatic = body["automatic"]
    assert automatic["chunk_rows"] == 500_000
    assert automatic["caching"] is True
    assert automatic["pipeline_time_limit_minutes"] == 30
    assert automatic["modelling_time_limit_minutes"] == 60
    assert automatic["optimisation_time_limit_minutes"] is None
    assert automatic["cache_size_gb"] > 0
    assert automatic["preview_memory_gb"] > 0
    assert automatic["kept_free_gb"] > 0


def test_patch_changes_only_the_keys_it_carries(project: Path, client) -> None:
    update_pipeline_settings(project, {"modelling_time_limit_minutes": 90})

    response = client.patch("/api/pipeline-settings", json={"preview_memory_gb": 6})

    assert response.status_code == 200
    assert response.json()["settings"]["preview_memory_gb"] == 6
    assert response.json()["settings"]["modelling_time_limit_minutes"] == 90
    assert json.loads(settings_file(project).read_text(encoding="utf-8")) == {
        "preview_memory_gb": 6,
        "modelling_time_limit_minutes": 90,
    }


def test_patch_null_restores_automatic(project: Path, client) -> None:
    update_pipeline_settings(project, {"caching": False, "cache_size_gb": 3})

    response = client.patch("/api/pipeline-settings", json={"cache_size_gb": None})

    assert response.status_code == 200
    assert response.json()["settings"]["cache_size_gb"] is None
    assert json.loads(settings_file(project).read_text(encoding="utf-8")) == {"caching": False}


def test_patch_applies_chunk_rows_at_once(project: Path, client) -> None:
    set_streaming_chunk_size(500_000)

    response = client.patch("/api/pipeline-settings", json={"chunk_rows": 123_000})

    assert response.status_code == 200
    assert current_streaming_chunk_size() == 123_000


@pytest.mark.parametrize(
    "body",
    [
        {"preview_memory_gb": 0},
        {"preview_memory_gb": True},
        # Refused as the file refuses them, not converted.
        {"preview_memory_gb": "6"},
        {"kept_free_gb": -1},
        {"chunk_rows": 0},
        {"chunk_rows": True},
        {"chunk_rows": 500000.0},
        {"caching": "yes"},
        {"pipeline_time_limit_minutes": 10_081},
        {"chunk_row": 1000},
    ],
)
def test_patch_refuses_an_invalid_body_and_writes_nothing(
    project: Path, client, body: dict[str, object]
) -> None:
    update_pipeline_settings(project, {"caching": False})
    before = settings_file(project).read_text(encoding="utf-8")

    response = client.patch("/api/pipeline-settings", json=body)

    assert response.status_code == 422
    assert settings_file(project).read_text(encoding="utf-8") == before


def test_an_invalid_file_answers_409_with_its_message(project: Path, client) -> None:
    path = _write(project, '{"preview_memory_gb": 0}')

    got = client.get("/api/pipeline-settings")
    patched = client.patch("/api/pipeline-settings", json={"caching": False})

    for response in (got, patched):
        assert response.status_code == 409
        assert response.json()["detail"].startswith(SETTINGS_PATH)
        assert "preview_memory_gb" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == '{"preview_memory_gb": 0}'


def test_an_invalid_file_stops_a_preview_with_its_message(project: Path, client) -> None:
    pl.DataFrame({"a": [1, 2, 3]}).write_parquet(project / "a.parquet")
    (project / "main.py").write_text("# pipeline\n", encoding="utf-8")
    _write(project, '{"pipeline_time_limit_minutes": 0}')
    graph = {
        "nodes": [
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": "dataInput",
                    "config": {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(project / "a.parquet"),
                    },
                },
            }
        ],
        "edges": [],
        "source_file": str(project / "main.py"),
    }

    response = client.post("/api/pipeline/preview", json={"graph": graph, "node_id": "src"})

    assert response.status_code == 409
    assert response.json()["detail"].startswith(SETTINGS_PATH)
    assert "pipeline_time_limit_minutes" in response.json()["detail"]
