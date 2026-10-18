"""Every frame join in Haute states its row order (PL2-04, specs/execution-engine).

Polars 2's streaming engine keeps a join's input order only when ``maintain_order``
says so. A join Haute builds, and one its assistant's examples teach, therefore names
it: ``"none"`` where nothing reads the order (a count, a table keyed by id, a sort
after it), so a join that leaves the order to Polars is a decision nobody made.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._source_files import source_files

SRC = Path(__file__).resolve().parents[1] / "src" / "haute"
# Keywords only a frame join takes; a string's, a thread's or a process's join takes none.
_FRAME_JOIN_KEYWORDS = frozenset({"on", "left_on", "right_on", "how"})


def unordered_joins(source: str) -> list[int]:
    """The lines of *source* holding a frame join that does not name ``maintain_order``."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
        ):
            continue
        # ``self.join`` is the deploy proof's own method, not a frame's.
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
            continue
        keywords = {keyword.arg for keyword in node.keywords}
        # Unpacked arguments come from a builder that states the order itself.
        if None in keywords or "maintain_order" in keywords:
            continue
        if keywords & _FRAME_JOIN_KEYWORDS:
            lines.append(node.lineno)
    return sorted(lines)


def test_every_frame_join_in_haute_states_its_order() -> None:
    reports = [
        f"src/haute/{path.relative_to(SRC).as_posix()}:{line}"
        for path in source_files(SRC)
        for line in unordered_joins(path.read_text(encoding="utf-8"))
    ]

    assert reports == [], "A join leaves its row order to Polars:\n" + "\n".join(reports)


def test_the_scan_finds_a_frame_join_without_an_order() -> None:
    source = "\n".join(
        [
            'a.join(b, on="id", how="left")',
            'a.join(b, on="id", how="left", maintain_order="left")',
            "a.join(b, **kwargs)",
            '", ".join(names)',
            "thread.join(timeout=2.0)",
            'self.join(node, how="left")',
            "a.join(b, left_on=['x'], right_on=['y'])",
        ]
    )

    assert unordered_joins(source) == [1, 7]
