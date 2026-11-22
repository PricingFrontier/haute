"""SRV-07: the destinations an analyst can type that a file cannot have, each with the words
of the rule its refusal names. The optimiser's save, the model save and a Data Output's
Write each refuse every one before anything is written."""

from __future__ import annotations

from pathlib import Path

import pytest

#: (the typed path, its extension left to the save as ``{ext}``; words its refusal holds).
UNPORTABLE_DESTINATIONS = [
    pytest.param("exports/ratebook 10:30{ext}", "has ':' in", id="colon"),
    pytest.param("exports/rate?{ext}", "has '?' in", id="question-mark"),
    pytest.param("exports/rate*{ext}", "has '*' in", id="asterisk"),
    pytest.param("exports/<rate>{ext}", "has '<' '>' in", id="angle-brackets"),
    pytest.param('exports/"rate"{ext}', "has '\"' in", id="double-quotes"),
    pytest.param("exports/rate|book{ext}", "has '|' in", id="pipe"),
    pytest.param("exports/run./rate{ext}", "ends with a dot or a space", id="trailing-dot"),
    pytest.param("exports/run /rate{ext}", "ends with a dot or a space", id="trailing-space"),
    pytest.param("exports/con{ext}", "reserved device name", id="device-name"),
    pytest.param("exports/" + "r" * 300 + "{ext}", "a saved file's name may be", id="long"),
    pytest.param("exports/taken/rate{ext}", "which is a file, not a folder", id="file-folder"),
    pytest.param("exports/" + "f" * 300 + "/rate{ext}", "a folder's name may be", id="long-folder"),
]


def prepare_exports(project_root: Path) -> set[Path]:
    """The project's ``exports/`` folder holding one file, ``taken``, where a path above
    puts a folder; the files then in the project, to compare with after a refusal."""
    exports = project_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "taken").write_text("not a folder")
    return files_in(project_root)


def files_in(project_root: Path) -> set[Path]:
    return {path for path in project_root.rglob("*") if path.is_file()}
