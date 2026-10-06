from __future__ import annotations

from pathlib import Path

from tests._source_files import source_files


def test_production_files_do_not_import_exec_user_code_from_executor() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "haute"
    offenders: list[str] = []

    for path in source_files(src_root):
        if "from haute.executor import _exec_user_code" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(src_root)))

    assert offenders == []


def test_executor_does_not_reexport_exec_user_code() -> None:
    import haute.executor as executor

    assert not hasattr(executor, "_exec_user_code")
