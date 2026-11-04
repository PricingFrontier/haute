"""Every key Haute's schemas own is snake_case (READ-29).

Spec: specs/pipeline-config/high-level.md, "Keys are snake_case". A sidecar, a decorator
keyword, a rating dtype descriptor or a submodel module's port endpoint (READ-45) still in
the camelCase form Haute wrote before is refused, naming each key's snake_case name.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path
from typing import Any

import pytest
from typing_extensions import is_typeddict

import haute
from haute._config_io import load_node_config
from haute._config_validation import SNAKE_CASE_KEYS
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._project_context import standalone_project_scope
from haute._rating_step_config import normalise_rating_step_config
from haute._types import NodeType
from haute.errors import ConfigError, ConfigSettingError, ParseError
from haute.parser import parse_pipeline_file

CAMEL_CASE_REFUSAL = "uses the camelCase keys Haute wrote before its keys were snake_case"


def _declared_keys(hint: Any, seen: set[int]) -> set[str]:
    keys: set[str] = set()
    origin = typing.get_origin(hint)
    if origin is not None:
        for arg in typing.get_args(hint):
            keys |= _declared_keys(arg, seen)
        return keys
    if is_typeddict(hint) and id(hint) not in seen:
        seen.add(id(hint))
        for name, item in typing.get_type_hints(hint, include_extras=True).items():
            keys.add(name)
            keys |= _declared_keys(item, seen)
    fields = getattr(hint, "model_fields", None)
    if isinstance(fields, dict) and id(hint) not in seen:
        seen.add(id(hint))
        for name, info in fields.items():
            keys.add(info.alias or name)
            keys |= _declared_keys(info.annotation, seen)
    return keys


def test_every_key_a_config_model_declares_is_snake_case() -> None:
    seen: set[int] = set()
    keys = set().union(
        *(_declared_keys(model.shape, seen) for model in NODE_CONFIG_MODELS.values())
    )
    assert keys, "the walk found no keys"
    assert sorted(key for key in keys if not re.fullmatch(r"[a-z0-9_]+", key)) == []
    # Every renamed key is declared somewhere, or lives in a shape the walk cannot see
    # (a rating dtype descriptor, a Polars step).
    unseen = {"time_unit", "time_zone", "order_by", "nulls_last", "number_type"}
    unseen |= {"variable_name", "value_name", "node_id", "handle_id"}
    assert set(SNAKE_CASE_KEYS.values()) - keys <= unseen


def test_a_sidecar_with_a_camel_case_key_is_refused_naming_its_snake_case_name(
    tmp_path: Path,
) -> None:
    banding = tmp_path / "config/banding/bands.json"
    banding.parent.mkdir(parents=True)
    banding.write_text(
        json.dumps(
            {"factors": [{"banding": "categorical", "column": "fuel", "outputColumn": "band"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=CAMEL_CASE_REFUSAL) as nested:
        load_node_config(str(banding), node_type=NodeType.BANDING)
    assert "'outputColumn' is now 'output_column'" in str(nested.value)

    join = tmp_path / "config/edge_join/join.json"
    join.parent.mkdir(parents=True)
    join.write_text(json.dumps({"how": "left", "leftOn": "id", "rightOn": "id"}), encoding="utf-8")
    with pytest.raises(ConfigError, match=CAMEL_CASE_REFUSAL) as top:
        load_node_config(str(join), node_type=NodeType.EDGE_JOIN)
    assert "'leftOn' is now 'left_on', 'rightOn' is now 'right_on'" in str(top.value)


def test_a_decorator_keyword_in_its_camel_case_form_is_refused_naming_its_snake_case_name(
    tmp_path: Path,
) -> None:
    """Parsed or registered live, ``inputMapping=`` is refused rather than dropped, which
    would leave the node bound as if it had no mapping."""
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "p"\npipeline = "main.py"\n', encoding="utf-8"
    )
    pipeline = tmp_path / "main.py"
    pipeline.write_text(
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        '@pipeline.polars(inputMapping={"src": "quotes"})\n'
        "def priced(src):\n"
        "    df = src\n"
        "    return df\n",
        encoding="utf-8",
    )
    refusal = re.escape("'inputMapping' is now 'input_mapping'")
    with standalone_project_scope(pipeline), pytest.raises(ConfigError, match=refusal):
        parse_pipeline_file(pipeline)

    live = haute.Pipeline("p")
    with pytest.raises(ConfigError, match=refusal):

        @live.polars(inputMapping={"src": "quotes"})
        def priced(src: object) -> object:
            return src


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        (
            {"kind": "Datetime", "timeUnit": "us", "timeZone": None},
            "'timeUnit' is now 'time_unit', 'timeZone' is now 'time_zone'",
        ),
        ({"kind": "Nope"}, "is not a valid rating dtype descriptor"),
    ],
    ids=["camel-case", "unknown"],
)
def test_a_rating_dtype_descriptor_in_the_camel_case_form_is_refused(
    descriptor: dict[str, Any], message: str
) -> None:
    """A table still being set up (no entries yet) has its descriptors checked too."""
    config = {
        "tables": [{"factors": ["a"], "output_column": "r", "factor_dtypes": {"a": descriptor}}]
    }
    with pytest.raises(ConfigSettingError, match=re.escape(message)):
        normalise_rating_step_config(config)


def test_a_table_with_no_factors_or_entries_yet_passes_through() -> None:
    config = {"tables": [{"output_column": "r"}]}
    assert normalise_rating_step_config(config) == config


_CAMEL_CASE_MODULE = """\
import haute
import polars as pl

submodel = haute.Submodel(
    "rating",
    definition_id="rating",
    input_ports=[{"name": "quotes", "targets": [{"nodeId": "priced", "handleId": None}]}],
    output_ports=[{"name": "priced", "source": {"node_id": "priced"}}],
    pipeline_dir="..",
)


@submodel.polars
def priced(quotes: pl.LazyFrame) -> pl.LazyFrame:
    df = quotes
    return df
"""


def test_a_module_writing_its_port_endpoints_in_camel_case_is_refused_naming_each_key() -> None:
    """READ-45: the module form is snake_case; the graph API keeps its own names."""
    from haute._parser_submodels import parse_submodel_source

    with pytest.raises(ParseError) as parsed:
        parse_submodel_source(_CAMEL_CASE_MODULE, "modules/rating.py")
    assert "'nodeId' is now 'node_id', 'handleId' is now 'handle_id'" in str(parsed.value)

    with pytest.raises(ValueError, match=re.escape("'nodeId' is now 'node_id'")):
        haute.Submodel(
            "rating",
            definition_id="rating",
            input_ports=[],
            output_ports=[{"name": "priced", "source": {"nodeId": "priced"}}],
        )


def test_a_module_writes_its_endpoints_in_snake_case_and_a_handle_only_when_set() -> None:
    from haute._types import SubmodelEndpoint, SubmodelInputPort, SubmodelOutputPort

    module = haute.Submodel(
        "rating",
        definition_id="rating",
        input_ports=[{"name": "quotes", "targets": [{"node_id": "join", "handle_id": "base"}]}],
        output_ports=[{"name": "priced", "source": {"node_id": "join"}}],
    )
    assert module.input_ports == [
        SubmodelInputPort(
            name="quotes", targets=[SubmodelEndpoint(node_id="join", handle_id="base")]
        )
    ]
    assert module.output_ports == [
        SubmodelOutputPort(name="priced", source=SubmodelEndpoint(node_id="join"))
    ]
