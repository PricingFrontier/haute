"""Guards for the parser helper patch-target migration."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

from haute import _config_builder
from haute._config_builder import _resolve_node_config
from haute._types import NodeType

LEGACY_PATCH_TARGETS = (
    "haute._parser_helpers." + "warn_unrecognized_config_keys",
    "haute._parser_helpers." + "load_node_config",
)


def test_legacy_parser_helper_patch_targets_are_gone() -> None:
    tests_root = Path(__file__).resolve().parent
    offenders: list[str] = []
    for path in tests_root.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        for target in LEGACY_PATCH_TARGETS:
            if target in text:
                offenders.append(f"{path.name}: {target}")
    assert offenders == []


def test_parser_helper_legacy_patch_attributes_are_absent() -> None:
    from haute import _parser_helpers

    assert not hasattr(_parser_helpers, "warn_unrecognized_config_keys")
    assert not hasattr(_parser_helpers, "load_node_config")


def test_config_builder_calls_real_helpers_directly(tmp_path: Path) -> None:
    config = {"path": "data.csv", "sourceType": "flat_file"}
    source = inspect.getsource(_config_builder)
    assert "from haute._config_io import" in source
    assert "load_node_config" in source
    assert "from haute._config_validation import warn_unrecognized_config_keys" in source
    with (
        patch("haute._config_builder.load_node_config", return_value=config) as load_mock,
        patch("haute._config_builder.warn_unrecognized_config_keys", return_value=[]) as warn_mock,
    ):
        node_type, resolved = _resolve_node_config(
            {"config": "config/data_source/sample.json"},
            "",
            [],
            0,
            tmp_path,
            explicit_node_type=NodeType.DATA_SOURCE,
        )

    assert node_type == NodeType.DATA_SOURCE
    assert resolved["path"] == "data.csv"
    assert resolved["sourceType"] == "flat_file"
    assert "code" in resolved
    load_mock.assert_called_once_with("config/data_source/sample.json", base_dir=tmp_path)
    warn_mock.assert_called_once()
