"""Isolated reproduction for V018.

Claim: ``TestAzureDevopsYamlValidation.test_yaml_header_parses`` only parses the
*header* of the Azure DevOps pipeline (it does ``raw.split("\\nstages:")[0] +
"\\nstages: []"`` and discards the entire ``stages:`` body). Because of this, the
production-deploy ``env:`` indentation defect in ``azure_devops_yml`` is invisible
to CI.  A full-document parse (mirroring the GitLab ``test_yaml_parses`` loop)
would raise ``ScannerError`` for every target *today*.

This script proves, by execution, three things:

  1. The existing header-only transform parses fine for all targets (so the
     current test is GREEN and cannot catch the defect).
  2. ``yaml.safe_load`` on the UNMODIFIED ``azure_devops_yml(target)`` output
     raises for every target.
  3. The failure is caused specifically by the DeployProduction ``env:`` block
     whose values (14-space indent) are *dedented* relative to the ``env:`` key
     (18-space indent) -- i.e. the predicted indentation bug, NOT an unrelated
     import/setup error.

No disk I/O, no reads of rating/ src/ tests/ or real project files: it only
exercises pure in-memory template strings via the public scaffold API.
"""

import yaml

from haute._scaffold import TARGETS, azure_devops_yml


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def main() -> None:
    targets = list(TARGETS)
    assert targets, "expected at least one deploy target"

    # ---- 1) The existing header-only test transform: parses for every target.
    header_ok = []
    for target in targets:
        raw = azure_devops_yml(target)
        header = raw.split("\nstages:")[0] + "\nstages: []"
        doc = yaml.safe_load(header)  # current test does exactly this
        assert isinstance(doc, dict), f"header parse unexpectedly failed for {target}"
        header_ok.append(target)
    assert header_ok == targets, (
        "Header-only parse should succeed for all targets, confirming the current "
        f"test is green and blind to the body. ok={header_ok}"
    )

    # ---- 2) Full-document parse raises for EVERY target (the stronger test).
    full_doc_errors: dict[str, yaml.YAMLError] = {}
    for target in targets:
        raw = azure_devops_yml(target)
        try:
            yaml.safe_load(raw)
        except yaml.YAMLError as exc:  # noqa: PERF203 - clarity over micro-perf
            full_doc_errors[target] = exc
        else:
            raise AssertionError(
                f"EXPECTED full-document yaml.safe_load to FAIL for target={target}, "
                "but it parsed cleanly. The indentation defect may have been fixed."
            )
    assert set(full_doc_errors) == set(targets), (
        "Every target's full Azure document should fail to parse. "
        f"failed={sorted(full_doc_errors)} vs targets={sorted(targets)}"
    )

    # ---- 3) Pin the failure to the DeployProduction env: block specifically.
    raw = azure_devops_yml("databricks")
    lines = raw.splitlines()
    exc = full_doc_errors["databricks"]
    mark = exc.problem_mark
    assert mark is not None, "expected a problem_mark on the YAML error"

    # The scanner trips on the line *after* the dedented mapping values
    # ('- script: |'), but the root cause is the preceding env: / values pair.
    fail_line_idx = mark.line  # 0-based
    # Find the offending 'env:' key (18-space indent) and its first value line.
    env_idx = None
    for i in range(fail_line_idx, -1, -1):
        if lines[i].strip() == "env:":
            env_idx = i
            break
    assert env_idx is not None, "could not locate the env: key preceding the failure"

    env_indent = _indent_of(lines[env_idx])
    value_indent = _indent_of(lines[env_idx + 1])

    # The defect: env: VALUES are indented LESS than the env: key -> invalid YAML.
    assert value_indent < env_indent, (
        "Predicted bug not present: env: values should be DEDENTED relative to the "
        f"env: key. env_indent={env_indent} value_indent={value_indent} "
        f"env_line={lines[env_idx]!r} value_line={lines[env_idx + 1]!r}"
    )

    # Concretely assert the exact wrong values (production block).
    assert env_indent == 18, f"expected production env: at 18 spaces, got {env_indent}"
    assert value_indent == 14, (
        f"expected secrets block at 14 spaces, got {value_indent}: "
        f"{lines[env_idx + 1]!r}"
    )

    print("PASS: V018 reproduced.")
    print(f"  header-only parse OK for all {len(targets)} targets (test is blind).")
    print(
        f"  full-document parse RAISES for all {len(targets)} targets: "
        f"{type(exc).__name__}: {exc.problem}"
    )
    print(
        f"  root cause @ rendered line {env_idx + 1}: env: at indent {env_indent}, "
        f"values at indent {value_indent} (values dedented => invalid YAML)."
    )
    print(f"  scanner reported failure at rendered line {fail_line_idx + 1}: "
          f"{lines[fail_line_idx]!r}")


if __name__ == "__main__":
    main()
