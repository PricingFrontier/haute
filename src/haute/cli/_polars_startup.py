"""Apply Haute's Polars settings before the CLI imports Polars.

Imported first by ``haute.cli``: Polars reads the unknown-extension setting only
when it is imported, and every command, the editor server and the workers they
spawn run under these settings (see ``haute._polars_environment``). The project
is the working directory, as for every command.
"""

from __future__ import annotations

from pathlib import Path

from haute._polars_environment import (
    apply_polars_process_environment,
    polars_spill_root,
    reap_polars_spill_dirs,
)

SPILL_ROOT = polars_spill_root(Path.cwd())
POLARS_SETTINGS = apply_polars_process_environment(SPILL_ROOT)
reap_polars_spill_dirs(SPILL_ROOT)
