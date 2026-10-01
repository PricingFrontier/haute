"""Check or refresh the golden snapshot of what the assistant's model sees.

The snapshot is the session-stable system prompt for one fixed source file,
the turn context of two turns of that project rendered from fixed data, the
turn context update after an apply in the second turn, the compacted history
of fixed earlier turns, the canonical tool
definitions, and the tools each provider lane sends (its projection of
them), plus a sha256 of each rendered file. The system prompt is rendered once
per turn and must come out identical, so the snapshot shows one prefix for
both turns. The Haute version and the capability hash in the prompt are
replaced with fixed placeholders, because both change on every release while
the text the model reads does not.

Without arguments it exits non-zero and prints a unified diff when a rendered
file differs from the checked-in one; ``--write`` rewrites the files.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

from haute.assistant._config import EgressPolicy
from haute.assistant._render import (
    Authoring,
    BriefFrame,
    BriefInput,
    BriefNode,
    ChangedGraph,
    ContextUpdate,
    GraphBrief,
    PreviewError,
    StepsProblem,
    StepSummary,
    TurnContext,
    render_turn_record,
)
from haute.assistant._session import AssistantTurn, SessionStore
from haute.schemas import AssistantBuildPlan, AssistantChangeRecord, AssistantTurnOutcome

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = PROJECT_ROOT / "tests" / "assistant_eval" / "golden"
HASHES_FILE = "hashes.json"

GOLDEN_SOURCE_FILE = "motor_pricing.py"
# A fixed policy, never the repository's haute.toml, so the snapshot does not
# follow local configuration. Row samples are off in the first turn, so it shows
# the context that asks the analyst for literal values; the second turn permits
# them, as a policy changed mid-session would.
GOLDEN_EGRESS_POLICY = EgressPolicy(
    trust="organization",
    max_sensitivity="restricted",
    allow_project_knowledge=True,
    allow_executable_source=True,
    allow_row_samples=False,
)
_POLICY_COLUMNS = ("policy_id", "driver_age", "vehicle_group", "region", "exposure")
_FEATURE_COLUMNS = (*_POLICY_COLUMNS, "young_driver")
_POLICIES = BriefNode(
    "policies",
    "dataInput",
    "Policies",
    Authoring("stepped", ()),
    (),
    (BriefFrame(None, _POLICY_COLUMNS),),
)
_ADD_FEATURES = BriefNode(
    "add_features",
    "polars",
    "Add features",
    Authoring(
        "stepped",
        (
            StepSummary("start", "source", ("policies",)),
            StepSummary("logic", "free_code", (), "Flag drivers under 25"),
        ),
    ),
    (BriefInput("policies", "policies", _POLICY_COLUMNS),),
    (BriefFrame(None, _FEATURE_COLUMNS),),
)
_PREMIUM = BriefNode(
    "premium",
    "output",
    "Premium",
    None,
    (BriefInput("add_features", "add_features", _FEATURE_COLUMNS),),
    (BriefFrame(None, ("policy_id", "premium")),),
)
_REGION_BANDS = BriefNode(
    "region_bands",
    "polars",
    "Region bands",
    Authoring("incomplete", (), StepsProblem(None, False, "Choose the input to start from.")),
    (BriefInput("add_features", "add_features", _FEATURE_COLUMNS),),
    None,
)
# An unfinished build plan: one item complete, one with no change yet, and one
# whose first change the analyst undid before a second was saved.
_GOLDEN_PLAN = AssistantBuildPlan.model_validate(
    {
        "items": [
            {
                "id": "young_driver",
                "title": "Young-driver flag",
                "complete": True,
                "changes": [{"id": "<change-a>", "undone": False}],
            },
            {"id": "region_band", "title": "Region banding", "complete": False, "changes": []},
            {
                "id": "premium",
                "title": "Premium output",
                "complete": False,
                "changes": [
                    {"id": "<change-b>", "undone": True},
                    {"id": "<change-c>", "undone": False},
                ],
            },
        ]
    }
)
GOLDEN_TURNS = (
    TurnContext(
        GOLDEN_EGRESS_POLICY,
        GraphBrief(
            pipeline_name="motor_pricing",
            revision="<revision-1>",
            nodes=(_ADD_FEATURES, _POLICIES, _PREMIUM),
            selected_node_ids=("add_features",),
            preview_error=PreviewError(
                "add_features",
                "ColumnNotFoundError in step 2 ('logic') of node 'add_features'; it "
                "names column(s) 'driver_age'. Its text is withheld because "
                "[assistant.egress].allow_row_samples is false and the text can quote "
                "row values.",
            ),
        ),
    ),
    TurnContext(
        EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=True,
            allow_executable_source=True,
            allow_row_samples=True,
        ),
        GraphBrief(
            pipeline_name="motor_pricing",
            revision="<revision-2>",
            nodes=(_POLICIES, _ADD_FEATURES, _REGION_BANDS, _PREMIUM),
            selected_node_ids=(),
            preview_error=None,
        ),
        build_plan=_GOLDEN_PLAN,
    ),
)
# The second turn saves a categorical banding in place of the incomplete
# Transform; the update names the new node and the node it reads from.
_SAVED_BANDS = BriefNode(
    "region_band",
    "banding",
    "Region band",
    None,
    (BriefInput("add_features", "add_features", _FEATURE_COLUMNS),),
    (BriefFrame(None, (*_FEATURE_COLUMNS, "region_group")),),
)
GOLDEN_UPDATE = ContextUpdate(
    GOLDEN_TURNS[1].egress,
    ChangedGraph(
        revision="<revision-3>",
        nodes=(_ADD_FEATURES, _SAVED_BANDS),
        removed_node_ids=("region_bands",),
        truncated=False,
    ),
)


def _golden_change(number: int, summary: str, node: str) -> dict[str, object]:
    return {
        "id": f"<change-{number}>",
        "summary": summary,
        "changes": {"nodes": [{"id": node, "type": "Banding", "change": "added"}]},
        "git_sha": None,
        "parent_sha": None,
        "revision": f"<document-revision-{number}>",
    }


def _golden_apply_round(call_id: str, change: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": call_id, "name": "apply_graph_plan", "arguments": {}}],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "apply_graph_plan",
            "content": {"applied_operations": 2, "change": change},
            "is_error": False,
        },
    ]


def _golden_history_turns() -> list[AssistantTurn]:
    """Three earlier turns: the first is left out, the other two become records."""

    quotes = _golden_change(0, "Load data/quotes.parquet as quotes.", "quotes")
    age = _golden_change(1, "Band driver_age into four age bands after quotes.", "age_band")
    vehicle = _golden_change(2, "Group vehicle_group into three vehicle bands.", "vehicle_band")
    question = "Which rating table should rate the age band?"
    return [
        AssistantTurn.from_messages(
            [
                {"role": "user", "content": "Load the quotes file."},
                *_golden_apply_round("apply-0", quotes),
                {"role": "assistant", "content": "Added the quotes input."},
            ],
            outcome=AssistantTurnOutcome(kind="applied", detail=None, changes=["<change-0>"]),
        ),
        AssistantTurn.from_messages(
            [
                {
                    "role": "user",
                    "content": "Add an age band after quotes, then a vehicle group band.",
                },
                *_golden_apply_round("apply-1", age),
                *_golden_apply_round("apply-2", vehicle),
                {"role": "assistant", "content": "Saved both bands."},
            ],
            outcome=AssistantTurnOutcome(
                kind="applied", detail=None, changes=["<change-1>", "<change-2>"]
            ),
            undone=[AssistantChangeRecord.model_validate(vehicle)],
            build_plan=AssistantBuildPlan.model_validate(
                {
                    "items": [
                        {
                            "id": "age_band",
                            "title": "Age bands",
                            "complete": True,
                            "changes": [{"id": "<change-1>", "undone": False}],
                        },
                        {
                            "id": "vehicle_band",
                            "title": "Vehicle group bands",
                            "complete": True,
                            "changes": [{"id": "<change-2>", "undone": False}],
                        },
                        {
                            "id": "rating",
                            "title": "Rate the age band",
                            "complete": False,
                            "changes": [],
                        },
                    ]
                }
            ),
        ),
        AssistantTurn.from_messages(
            [
                {"role": "user", "content": "Rate the age band."},
                {"role": "assistant", "content": f"NEEDS_INPUT: {question}"},
            ],
            outcome=AssistantTurnOutcome(kind="needs_input", detail=question, changes=[]),
        ),
    ]


def render_golden_history() -> str:
    """The provider history the fourth turn of a chat receives, one message per section.

    The budget fits exactly the two newest records, so the oldest turn is left
    out and the omission note leads.
    """

    turns = _golden_history_turns()
    budget = sum(
        len(turn.record().request) + len(render_turn_record(turn.record())) for turn in turns[1:]
    )
    store = SessionStore(max_provider_history_chars=budget)
    session = store.create(GOLDEN_SOURCE_FILE)
    for turn in turns:
        store.append(session, turn)
    return "\n\n".join(
        f"----- {message['role']} -----\n{message['content']}"
        for message in store.provider_history(session)
    )


HAUTE_VERSION_PLACEHOLDER = "<haute-version>"
CAPABILITY_HASH_PLACEHOLDER = "<capability-hash>"


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {old!r} in the system prompt, found {count}")
    return text.replace(old, new)


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def render_golden() -> dict[str, str]:
    """Render every golden file, keyed by its name under ``GOLDEN_ROOT``."""

    from haute.assistant._catalog import capability_manifest
    from haute.assistant._loop import build_system_prompt
    from haute.assistant._providers import _canonical_tools, _compatible_tools, _openai_tools
    from haute.assistant._render import render_context_update, render_turn_context
    from haute.assistant._tools import TOOL_DEFINITIONS

    manifest = capability_manifest()
    prompts = {build_system_prompt(source_file=GOLDEN_SOURCE_FILE) for _turn in GOLDEN_TURNS}
    if len(prompts) != 1:
        raise ValueError("The system prompt differs between the turns of one session")
    (prompt,) = prompts
    prompt = _replace_once(
        prompt,
        f"- Haute version: `{manifest.haute_version}`",
        f"- Haute version: `{HAUTE_VERSION_PLACEHOLDER}`",
    )
    prompt = _replace_once(
        prompt,
        f"- Capability hash: `{manifest.capability_hash}`",
        f"- Capability hash: `{CAPABILITY_HASH_PLACEHOLDER}`",
    )
    files = {
        "system_prompt.md": prompt + "\n",
        **{
            f"turn_context_{number}.md": render_turn_context(context) + "\n"
            for number, context in enumerate(GOLDEN_TURNS, start=1)
        },
        "context_update.md": render_context_update(GOLDEN_UPDATE) + "\n",
        "turn_records.md": render_golden_history() + "\n",
        "tools_canonical.json": _json_text(TOOL_DEFINITIONS),
        "tools_anthropic.json": _json_text(_canonical_tools(TOOL_DEFINITIONS, "anthropic")),
        "tools_openai.json": _json_text(
            _openai_tools(_canonical_tools(TOOL_DEFINITIONS, "openai"))
        ),
        "tools_databricks.json": _json_text(_openai_tools(_compatible_tools(TOOL_DEFINITIONS))),
    }
    files[HASHES_FILE] = _json_text(
        {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in files.items()
        }
    )
    return files


def golden_diff(name: str, expected: str, actual: str) -> str:
    """Return a unified diff from the checked-in file to the rendered one."""

    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"golden/{name}",
            tofile=f"rendered/{name}",
        )
    )


def read_golden(name: str) -> str | None:
    path = GOLDEN_ROOT / name
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8")


def check() -> list[str]:
    """Return one unified diff per golden file that differs from its rendering."""

    rendered = render_golden()
    diffs = [
        f"golden/{path.name} is not rendered by this script; delete it\n"
        for path in sorted(GOLDEN_ROOT.glob("*"))
        if path.name not in rendered
    ]
    for name, actual in rendered.items():
        expected = read_golden(name)
        if expected is None:
            diffs.append(f"golden/{name} is missing\n")
        elif expected != actual:
            diffs.append(golden_diff(name, expected, actual))
    return diffs


def write() -> list[str]:
    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for name, content in render_golden().items():
        if read_golden(name) != content:
            (GOLDEN_ROOT / name).write_bytes(content.encode("utf-8"))
            changed.append(name)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the golden files instead of failing with a unified diff",
    )
    args = parser.parse_args(argv)
    if not args.write:
        diffs = check()
        for diff in diffs:
            sys.stdout.write(diff)
        return 1 if diffs else 0
    for name in write():
        print(f"updated {GOLDEN_ROOT.relative_to(PROJECT_ROOT).as_posix()}/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
