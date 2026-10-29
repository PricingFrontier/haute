"""Databricks Apps entry point for haute.

The trust adaptation lives in haute itself (haute.hosted — see
specs/hosted-databricks-app/high-level.md); this file is only the
container glue: the locked runtime install, one-shot boot diagnostics,
first-boot project seeding, and the uvicorn invocation on the
platform-assigned port.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

#: The build's lock of haute's runtime and the vendored wheel's recorded digest,
#: both written by scripts/build_bundle.sh beside the wheel.
LOCK_FILE = "requirements.txt"
WHEEL_DIGEST_FILE = "wheel.sha256"


def _install_locked_runtime(bundle: Path) -> None:
    """Install the bundle's lock, then its checked haute wheel, resolving nothing.

    The lock pins every distribution of haute's runtime by version and hash, so
    ``--require-hashes --no-deps`` installs exactly those files: two boots of one
    deployment run the same dependencies, and nothing is resolved from haute's
    ranges at boot. The platform has installed the lock at deploy already; this
    install keeps a persistent app venv on it. The wheel installs after it with
    ``--no-deps``, its dependencies being the lock's, and with
    ``--force-reinstall``: dependency reinstalls are keyed on the TEXT of
    requirements.txt, and pip skips a same-version wheel as "already satisfied"
    in the persistent venv (see LEARNINGS.md), so without it a rebuilt wheel
    would leave the old code running.
    """
    wheel = _checked_wheel(bundle)
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", "--no-deps"]
    subprocess.run([*pip, "--require-hashes", "-r", str(bundle / LOCK_FILE)], check=True)
    subprocess.run([*pip, "--force-reinstall", str(wheel)], check=True)


def _checked_wheel(bundle: Path) -> Path:
    """The bundle's one haute wheel, refused unless it is the one the build recorded."""
    wheels = sorted(bundle.glob("haute-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one vendored haute wheel, found {wheels}")
    wheel = wheels[0]
    recorded = (bundle / WHEEL_DIGEST_FILE).read_text(encoding="utf-8").split()
    if recorded != [hashlib.sha256(wheel.read_bytes()).hexdigest(), wheel.name]:
        raise RuntimeError(
            f"{wheel.name} does not match {WHEEL_DIGEST_FILE} ({' '.join(recorded)}): "
            "rebuild the bundle with scripts/build_bundle.sh and deploy it again."
        )
    return wheel


if __name__ == "__main__":
    _install_locked_runtime(Path(__file__).parent)

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
