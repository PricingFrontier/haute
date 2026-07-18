"""Isolated reproduction for BUG-4.

Claim: `_friendly_error` (src/haute/routes/_train_service.py:297-322) classifies
ANY exception whose *message* contains the substring 'catboost' (case-insensitive)
as a "CatBoost error", because the branch at line 308 tests `'catboost' in msg.lower()`
and is evaluated BEFORE the `isinstance(exc, OSError)` branch at line 319 and before the
generic fallback at line 322.

Consequence: a PermissionError / generic OSError / RuntimeError that merely references a
catboost artifact path in its message is narrated as a "CatBoost error: ..." instead of the
accurate "Could not save model file: ..." (OSError) or "Training failed (RuntimeError): ..."
(generic). The user is sent to fix the model/data rather than the file-system / underlying
problem.

This repro ONLY imports the pure function `_friendly_error` and feeds it synthetic, in-memory
exception objects. It touches NO disk, no rating/, no project files. The function itself does
no IO.

Run: uv run python review/03-simplification/repro/routes__BUG-4.py
Exit 0 == bug reproduced; exit 1 == bug NOT reproduced (claim refuted).
"""

from __future__ import annotations

from haute.routes._train_service import _friendly_error

failures: list[str] = []


def expect(label: str, got: str, *, must_start: str | None = None, must_not_start: str | None = None) -> None:
    print(f"  [{label}] -> {got!r}")
    if must_start is not None and not got.startswith(must_start):
        failures.append(f"{label}: expected message to start with {must_start!r}, got {got!r}")
    if must_not_start is not None and got.startswith(must_not_start):
        failures.append(f"{label}: message wrongly started with {must_not_start!r}: {got!r}")


# --- Control: a genuine CatBoost-typed error (simulated by class name) -------------------
# The first disjunct of line 308 is `'CatBoost' in exc_type`. Reproduce a real CatBoost
# exception type by name without importing catboost.
class CatBoostError(Exception):
    """Mirror of catboost's exception class name (matched via type(exc).__name__)."""


print("== sanity: a genuinely CatBoost-typed error is still narrated as CatBoost ==")
genuine = CatBoostError("bad value in column 3")
expect("CatBoostError type", _friendly_error(genuine), must_start="CatBoost error:")


# --- BUG core case 1: PermissionError (an OSError subclass) with a catboost-pathed message
# Real-world: the model-save directory is read-only, OS raises PermissionError whose strerror
# embeds the catboost .cbm path. Line 304 only catches FileNotFoundError, NOT PermissionError,
# so flow reaches line 308; 'catboost' is in the message -> mislabeled.
print("\n== BUG case 1: PermissionError mentioning a catboost artifact path ==")
perm = PermissionError(13, "Permission denied", "/models/catboost_model.cbm")
got_perm = _friendly_error(perm)
# It SHOULD be the OSError branch (line 319/320): "Could not save model file: ..."
expect(
    "PermissionError w/ catboost path",
    got_perm,
    must_start="CatBoost error:",          # <-- the WRONG label we assert is produced
    must_not_start="Could not save model file:",  # the correct label it should have had
)


# --- BUG core case 2: a bare OSError whose message references a catboost path ----------
print("\n== BUG case 2: generic OSError mentioning a catboost artifact path ==")
ose = OSError("could not open /models/catboost_model.cbm: disk full")
got_ose = _friendly_error(ose)
expect(
    "OSError w/ catboost path",
    got_ose,
    must_start="CatBoost error:",
    must_not_start="Could not save model file:",
)


# --- BUG core case 3: a wrapped third-party RuntimeError mentioning catboost -----------
# Not an OSError at all; correct narration would be the generic
# "Training failed (RuntimeError): ...". Instead it is hijacked by the catboost-message branch.
print("\n== BUG case 3: RuntimeError mentioning catboost (should be generic fallback) ==")
rte = RuntimeError("wrapped: failed to load catboost plugin shim")
got_rte = _friendly_error(rte)
expect(
    "RuntimeError mentioning catboost",
    got_rte,
    must_start="CatBoost error:",
    must_not_start="Training failed (RuntimeError):",
)


# --- Negative control: identical OSError WITHOUT the word catboost is narrated correctly
# This proves the mislabel is driven by the message substring, not by the exception type.
print("\n== control: same PermissionError WITHOUT 'catboost' in the message ==")
perm_clean = PermissionError(13, "Permission denied", "/models/model_v3.bin")
got_clean = _friendly_error(perm_clean)
expect(
    "PermissionError w/o catboost",
    got_clean,
    must_start="Could not save model file:",   # correct OSError narration
    must_not_start="CatBoost error:",
)
control_diff = got_perm.startswith("CatBoost error:") and got_clean.startswith("Could not save model file:")


# --- Safe case asserted by the claim: FileNotFoundError IS caught earlier (line 304) ---
print("\n== claim's stated-safe case: FileNotFoundError with catboost path stays 'File not found' ==")
fnf = FileNotFoundError(2, "No such file or directory", "/models/catboost_model.cbm")
got_fnf = _friendly_error(fnf)
expect(
    "FileNotFoundError w/ catboost path",
    got_fnf,
    must_start="File not found:",
    must_not_start="CatBoost error:",
)


print("\n================ RESULT ================")
if failures:
    print("CLAIM NOT REPRODUCED — _friendly_error behaved as the (correct) expectation:")
    for f in failures:
        print("  FAIL:", f)
    raise SystemExit(1)

print("CLAIM REPRODUCED: a non-CatBoost exception whose MESSAGE mentions 'catboost'")
print("is mislabeled 'CatBoost error:' because line 308 (message substring) precedes")
print("the OSError branch (line 319) and the generic fallback (line 322).")
print(f"Discriminating control held: catboost-msg -> CatBoost label, clean-msg -> OSError label = {control_diff}")
print(f"  PermissionError(catboost) : {got_perm!r}")
print(f"  PermissionError(clean)    : {got_clean!r}")
raise SystemExit(0)
