"""Cover the dependency-pin lint, including the regression that motivated it.

The founding defect: `anthropic` and `openai` entered `[project] dependencies`
uncapped, six days after the floor+cap CI lanes shipped, and nothing noticed
because every gated job resolves from the lockfile.  `test_live_tree_is_clean`
is the standing ratchet -- it fails the build on the next entrant that drifts.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

# Reads files rather than exercising behaviour, so the result cannot vary by
# interpreter -- deselected from the compat lanes like the other repo-health checks.
pytestmark = pytest.mark.meta

# Anchored to this file, not the working directory: a runner invoked from
# tests/ would otherwise fail to find the script before any test could run.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lint_pins.py"
SPEC = importlib.util.spec_from_file_location("lint_pins", _SCRIPT)
assert SPEC and SPEC.loader
lint_pins = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lint_pins
SPEC.loader.exec_module(lint_pins)


def write_project(root: Path, *, dependencies="", optional="", dev="", extra="") -> Path:
    """A minimal but structurally faithful pyproject.toml."""
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "haute"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        f"dependencies = [{dependencies}]\n"
        f"{extra}"
        "\n[project.optional-dependencies]\n"
        f"databricks = [{optional}]\n"
        "\n[dependency-groups]\n"
        f"dev = [{dev}]\n",
        encoding="utf-8",
    )
    return root / "pyproject.toml"


def write_package_json(root: Path, *, deps=None, dev=None, scripts=None) -> Path:
    frontend = root / "frontend"
    frontend.mkdir(exist_ok=True)
    path = frontend / "package.json"
    path.write_text(
        json.dumps(
            {
                "name": "frontend",
                "private": True,
                "dependencies": deps or {},
                "devDependencies": dev or {},
                "scripts": scripts or {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def messages(violations):
    return " ".join(v.message for v in violations)


# ── Published runtime dependencies: floor AND cap, never exact ──────────────


def test_uncapped_published_dependency_is_caught(tmp_path):
    """The founding regression: a floor with no upper bound."""
    path = write_project(tmp_path, dependencies='"anthropic>=0.40"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "has no cap" in violations[0].message
    assert "anthropic" in violations[0].message


def test_published_dependency_without_floor_is_caught(tmp_path):
    path = write_project(tmp_path, dependencies='"polars<2"')
    assert "has no floor" in messages(lint_pins.check_pyproject(path))


def test_exact_pinned_published_dependency_is_caught(tmp_path):
    """Exact pins make a fresh install unresolvable when an artefact is yanked."""
    path = write_project(tmp_path, dependencies='"polars==1.39.3"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "exact-pinned" in violations[0].message


def test_floor_and_cap_passes(tmp_path):
    path = write_project(tmp_path, dependencies='"polars>=1.39.3,<2", "anthropic>=0.40,<1"')
    assert lint_pins.check_pyproject(path) == []


def test_cap_tightness_is_not_policed(tmp_path):
    """`<1` on a 0.x package is a review decision, not a lint failure.

    A literal reading of the doctrine would demand `anthropic<0.41`, which the
    version actually locked (0.117.0) does not satisfy. Both a one-minor band
    and a whole-major band must pass, or the lint would be legislating the
    value rather than asserting a bound exists.
    """
    for spec in ('"anthropic>=0.40,<0.41"', '"anthropic>=0.40,<1"', '"anthropic>=0.40,<99"'):
        assert lint_pins.check_pyproject(write_project(tmp_path, dependencies=spec)) == [], spec


def test_compatible_release_operator_reports_the_real_problem(tmp_path):
    """`~=` bounds both sides, so 'no floor'/'no cap' would both be wrong."""
    path = write_project(tmp_path, dependencies='"polars~=1.39"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "compatible-release" in violations[0].message
    assert "no floor" not in violations[0].message
    assert "no cap" not in violations[0].message


def test_direct_url_reference_is_caught(tmp_path):
    """A published dependency must resolve from the index."""
    path = write_project(tmp_path, dependencies='"polars @ https://example.invalid/p.whl"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "direct reference" in violations[0].message


def test_exclusion_alongside_a_band_is_accepted(tmp_path):
    """`!=` narrows within a band; the band is still floor+cap."""
    path = write_project(tmp_path, dependencies='"polars>=1.39.3,!=1.40.0,<2"')
    assert lint_pins.check_pyproject(path) == []


def test_optional_dependencies_are_held_to_the_published_rule(tmp_path):
    path = write_project(tmp_path, dependencies="", optional='"databricks-sdk>=0.88.0"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "optional-dependencies" in violations[0].message


def test_extras_and_markers_do_not_confuse_the_parser(tmp_path):
    """`uvicorn[standard]>=0.40,<0.41; python_version >= '3.11'` is well-formed."""
    path = write_project(
        tmp_path,
        dependencies="\"uvicorn[standard]>=0.40.0,<0.41; python_version >= '3.11'\"",
    )
    assert lint_pins.check_pyproject(path) == []


def test_requires_python_floor_is_never_flagged(tmp_path):
    """It is the downstream interpreter contract, not a dependency spec.

    Discriminating, not tautological: the project below is otherwise DIRTY, so
    the check definitely ran and definitely reported. The assertion is that
    `requires-python` is absent from what it reported -- a change that started
    walking it would fail here.
    """
    path = write_project(tmp_path, dependencies='"polars>=1.39.3"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "polars" in violations[0].message
    assert "requires-python" not in violations[0].message
    assert "3.11" not in violations[0].message


# ── Tooling dependencies: exact, always ────────────────────────────────────


def test_non_exact_dev_group_pin_is_caught(tmp_path):
    path = write_project(tmp_path, dev='"pytest>=9.0"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "not exact-pinned" in violations[0].message


def test_wildcard_dev_group_pin_is_not_exact(tmp_path):
    """`pytest==9.*` carries the exact operator but matches the whole 9.x line."""
    path = write_project(tmp_path, dev='"pytest==9.*"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "not exact-pinned" in violations[0].message


def test_arbitrary_equality_wildcard_is_also_caught(tmp_path):
    path = write_project(tmp_path, dev='"pytest===9.*"')
    assert len(lint_pins.check_pyproject(path)) == 1


def test_published_wildcard_reports_prefix_matching_not_exact_pinning(tmp_path):
    """Calling `==1.*` an exact pin would push the author towards an uncapped floor."""
    path = write_project(tmp_path, dependencies='"polars==1.*"')
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert "prefix matching" in violations[0].message
    assert "exact-pinned" not in violations[0].message


def test_exact_dev_group_pin_passes(tmp_path):
    path = write_project(tmp_path, dev='"pytest==9.0.3"')
    assert lint_pins.check_pyproject(path) == []


def test_self_reference_in_dev_group_is_exempt(tmp_path):
    """`haute[databricks]` installs the project itself and carries no version."""
    path = write_project(tmp_path, dev='"haute[databricks]"')
    assert lint_pins.check_pyproject(path) == []


# ── npm manifest ───────────────────────────────────────────────────────────


def test_caret_range_in_package_json_is_caught(tmp_path):
    path = write_package_json(tmp_path, dev={"vitest": "^4.0.18"})
    violations = lint_pins.check_package_json(path)

    assert len(violations) == 1
    assert "not an exact version" in violations[0].message


def test_dist_tag_and_protocol_specs_are_caught(tmp_path):
    path = write_package_json(
        tmp_path,
        deps={"a": "latest", "b": "*", "c": "git+https://example.invalid/x.git"},
    )
    assert len(lint_pins.check_package_json(path)) == 3


def test_a_package_sharing_the_manifest_name_does_not_steal_the_annotation(tmp_path):
    """`"name": "vitest"` appears before the dependency entry of the same name."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    path = frontend / "package.json"
    path.write_text(
        '{\n  "name": "vitest",\n  "devDependencies": {\n    "vitest": "^4.0.18"\n  }\n}\n',
        encoding="utf-8",
    )
    violations = lint_pins.check_package_json(path)

    assert len(violations) == 1
    assert violations[0].line == 4, "annotation landed on the manifest's own name field"


def test_peer_dependencies_may_declare_a_range(tmp_path):
    """A peer dependency states which hosts are acceptable, not what is installed."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    path = frontend / "package.json"
    path.write_text(
        json.dumps({"name": "frontend", "private": True, "peerDependencies": {"react": ">=18"}}),
        encoding="utf-8",
    )

    assert lint_pins.check_package_json(path) == []


def test_optional_dependencies_still_require_exactness(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    path = frontend / "package.json"
    path.write_text(
        json.dumps({"name": "frontend", "optionalDependencies": {"fsevents": "^2.3.0"}}),
        encoding="utf-8",
    )

    assert len(lint_pins.check_package_json(path)) == 1


def test_a_superseded_spec_in_a_comment_does_not_steal_the_annotation(tmp_path):
    """`_line_of` must point at the declaration, not a commented-out predecessor."""
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[project]\n"
        'name = "haute"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        "    # was polars>=1.39.3\n"
        '    "polars>=1.39.3",\n'
        "]\n",
        encoding="utf-8",
    )
    violations = lint_pins.check_pyproject(path)

    assert len(violations) == 1
    assert violations[0].line == 6, "annotation landed on the comment, not the declaration"


def test_exact_npm_versions_pass(tmp_path):
    path = write_package_json(
        tmp_path,
        deps={"react": "19.2.0"},
        dev={"vitest": "4.1.9", "pre": "1.0.0-beta.1"},
    )
    assert lint_pins.check_package_json(path) == []


def test_npx_in_a_package_script_is_caught(tmp_path):
    path = write_package_json(tmp_path, scripts={"test": "npx vitest run"})
    violations = lint_pins.check_package_json(path)

    assert len(violations) == 1
    assert "npx" in violations[0].message


def test_npm_exec_is_not_mistaken_for_npx(tmp_path):
    path = write_package_json(tmp_path, scripts={"test": "npm exec vitest run"})
    assert lint_pins.check_package_json(path) == []


# ── npx on executable surfaces ─────────────────────────────────────────────


def test_npx_in_a_workflow_is_caught(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("jobs:\n  a:\n    steps:\n      - run: npx vitest\n", "utf-8")

    violations = lint_pins.check_npx(tmp_path)

    assert len(violations) == 1
    assert violations[0].line == 4


def test_npx_in_a_shell_script_is_caught(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "preflight.sh").write_text("#!/bin/bash\nnpx tsc --noEmit\n", "utf-8")

    assert len(lint_pins.check_npx(tmp_path)) == 1


def test_prose_is_not_scanned_for_npx(tmp_path):
    """Docs must be free to quote the offending command while teaching the rule."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "COMMIT_STANDARDS.md").write_text("Never run `npx vitest` -- use npm exec.\n", "utf-8")

    assert lint_pins.check_npx(tmp_path) == []


def test_npx_wrapped_in_shell_grouping_is_caught(tmp_path):
    """`(npx …)` and `{npx …;}` are real invocations a whitespace-only rule misses."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text("(npx vitest run)\n", "utf-8")
    (scripts / "b.sh").write_text("{npx vitest run;}\n", "utf-8")

    assert len(lint_pins.check_npx(tmp_path)) == 2


def test_npx_with_line_continuation_is_caught(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text("npx \\\n  vitest run\n", "utf-8")

    assert len(lint_pins.check_npx(tmp_path)) == 1


def test_npx_mentioned_in_a_shell_comment_is_not_flagged(tmp_path):
    """The check finds invocations, not mentions — a script may warn about npx."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text("# never use npx here\necho ok  # not npx either\n", "utf-8")

    assert lint_pins.check_npx(tmp_path) == []


def test_a_hash_inside_a_quoted_string_does_not_hide_a_later_call(tmp_path):
    """Comment-stripping must not become a bypass.

    A naive cut at the first `#` would truncate this line before the
    invocation, turning the false-positive fix into a false negative.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        '      - run: echo "shard #1"; npx playwright install\n', "utf-8"
    )

    assert len(lint_pins.check_npx(tmp_path)) == 1


def test_nested_script_directories_are_scanned(tmp_path):
    scripts = tmp_path / "scripts" / "ci"
    scripts.mkdir(parents=True)
    (scripts / "deploy.sh").write_text("npx vitest run\n", "utf-8")

    assert len(lint_pins.check_npx(tmp_path)) == 1


def test_a_real_call_before_a_comment_still_counts(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text("npx vitest run  # TODO: use npm exec\n", "utf-8")

    assert len(lint_pins.check_npx(tmp_path)) == 1


def test_npx_shim_paths_are_not_command_words(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text("./npx-shim run\necho npxfoo\n", "utf-8")

    assert lint_pins.check_npx(tmp_path) == []


def test_substring_of_a_longer_word_is_not_an_npx_call(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "x.sh").write_text("echo mynpx not-a-call\n", "utf-8")

    assert lint_pins.check_npx(tmp_path) == []


# ── The standing ratchet ───────────────────────────────────────────────────


def test_main_fails_the_build_and_annotates_the_line(monkeypatch, capsys):
    """The CI step's whole contract. Without this, `return 1` -> `return 0`
    would turn the gate into a permanent no-op with every other test green."""
    monkeypatch.setattr(
        lint_pins,
        "collect",
        lambda: [lint_pins.Violation("pyproject.toml", 62, "'anthropic>=0.40' has no cap.")],
    )

    assert lint_pins.main() == 1
    captured = capsys.readouterr()
    assert "::error file=pyproject.toml,line=62,title=Dependency pin::" in captured.out
    assert "pyproject.toml:62: 'anthropic>=0.40' has no cap." in captured.err


def test_main_passes_on_a_clean_tree(monkeypatch):
    monkeypatch.setattr(lint_pins, "collect", list)
    assert lint_pins.main() == 0


def test_live_tree_is_clean():
    """The real manifests obey the doctrine. Fails on the next silent entrant.

    One-sided by construction: it proves the lint reports nothing, not that
    the tree is clean. `test_removing_a_real_cap_is_caught` covers the other
    direction against the same file.
    """
    violations = lint_pins.collect()
    assert violations == [], "\n".join(v.render() for v in violations)


def test_removing_a_real_cap_is_caught(tmp_path):
    """Mutate the REAL manifest and confirm the lint fails on it.

    Guards the false-negative direction: a refactor that quietly stopped
    inspecting `[project] dependencies` would leave every other test green.
    """
    live = (_SCRIPT.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    # Found rather than hard-coded: the floor moves with the lockfile and this
    # test must not go red for a floor bump that leaves the cap in place.
    match = re.search(r'"polars>=[^",<]+(,<[^"]+)"', live)
    assert match, "fixture assumption broken: polars is no longer declared with a floor and a cap"

    mutated = tmp_path / "pyproject.toml"
    mutated.write_text(live.replace(match.group(1), "", 1), encoding="utf-8")
    violations = lint_pins.check_pyproject(mutated)

    assert len(violations) == 1
    assert "has no cap" in violations[0].message
    assert "polars" in violations[0].message
