"""Fail closed dependency-advisory policy checker (stdlib only)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class Finding:
    ecosystem: str
    package: str
    advisory: str


class PolicyError(Exception):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"invalid report {path}: {exc}") from exc


def parse_python_report(report: Any) -> set[Finding]:
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        raise PolicyError("pip-audit report is missing dependencies")
    findings: set[Finding] = set()
    for dependency in report["dependencies"]:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("name"), str):
            raise PolicyError("pip-audit report has an invalid dependency")
        vulns = dependency.get("vulns")
        if not isinstance(vulns, list):
            raise PolicyError("pip-audit report has an invalid vulns field")
        for vuln in vulns:
            if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str):
                raise PolicyError("pip-audit report has an invalid vulnerability")
            findings.add(Finding("python", dependency["name"], vuln["id"]))
    return findings


def _advisory_id(advisory: dict[str, Any]) -> str:
    url = advisory.get("url")
    if isinstance(url, str):
        match = re.search(r"GHSA-[0-9A-Za-z-]+", url, re.IGNORECASE)
        if match:
            return match.group(0).upper()
    source = advisory.get("source")
    if isinstance(source, int | str):
        return f"npm:{source}"
    raise PolicyError("npm advisory has no stable identity")


def parse_npm_report(report: Any) -> set[Finding]:
    if not isinstance(report, dict) or report.get("auditReportVersion") != 2:
        raise PolicyError("npm report is not auditReportVersion 2")
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise PolicyError("npm report is missing vulnerabilities")
    findings: set[Finding] = set()
    for package, vulnerability in vulnerabilities.items():
        if not isinstance(package, str) or not isinstance(vulnerability, dict):
            raise PolicyError("npm report has an invalid vulnerability")
        severity = vulnerability.get("severity")
        via = vulnerability.get("via")
        if not isinstance(severity, str) or not isinstance(via, list):
            raise PolicyError("npm report has incomplete vulnerability data")
        if severity not in {"high", "critical"}:
            continue
        advisory_objects = [item for item in via if isinstance(item, dict)]
        if advisory_objects:
            for advisory in advisory_objects:
                findings.add(Finding("npm", package, _advisory_id(advisory)))
        elif via and all(isinstance(item, str) for item in via):
            # npm's meta finding has no advisory object; keep a deterministic identity.
            findings.add(Finding("npm", package, f"npm:transitive:{'|'.join(sorted(via))}"))
        else:
            raise PolicyError("npm high/critical vulnerability has incomplete via data")
    return findings


REQUIRED_ACCEPTANCE = {
    "ecosystem",
    "package",
    "advisory",
    "owner",
    "exposure",
    "compensating_control",
    "approved_on",
    "review_by",
}


def load_acceptances(path: Path, today: dt.date) -> set[Finding]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"invalid acceptance registry: {exc}") from exc
    if data.get("version") != 1 or set(data) - {"version", "acceptance"}:
        raise PolicyError("acceptance registry must use schema version 1")
    entries = data.get("acceptance", [])
    if not isinstance(entries, list):
        raise PolicyError("acceptance must be an array")
    accepted: set[Finding] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != REQUIRED_ACCEPTANCE:
            raise PolicyError("acceptance entry has missing or unknown fields")
        if not all(isinstance(entry[field], str) and entry[field] for field in REQUIRED_ACCEPTANCE):
            raise PolicyError("acceptance entry fields must be non-empty strings")
        try:
            approved, review = (
                dt.date.fromisoformat(entry["approved_on"]),
                dt.date.fromisoformat(entry["review_by"]),
            )
        except ValueError as exc:
            raise PolicyError("acceptance dates must be ISO dates") from exc
        if approved > today or review < today or approved > review:
            raise PolicyError("acceptance is not currently valid")
        finding = Finding(entry["ecosystem"], entry["package"], entry["advisory"])
        if finding in accepted:
            raise PolicyError(f"duplicate acceptance: {finding}")
        accepted.add(finding)
    return accepted


def evaluate(
    python_report: Any, npm_report: Any, registry: Path, *, today: dt.date | None = None
) -> int:
    findings = parse_python_report(python_report) | parse_npm_report(npm_report)
    accepted = load_acceptances(registry, today or dt.date.today())
    unused = accepted - findings
    if unused:
        raise PolicyError(
            "unused acceptance: "
            + ", ".join(f"{f.ecosystem}/{f.package}/{f.advisory}" for f in sorted(unused))
        )
    blocked = findings - accepted
    for finding in sorted(blocked):
        print(f"BLOCK {finding.ecosystem} {finding.package} {finding.advisory}")
    if accepted:
        print(f"Accepted {len(accepted)} current advisory finding(s).")
    if not blocked:
        print("Dependency advisory policy passed.")
        return 0
    return 1


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def live_reports(reports_dir: Path | None) -> tuple[Any, Any]:
    temporary = tempfile.TemporaryDirectory()
    directory = reports_dir or Path(temporary.name)
    directory.mkdir(parents=True, exist_ok=True)
    requirements, python_path, npm_path = (
        directory / "requirements.txt",
        directory / "pip-audit.json",
        directory / "npm-audit.json",
    )
    export = _run(
        [
            "uv",
            "export",
            "--locked",
            "--all-groups",
            "--all-extras",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
        ]
    )
    if export.returncode:
        raise PolicyError("locked Python export failed")
    python = _run(
        [
            "uvx",
            "--from",
            "pip-audit==2.10.1",
            "pip-audit",
            "--no-deps",
            "--requirement",
            str(requirements),
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]
    )
    python_path.write_text(python.stdout, encoding="utf-8")
    if python.returncode not in {0, 1}:
        raise PolicyError("pip-audit scanner failed")
    npm = _run(
        ["npm", "audit", "--json", "--audit-level=none", "--ignore-scripts"], cwd=Path("frontend")
    )
    npm_path.write_text(npm.stdout, encoding="utf-8")
    if npm.returncode not in {0, 1}:
        raise PolicyError("npm audit scanner failed")
    result = _load_json(python_path), _load_json(npm_path)
    if reports_dir is None:
        temporary.cleanup()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-report", type=Path)
    parser.add_argument("--npm-report", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--registry", type=Path, default=Path("security/accepted-risks.toml"))
    args = parser.parse_args(argv)
    if bool(args.python_report) != bool(args.npm_report):
        parser.error("--python-report and --npm-report must be supplied together")
    try:
        reports = (
            (_load_json(args.python_report), _load_json(args.npm_report))
            if args.python_report
            else live_reports(args.reports_dir)
        )
        return evaluate(*reports, args.registry)
    except PolicyError as exc:
        print(f"Dependency advisory policy failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
