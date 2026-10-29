"""The bash a test runs shell code with, as a CI runner would.

A bare ``bash`` on Windows may be WSL's, which sees neither the Windows paths
nor the Windows git, so tests that run generated or shipped shell code use Git
for Windows' own there.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def posix_shell() -> str:
    """The bash a CI runner would use: Git for Windows' own on Windows."""
    if os.name != "nt":
        return "bash"
    exec_path = subprocess.run(
        ["git", "--exec-path"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(Path(exec_path).parents[2] / "bin" / "bash.exe")
