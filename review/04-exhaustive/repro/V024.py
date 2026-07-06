"""Isolated reproduction for V024.

Claim: ``haute._git.get_status`` parses ``git status --porcelain`` with the
slice ``line[3:].strip().strip('"')`` (``_git.py:382-388``). This is wrong for
two porcelain shapes:

  1. RENAME: git emits a single line ``R  old.py -> new.py``. The slice yields
     the literal ``old.py -> new.py`` as ONE ``changed_files`` entry instead of
     the new path ``new.py``. The status panel then shows a nonsensical
     pseudo-filename for every rename, and any consumer treating entries as
     real paths breaks.

  2. C-QUOTED PATHS: git quotes and C-escapes paths containing non-ASCII or
     special bytes, e.g. ``"na\\303\\257ve.py"`` for ``naïve.py``.
     ``.strip('"')`` removes only the outer quotes and leaves the embedded
     octal escapes intact, so the entry is ``na\\303\\257ve.py`` rather than
     the real on-disk path ``naïve.py``.

This repro is fully isolated:
  * the only disk I/O is a real git repo created under
    ``tempfile.TemporaryDirectory``;
  * NO real project files (rating/, src/, tests/) are read or written;
  * ``get_status`` is called with ``cwd=<tmp repo>`` — it never touches the
    haute project root, so ``_sandbox.set_project_root`` is unnecessary.

It asserts on the SPECIFIC wrong VALUES (expected vs actual), not merely that
"something raised".
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from haute._git import get_status


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    # core.quotepath defaults to true; leave it so we exercise the real
    # quoting/escaping git uses for non-ASCII paths in the wild.
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    # Commit an initial file so we have history to rename against. Use a
    # non-protected feature branch so get_status does not short-circuit and
    # does not try to fetch (no remote configured anyway).
    (repo / "a.py").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-q", "-b", "pricing/t/feat")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)

        # --- Scenario 1: a staged RENAME a.py -> b.py.
        _git(repo, "mv", "a.py", "b.py")

        # --- Scenario 2: a new file with a non-ASCII name that git C-quotes.
        naive_name = "naïve.py"  # naïve.py
        (repo / naive_name).write_text("x\n", encoding="utf-8")
        _git(repo, "add", naive_name)

        # Show the raw porcelain we are parsing (diagnostic only).
        porcelain = _git(repo, "status", "--porcelain")
        print("---- raw `git status --porcelain` ----")
        print(porcelain)
        print("--------------------------------------")

        status = get_status(repo)
        changed = status.changed_files
        print(f"changed_files = {changed!r}")

        # ===== Assertions on the RENAME entry =====
        # The bug: the arrow form is kept verbatim as one entry.
        mangled_rename = "a.py -> b.py"
        assert mangled_rename in changed, (
            "BUG NOT REPRODUCED: expected the mangled rename entry "
            f"{mangled_rename!r} to be present (proving the arrow form is kept "
            f"verbatim); changed_files={changed!r}"
        )
        # And the CORRECT value (the new path on its own) is absent.
        assert "b.py" not in changed, (
            "BUG NOT REPRODUCED: 'b.py' is present, so the rename WAS parsed "
            f"to the new path correctly; changed_files={changed!r}"
        )
        print(
            "[1] CONFIRMED wrong value: rename produced "
            f"{mangled_rename!r} as a single entry instead of 'b.py'."
        )

        # ===== Assertions on the C-QUOTED non-ASCII entry =====
        # The file that actually exists on disk:
        assert (repo / naive_name).is_file()
        # The bug: the entry still carries git's octal C-escapes and is NOT the
        # real path. We assert the precise wrong string git produced.
        escaped_entry = "na\\303\\257ve.py"  # literal backslash-3-0-3 ... etc.
        assert escaped_entry in changed, (
            "BUG NOT REPRODUCED: expected the octal-escaped entry "
            f"{escaped_entry!r} (proving .strip('\"') left C-escapes intact); "
            f"changed_files={changed!r}"
        )
        # And the CORRECT, usable path is absent.
        assert naive_name not in changed, (
            "BUG NOT REPRODUCED: the real decoded path "
            f"{naive_name!r} is present, so C-unescaping happened; "
            f"changed_files={changed!r}"
        )
        # Critically: no entry in changed_files is a path that exists on disk
        # for the renamed/escaped files — proving a downstream consumer that
        # opens these entries would fail.
        for entry in changed:
            exists = (repo / entry).exists()
            print(f"    entry {entry!r} exists_on_disk={exists}")
        assert not (repo / mangled_rename).exists()
        assert not (repo / escaped_entry).exists()
        print(
            "[2] CONFIRMED wrong value: non-ASCII path kept octal escapes "
            f"({escaped_entry!r}) instead of the real on-disk path "
            f"{naive_name!r}."
        )

        print("\nV024 REPRODUCED: get_status mangles renames and C-quoted paths.")


if __name__ == "__main__":
    main()
