"""Repro for: eventbus-graph-update-payload-underdeclares-fingerprint

CLAIM: `GraphUpdatePayload` (src/haute/_event_bus.py:83-87) declares only
{graph, source_file}, but the file-watcher publishes
{graph, graph_fingerprint, source_file} (src/haute/server.py:644-651).
The publish/subscribe @overloads advertise the narrow (incomplete)
GraphUpdatePayload contract while the impl widens to dict[str, Any], so the
module's documented "static type-checking at every call site" guarantee is
unmet for the graph.update event.

This is a STATIC-CONTRACT (type-checking) finding -- `testable: false` in the
claim. There is no runtime failure: the wire is correct today because
_ws_graph_update_subscriber forwards the whole payload dict (incl.
graph_fingerprint) into the WS frame. So the appropriate reproduction is a
*type-check* reproduction: we run mypy (the project's own checker, configured
in pyproject.toml [tool.mypy] and enforced via .pre-commit-config.yaml
`entry: uv run mypy src/haute/`) on a synthetic probe and ASSERT on the
specific declared-vs-actual behaviour.

ISOLATION: pure in-memory probe text + mypy API. No reads/writes of rating/,
src/, tests/, or any real project file (other than importing haute, which the
probe needs in order to resolve GraphUpdatePayload). mypy writes its cache to
a tempdir we hand it.

Run:  uv run python review/02-findings/repro/routes__eventbus-graph-update-payload-underdeclares-fingerprint.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from mypy import api as mypy_api

# Two independent probes. Each is a standalone module typed against the real
# haute._event_bus contract.

# Probe 1: a subscriber typed against the DECLARED contract (GraphUpdatePayload)
# that accesses graph_fingerprint. If graph_fingerprint were part of the
# contract this would type-check; because it is NOT declared, mypy must reject
# the access -- proving the field the watcher actually sends is absent from the
# declared shape. We also reveal_type to capture the exact declared keys.
PROBE_SUBSCRIBER = """
from __future__ import annotations
from haute._event_bus import EventBus, GraphUpdatePayload

bus = EventBus()

def sub(p: GraphUpdatePayload) -> None:
    reveal_type(p)
    _ = p["graph_fingerprint"]  # field the watcher sends; not in the TypedDict

bus.subscribe("graph.update", sub)
"""

# Probe 2: the PRODUCER side -- the exact 3-key dict literal the watcher
# publishes at server.py:644-651, plus a deliberately WRONG-typed declared key
# (graph=123 where graph: dict[str, Any] is required). If the narrow
# GraphUpdatePayload overload actually constrained the publish call, mypy would
# flag the extra `graph_fingerprint` key and/or the wrong `graph` type. We
# capture whether mypy stays silent (= the narrow contract is NOT enforced at
# the producer; the bare dict literal binds to the wide dict[str, Any]
# fallback overload instead).
PROBE_PRODUCER = """
from __future__ import annotations
from haute._event_bus import EventBus

bus = EventBus()

# Exact server.py:644-651 shape (3 keys, incl. the undeclared graph_fingerprint)
bus.publish(
    "graph.update",
    {"graph": {}, "graph_fingerprint": "v1:abcd", "source_file": "p.py"},
)

# Wrong type for a DECLARED key -- would be caught if the narrow overload bound.
bus.publish(
    "graph.update",
    {"graph": 123, "source_file": "p.py"},
)
"""


def run_mypy(probe_text: str, cache_dir: Path, fname: str) -> tuple[str, str, int]:
    probe_path = cache_dir / fname
    probe_path.write_text(probe_text, encoding="utf-8")
    stdout, stderr, status = mypy_api.run(
        [
            "--config-file",
            str(Path(__file__).resolve().parents[3] / "pyproject.toml"),
            "--cache-dir",
            str(cache_dir / ".mypy_cache"),
            "--no-error-summary",
            str(probe_path),
        ]
    )
    return stdout, stderr, status


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="eb_contract_repro_") as td:
        cache_dir = Path(td)

        sub_out, sub_err, _ = run_mypy(PROBE_SUBSCRIBER, cache_dir, "probe_sub.py")
        prod_out, prod_err, _ = run_mypy(PROBE_PRODUCER, cache_dir, "probe_prod.py")

    print("=== PROBE 1 (subscriber against declared GraphUpdatePayload) ===")
    print(sub_out.strip() or "(no stdout)")
    if sub_err.strip():
        print("[stderr]", sub_err.strip())
    print()
    print("=== PROBE 2 (producer: exact server.py:644 literal + wrong type) ===")
    print(prod_out.strip() or "(no stdout / no issues)")
    if prod_err.strip():
        print("[stderr]", prod_err.strip())
    print()

    failures: list[str] = []

    # --- Assertion 1: declared shape is exactly {graph, source_file}. ---------
    # reveal_type output names every declared key. graph_fingerprint must be
    # absent; graph and source_file must be present.
    if "GraphUpdatePayload" not in sub_out or "reveal" not in sub_out.lower():
        # reveal_type emits a 'note: Revealed type is ...' line.
        if "Revealed type" not in sub_out:
            failures.append(
                "PROBE 1 did not reveal GraphUpdatePayload's type "
                "(probe failed to import/resolve -- setup error, NOT a repro)."
            )
    revealed_line = next(
        (ln for ln in sub_out.splitlines() if "Revealed type" in ln), ""
    )
    if revealed_line:
        if "graph_fingerprint" in revealed_line:
            failures.append(
                "EXPECTED graph_fingerprint ABSENT from declared shape, but "
                f"reveal_type shows it present: {revealed_line!r}"
            )
        if "'graph'" not in revealed_line or "source_file" not in revealed_line:
            failures.append(
                f"declared shape missing expected keys graph/source_file: {revealed_line!r}"
            )

    # --- Assertion 2: accessing graph_fingerprint on the declared TypedDict ---
    # is a hard mypy error (the field the watcher sends is not in the contract).
    if 'has no key "graph_fingerprint"' not in sub_out:
        failures.append(
            "EXPECTED mypy error: TypedDict GraphUpdatePayload has no key "
            '"graph_fingerprint" -- but mypy did NOT flag the access. '
            "If this is missing, the field might actually be declared (claim refuted)."
        )

    # --- Assertion 3: the PRODUCER call site is silently accepted. ------------
    # The exact server.py:644 3-key literal AND a wrong-typed `graph` are both
    # accepted with no error -> the narrow GraphUpdatePayload overload provides
    # no enforcement for publishers; the bare dict binds to dict[str, Any].
    prod_has_error = "error:" in prod_out
    if prod_has_error:
        failures.append(
            "UNEXPECTED: mypy flagged the producer probe. If it flagged the "
            "extra graph_fingerprint key, the narrow overload WOULD be "
            "enforcing the contract -- weakening the 'cannot catch' framing. "
            f"Output: {prod_out.strip()!r}"
        )

    print("=== VERDICT ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(
            "\nRESULT: assertions failed -- claim NOT reproduced as predicted."
        )
        return 1

    print(
        "PASS: GraphUpdatePayload declares exactly {graph, source_file} "
        "(graph_fingerprint ABSENT); a subscriber typed against the declared "
        "contract that reads graph_fingerprint is a mypy error "
        '(`has no key "graph_fingerprint"`); yet the producer\'s exact 3-key '
        "publish literal at server.py:644 -- and even a wrong-typed declared "
        "key -- pass mypy clean (bound to the wide dict[str, Any] fallback "
        "overload). The declared bus contract under-declares graph_fingerprint "
        "and the documented per-call-site static-typing guarantee is unmet for "
        "graph.update. CLAIM REPRODUCED (static/type-check)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
