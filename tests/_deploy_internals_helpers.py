"""The fixture pipeline path and API Input config shared by the deploy-internals suites.

Several modules that test the deploy package's internals score the fixture pipeline or build a
single-frame API Input, so each imports these from one place.
"""

from __future__ import annotations

from tests._deploy_helpers import FIXTURE_DIR

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


def _single_frame_api_input_config(
    column_name: str,
    column_type: str = "str",
    *,
    label: str = "quotes",
    **overrides: object,
) -> dict[str, object]:
    """Minimal converged apiInput config with one labelled emitted frame."""
    config: dict[str, object] = {
        "path": "",
        "tables": [
            {
                "path": "$[:]",
                "label": label,
                "emit": True,
                "columns": [
                    {
                        "name": column_name,
                        "path": f"$[:].{column_name}",
                        "type": column_type,
                        "selected": True,
                    }
                ],
            }
        ],
    }
    config.update(overrides)
    return config
