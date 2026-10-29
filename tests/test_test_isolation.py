"""Test orders that once leaked state from one module into a later one.

Under other xdist schedulers (``--dist loadfile``, ``-n 8``) the CI study put
each pair below on one worker in this order, and the second test failed for a
reason outside the code it tests. Each case runs its pair in one child pytest
process, in that order, so the leak cannot hide behind the usual distribution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._source_files import REPO_ROOT

_ORDERS = {
    # The fallback test reloaded ``haute`` while its patch of
    # ``importlib.metadata.version`` was still in place.
    "package-version": (
        "tests/test_small_module_contracts.py::TestPackageInit"
        "::test_version_falls_back_in_editable_dev_context",
        "tests/test_infrastructure_contracts.py::TestApiRouteContracts"
        "::test_openapi_and_installed_package_versions_match_pyproject",
    ),
    # A test's monkeypatch of the port probe, undone after the module's own
    # patch of it, restored an always-free probe.
    "port-probe": (
        "tests/test_host_binding.py::test_direct_handler_rejects_non_loopback_before_startup",
        "tests/test_cli_fail_loudly.py::TestServePortConflictDetection"
        "::test_serve_fails_loudly_when_port_is_bound",
    ),
}


def _child_environment() -> dict[str, str]:
    """This environment without the session state of this pytest run."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "HAUTE_TEST_"))
    }


@pytest.mark.timeout(180)
@pytest.mark.parametrize(("first", "second"), _ORDERS.values(), ids=_ORDERS)
def test_the_earlier_test_leaves_nothing_the_later_one_reads(
    first: str, second: str, tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            first,
            second,
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'basetemp'}",
        ],
        cwd=REPO_ROOT,
        env=_child_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=150,
    )

    # Exit 0 needs both node ids to resolve and pass; a renamed test exits 4.
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
