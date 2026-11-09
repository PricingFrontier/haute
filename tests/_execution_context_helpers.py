"""The memory-environment reset shared by the execution-context suites.

Several modules that test admission and ``ExecutionContext`` start from the default memory policy,
so each imports this reset from one place.
"""

import pytest

from haute._execution_context import (
    ExecutionProfile,
)
from tests._process_state import clear_execution_admission_reservations


def _clear_execution_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove memory-budget env vars so tests exercise default policy."""
    from haute import _execution_admission as admission_mod

    for profile in admission_mod._PROFILE_MEMORY_ENV:
        for key, _multiplier in admission_mod._memory_env_candidates(profile):
            monkeypatch.delenv(key, raising=False)
    for profile in ExecutionProfile:
        for key, _multiplier in admission_mod._process_rss_env_candidates(profile):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("HAUTE_EXECUTION_MEMORY_POLICY", raising=False)
    clear_execution_admission_reservations()
