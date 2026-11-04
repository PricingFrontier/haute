"""Every key Haute's schemas own is snake_case (READ-29).

Spec: specs/pipeline-config/high-level.md, "Keys are snake_case". The conversion is the
one-off ``scripts/one_off/read_29_snake_case_keys.py``, which these tests load and which goes
with them once the owner has run it. A key is renamed only where its node type's config model
declares the new key, so a column, level, input name, factor or constant the analyst named
keeps its name whatever its casing; a sidecar still in the camelCase form is refused, naming
the conversion.
"""

from __future__ import annotations

import importlib.util
import json
import re
import typing
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typing_extensions import is_typeddict

import haute
from haute._config_io import load_node_config, node_config_json
from haute._config_validation import SNAKE_CASE_CONVERSION, SNAKE_CASE_KEYS
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._project_context import standalone_project_scope
from haute._rating_step_config import normalise_rating_step_config
from haute._types import NodeType
from haute.errors import ConfigError, ConfigSettingError
from haute.parser import parse_pipeline_file

SCRIPT = Path(__file__).parents[1] / "scripts" / "one_off" / "read_29_snake_case_keys.py"


@pytest.fixture(scope="module")
def conversion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("read_29_snake_case_keys", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    unseen |= {"variable_name", "value_name"}
    assert set(SNAKE_CASE_KEYS.values()) - keys <= unseen


def test_keys_are_renamed_where_the_shape_declares_them_and_analyst_names_stay(
    conversion: ModuleType,
) -> None:
    banding = {
        "factors": [
            {
                "banding": "categorical",
                "column": "rightClosed",
                "outputColumn": "outputColumn",
                "rules": {"outputColumn": "onMissing"},
            }
        ],
        "inputMapping": {"leftOn": "quotes"},
    }
    assert conversion.rename(banding, NODE_CONFIG_MODELS[NodeType.BANDING].shape) == {
        "factors": [
            {
                "banding": "categorical",
                "column": "rightClosed",
                "output_column": "outputColumn",
                "rules": {"outputColumn": "onMissing"},
            }
        ],
        "input_mapping": {"leftOn": "quotes"},
    }
    rating = {
        "tables": [
            {
                "factors": ["sourceType"],
                "outputColumn": "rate",
                "defaultValue": "1",
                "onMissing": "neutral",
                "factorDtypes": {
                    "sourceType": {"kind": "Datetime", "timeUnit": "us", "timeZone": None}
                },
                "rows": [["defaultValue", 1]],
            }
        ],
        "steps": [{"id": "s1", "kind": "sort", "keys": [], "nullsLast": True}],
    }
    assert conversion.rename(rating, NODE_CONFIG_MODELS[NodeType.RATING_STEP].shape) == {
        "tables": [
            {
                "factors": ["sourceType"],
                "output_column": "rate",
                "default_value": "1",
                "on_missing": "neutral",
                "factor_dtypes": {
                    "sourceType": {"kind": "Datetime", "time_unit": "us", "time_zone": None}
                },
                "rows": [["defaultValue", 1]],
            }
        ],
        "steps": [{"id": "s1", "kind": "sort", "keys": [], "nulls_last": True}],
    }
    constants = {"values": {"onMissing": 1.0, "stepCount": "x"}}
    assert conversion.rename(constants, NODE_CONFIG_MODELS[NodeType.CONSTANT].shape) == constants


def _project(root: Path) -> Path:
    """A pipeline whose sidecars, keyword and unfinished steps are in the camelCase form."""
    pipeline = root / "main.py"
    files = {
        "main.py": (
            "import haute\n\n"
            'pipeline = haute.Pipeline("p")\n\n\n'
            '@pipeline.banding(config="config/banding/bands.json")\n'
            "def bands(quotes): ...\n\n\n"
            '@pipeline.constant(config="config/constant/rates.json")\n'
            "def rates(): ...\n\n\n"
            '@pipeline.polars(inputMapping={"sourceType": "bands"})\n'
            "def priced(sourceType):\n"
            "    # inputMapping stays as written in a comment\n"
            "    return sourceType\n\n\n"
            '@pipeline.edge_join(how="left", leftOn="id", rightOn="id", maintain_order="left")\n'
            "def joined(priced, rates): ...\n"
        ),
        "config/banding/bands.json": json.dumps(
            {
                "factors": [
                    {
                        "banding": "categorical",
                        "column": "fuel",
                        "outputColumn": "outputColumn",
                        "rules": {"outputColumn": "onMissing"},
                    }
                ]
            }
        ),
        "config/constant/rates.json": json.dumps({"values": {"onMissing": 1.0}}),
        "main.haute.json": json.dumps(
            {
                "positions": {"bands": {"x": 0, "y": 0}},
                "unfinished_steps": {
                    "priced": [{"id": "s1", "kind": "sort", "keys": [], "nullsLast": True}]
                },
            }
        ),
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return pipeline


def test_a_project_converts_once_and_reads_as_haute_saves_it(
    tmp_path: Path, conversion: ModuleType
) -> None:
    pipeline = _project(tmp_path)
    constants_before = (tmp_path / "config/constant/rates.json").read_bytes()

    written = conversion.convert([tmp_path])

    assert sorted(path.name for path in written) == ["bands.json", "main.haute.json", "main.py"]
    # A constant named onMissing is the analyst's: the file had no key to rename.
    assert (tmp_path / "config/constant/rates.json").read_bytes() == constants_before
    source = pipeline.read_text(encoding="utf-8")
    assert '@pipeline.polars(input_mapping={"sourceType": "bands"})' in source
    assert "# inputMapping stays as written in a comment" in source
    assert (
        '@pipeline.edge_join(how="left", left_on="id", right_on="id", maintain_order="left")'
        in source
    )
    sidecar = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert sidecar["unfinished_steps"]["priced"][0]["nulls_last"] is True

    bands = tmp_path / "config/banding/bands.json"
    config = load_node_config(str(bands), node_type=NodeType.BANDING)
    assert config["factors"][0]["output_column"] == "outputColumn"
    assert bands.read_text(encoding="utf-8") == node_config_json(
        NodeType.BANDING, config, node_label="bands"
    )
    assert conversion.convert([tmp_path]) == []


def test_a_submodel_definition_s_sidecars_resolve_against_its_pipeline(
    tmp_path: Path, conversion: ModuleType
) -> None:
    """A definition below the pipeline names its sidecars from the pipeline's directory
    (``pipeline_dir=".."``), as the parser reads them."""
    module = tmp_path / "modules" / "sub.py"
    module.parent.mkdir()
    module.write_text(
        "import haute\n\n"
        'submodel = haute.Submodel("sub", pipeline_dir="..")\n\n\n'
        '@submodel.banding(config="config/banding/sub_bands.json")\n'
        "def sub_bands(quotes): ...\n",
        encoding="utf-8",
    )
    sidecar = tmp_path / "config" / "banding" / "sub_bands.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps({"factors": [{"banding": "categorical", "column": "c", "outputColumn": "b"}]}),
        encoding="utf-8",
    )

    assert conversion.convert([tmp_path]) == [sidecar.resolve()]
    assert json.loads(sidecar.read_text(encoding="utf-8"))["factors"][0]["output_column"] == "b"


def _files(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    ("relative", "text", "message"),
    [
        (
            "config/banding/bands.json",
            '{"factors": [{"banding": "categorical", "column": "fuel",'
            ' "outputColumn": "a", "output_column": "b"}]}',
            "'output_column' is written twice",
        ),
        (
            "config/banding/bands.json",
            '{"factors": [{"banding": "categorical", "column": "fuel", "outputColumn": "a",'
            ' "column": "other"}]}',
            "duplicate JSON key 'column'",
        ),
        (
            "main.haute.json",
            '{"unfinished_steps": {"priced": [{"id": "s1", "kind": "sort", "keys": [],'
            ' "nullsLast": true, "nulls_last": false}]}}',
            "'nulls_last' is written twice",
        ),
    ],
    ids=["renamed-key-collides", "repeated-key", "step-key-collides"],
)
def test_a_file_whose_conversion_would_lose_a_value_refuses_the_whole_run(
    tmp_path: Path, conversion: ModuleType, relative: str, text: str, message: str
) -> None:
    """Keeping either of two values for one key would silently discard the other."""
    _project(tmp_path)
    (tmp_path / relative).write_text(text, encoding="utf-8")
    before = _files(tmp_path)

    with pytest.raises(SystemExit, match=re.escape(message)):
        conversion.convert([tmp_path])
    assert _files(tmp_path) == before


def test_a_decorator_with_unrelated_and_unpacked_keywords_is_left_as_written(
    tmp_path: Path, conversion: ModuleType
) -> None:
    """Only a keyword in its camelCase form is a key's spelling; ``ids=`` beside a
    ``**options`` argument is neither, and the decorator is left alone."""
    pipeline = _project(tmp_path)
    helper = tmp_path / "helpers.py"
    helper.write_text(
        "import pytest\n\noptions = {}\n\n\n"
        '@pytest.mark.parametrize("x", [1], ids=["one"], **options)\n'
        "def check(x): ...\n",
        encoding="utf-8",
    )
    helper_before = helper.read_bytes()

    written = conversion.convert([tmp_path])

    assert pipeline.resolve() in written
    assert helper.read_bytes() == helper_before


def test_a_decorator_with_both_spellings_of_a_keyword_refuses_the_whole_run(
    tmp_path: Path, conversion: ModuleType
) -> None:
    source = _project(tmp_path).read_text(encoding="utf-8")
    (tmp_path / "main.py").write_text(
        source.replace(
            '@pipeline.polars(inputMapping={"sourceType": "bands"})',
            '@pipeline.polars(inputMapping={"sourceType": "bands"}, input_mapping={})',
        ),
        encoding="utf-8",
    )
    before = _files(tmp_path)

    with pytest.raises(SystemExit, match="inputMapping= and input_mapping="):
        conversion.convert([tmp_path])
    assert _files(tmp_path) == before


def test_a_sidecar_a_decorator_names_but_is_missing_refuses_the_whole_run(
    tmp_path: Path, conversion: ModuleType
) -> None:
    source = _project(tmp_path).read_text(encoding="utf-8")
    (tmp_path / "main.py").write_text(
        source
        + '\n\n@pipeline.banding(config="config/banding/gone.json")\ndef gone(quotes): ...\n',
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(SystemExit, match="gone.json"):
        conversion.convert([tmp_path])
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_a_sidecar_with_a_camel_case_key_is_refused_naming_the_conversion(
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
    with pytest.raises(ConfigError, match=re.escape(SNAKE_CASE_CONVERSION)) as nested:
        load_node_config(str(banding), node_type=NodeType.BANDING)
    assert "'outputColumn' is now 'output_column'" in str(nested.value)

    join = tmp_path / "config/edge_join/join.json"
    join.parent.mkdir(parents=True)
    join.write_text(json.dumps({"how": "left", "leftOn": "id", "rightOn": "id"}), encoding="utf-8")
    with pytest.raises(ConfigError, match=re.escape(SNAKE_CASE_CONVERSION)) as top:
        load_node_config(str(join), node_type=NodeType.EDGE_JOIN)
    assert "'leftOn' is now 'left_on', 'rightOn' is now 'right_on'" in str(top.value)


def test_a_decorator_keyword_in_its_camel_case_form_is_refused_naming_the_conversion(
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
    with pytest.raises(ConfigError, match=re.escape(SNAKE_CASE_CONVERSION)):

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
def test_a_rating_dtype_descriptor_in_the_camel_case_form_names_the_conversion(
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
