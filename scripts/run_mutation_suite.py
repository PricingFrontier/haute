"""Run bounded Cosmic Ray mutation suites with explicit gate metadata."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COSMIC_RAY_PACKAGE = "cosmic-ray==8.4.6"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_CONFIG = "mutation/targets.json"
PYTHON_PLACEHOLDER = "__HAUTE_PYTHON__"


@dataclass(frozen=True)
class MutationTargetSpec:
    name: str
    config_path: Path
    fail_over: float
    rationale: str


@dataclass(frozen=True)
class MutationTarget:
    name: str
    config_path: Path
    module_path: Path
    test_paths: tuple[Path, ...]
    fail_over: float
    rationale: str


@dataclass(frozen=True)
class MutationStageResult:
    stage: str
    returncode: int
    stdout: str
    stderr: str


def _uvx_command(*args: str) -> list[str]:
    return ["uvx", "--from", COSMIC_RAY_PACKAGE, *args]


def _run_command(
    args: list[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return result


def _relative_path(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _resolve_repo_path(raw_path: str, *, base: Path = REPO_ROOT) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _active_python_executable() -> str:
    if not sys.executable:
        raise SystemExit("Cannot materialize mutation config because sys.executable is empty.")
    path = Path(sys.executable)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute().as_posix()


def _safe_target_name(config_path: Path) -> str:
    return config_path.stem.replace(".", "-")


def _materialize_config(template_path: Path, target_dir: Path) -> Path:
    text = template_path.read_text(encoding="utf-8")

    module_match = re.search(r'^module-path = "(?P<path>[^"]+)"$', text, flags=re.MULTILINE)
    if module_match is None:
        raise SystemExit(f"Config file is missing module-path: {template_path}")
    module_path = _resolve_repo_path(module_match.group("path"))
    escaped_module = json.dumps(module_path.as_posix())[1:-1]
    text = re.sub(
        r'^module-path = "(?P<path>[^"]+)"$',
        f'module-path = "{escaped_module}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )

    command_match = re.search(r'^test-command = "(?P<command>.*)"$', text, flags=re.MULTILINE)
    if command_match is None:
        raise SystemExit(f"Config file is missing test-command: {template_path}")
    if PYTHON_PLACEHOLDER not in command_match.group("command"):
        raise SystemExit(f"Config file is missing {PYTHON_PLACEHOLDER}: {template_path}")
    python_executable = f'"{_active_python_executable()}"'
    command = command_match.group("command").replace(PYTHON_PLACEHOLDER, python_executable)
    escaped_command = json.dumps(command)[1:-1]
    text = re.sub(
        r'^test-command = "(?P<command>.*)"$',
        f'test-command = "{escaped_command}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )

    materialized = target_dir / "cosmic-ray.toml"
    materialized.write_text(text, encoding="utf-8")
    return materialized


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Invalid TOML in {path}: {exc}") from exc


def _extract_test_paths(test_command: str) -> tuple[Path, ...]:
    try:
        tokens = shlex.split(test_command)
    except ValueError as exc:
        raise SystemExit(f"Could not parse test-command {test_command!r}: {exc}") from exc

    test_paths: set[Path] = set()
    for token in tokens:
        normalized = token.replace("\\", "/")
        if normalized.startswith("tests/") or normalized == "tests":
            test_paths.add(_resolve_repo_path(normalized))
    return tuple(sorted(test_paths, key=_relative_path))


def _load_target_specs(target_config: Path) -> list[MutationTargetSpec]:
    if not target_config.exists():
        raise SystemExit(f"Missing mutation target config: {target_config}")
    try:
        payload = json.loads(target_config.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {target_config}: {exc}") from exc

    if payload.get("schema_version") != 1:
        raise SystemExit(f"Mutation target config must use schema_version 1: {target_config}")
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise SystemExit(
            f"Mutation target config must define a non-empty targets list: {target_config}"
        )

    specs: list[MutationTargetSpec] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict):
            raise SystemExit(f"Mutation target entry {index} must be an object: {target_config}")

        name = raw_target.get("name")
        config = raw_target.get("config")
        fail_over = raw_target.get("max_survival_rate")
        rationale = raw_target.get("rationale")
        if not isinstance(name, str) or not name:
            raise SystemExit(f"Mutation target entry {index} must define name: {target_config}")
        if not isinstance(config, str) or not config:
            raise SystemExit(f"Mutation target {name} must define config: {target_config}")
        if not isinstance(fail_over, int | float):
            raise SystemExit(f"Mutation target {name} max_survival_rate must be numeric")
        if fail_over < 0 or fail_over > 100:
            raise SystemExit(f"Mutation target {name} max_survival_rate must be between 0 and 100")
        if not isinstance(rationale, str) or not rationale:
            raise SystemExit(f"Mutation target {name} must define rationale: {target_config}")

        config_path = _resolve_repo_path(config)
        if not config_path.exists():
            raise SystemExit(f"Mutation target {name} config does not exist: {config}")
        specs.append(
            MutationTargetSpec(
                name=name,
                config_path=config_path,
                fail_over=float(fail_over),
                rationale=rationale,
            )
        )

    names = [spec.name for spec in specs]
    configs = [spec.config_path for spec in specs]
    if len(set(names)) != len(names):
        raise SystemExit(f"Mutation target names must be unique: {', '.join(names)}")
    if len(set(configs)) != len(configs):
        raise SystemExit("Mutation target configs must be unique.")
    return specs


def _select_target_specs(
    specs: list[MutationTargetSpec],
    *,
    explicit_configs: list[str],
) -> list[MutationTargetSpec]:
    if not explicit_configs:
        return specs

    specs_by_config = {spec.config_path: spec for spec in specs}
    selected_specs: list[MutationTargetSpec] = []
    for raw_config in explicit_configs:
        config_path = _resolve_repo_path(raw_config)
        if config_path not in specs_by_config:
            raise SystemExit(
                f"Mutation config is not declared in {DEFAULT_TARGET_CONFIG}: {raw_config}"
            )
        selected_specs.append(specs_by_config[config_path])
    return selected_specs


def _load_target(
    spec: MutationTargetSpec,
    *,
    fail_over_override: float | None,
) -> MutationTarget:
    payload = _load_toml(spec.config_path)
    cosmic_ray = payload.get("cosmic-ray")
    if not isinstance(cosmic_ray, dict):
        raise SystemExit(f"Config file is missing [cosmic-ray]: {spec.config_path}")

    module_raw = cosmic_ray.get("module-path")
    test_command = cosmic_ray.get("test-command")
    if not isinstance(module_raw, str):
        raise SystemExit(f"Config file is missing module-path: {spec.config_path}")
    if not isinstance(test_command, str):
        raise SystemExit(f"Config file is missing test-command: {spec.config_path}")
    if PYTHON_PLACEHOLDER not in test_command:
        raise SystemExit(f"Config file is missing {PYTHON_PLACEHOLDER}: {spec.config_path}")

    fail_over = spec.fail_over
    if fail_over_override is not None:
        if fail_over_override < 0 or fail_over_override > 100:
            raise SystemExit("Mutation fail-over override must be between 0 and 100.")
        if fail_over_override > spec.fail_over:
            raise SystemExit(
                f"Mutation fail-over override {fail_over_override:g} would loosen "
                f"{spec.name}'s checked-in threshold of {spec.fail_over:g}."
            )
        fail_over = fail_over_override

    return MutationTarget(
        name=spec.name,
        config_path=spec.config_path,
        module_path=_resolve_repo_path(module_raw),
        test_paths=_extract_test_paths(test_command),
        fail_over=fail_over,
        rationale=spec.rationale,
    )


def _load_targets(
    explicit_configs: list[str],
    *,
    target_config: Path,
    fail_over_override: float | None,
) -> list[MutationTarget]:
    specs = _select_target_specs(
        _load_target_specs(target_config),
        explicit_configs=explicit_configs,
    )
    return [
        _load_target(
            spec,
            fail_over_override=fail_over_override,
        )
        for spec in specs
    ]


def _path_matches(candidate: Path, changed_path: Path) -> bool:
    candidate = candidate.resolve()
    changed_path = changed_path.resolve()
    if candidate == changed_path:
        return True
    if candidate.is_dir():
        try:
            changed_path.relative_to(candidate)
        except ValueError:
            return False
        return True
    return False


def _select_targets_for_changed_files(
    targets: list[MutationTarget],
    changed_files: list[str],
) -> list[MutationTarget]:
    if not changed_files:
        return targets

    normalized_changed = [_resolve_repo_path(path) for path in changed_files if path.strip()]
    if not normalized_changed:
        return targets

    global_gate_paths = {
        (REPO_ROOT / ".github" / "workflows" / "mutation.yml").resolve(),
        (REPO_ROOT / "mutation" / "README.md").resolve(),
        (REPO_ROOT / DEFAULT_TARGET_CONFIG).resolve(),
    }
    if any(path in global_gate_paths for path in normalized_changed):
        return targets

    selected: list[MutationTarget] = []
    for target in targets:
        owned_paths = [target.config_path, target.module_path, *target.test_paths]
        target_matches = False
        for owned_path in owned_paths:
            for changed_path in normalized_changed:
                if _path_matches(owned_path, changed_path):
                    target_matches = True
        if target_matches:
            selected.append(target)

    return selected


def _target_summary(target: MutationTarget) -> dict[str, object]:
    return {
        "name": target.name,
        "config": _relative_path(target.config_path),
        "module": _relative_path(target.module_path),
        "tests": [_relative_path(path) for path in target.test_paths],
        "fail_over": target.fail_over,
        "rationale": target.rationale,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_manifest(
    *,
    output_dir: Path,
    run_id: str,
    mode: str,
    selected_targets: list[MutationTarget],
    all_targets: list[MutationTarget],
    changed_files: list[str],
    dry_run: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "mode": mode,
        "dry_run": dry_run,
        "changed_files": [Path(path).as_posix() for path in changed_files],
        "selected_targets": [_target_summary(target) for target in selected_targets],
        "all_targets": [_target_summary(target) for target in all_targets],
    }
    _write_json(output_dir / "manifest.json", manifest)


def _print_target_list(targets: list[MutationTarget]) -> None:
    for target in targets:
        print(
            f"{target.name}\t{_relative_path(target.config_path)}\t"
            f"{_relative_path(target.module_path)}\tfail-over={target.fail_over:g}"
        )


def _parse_survival_rate(rate_path: Path) -> float | None:
    if not rate_path.exists():
        return None
    match = re.search(
        r"(?P<rate>\d+(?:\.\d+)?)",
        rate_path.read_text(encoding="utf-8", errors="replace"),
    )
    if match is None:
        return None
    return float(match.group("rate"))


def _run_stage(
    stage: str,
    args: list[str],
    *,
    target_dir: Path,
    stdout_name: str | None = None,
    stderr_name: str | None = None,
) -> MutationStageResult:
    stdout_path = target_dir / (stdout_name or f"{stage}.stdout.txt")
    stderr_path = target_dir / (stderr_name or f"{stage}.stderr.txt")
    result = _run_command(args, stdout_path=stdout_path, stderr_path=stderr_path)
    return MutationStageResult(
        stage=stage,
        returncode=result.returncode,
        stdout=stdout_path.relative_to(target_dir).as_posix(),
        stderr=stderr_path.relative_to(target_dir).as_posix(),
    )


def _write_summary_markdown(summary: dict[str, object], path: Path) -> None:
    results = summary["results"]
    assert isinstance(results, list)
    lines = [
        "# Mutation Summary",
        "",
        f"- Run id: `{summary['run_id']}`",
        "",
        "| Target | Status | Survival | Threshold | Failures |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for result in results:
        assert isinstance(result, dict)
        survival = result.get("survival_rate")
        fail_over = result.get("fail_over")
        failures = result.get("failures")
        assert isinstance(failures, list)
        survival_text = "n/a" if survival is None else f"{survival:.2f}%"
        fail_over_text = "n/a" if fail_over is None else f"{fail_over:.2f}%"
        failures_text = "<br>".join(str(item) for item in failures) or "-"
        lines.append(
            f"| `{result['name']}` | {result['status']} | {survival_text} | "
            f"{fail_over_text} | {failures_text} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_run_summary(output_dir: Path, run_id: str, results: list[dict[str, object]]) -> None:
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "status": "failed" if any(result["status"] == "failed" for result in results) else "passed",
        "results": results,
    }
    _write_json(output_dir / "mutation-summary.json", summary)
    _write_summary_markdown(summary, output_dir / "mutation-summary.md")


def _format_optional_rate(value: object) -> str:
    if isinstance(value, int | float):
        return f"{float(value):.2f}%"
    return "n/a"


def _print_result_summary(results: list[dict[str, object]]) -> None:
    for result in results:
        name = result["name"]
        status = result["status"]
        survival = _format_optional_rate(result.get("survival_rate"))
        threshold = _format_optional_rate(result.get("fail_over"))
        print(f"[mutation] {name} {status} survival={survival} threshold={threshold}")

        failures = result.get("failures")
        if isinstance(failures, list):
            for failure in failures:
                print(f"[mutation]   failure: {failure}")

        stages = result.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, dict):
                    continue
                returncode = stage.get("returncode")
                if returncode == 0:
                    continue
                print(
                    f"[mutation]   stage {stage.get('stage')} exited with {returncode} "
                    f"stdout={stage.get('stdout')} stderr={stage.get('stderr')}"
                )


def _run_target(target: MutationTarget, output_dir: Path) -> dict[str, object]:
    target_dir = output_dir / _safe_target_name(target.config_path)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    runtime_config = _materialize_config(target.config_path, target_dir)

    baseline_session = target_dir / "baseline.sqlite"
    session_file = target_dir / "session.sqlite"
    stages: list[MutationStageResult] = []
    failures: list[str] = []

    critical_stages = [
        (
            "baseline",
            _uvx_command(
                "cosmic-ray",
                "baseline",
                str(runtime_config),
                "--session-file",
                str(baseline_session),
            ),
            "baseline.stdout.txt",
            "baseline.stderr.txt",
        ),
        (
            "init",
            _uvx_command("cosmic-ray", "init", str(runtime_config), str(session_file)),
            "init.stdout.txt",
            "init.stderr.txt",
        ),
        (
            "filter-pragma",
            _uvx_command("cr-filter-pragma", str(session_file)),
            "filter-pragma.stdout.txt",
            "filter-pragma.stderr.txt",
        ),
        (
            "exec",
            _uvx_command("cosmic-ray", "exec", str(runtime_config), str(session_file)),
            "exec.stdout.txt",
            "exec.stderr.txt",
        ),
        (
            "report",
            _uvx_command("cr-report", str(session_file)),
            "report.txt",
            "report.stderr.txt",
        ),
        (
            "rate",
            _uvx_command("cr-rate", str(session_file), "--estimate"),
            "rate.txt",
            "rate.stderr.txt",
        ),
        (
            "html",
            _uvx_command("cr-html", str(session_file), "--only-completed", "--skip-success"),
            "report.html",
            "report-html.stderr.txt",
        ),
    ]

    for stage, command, stdout_name, stderr_name in critical_stages:
        print(f"[mutation] {stage} {target.config_path.name}")
        result = _run_stage(
            stage,
            command,
            target_dir=target_dir,
            stdout_name=stdout_name,
            stderr_name=stderr_name,
        )
        stages.append(result)
        if result.returncode != 0:
            failures.append(f"{stage} exited with code {result.returncode}")
            if stage in {"baseline", "init", "filter-pragma", "exec"}:
                break

    if not failures:
        dump_result = _run_stage(
            "dump",
            _uvx_command("cosmic-ray", "dump", str(session_file)),
            target_dir=target_dir,
            stdout_name="session.jsonl",
            stderr_name="dump.stderr.txt",
        )
        stages.append(dump_result)
        if dump_result.returncode != 0:
            session_jsonl = target_dir / "session.jsonl"
            if session_jsonl.exists():
                session_jsonl.unlink()

    survival_rate = _parse_survival_rate(target_dir / "rate.txt")
    if session_file.exists() and survival_rate is None:
        failures.append("survival rate could not be parsed from rate.txt")
    if survival_rate is not None and survival_rate > target.fail_over:
        failures.append(
            f"survival rate {survival_rate:.2f}% exceeds threshold {target.fail_over:.2f}%"
        )

    target_summary = {
        "name": target.name,
        "config": _relative_path(target.config_path),
        "status": "failed" if failures else "passed",
        "fail_over": target.fail_over,
        "survival_rate": survival_rate,
        "failures": failures,
        "stages": [stage.__dict__ for stage in stages],
    }
    _write_json(target_dir / "target-summary.json", target_summary)
    return target_summary


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Cosmic Ray mutation suites.")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="Mutation config file to run. Repeat for multiple configs.",
    )
    parser.add_argument(
        "--target-config",
        type=Path,
        default=Path(DEFAULT_TARGET_CONFIG),
        help="JSON file declaring mutation targets and per-target survivor thresholds.",
    )
    parser.add_argument(
        "--output-dir",
        default=".mutation",
        help="Directory for mutation artifacts.",
    )
    parser.add_argument(
        "--fail-over",
        type=float,
        default=None,
        help="Tighten all per-target fail-over thresholds. Cannot exceed target config.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run identifier. Defaults to a UTC timestamp.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file used to select the PR smoke subset. Repeat for multiple files.",
    )
    parser.add_argument(
        "--changed-files-from",
        type=Path,
        default=None,
        help="Read newline-delimited changed files for PR smoke selection.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List resolved mutation targets in deterministic order and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the manifest and print selected targets without invoking Cosmic Ray.",
    )
    return parser.parse_args(argv)


def _changed_files_from_args(args: argparse.Namespace) -> list[str]:
    changed_files = list(args.changed_file)
    if args.changed_files_from is not None:
        changed_files.extend(
            line.strip()
            for line in args.changed_files_from.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return changed_files


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / run_id

    all_targets = _load_targets(
        args.config,
        target_config=_resolve_repo_path(str(args.target_config)),
        fail_over_override=args.fail_over,
    )
    changed_files = _changed_files_from_args(args)
    selected_targets = _select_targets_for_changed_files(all_targets, changed_files)

    if args.list:
        _print_target_list(all_targets)
        return 0

    _write_manifest(
        output_dir=output_dir,
        run_id=run_id,
        mode="pr-smoke" if changed_files else "full",
        selected_targets=selected_targets,
        all_targets=all_targets,
        changed_files=changed_files,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        _print_target_list(selected_targets)
        print(f"[mutation] dry-run manifest written to {output_dir}")
        return 0

    if not selected_targets:
        message = "[mutation] no mutation-relevant files changed; skipping mutation run"
        print(message)
        print(f"[mutation] manifest written to {output_dir}")
        if changed_files:
            _write_run_summary(
                output_dir,
                run_id,
                [
                    {
                        "name": "target-selection",
                        "status": "skipped",
                        "fail_over": None,
                        "survival_rate": None,
                        "failures": [],
                        "stages": [],
                    }
                ],
            )
            return 0
        _write_run_summary(output_dir, run_id, [])
        return 0

    results = [_run_target(target, output_dir) for target in selected_targets]
    _write_run_summary(output_dir, run_id, results)
    _print_result_summary(results)
    print(f"[mutation] artifacts written to {output_dir}")
    return 1 if any(result["status"] == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
