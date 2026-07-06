"""Isolated reproduction for V005.

Claim: In the no-haute.toml branch of ``handle_deploy``, the pipeline_file
override (``_deploy.py:118-124``) puts the RAW CLI argument back, clobbering
the path already resolved by ``resolve_pipeline_file``. When the argument is a
DIRECTORY (a documented/supported form), the override replaces the discovered
*file* with the *directory*, so ``resolve_config`` -> ``parse_pipeline_file``
-> ``read_user_text`` -> ``Path(<dir>).read_text()`` raises, surfaced as a
confusing "Resolution failed" SystemExit.

Contrast: ``haute run ./dir`` / ``haute lint ./dir`` use the ``resolved`` value
directly and work.

This repro is fully isolated:
  * all disk I/O is under a tempfile.TemporaryDirectory
  * NO real project files (rating/, src/, tests/) are read or written
  * cwd is moved into the temp dir so the *implicit* Path.cwd()/haute.toml
    check in handle_deploy sees the synthetic sandbox (which has no haute.toml)

It asserts on the SPECIFIC wrong behaviour:
  1. resolve_pipeline_file(<dir>) returns the discovered FILE (sanity).
  2. After the override step, deploy_config.pipeline_file is wrongly the
     DIRECTORY (the demonstrably wrong value).
  3. handle_deploy(... ./dir ..., dry_run=True) raises SystemExit with a
     "Resolution failed" message caused by the directory read — even though
     the equivalent ``haute run`` path succeeds.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click.testing

PIPELINE_SRC = '''\
import haute

pipeline = haute.Pipeline()


@pipeline.api_input(path="quote")
def quote():
    import polars as pl

    return pl.DataFrame({"premium": [1.0, 2.0, 3.0]})


@pipeline.transform
def out(quote):
    return quote
'''


def _write_sandbox(tmp: Path) -> Path:
    """Create a synthetic project dir with a discoverable pipeline (no haute.toml)."""
    proj = tmp / "somedir"
    proj.mkdir()
    # Use main.py so _resolve_default_in tier-2 (main.py convention) picks it
    # deterministically regardless of discovery glob ordering.
    (proj / "main.py").write_text(PIPELINE_SRC, encoding="utf-8")
    return proj


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj_dir = _write_sandbox(tmp)

        # Move cwd into the sandbox so handle_deploy's implicit
        # ``Path.cwd() / "haute.toml"`` check resolves inside the temp dir
        # (which has no haute.toml) -> forces the no-toml branch.
        old_cwd = Path.cwd()
        os.chdir(tmp)
        try:
            from haute._project import resolve_pipeline_file
            from haute.deploy._config import DeployConfig

            # --- Step 1: resolver correctly discovers the FILE inside the dir.
            dir_arg = Path("./somedir")
            resolved = resolve_pipeline_file(dir_arg)
            assert resolved.is_file(), f"expected a file, got {resolved}"
            assert resolved.name == "main.py", resolved
            print(f"[1] resolve_pipeline_file('./somedir') -> {resolved}  (a FILE, correct)")

            # --- Step 2: replicate the exact handle_deploy override sequence.
            deploy_config = DeployConfig.from_cli_args(
                pipeline_file=resolved,
                model_name=resolved.stem,
            )
            assert deploy_config.pipeline_file == resolved, (
                "from_cli_args should hold the resolved file path"
            )
            print(f"[2a] after from_cli_args, pipeline_file = {deploy_config.pipeline_file}  (FILE)")

            # The buggy override: config.pipeline_file (the RAW dir arg) is truthy.
            overrides: dict[str, object] = {}
            raw_cli_pipeline_file = "./somedir"  # what DeployCliConfig.pipeline_file holds
            if raw_cli_pipeline_file:
                overrides["pipeline_file"] = Path(raw_cli_pipeline_file)
            deploy_config = deploy_config.override(**overrides)

            clobbered = deploy_config.pipeline_file
            print(f"[2b] after override(...), pipeline_file = {clobbered}  (should still be the FILE)")

            # The demonstrably WRONG value: pipeline_file is now the DIRECTORY.
            assert Path(clobbered).is_dir(), (
                "BUG NOT REPRODUCED: override did not clobber the resolved file "
                f"with the directory; pipeline_file={clobbered!r}"
            )
            assert Path(clobbered).name == "somedir", clobbered
            print(
                "[2c] CONFIRMED wrong value: override clobbered the resolved FILE "
                f"with the DIRECTORY ({clobbered})."
            )

            # --- Step 3: end-to-end handle_deploy reproduces the failure.
            from haute.cli._deploy import deploy as deploy_cmd

            runner = click.testing.CliRunner()
            # dry-run avoids the CI gate and any real deployment.
            result = runner.invoke(deploy_cmd, ["./somedir", "--dry-run"])

            print(f"[3] `haute deploy ./somedir --dry-run` exit_code = {result.exit_code}")
            combined = result.output

            def _safe(s: str) -> str:
                # The Windows console is cp1252; deploy output contains U+2717
                # etc. Strip to ASCII for printing only — assertions below run
                # against the original ``combined`` string.
                return s.encode("ascii", "replace").decode("ascii")

            print("---- deploy output (ascii-safe) ----")
            print(_safe(combined).rstrip())
            print("------------------------------------")

            assert result.exit_code == 1, (
                f"expected SystemExit(1) from the clobbered directory read, "
                f"got exit_code={result.exit_code}"
            )
            assert "Resolution failed" in combined, (
                "expected a 'Resolution failed' message caused by reading the "
                f"directory as a file; output was:\n{combined}"
            )
            # The underlying cause is a directory being read as a file.
            assert (
                "Is a directory" in combined
                or "IsADirectoryError" in combined
                or "Permission" in combined  # Windows surfaces PermissionError
                or "directory" in combined.lower()
            ), f"expected directory-read error detail in:\n{combined}"
            print(
                "[3] CONFIRMED: `haute deploy ./somedir` fails with 'Resolution "
                "failed' due to the clobbered directory path."
            )

            # --- Control: prove the resolved-path config does NOT hit the
            # directory read (i.e. the failure is specifically the override's
            # fault, not an inherent problem with directory args). We build a
            # config from the RESOLVED file and confirm resolve_config gets
            # past the parse step without an IsADirectory/Permission error.
            from haute.deploy._config import resolve_config

            good_config = DeployConfig.from_cli_args(
                pipeline_file=resolved,
                model_name=resolved.stem,
            )
            try:
                resolve_config(good_config)
                control_ok = True
                control_err = ""
            except Exception as exc:  # noqa: BLE001 - control diagnostic
                control_ok = False
                control_err = f"{type(exc).__name__}: {exc}"
            # The resolved-file path must NOT fail with a directory-read error.
            assert "IsADirectory" not in control_err and "Is a directory" not in control_err, (
                f"control unexpectedly hit a directory read: {control_err}"
            )
            print(
                _safe(
                    f"[control] resolve_config(<resolved FILE>) ok={control_ok} "
                    f"err={control_err or 'none'} (no directory-read error -> "
                    "the bug is the override, not directory args)."
                )
            )

            print("\nV005 REPRODUCED: override clobbers resolved dir->file path.")
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    main()
