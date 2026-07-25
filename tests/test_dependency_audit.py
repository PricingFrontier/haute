import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "dependency_audit", Path("scripts/check_dependency_audit.py")
)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def python_report(vulns=None):
    return {"dependencies": [{"name": "requests", "vulns": vulns or []}]}


def npm_report(vulnerabilities=None):
    return {"auditReportVersion": 2, "vulnerabilities": vulnerabilities or {}}


def registry(tmp_path, entries=""):
    path = tmp_path / "risks.toml"
    path.write_text("version = 1\n" + entries, encoding="utf-8")
    return path


def acceptance(package="requests", advisory="PYSEC-1", ecosystem="python"):
    return f'''[[acceptance]]
ecosystem = "{ecosystem}"
package = "{package}"
advisory = "{advisory}"
owner = "security"
exposure = "build"
compensating_control = "pinned"
approved_on = "2026-01-01"
review_by = "2026-12-31"
'''


def test_clean_reports_pass(tmp_path):
    assert (
        audit.evaluate(python_report(), npm_report(), registry(tmp_path), today=dt.date(2026, 7, 1))
        == 0
    )


def test_python_finding_blocks(tmp_path):
    assert (
        audit.evaluate(
            python_report([{"id": "PYSEC-1"}]),
            npm_report(),
            registry(tmp_path),
            today=dt.date(2026, 7, 1),
        )
        == 1
    )


def test_npm_low_ignored_high_blocked_and_ghsa_identity(tmp_path):
    report = npm_report(
        {
            "low": {"severity": "low", "via": [{"source": 1}]},
            "high": {
                "severity": "high",
                "via": [{"url": "https://github.com/advisories/GHSA-abcd-1234-zzzz", "source": 2}],
            },
        }
    )
    assert audit.parse_npm_report(report) == {audit.Finding("npm", "high", "GHSA-ABCD-1234-ZZZZ")}
    assert (
        audit.evaluate(python_report(), report, registry(tmp_path), today=dt.date(2026, 7, 1)) == 1
    )


def test_npm_transitive_meta_finding(tmp_path):
    report = npm_report({"parent": {"severity": "critical", "via": ["child", "other"]}})
    assert audit.parse_npm_report(report) == {
        audit.Finding("npm", "parent", "npm:transitive:child|other")
    }


def test_valid_acceptance_passes(tmp_path):
    risks = registry(tmp_path, acceptance())
    assert (
        audit.evaluate(
            python_report([{"id": "PYSEC-1"}]), npm_report(), risks, today=dt.date(2026, 7, 1)
        )
        == 0
    )


@pytest.mark.parametrize(
    "entries",
    [
        acceptance().replace("2026-12-31", "2026-01-02"),
        acceptance().replace('review_by = "2026-12-31"', 'review_by = "not-a-date"'),
        acceptance() + acceptance(),
    ],
)
def test_expired_malformed_and_duplicate_acceptances_fail(tmp_path, entries):
    with pytest.raises(audit.PolicyError):
        audit.load_acceptances(registry(tmp_path, entries), dt.date(2026, 7, 1))


def test_unused_and_package_mismatch_acceptances_fail(tmp_path):
    with pytest.raises(audit.PolicyError, match="unused"):
        audit.evaluate(
            python_report(),
            npm_report(),
            registry(tmp_path, acceptance()),
            today=dt.date(2026, 7, 1),
        )
    with pytest.raises(audit.PolicyError, match="unused"):
        audit.evaluate(
            python_report([{"id": "PYSEC-1"}]),
            npm_report(),
            registry(tmp_path, acceptance(package="other")),
            today=dt.date(2026, 7, 1),
        )


@pytest.mark.parametrize(
    "report, parser",
    [
        ({"dependencies": "no"}, "parse_python_report"),
        ({"auditReportVersion": 1}, "parse_npm_report"),
    ],
)
def test_malformed_reports_fail_closed(report, parser):
    with pytest.raises(audit.PolicyError):
        getattr(audit, parser)(report)


def test_live_scanner_rc_one_is_parseable_and_commands_are_orchestrated(monkeypatch, tmp_path):
    calls = []
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 1, json.dumps(python_report()), ""),
            subprocess.CompletedProcess([], 1, json.dumps(npm_report()), ""),
        ]
    )

    def fake_run(command, *, cwd=None):
        calls.append((command, cwd))
        return next(results)

    monkeypatch.setattr(audit, "_run", fake_run)
    assert audit.live_reports(tmp_path) == (python_report(), npm_report())
    assert calls[0][0][:3] == ["uv", "export", "--locked"]
    assert calls[1][0][:4] == ["uvx", "--from", "pip-audit==2.10.1", "pip-audit"]
    assert calls[2] == (
        ["npm", "audit", "--json", "--audit-level=none", "--ignore-scripts"],
        Path("frontend"),
    )


@pytest.mark.parametrize("returncode", [2, 127])
def test_live_scanner_bad_return_code_fails(monkeypatch, tmp_path, returncode):
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], returncode, "{}", ""),
        ]
    )
    monkeypatch.setattr(audit, "_run", lambda *args, **kwargs: next(results))
    with pytest.raises(audit.PolicyError, match="pip-audit scanner failed"):
        audit.live_reports(tmp_path)


def test_live_locked_export_failure_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", "export failed"),
    )

    with pytest.raises(audit.PolicyError, match="locked Python export failed"):
        audit.live_reports(tmp_path)


@pytest.mark.parametrize("returncode", [2, 127])
def test_live_npm_scanner_bad_return_code_fails(monkeypatch, tmp_path, returncode):
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, json.dumps(python_report()), ""),
            subprocess.CompletedProcess([], returncode, "{}", ""),
        ]
    )
    monkeypatch.setattr(audit, "_run", lambda *args, **kwargs: next(results))

    with pytest.raises(audit.PolicyError, match="npm audit scanner failed"):
        audit.live_reports(tmp_path)
