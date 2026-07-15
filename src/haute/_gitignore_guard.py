"""Guard entries haute asserts into a project's ``.gitignore``.

Two sites write these entries and must never drift: ``haute init`` (project
scaffolding) and the unborn-repo seed in :func:`haute._git.set_working_branch`
(which stages the whole working tree for the root commit and therefore cannot
trust an ambient ``.gitignore`` the user may never have written).

NB: ``<pipeline>.haute.json`` is STABLE-LAYER (node positions etc.) and MUST
stay tracked — it rides the save ledger.  Never add ``*.haute.json`` here.
The per-clone ``.haute/`` state directory is the untracked part: committing
one clone's ``state.json`` locks out every other clone.
"""

from __future__ import annotations

from pathlib import Path

GITIGNORE_GUARD_ENTRIES: tuple[str, ...] = (
    ".env",
    ".haute/",
    "impact_report.md",
    ".haute_cache/",
    "mlruns/",
    "data/",
    # Without this, .venv/ contents would pass the seed's *.py allowlist
    # pathspec — the deny gate has to carry it.
    ".venv/",
)


def ensure_gitignore_guards(project_dir: Path) -> list[str]:
    """Ensure every guard entry is present in ``project_dir/.gitignore``.

    Appends the missing entries (creating the file when absent) and returns
    them; an empty list means the file already carried the full set and was
    left byte-identical.
    """
    gitignore_path = project_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("\n".join(GITIGNORE_GUARD_ENTRIES) + "\n", encoding="utf-8")
        return list(GITIGNORE_GUARD_ENTRIES)

    # User-authored files may carry non-UTF-8 bytes; replace instead of raising
    # (same posture as haute._io.read_user_text, inlined to keep this module
    # free of the polars import chain).
    existing = set(gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines())
    missing = [entry for entry in GITIGNORE_GUARD_ENTRIES if entry not in existing]
    if missing:
        with open(gitignore_path, "a", encoding="utf-8") as fh:
            fh.write("\n# Haute\n" + "\n".join(missing) + "\n")
    return missing
