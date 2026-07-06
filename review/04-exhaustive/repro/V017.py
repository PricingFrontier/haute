"""Reproduction for V017.

Claim: azure_devops_yml() interpolates the same {secrets_env} block (hard-coded
to 14-space indent by _azure_devops_secrets_env) into FOUR env: sites. The three
job-level steps place env: at 12 spaces (so 14-space keys nest correctly, +2),
but the DeployProduction `deployment` job nests its step env: at 18 spaces, so
the 14-space secret keys are LESS indented than their parent env:. This dedents
them out of the mapping and collides with the following `- script:` sibling,
making the whole generated .azure-pipelines.yml unparseable.

This repro ISOLATES on pure in-memory string generation (no disk, no project
root: azure_devops_yml / _get_target are pure functions over the in-memory
TARGETS dict). It ASSERTS on the specific wrong VALUE (indentation columns) and
on the resulting parse failure, and contrasts with the job-level env: block
which parses fine on its own.
"""

import sys

import yaml

from haute import _scaffold


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def main() -> None:
    target = "databricks"
    doc = _scaffold.azure_devops_yml(target)
    lines = doc.splitlines()

    # ---- 1. Locate the production-deploy env: site and the injected keys ----
    # The DeployProduction stage uses a `deployment` job whose step env: is the
    # MOST deeply indented env: in the document. Find that env: line.
    env_indents = [
        (i, _indent_of(ln)) for i, ln in enumerate(lines) if ln.strip() == "env:"
    ]
    assert env_indents, "no env: lines found at all"
    # The deepest env: belongs to the DeployProduction deployment step.
    prod_env_idx, prod_env_indent = max(env_indents, key=lambda t: t[1])

    # The line(s) immediately after that env: are the injected secret keys.
    first_secret_line = lines[prod_env_idx + 1]
    first_secret_indent = _indent_of(first_secret_line)

    print(f"[prod env:]        line {prod_env_idx + 1:>3}  indent={prod_env_indent}  {lines[prod_env_idx]!r}")
    print(f"[prod 1st secret]  line {prod_env_idx + 2:>3}  indent={first_secret_indent}  {first_secret_line!r}")

    # Sanity: the secret line really is a DATABRICKS_* mapping key, not something else.
    assert "DATABRICKS_RATING_HOST" in first_secret_line, (
        f"expected first prod secret to be DATABRICKS_RATING_HOST, got {first_secret_line!r}"
    )

    # ---- 2. THE BUG: secret keys are LESS indented than their parent env: ----
    # A correctly nested child of `env:` must be MORE indented than env:.
    # Here env: is at 18 and the secret key is at 14 -> 4 columns too shallow.
    assert prod_env_indent == 18, (
        f"expected production deployment env: at 18 spaces, got {prod_env_indent}"
    )
    assert first_secret_indent == 14, (
        f"expected injected secret at hard-coded 14 spaces, got {first_secret_indent}"
    )
    assert first_secret_indent < prod_env_indent, (
        "BUG NOT PRESENT: production secret keys are >= parent env: indent "
        f"(secret={first_secret_indent}, env={prod_env_indent})"
    )
    print(
        f"CONFIRMED under-indentation: secret keys at {first_secret_indent} spaces are "
        f"{prod_env_indent - first_secret_indent} columns SHALLOWER than parent env: "
        f"at {prod_env_indent} spaces."
    )

    # ---- 3. The full document is not loadable by any YAML parser ----
    parse_error = None
    try:
        yaml.safe_load(doc)
    except yaml.YAMLError as exc:
        parse_error = exc
    assert parse_error is not None, (
        "BUG NOT PRESENT: full Azure DevOps document parsed cleanly as YAML"
    )
    print(f"yaml.safe_load(full document) RAISED: {type(parse_error).__name__}")
    print(f"  -> {str(parse_error).splitlines()[0]}")

    # ---- 4. Contrast: a job-level env: block (12-space env:) nests fine ----
    # Build the equivalent minimal staging snippet to show the SAME secrets_env
    # is valid at the shallower nesting -> proving the defect is the indent, not
    # the secret values themselves.
    secrets_env = _scaffold._azure_devops_secrets_env(target)
    staging_snippet = (
        "steps:\n"
        "  - script: uv run haute deploy --endpoint-suffix \"-staging\"\n"
        "    displayName: Deploy staging\n"
        "    env:\n"  # 4-space env: here; secrets at 14 still nest because 14 > 4
        f"{secrets_env}\n"
    )
    loaded = yaml.safe_load(staging_snippet)
    env_map = loaded["steps"][0]["env"]
    assert env_map["DATABRICKS_RATING_HOST"] == "$(DATABRICKS_RATING_HOST)", (
        f"staging env mapping wrong: {env_map}"
    )
    print(
        "CONTRAST: identical secrets_env block parses fine when env: is shallower "
        f"than 14 -> env map = {env_map}"
    )

    # ---- 5. Confirm every TARGETS entry fails the full parse the same way ----
    failed = []
    for t in _scaffold.TARGETS:
        try:
            yaml.safe_load(_scaffold.azure_devops_yml(t))
        except yaml.YAMLError:
            failed.append(t)
    assert set(failed) == set(_scaffold.TARGETS), (
        f"expected ALL targets to fail full parse; failed={failed} "
        f"out of {list(_scaffold.TARGETS)}"
    )
    print(f"ALL {len(failed)} targets fail full YAML parse: {failed}")

    print("\nV017 REPRODUCED: production deploy env: secrets are under-indented; "
          "generated .azure-pipelines.yml is unparseable.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"REPRO ASSERTION FAILED (bug NOT demonstrated): {exc}", file=sys.stderr)
        sys.exit(1)
