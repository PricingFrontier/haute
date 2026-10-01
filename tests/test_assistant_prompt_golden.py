"""Golden snapshot of the system prompt and tool schemas the model receives."""

from __future__ import annotations

import json

import pytest

_UPDATE_COMMAND = "uv run python scripts/update_assistant_prompt_golden.py --write"


def test_model_facing_prompt_and_tool_schemas_match_the_golden_files() -> None:
    from scripts.update_assistant_prompt_golden import check

    diffs = check()
    if diffs:
        pytest.fail(
            "What the assistant's model sees has changed. Review the diff and, if it is "
            f"intended, run `{_UPDATE_COMMAND}`.\n\n" + "\n".join(diffs),
            pytrace=False,
        )


def test_golden_prompt_excludes_the_release_version_and_capability_hash() -> None:
    from haute.assistant._catalog import capability_manifest
    from scripts.update_assistant_prompt_golden import render_golden

    manifest = capability_manifest()
    prompt = render_golden()["system_prompt.md"]
    assert "- Haute version: `<haute-version>`" in prompt
    assert "- Capability hash: `<capability-hash>`" in prompt
    assert manifest.capability_hash not in prompt


def test_a_changed_prompt_sentence_or_wire_description_fails_with_the_changed_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.assistant._loop as loop_module
    import haute.assistant._tools as tools_module
    from haute.assistant._wire_ops import _NODE_REFERENCE_DESCRIPTION
    from scripts.update_assistant_prompt_golden import check

    monkeypatch.setattr(
        loop_module,
        "_PROMPT_OUTCOME_CONTRACT",
        loop_module._PROMPT_OUTCOME_CONTRACT + "A sentence added for the golden test.\n",
    )
    wire_description = json.dumps(_NODE_REFERENCE_DESCRIPTION)
    canonical = json.dumps(tools_module.TOOL_DEFINITIONS)
    assert wire_description in canonical
    edited = json.loads(
        canonical.replace(wire_description, '"A description edited for the golden test."')
    )
    monkeypatch.setattr(tools_module, "TOOL_DEFINITIONS", edited)

    diffs = "\n".join(check())

    added = [line for line in diffs.splitlines() if line.startswith("+")]

    assert any("A sentence added for the golden test." in line for line in added)
    assert any('"A description edited for the golden test."' in line for line in added)
    for name in (
        "system_prompt.md",
        "tools_canonical.json",
        "tools_anthropic.json",
        "tools_openai.json",
        "tools_databricks.json",
        "hashes.json",
    ):
        assert f"--- golden/{name}" in diffs
