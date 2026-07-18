"""Repro for V060.

The frontend EdgeJoinEditor's paired-key handlers (updatePairedKey /
removePairedKey) call normalizeKeyRows() INDEPENDENTLY on the left and right
arrays. normalizeKeyRows trims only TRAILING empty strings. Clearing the last
cell on just one side therefore writes unequal-length leftOn/rightOn to config.

This script demonstrates the two endpoints of the problem in isolation:

  1. It replicates the three pure helper functions verbatim from
     EdgeJoinEditor.tsx to compute the EXACT config the editor persists after a
     single cell edit (clear Join key 2 / right side / index 1).

  2. It feeds that persisted config to the REAL backend validator
     haute._edge_join.build_edge_join_kwargs and asserts it raises the
     equal-length ConfigError -- i.e. the editor produced a config the engine
     rejects at runtime.

No project files are read or written; config is a small in-memory dict.
"""

from __future__ import annotations

from haute._edge_join import build_edge_join_kwargs
from haute.errors import ConfigError


# --- Verbatim pure helpers from EdgeJoinEditor.tsx (lines 522-544) ---------


def build_paired_rows(left_keys: list[str], right_keys: list[str]) -> list[dict[str, str]]:
    count = max(len(left_keys), len(right_keys), 1)
    return [
        {
            "left": left_keys[i] if i < len(left_keys) else "",
            "right": right_keys[i] if i < len(right_keys) else "",
        }
        for i in range(count)
    ]


def replace_at(values: list[str], index: int, value: str) -> list[str]:
    return [value if i == index else existing for i, existing in enumerate(values)]


def normalize_key_rows(values: list[str]) -> list[str]:
    last_meaningful = -1
    for i in range(len(values) - 1, -1, -1):
        if values[i] != "":
            last_meaningful = i
            break
    if last_meaningful == -1:
        return []
    return values[: last_meaningful + 1]


# --- Step 1: compute what the editor persists ------------------------------

# Start: paired config leftOn=['a','b'], rightOn=['c','d'] (2 aligned rows).
left_keys = ["a", "b"]
right_keys = ["c", "d"]
paired_rows = build_paired_rows(left_keys, right_keys)

# User clears "Join key 2" -> updatePairedKey(index=1, side="right", value="").
index, side, value = 1, "right", ""
next_left = replace_at(
    [r["left"] for r in paired_rows], index, value if side == "left" else paired_rows[index]["left"]
)
next_right = replace_at(
    [r["right"] for r in paired_rows], index, value if side == "right" else paired_rows[index]["right"]
)
persisted_left_on = normalize_key_rows(next_left)
persisted_right_on = normalize_key_rows(next_right)

print(f"persisted leftOn  = {persisted_left_on!r} (len {len(persisted_left_on)})")
print(f"persisted rightOn = {persisted_right_on!r} (len {len(persisted_right_on)})")

# The bug: a single right-side edit yields UNEQUAL lengths.
assert persisted_left_on == ["a", "b"], persisted_left_on
assert persisted_right_on == ["c"], persisted_right_on
assert len(persisted_left_on) != len(persisted_right_on), "expected misalignment"

# --- Step 2: feed that config to the REAL backend validator ----------------

config = {
    "how": "left",
    "on": [],
    "leftOn": persisted_left_on,
    "rightOn": persisted_right_on,
}

raised = None
try:
    build_edge_join_kwargs(config)
except ConfigError as exc:
    raised = exc

assert raised is not None, "backend accepted misaligned leftOn/rightOn (no error) -- claim would be refuted"
msg = str(raised)
print(f"backend raised: {msg}")
assert "same number of keys" in msg, f"unexpected error message: {msg!r}"

print(
    "RESULT: BUG REPRODUCED -- a single ordinary cell edit produced "
    "leftOn(len 2)/rightOn(len 1), which the backend rejects with ConfigError."
)
