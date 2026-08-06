"""Databricks Apps entry point for haute.

The trust adaptation lives in haute itself (haute.hosted — see
specs/hosted-databricks-app/high-level.md); this file is only the
container glue: one-shot boot diagnostics, first-boot project seeding,
and the uvicorn invocation on the platform-assigned port.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _refresh_vendored_wheel() -> None:
    """Guarantee the running haute matches the vendored wheel.

    Two platform behaviours conspire to keep a stale haute installed
    across deployments (see LEARNINGS.md): dependency reinstalls are
    keyed on the TEXT of requirements.txt, and even when pip runs it
    skips a same-version wheel as "already satisfied" in the persistent
    app venv. A forced no-deps reinstall of the single wheel is cheap
    (seconds) and makes the deployed code trustworthy; the follow-up
    plain install resolves any newly added dependencies loudly.
    """
    wheels = sorted(Path(__file__).parent.glob("haute-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one vendored haute wheel, found {wheels}")
    wheel = str(wheels[0])
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
    subprocess.run([*pip, "install", "--force-reinstall", "--no-deps", wheel], check=True)
    subprocess.run([*pip, "install", f"{wheel}[databricks]"], check=True)


if __name__ == "__main__":
    _refresh_vendored_wheel()

    import bootstrap
    import probe
    import uvicorn

    from haute.hosted import create_app

    probe.run()
    bootstrap.ensure_project()
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")),
    )
