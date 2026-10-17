"""Run the test session under the Polars settings every Haute process starts with.

Imported first by ``tests/conftest.py``, before Polars, as ``haute.cli`` does, so
the test process and every worker a test spawns agree; a spill, if any, goes to
a folder of this session's own.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from haute._polars_environment import apply_polars_process_environment

SPILL_ROOT = Path(tempfile.gettempdir()) / f"haute-tests-polars-spill-{os.getpid()}"
apply_polars_process_environment(SPILL_ROOT)
