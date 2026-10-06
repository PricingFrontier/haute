"""The model family registry: the one place a scoring family is described.

Each :class:`ModelFamily` is one adapter: the flavor a loaded model carries,
the file suffixes it loads from, its file loader, which feature contract it
needs, how its predict frame is prepared, how it reads and receives its
offset, its trace explanation method, and the distributions its runtime needs.
Loading, artifact discovery, predict-frame preparation, the scorer's offset
dispatch, the trace explanation, the deploy image's dependencies and the
frontend's suffix list all read this registry, so a further family is a single
:func:`register_model_family` call.

``ModelFlavor`` types the built-in flavors and ``_SUPPORTED_FLAVORS`` is
derived from it via ``get_args``; the built-in registrations below are exactly
that set (``tests/test_model_families.py`` pins it).

This is a dependency-free leaf module. ``_model_scorer`` imports ``_mlflow_io``
at module load, so the family domain is hoisted out of both rather than one
importing the other. Loaders and offset readers are bound lazily by module
path, so importing the registry imports no engine and no other ``haute``
module.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, get_args

if TYPE_CHECKING:
    from haute._mlflow_io import ScoringModel

ModelFlavor: TypeAlias = Literal[
    "catboost", "pyfunc", "rustystats", "xgboost", "lightgbm", "ebm", "tboost"
]

# Derived — never hand-duplicated.  ``get_args`` reads the literal members off
# ``ModelFlavor`` so the frozenset cannot fall out of sync with the type.
_SUPPORTED_FLAVORS: frozenset[ModelFlavor] = frozenset(get_args(ModelFlavor))

#: How a family's predict frame is prepared: ``polars`` hands the selected
#: Polars frame to a model that encodes its own inputs; ``tabular`` casts
#: numerics to Float32 and carries ``_MISSING_``-filled categoricals through
#: pandas (numpy when there are none); ``pandas`` is a named pandas frame with
#: native dtypes, for a model that enforces its own signature.
PredictFrame: TypeAlias = Literal["polars", "tabular", "pandas"]

#: How a model receives its offset: ``column`` rides in the predict frame and
#: the model applies it; ``baseline`` is supplied by Haute as a CatBoost
#: ``Pool`` baseline built from the recorded offset link.
OffsetInput: TypeAlias = Literal["column", "baseline"]

#: The pip distribution of the XGBoost runtime. macOS wheels bundle the CPU
#: and GPU builds; elsewhere the CPU-only wheel keeps the image small.
XGBOOST_DISTRIBUTION = "xgboost" if sys.platform == "darwin" else "xgboost-cpu"


class ModelFileLoader(Protocol):
    """Load one model file as the *task* it will score."""

    def __call__(
        self, path: str, task: str, *, contract_path: str | None, source: str | None
    ) -> ScoringModel: ...


OffsetReader: TypeAlias = Callable[[Any], "str | None"]


@dataclass(frozen=True, slots=True)
class ModelFamily:
    """One scoring family's adapter.

    Attributes:
        flavor: The ``ScoringModel.flavor`` the family's models load as.
        label: The family's user-facing name.
        suffixes: The file suffixes it loads from (lowercase, with the dot);
            empty for a family stored as a directory (MLflow pyfunc).
        load_file: Its file loader, or ``None`` when it loads only from an
            MLflow URI.
        algorithm: The Haute training algorithm whose models load as this
            family, or ``None`` when Haute does not train it.
        requires_contract: The file loads only with its feature contract: an
            explicit or sibling contract locally, the contract logged beside it
            in a run, and the contract's bytes join its cache identity.
        self_describing: The loaded model is a Haute wrapper that encodes its
            own inputs, applies its own offset and labels binary predictions;
            its contract identity is checked against its native objective.
        predict_frame: How its predict frame is prepared.
        offset_input: How it receives its offset.
        offset_column: Reads the offset column a raw model records.
        offset_link: Reads how that offset enters the raw score.
        explanation: Its trace explanation method, or ``None``.
        distributions: The pip distributions a deployed image installs for one
            of its artifacts.
    """

    flavor: str
    label: str
    suffixes: tuple[str, ...]
    load_file: ModelFileLoader | None
    algorithm: str | None
    requires_contract: bool
    self_describing: bool
    predict_frame: PredictFrame
    offset_input: OffsetInput
    offset_column: OffsetReader | None
    offset_link: OffsetReader | None
    explanation: str | None
    distributions: tuple[str, ...]

    def __post_init__(self) -> None:
        for suffix in self.suffixes:
            if not suffix.startswith(".") or len(suffix) < 2 or suffix != suffix.lower():
                raise ValueError(
                    f"model family {self.flavor!r} has suffix {suffix!r}; expected a "
                    "lowercase suffix with its dot, such as '.cbm'"
                )
        if bool(self.suffixes) != (self.load_file is not None):
            raise ValueError(
                f"model family {self.flavor!r} must have a file loader exactly when it "
                "has file suffixes"
            )


def _lazy(module: str, name: str) -> Callable[..., Any]:
    """A callable that resolves ``module.name`` on each call.

    Binding by path keeps this module a leaf, and resolving on each call keeps
    the target patchable as a module attribute.
    """

    def call(*args: Any, **kwargs: Any) -> Any:
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    call.__name__ = name
    call.__qualname__ = f"{module}.{name}"
    return call


def _attribute(name: str) -> OffsetReader:
    def read(model: Any) -> str | None:
        value = getattr(model, name)
        return value if isinstance(value, str) and value else None

    read.__name__ = f"read_{name}"
    return read


_MLFLOW_IO = "haute._mlflow_io"

_FAMILIES: dict[str, ModelFamily] = {}


def register_model_family(family: ModelFamily) -> None:
    """Add *family* to the registry.

    Raises:
        ValueError: its flavor is already registered, or another family
            already claims one of its suffixes.
    """
    if family.flavor in _FAMILIES:
        raise ValueError(f"model family {family.flavor!r} is already registered")
    claimed = {suffix: other.flavor for other in _FAMILIES.values() for suffix in other.suffixes}
    for suffix in family.suffixes:
        if suffix in claimed:
            raise ValueError(
                f"model family {family.flavor!r} claims suffix {suffix!r}, which "
                f"{claimed[suffix]!r} already loads"
            )
    _FAMILIES[family.flavor] = family


def model_families() -> tuple[ModelFamily, ...]:
    """Every registered family, in registration (and artifact discovery) order."""
    return tuple(_FAMILIES.values())


def is_registered_flavor(flavor: str) -> bool:
    return flavor in _FAMILIES


def model_family(flavor: str) -> ModelFamily:
    """The family a loaded model's *flavor* names.

    Raises:
        ValueError: no family is registered under *flavor*.
    """
    family = _FAMILIES.get(flavor)
    if family is None:
        raise ValueError(f"Unknown model flavor {flavor!r}. Expected one of: {sorted(_FAMILIES)}.")
    return family


def family_for_algorithm(algorithm: str) -> ModelFamily | None:
    """The family a Haute training *algorithm*'s models load as, if any."""
    return next((f for f in _FAMILIES.values() if f.algorithm == algorithm), None)


def model_file_suffixes() -> tuple[str, ...]:
    """Every registered model file suffix, in registration order."""
    return tuple(suffix for family in _FAMILIES.values() for suffix in family.suffixes)


def family_for_suffix(suffix: str) -> ModelFamily | None:
    """The family that loads files ending in *suffix*, or ``None``."""
    return next((f for f in _FAMILIES.values() if suffix in f.suffixes), None)


def _directory_family() -> ModelFamily:
    return next(f for f in _FAMILIES.values() if not f.suffixes)


def supported_model_files_description() -> str:
    """The registered suffixes and the directory form, for error messages."""
    return (
        f"a file ending in {', '.join(model_file_suffixes())}, or an MLflow pyfunc model directory"
    )


def family_for_artifact(path: str) -> ModelFamily:
    """The family a model artifact *path* loads as.

    A path whose suffix a family registers loads as that family. A path with
    no suffix names a directory, which only an MLflow pyfunc model is; MLflow's
    loader then requires the ``MLmodel`` file inside it.

    Raises:
        ConfigError: the path has a suffix no family registers.
    """
    suffix = PurePosixPath(re.split(r"[\\/]", path.rstrip("/\\"))[-1]).suffix.lower()
    if not suffix:
        return _directory_family()
    family = family_for_suffix(suffix)
    if family is None:
        from haute.errors import ConfigError

        raise ConfigError(
            f"{path!r} is not a model file Haute can score. Expected "
            f"{supported_model_files_description()}.",
            artifact_path=path,
            supported_suffixes=list(model_file_suffixes()),
        )
    return family


def model_family_fixture() -> dict[str, dict[str, Any]]:
    """The serialisable family table the frontend's ``modelFamilies.json`` mirrors."""
    return {
        family.flavor: {
            "label": family.label,
            "algorithm": family.algorithm,
            "suffixes": list(family.suffixes),
        }
        for family in _FAMILIES.values()
    }


for _family in (
    ModelFamily(
        flavor="catboost",
        label="CatBoost",
        suffixes=(".cbm",),
        load_file=_lazy(_MLFLOW_IO, "_load_catboost_file"),
        algorithm="catboost",
        requires_contract=False,
        self_describing=False,
        predict_frame="tabular",
        offset_input="baseline",
        offset_column=_lazy(_MLFLOW_IO, "_catboost_offset_column"),
        offset_link=_lazy(_MLFLOW_IO, "_catboost_offset_link"),
        explanation="catboost_shap",
        distributions=("catboost",),
    ),
    ModelFamily(
        flavor="rustystats",
        label="RustyStats GLM",
        suffixes=(".rsglm",),
        load_file=_lazy(_MLFLOW_IO, "_load_rustystats_file"),
        algorithm="glm",
        requires_contract=False,
        self_describing=False,
        predict_frame="polars",
        offset_input="column",
        offset_column=_lazy(_MLFLOW_IO, "rustystats_offset_column"),
        offset_link=_lazy(_MLFLOW_IO, "rustystats_offset_link"),
        explanation="rustystats_glm_contributions",
        distributions=("rustystats",),
    ),
    ModelFamily(
        flavor="xgboost",
        label="XGBoost",
        suffixes=(".ubj",),
        load_file=_lazy(_MLFLOW_IO, "_load_xgboost_file"),
        algorithm="xgboost",
        requires_contract=False,
        self_describing=True,
        predict_frame="polars",
        offset_input="column",
        offset_column=_attribute("offset_column"),
        offset_link=_attribute("offset_link"),
        explanation="xgboost_contributions",
        distributions=(XGBOOST_DISTRIBUTION, "pandas"),
    ),
    ModelFamily(
        flavor="lightgbm",
        label="LightGBM",
        suffixes=(".lgbm",),
        load_file=_lazy(_MLFLOW_IO, "_load_lightgbm_file"),
        algorithm="lightgbm",
        requires_contract=False,
        self_describing=True,
        predict_frame="polars",
        offset_input="column",
        offset_column=_attribute("offset_column"),
        offset_link=_attribute("offset_link"),
        explanation="lightgbm_contributions",
        distributions=("lightgbm", "pandas"),
    ),
    ModelFamily(
        flavor="ebm",
        label="EBM",
        suffixes=(".ebm",),
        load_file=_lazy(_MLFLOW_IO, "_load_ebm_file"),
        algorithm="ebm",
        requires_contract=True,
        self_describing=True,
        predict_frame="polars",
        offset_input="column",
        offset_column=_attribute("offset_column"),
        offset_link=_attribute("offset_link"),
        explanation="ebm_terms",
        distributions=("interpret-core", "pandas"),
    ),
    ModelFamily(
        flavor="tboost",
        label="t-boost",
        suffixes=(".tboost",),
        load_file=_lazy(_MLFLOW_IO, "_load_tboost_file"),
        algorithm="tboost",
        requires_contract=False,
        self_describing=True,
        predict_frame="polars",
        offset_input="column",
        offset_column=_attribute("offset_column"),
        offset_link=_attribute("offset_link"),
        explanation="tboost_tables",
        # t-boost scores Polars frames, so its runtime needs no pandas.
        distributions=("t-boost",),
    ),
    ModelFamily(
        flavor="pyfunc",
        label="MLflow pyfunc",
        suffixes=(),
        load_file=None,
        algorithm=None,
        requires_contract=False,
        self_describing=False,
        predict_frame="pandas",
        offset_input="column",
        offset_column=None,
        offset_link=None,
        explanation=None,
        distributions=(),
    ),
):
    register_model_family(_family)
del _family
