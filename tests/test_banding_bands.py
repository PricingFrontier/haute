"""Banding is written as a list of bands (READ-27).

Spec: specs/rating/high-level.md, the banding sidecar. A breakpoint factor writes one
``{"upper", "label"}`` a band in order, the open-ended band, with no ``upper``, last;
categorical banding keeps its map.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._banding_config import BANDS_CONVERSION
from haute._config_io import load_node_config, node_config_json
from haute._types import NodeType
from haute._validation_error import ConfigSettingError


def _factor(rules: list[dict], **settings: object) -> dict:
    return {
        "banding": "breakpoints",
        "column": "x",
        "outputColumn": "x_band",
        "rules": rules,
        **settings,
    }


def _round_trip(tmp_path: Path, config: dict) -> tuple[str, dict]:
    text = node_config_json(NodeType.BANDING, config, node_label="bands")
    sidecar = tmp_path / "config" / "banding" / "bands.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(text.encode("utf-8"))
    return text, load_node_config("config/banding/bands.json", base_dir=tmp_path)


@pytest.mark.parametrize(
    ("boundaries", "written"),
    [
        (["4", "6.5"], ['"upper": 4', '"upper": 6.5']),
        (["2024-01-01", "2025-06-30"], ['"upper": "2024-01-01"', '"upper": "2025-06-30"']),
        (["2024-01-01T00:00:00"], ['"upper": "2024-01-01T00:00:00"']),
        (["4.0", "1e3"], ['"upper": 4.0', '"upper": "1e3"']),
    ],
    ids=["numbers", "dates", "datetimes", "text-kept-as-typed"],
)
@pytest.mark.parametrize("right_closed", [True, False])
def test_bands_round_trip_with_their_edges_as_written(
    tmp_path: Path, boundaries: list[str], written: list[str], right_closed: bool
) -> None:
    rules = [{"boundary": b, "label": f"band {i}"} for i, b in enumerate(boundaries)]
    rules.append({"boundary": "", "label": "top"})
    config = {"factors": [_factor(rules, rightClosed=right_closed)]}

    text, loaded = _round_trip(tmp_path, config)

    for fragment in written:
        assert fragment in text
    assert '{"label": "top"}' in text
    assert loaded["factors"][0]["rules"] == rules
    assert loaded["factors"][0]["rightClosed"] is right_closed


def test_the_open_ended_band_comes_last_and_a_null_default_is_left_out(tmp_path: Path) -> None:
    rules = [{"boundary": "", "label": "top"}, {"boundary": "4", "label": "1-4"}]
    text, loaded = _round_trip(tmp_path, {"factors": [_factor(rules, default=None)]})
    assert text.index('"upper": 4') < text.index('{"label": "top"}')
    assert '"default"' not in text
    assert [rule["boundary"] for rule in loaded["factors"][0]["rules"]] == ["4", ""]


def test_categorical_banding_keeps_its_map(tmp_path: Path) -> None:
    factor = {
        "banding": "categorical",
        "column": "c",
        "outputColumn": "c_band",
        "rules": [
            {"value": "Frame", "assignment": "Frame"},
            {"value": "Brick", "assignment": "Masonry"},
        ],
    }
    text, loaded = _round_trip(tmp_path, {"factors": [factor]})
    assert '"Frame": "Frame"' in text
    assert loaded["factors"][0]["rules"] == factor["rules"]


def test_a_sidecar_with_breakpoints_as_a_map_is_refused_naming_the_conversion(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "config" / "banding" / "bands.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(
        b'{"factors": [{"banding": "breakpoints", "column": "x", "outputColumn": "b", '
        b'"rules": {"4": "1-4", "": "5+"}}]}'
    )
    with pytest.raises(ConfigSettingError, match=BANDS_CONVERSION):
        load_node_config("config/banding/bands.json", base_dir=tmp_path)


def test_only_the_last_band_may_be_open_ended(tmp_path: Path) -> None:
    sidecar = tmp_path / "config" / "banding" / "bands.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(
        b'{"factors": [{"banding": "breakpoints", "column": "x", "outputColumn": "b", '
        b'"rules": [{"label": "low"}, {"upper": 4, "label": "1-4"}]}]}'
    )
    with pytest.raises(ConfigSettingError, match="only the last band"):
        load_node_config("config/banding/bands.json", base_dir=tmp_path)
