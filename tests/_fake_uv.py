"""A stand-in ``uv`` for tests, which never reaches a package index.

``pip compile INPUT ... --output-file OUT`` writes each ``name[extras]==version`` root as
a lock line with a digest derived from the pin, a wheel path as ``haute @ file://...``, and
an index option carrying a credential, as a real resolve can; it adds no transitive
dependency. ``--version`` names it. With ``HAUTE_FAKE_UV_FAIL`` set it fails, printing that
message, as a resolve with no wheel for the target does. The lock's first line is a
comment holding the arguments it was given, as JSON (``ARGS_COMMENT``). Tests that need
the real resolver are marked ``index`` and skip the fixture that installs this one
(``tests/conftest.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

#: The comment line that opens every lock the fake writes, holding the arguments it was given.
ARGS_COMMENT = "# fake uv args: "

_ROOT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>\S+)$")


def path_with_fake_uv(directory: Path) -> str:
    """``PATH`` with this fake first, as ``uv``, for a subprocess the test's fixture cannot
    reach (a script run as its own process): an executable shim in *directory* runs it."""
    directory.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    if os.name == "nt":
        (directory / "uv.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        shim = directory / "uv"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        shim.chmod(0o755)
    return os.pathsep.join([str(directory), os.environ.get("PATH", "")])


def main(args: list[str]) -> int:
    if args == ["--version"]:
        print("uv 0.0.0 (fake)")
        return 0
    if args[:2] != ["pip", "compile"]:
        print(f"fake uv: unexpected command {args}", file=sys.stderr)
        return 2
    failure = os.environ.get("HAUTE_FAKE_UV_FAIL")
    if failure:
        print(failure, file=sys.stderr)
        return 1
    output = Path(args[args.index("--output-file") + 1])
    lines = [
        f"{ARGS_COMMENT}{json.dumps(args)}",
        "--index-url https://deploy-bot:s3cret@index.example/simple",
        "",
    ]
    for root in Path(args[2]).read_text(encoding="utf-8").splitlines():
        root = root.strip()
        if not root:
            continue
        if root.endswith(".whl"):
            lines.append(f"haute @ {Path(root).resolve().as_uri()}")
            continue
        match = _ROOT.match(root)
        if match is None:
            print(f"fake uv: cannot pin {root!r}", file=sys.stderr)
            return 2
        pin = f"{match['name']}=={match['version']}"
        digest = hashlib.sha256(pin.encode()).hexdigest()
        lines.append(f"{pin} \\\n    --hash=sha256:{digest}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
