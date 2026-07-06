"""Single source of truth for the model-scoring flavor domain.

``ModelFlavor`` enumerates every model flavor the scoring stack can load,
prepare, and dispatch on.  ``_SUPPORTED_FLAVORS`` is *derived* from it via
``get_args`` so the valid set is never hand-duplicated.

This lives in its own dependency-free module so that both
:mod:`haute._model_scorer` (the scoring entry point + flavor dispatch) and
:mod:`haute._mlflow_io` (model loading + predict-frame preparation) import the
*same* domain object rather than each carrying a parallel spelling that can
drift.  The SSOT is hoisted out of both — instead of one owning it and the
other importing across — because ``_model_scorer`` already imports
``_mlflow_io`` at module load time (for the model-cache-size constant), so a
reciprocal module-level import of the flavor domain between the two would be a
load-order-fragile import cycle.  A leaf module with no ``haute`` dependencies
sidesteps that entirely.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, get_args

# The scoring flavor domain.  Add a flavor here and both the scorer dispatch
# and ``_mlflow_io``'s predict-frame preparation must be taught to handle it —
# ``tests/test_mlflow_io.py::TestFlavorSsot`` fails loudly if only one side is
# updated.
ModelFlavor: TypeAlias = Literal["catboost", "pyfunc", "rustystats"]

# Derived — never hand-duplicated.  ``get_args`` reads the literal members off
# ``ModelFlavor`` so the frozenset cannot fall out of sync with the type.
_SUPPORTED_FLAVORS: frozenset[ModelFlavor] = frozenset(get_args(ModelFlavor))
