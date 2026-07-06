"""Isolated reproduction for platform BUG-4.

Claim: `_detect_ci_env` (src/haute/cli/_deploy.py:38-52) decides a CI marker
variable is "set" (truthy) whenever its value is NOT a member of the literal
falsy set::

    {"", "0", "false", "False", "FALSE", "no", "No", "NO"}

The value is compared VERBATIM -- there is no `.strip()` and no full `.lower()`
(only three hand-picked case spellings of "false"/"no" are covered). As a
consequence, common ways a developer/shell can express "not in CI" are misread
as truthy -> `is_ci = True`:

    * CI=" false "   (leading/trailing whitespace -> not in set)
    * CI="off"       (a normal falsy spelling, absent from the set)
    * CI="disabled"
    * GITHUB_ACTIONS="n"   (short form of "no", absent from the set)
    * CI="FaLsE"     (mixed case, absent from the set)

The gate in handle_deploy at line 111 (`if not config.dry_run and not is_ci:
refuse`) is the ONLY guard preventing a real production deploy from a developer
machine ("Deploys must go through CI/CD."). A spurious is_ci=True therefore
opens the door to a real local production deploy.

This repro imports ONLY the pure function `_detect_ci_env` and feeds it
synthetic, in-memory dict mappings. It touches NO disk, no env, no rating/, no
project files. The function itself does no IO and does not read os.environ.

Run: uv run python review/03-simplification/repro/platform__BUG-4.py
Exit 0 == bug reproduced; exit 1 == bug NOT reproduced (claim refuted).
"""

from __future__ import annotations

from haute.cli._deploy import _detect_ci_env

failures: list[str] = []


def expect(label: str, env: dict[str, str], *, want: bool) -> None:
    """Assert _detect_ci_env(env) == want; record mismatch as a failure."""
    got = _detect_ci_env(env)
    verdict = "OK" if got == want else "MISMATCH"
    print(f"  [{verdict}] {label:<42} env={env!r:<34} -> is_ci={got} (want {want})")
    if got != want:
        failures.append(f"{label}: got is_ci={got}, expected {want}")


# ---------------------------------------------------------------------------
# Control: behaviour everyone agrees on (sanity that the import/function works).
# ---------------------------------------------------------------------------
print("== controls (function sanity) ==")
# A real provider truthy marker -> genuinely in CI.
expect("GITHUB_ACTIONS=true (real CI)", {"GITHUB_ACTIONS": "true"}, want=True)
# Exact-match falsy spellings the whitelist DOES cover -> correctly not-CI.
expect("CI=false (exact falsy)", {"CI": "false"}, want=False)
expect("CI=0 (exact falsy)", {"CI": "0"}, want=False)
expect("CI='' (empty)", {"CI": ""}, want=False)
# No markers at all -> not CI.
expect("no markers (clean local shell)", {"PATH": "/usr/bin"}, want=False)

# ---------------------------------------------------------------------------
# The BUG: values a human would obviously read as "NOT in CI", which a safe
# normaliser (value.strip().lower() not in {"", "0", "false", "no", "off"})
# would treat as falsy, but which the brittle whitelist treats as TRUTHY.
#
# For the bug to be REPRODUCED we expect these to (wrongly) return is_ci=True,
# i.e. want=False but the function returns True. We encode the CORRECT
# expectation (want=False) so a MISMATCH == the bug firing.
# ---------------------------------------------------------------------------
print("\n== bug cases: should be NOT-CI, but the whitelist misreads them as CI ==")
expect("CI=' false ' (whitespace padded)", {"CI": " false "}, want=False)
expect("CI='off'", {"CI": "off"}, want=False)
expect("CI='disabled'", {"CI": "disabled"}, want=False)
expect("GITHUB_ACTIONS='n'", {"GITHUB_ACTIONS": "n"}, want=False)
expect("CI='FaLsE' (mixed case)", {"CI": "FaLsE"}, want=False)
expect("CI='No ' (trailing space)", {"CI": "No "}, want=False)

print("\n================ RESULT ================")
# Each recorded "failure" here is actually the function disagreeing with the
# SAFE expectation -> i.e. the brittle whitelist firing. The bug is reproduced
# iff at least all six bug cases mismatched (the controls must still pass).
bug_case_labels = (
    "CI=' false ' (whitespace padded)",
    "CI='off'",
    "CI='disabled'",
    "GITHUB_ACTIONS='n'",
    "CI='FaLsE' (mixed case)",
    "CI='No ' (trailing space)",
)
control_failures = [f for f in failures if not any(f.startswith(b) for b in bug_case_labels)]
bug_firings = [f for f in failures if any(f.startswith(b) for b in bug_case_labels)]

if control_failures:
    print("SETUP ERROR -- a control assertion failed; cannot trust the repro:")
    for f in control_failures:
        print("  CONTROL FAIL:", f)
    raise SystemExit(1)

if len(bug_firings) == len(bug_case_labels):
    print("CLAIM REPRODUCED: every 'not-in-CI' spelling below was misread as is_ci=True")
    print("because _detect_ci_env compares the raw value against a fixed falsy whitelist")
    print('{"", "0", "false", "False", "FALSE", "no", "No", "NO"} with NO .strip()/.lower():')
    for f in bug_firings:
        print("  BUG:", f)
    print("\nImpact: with --dry-run absent and one of these CI vars exported, the gate at")
    print("_deploy.py:111 (`if not config.dry_run and not is_ci`) is bypassed -> a real")
    print("production deploy proceeds from a local developer machine.")
    raise SystemExit(0)

print("CLAIM NOT REPRODUCED: _detect_ci_env normalised the values as a human would expect.")
print(f"  bug cases that fired: {len(bug_firings)}/{len(bug_case_labels)}")
raise SystemExit(1)
