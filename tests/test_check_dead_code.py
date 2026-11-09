"""The dead-code gate reports what nothing calls, and keeps its allowlist current."""

from __future__ import annotations

from pathlib import Path

from scripts import check_dead_code as gate


def _package(root: Path) -> None:
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(
        "def used() -> int:\n"
        "    return 1\n"
        "\n"
        "\n"
        "def unused() -> int:\n"
        "    return used()\n"
        "\n"
        "\n"
        "class Renderer:\n"
        "    def render(self, kind: str) -> str:\n"
        "        return getattr(self, f'_render_{kind}')()\n"
        "\n"
        "    def _render_plain(self) -> str:\n"
        "        return 'plain'\n"
        "\n"
        "\n"
        "Renderer().render('plain')\n",
        encoding="utf-8",
    )


def test_an_unreferenced_function_is_reported_with_its_line(tmp_path: Path) -> None:
    _package(tmp_path)

    found = gate.findings(root=tmp_path, paths=("pkg",), exclude=(), allowlist={})

    assert found == ["pkg/mod.py:5: unused function 'unused'"]


def test_an_allowlisted_function_passes(tmp_path: Path) -> None:
    _package(tmp_path)

    found = gate.findings(
        root=tmp_path, paths=("pkg",), exclude=(), allowlist={"pkg/mod.py::unused": "why"}
    )

    assert found == []


def test_an_allowlist_entry_nothing_reports_is_stale(tmp_path: Path) -> None:
    _package(tmp_path)

    found = gate.findings(
        root=tmp_path,
        paths=("pkg",),
        exclude=(),
        allowlist={"pkg/mod.py::unused": "why", "pkg/mod.py::used": "why"},
    )

    assert found == ["pkg/mod.py::used: allowlisted but no longer reported; remove it"]
