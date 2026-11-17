#!/usr/bin/env python3
"""The step round-trip lane: a step made in the editor survives being saved and read back.

The step editor and the backend each describe a step by hand. The step shapes
fixture (``scripts/regen_step_shapes_fixture.py``) pins the shapes the backend
reads back for every vocabulary entry on every pull request; this lane checks,
weekly and wider, what that fixture cannot enumerate:

1. ``tests/test_polars_steps_lift.py``'s generators draw random step lists, which
   the backend renders and reads back (``backend_read_backs.json``).
2. The editor's lane test ``roundTripEdits.lane.test.tsx`` makes every single
   edit each form offers, and one more below each button, from every step of the
   step shapes fixture (``form_edits.json``).
3. Each edited step is rendered in its seed's step list and read back with
   ``lift_steps`` (``round_trips.json``). A refusal is a finding unless an entry
   of ``ALLOWED_REFUSALS`` explains it for that kind of edit; an edit that renders
   but does not read back as one step is a finding.
4. The editor's lane test ``roundTripCheck.lane.test.ts`` requires the editor to
   take every read-back of steps 1 and 3, a round trip as the same kind of step
   with the same settings and the same card summary (``editor_report.json``).
   Set aside are only what the code cannot hold and no result depends on: an
   expression's typed formula text, an operand wrapping a plain operand, and the
   match of a group of one condition.

The report goes to standard output, ``report.md`` in the work directory and the
GitHub job summary; the lane fails on any finding::

    uv run python scripts/run_step_round_trip.py --work-dir <empty directory>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from haute._polars_steps import PolarsStepError, render_node_steps  # noqa: E402
from haute._polars_steps_lift import StepLiftError, lift_steps  # noqa: E402
from haute._types import NodeType  # noqa: E402

STEP_SHAPES = ROOT / "frontend" / "src" / "panels" / "editors" / "polarsSteps" / "__tests__"
EDITS_TEST = STEP_SHAPES / "roundTripEdits.lane.test.tsx"
CHECK_TEST = STEP_SHAPES / "roundTripCheck.lane.test.ts"
FIXTURE = STEP_SHAPES / "fixtures" / "stepShapes.json"

Step = dict[str, Any]
#: Every kind of edit the editor's lane test records; "button>" is an edit after a button.
EVERY_EDIT = frozenset(
    {
        "select",
        "toggle",
        "type",
        "button",
        "button>select",
        "button>toggle",
        "button>type",
        "button>button",
    }
)
TYPED = frozenset({"type", "button>type"})


@dataclass(frozen=True)
class Allowed:
    """A renderer refusal a user meets by an edit the form rightly allows, and why."""

    pattern: str
    edits: frozenset[str]
    why: str


#: Reviewed: each refusal the editor's own edits lead to, for the edits that lead to it.
ALLOWED_REFUSALS: tuple[Allowed, ...] = (
    Allowed(
        r"must be a non-empty string|must be a list of non-empty strings|must list at least one|"
        r"must name at least one|key lists must be non-empty|Add at least one|Name at least one",
        EVERY_EDIT,
        "a setting the edit leaves to fill in (a row just added, a list's last entry removed, an "
        "operator or join type that now needs a value or keys); the card names it",
    ),
    Allowed(
        r"must be a date written as YYYY-MM-DD",
        EVERY_EDIT,
        "a date value not filled in yet, after its type became date",
    ),
    Allowed(
        r"needs partition columns to order within",
        frozenset({"button", "button>select", "button>type", "button>toggle", "button>button"}),
        "an in-group order added to a window with no group columns; the form's hint says ordering "
        "needs one",
    ),
    Allowed(
        r"repeats the value (True|False)\.",
        frozenset({"select", "button>select", "button"}),
        "a true/false pivot given a third column: there are only two values, so the card asks "
        "for one to go",
    ),
    Allowed(
        r"must be whole numbers to match a whole-number column",
        frozenset({"select", "button>select"}),
        "a membership list's number type changed to whole numbers while it holds fractions",
    ),
    Allowed(
        r"format must be a date format|is not a valid name|is already defined|"
        r"is renamed more than once|cannot be both unpivoted and kept as index|"
        r"is used more than once|repeats the value|is not defined by an earlier step",
        TYPED,
        "a typed value that contradicts another setting, or renames a variable a later step "
        "still reads (the reference stays, flagged)",
    ),
)


def _numbered(steps: list[Step]) -> list[Step]:
    return [{**step, "id": f"s{index}"} for index, step in enumerate(steps)]


def backend_read_backs(
    examples: int, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Random step lists as the backend reads them back, and those that fail to read back."""
    from hypothesis import HealthCheck, given, settings
    from hypothesis import seed as hypothesis_seed

    from tests.test_polars_steps_lift import programs

    read_backs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    @hypothesis_seed(seed)
    @settings(
        max_examples=examples, database=None, deadline=None, suppress_health_check=list(HealthCheck)
    )
    @given(program=programs())
    def draw(program: tuple[NodeType, list[Step]]) -> None:
        node_type, steps = program
        try:
            code = render_node_steps(node_type, steps).code
        except PolarsStepError:
            return
        try:
            read_backs.append({"node_type": node_type.value, "steps": lift_steps(node_type, code)})
        except StepLiftError as exc:
            failures.append({"check": "backend read-back", "detail": str(exc), "example": steps})

    draw()
    return read_backs, failures


def round_trips(
    edits: list[dict[str, Any]], seeds: dict[str, list[Step]]
) -> tuple[list[dict[str, Any]], Counter[tuple[str, str, bool]], list[dict[str, Any]]]:
    """Each edit rendered in its seed's step list and read back; refusals counted by message."""
    trips: list[dict[str, Any]] = []
    refusals: Counter[tuple[str, str, bool]] = Counter()
    findings: list[dict[str, Any]] = []
    for edit in edits:
        program = list(seeds[edit["seed"]])
        program[edit["index"]] = edit["step"]
        try:
            code = render_node_steps(NodeType.POLARS, _numbered(program)).code
        except PolarsStepError as exc:
            message = re.sub(r"^Step \d+: ", "", str(exc))
            allowed = any(
                re.search(entry.pattern, message) and edit["action"] in entry.edits
                for entry in ALLOWED_REFUSALS
            )
            refusals[(message, edit["action"], allowed)] += 1
            if not allowed:
                findings.append(
                    {"check": "refusal no entry explains", "detail": message, "example": edit}
                )
            continue
        try:
            lifted = lift_steps(NodeType.POLARS, code)
        except StepLiftError as exc:
            findings.append(
                {
                    "check": "round trip",
                    "detail": f"renders but does not read back: {exc}",
                    "example": {**edit, "code": code},
                }
            )
            continue
        if len(lifted) != len(program):
            kinds = [step["kind"] for step in lifted]
            findings.append(
                {
                    "check": "round trip",
                    "detail": f"reads back as {len(lifted)} steps ({kinds})",
                    "example": {**edit, "code": code},
                }
            )
            continue
        trips.append(
            {
                "seed": edit["seed"],
                "action": edit["action"],
                "before": edit["step"],
                "after": lifted[edit["index"]],
            }
        )
    return trips, refusals, findings


def run_lane_test(test: Path, work_dir: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("The step round-trip lane needs Node and npm on PATH.")
    command = [
        npm,
        "--prefix",
        str(ROOT / "frontend"),
        "run",
        "test:lanes",
        "--",
        test.relative_to(ROOT / "frontend").as_posix(),
    ]
    completed = subprocess.run(
        command, env={**os.environ, "HAUTE_STEP_ROUND_TRIP_DIR": str(work_dir)}, check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"{test.name} failed with exit code {completed.returncode}.")


def _grouped(findings: list[dict[str, Any]]) -> list[tuple[str, int, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, Any] = {}
    for finding in findings:
        key = f"{finding['check']}: {re.sub(r'[0-9]+', 'N', finding['detail'])}"
        counts[key] += 1
        examples.setdefault(key, finding["example"])
    return [(key, count, examples[key]) for key, count in counts.most_common()]


def report(
    seed: int,
    counts: dict[str, int],
    refusals: Counter[tuple[str, str, bool]],
    findings: list[dict[str, Any]],
) -> str:
    lines = [
        "# Step round trip",
        "",
        f"Hypothesis seed {seed}. "
        + ", ".join(f"{value} {name}" for name, value in counts.items())
        + ".",
        "",
    ]
    if findings:
        lines += [f"## {len(findings)} findings", ""]
        for key, count, example in _grouped(findings):
            lines += [f"- **{count}** {key}", f"  - e.g. `{json.dumps(example)[:600]}`"]
    else:
        lines += [
            "No findings: every edit that renders reads back, and the editor takes every "
            "read-back as the step it was."
        ]
    by_message: Counter[tuple[str, bool]] = Counter()
    for (message, _action, allowed), count in refusals.items():
        by_message[(re.sub(r"[0-9]+", "N", message), allowed)] += count
    lines += [
        "",
        "## Renderer refusals of form edits",
        "",
        "| Count | Explained | Message |",
        "|---:|---|---|",
    ]
    lines += [
        f"| {count} | {'yes' if allowed else '**no**'} | {message[:160]} |"
        for (message, allowed), count in by_message.most_common()
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="An empty directory for the lane's files (default: a new temporary one).",
    )
    parser.add_argument(
        "--examples", type=int, default=3000, help="Random step lists to draw (default 3000)."
    )
    parser.add_argument("--seed", type=int, default=None, help="Hypothesis seed, to repeat a run.")
    args = parser.parse_args(argv)
    work_dir: Path = args.work_dir or Path(tempfile.mkdtemp(prefix="step-round-trip-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**32)

    def write(name: str, value: object) -> None:
        (work_dir / name).write_text(json.dumps(value), encoding="utf-8")

    def read(name: str) -> Any:
        return json.loads((work_dir / name).read_text(encoding="utf-8"))

    step: Callable[[str], None] = lambda text: print(f"[step round trip] {text}", flush=True)  # noqa: E731
    step(f"drawing {args.examples} random step lists (seed {seed})")
    read_backs, findings = backend_read_backs(args.examples, seed)
    write("backend_read_backs.json", read_backs)
    step("making every edit each step form offers")
    run_lane_test(EDITS_TEST, work_dir)
    edits = read("form_edits.json")
    seeds = {
        case["id"]: case["steps"]
        for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    }
    step(f"saving and reading back {len(edits)} edited steps")
    trips, refusals, trip_findings = round_trips(edits, seeds)
    findings += trip_findings
    write("round_trips.json", trips)
    step("checking the editor against every read-back")
    run_lane_test(CHECK_TEST, work_dir)
    editor = read("editor_report.json")
    findings += editor["findings"]
    counts = {
        "random read-backs": len(read_backs),
        "form edits": len(edits),
        "round trips": len(trips),
        "refused edits": sum(refusals.values()),
    }
    text = report(seed, counts, refusals, findings)
    (work_dir / "report.md").write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
