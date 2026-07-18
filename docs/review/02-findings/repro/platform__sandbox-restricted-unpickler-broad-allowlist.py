"""Adversarial repro for claim: sandbox-restricted-unpickler-broad-allowlist.

Claim: safe_unpickle/safe_joblib_load allow the ENTIRE numpy/sklearn/scipy/
pandas/joblib trees (single-segment prefix entries). Because pickle's reduce
machinery still executes for allowlisted callables, a crafted .pkl using ONLY
allowlisted globals can drive non-trivial object construction / state injection
during load — so the restriction provides little real isolation.

This repro does NOT try to call os.system (that IS blocked — those modules are
not allowlisted). Instead it proves the NARROWER claim the finding actually
makes: allowlisted reconstructors/estimators are CONSTRUCTION-CAPABLE and the
unpickler returns them and runs them, so attacker-chosen state is materialised.

Isolation: all disk I/O via tempfile; project root set to the tempdir via
haute._sandbox.set_project_root; never touches src/, tests/, or rating/.

Each check ASSERTS on a specific attacker-chosen VALUE (expected vs actual),
not merely that "something raised".
"""

from __future__ import annotations

import pickle
import pickletools
import tempfile
from pathlib import Path

import numpy as np

from haute import _sandbox
from haute._sandbox import (
    _ALLOWED_PICKLE_PREFIXES,
    _pickle_global_is_allowed,
    safe_joblib_load,
    safe_unpickle,
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}: {detail}")
    if not condition:
        failures.append(f"{label}: {detail}")


# ---------------------------------------------------------------------------
# Part 1 — the allowlist permits ARBITRARY qualnames under single-segment pkgs
# ---------------------------------------------------------------------------
# The finding's premise: ('numpy',), ('sklearn',), ('scipy',), ('pandas',),
# ('joblib',) are whole-package entries, so any submodule/qualname matches.
print("\n=== Part 1: prefix allowlist breadth ===")

single_segment = {p[0] for p in _ALLOWED_PICKLE_PREFIXES if len(p) == 1}
for pkg in ("numpy", "sklearn", "scipy", "pandas", "joblib"):
    check(
        f"single-segment-entry::{pkg}",
        pkg in single_segment,
        f"{pkg!r} is a whole-package allowlist entry: {pkg in single_segment}",
    )

# Arbitrary deep qualnames under those packages are permitted regardless of the
# *name* — find_class only filters by module prefix.
broad_cases = [
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core._multiarray_umath", "_reconstruct"),
    ("numpy", "literally_anything_at_all"),
    ("sklearn.linear_model._base", "LinearRegression"),
    ("sklearn.some.deeply.nested.module", "AnyName"),
    ("scipy.sparse._csr", "csr_matrix"),
    ("pandas.core.internals.blocks", "new_block"),
    ("joblib.numpy_pickle", "NumpyArrayWrapper"),
]
for mod, name in broad_cases:
    check(
        f"prefix-permits::{mod}.{name}",
        _pickle_global_is_allowed(mod, name),
        f"_pickle_global_is_allowed({mod!r}, {name!r}) -> True (arbitrary name allowed)",
    )

# Sanity: a sibling that only shares a prefix string is NOT allowed (this is the
# one real narrowing — guards against 'numpy_evil'). Confirms our match logic
# understanding is right and the breadth is intentional, not accidental.
check(
    "prefix-narrowing::numpy_evil-blocked",
    not _pickle_global_is_allowed("numpy_evil", "x"),
    f"_pickle_global_is_allowed('numpy_evil', 'x') -> {_pickle_global_is_allowed('numpy_evil', 'x')} (must be False)",
)

# ---------------------------------------------------------------------------
# Part 2 — a pickle using ONLY an allowlisted global constructs attacker data
# ---------------------------------------------------------------------------
# Build an array with attacker-chosen contents the *normal* way, then dump and
# RE-LOAD through safe_unpickle. The opcode stream uses numpy reconstruct +
# multiarray scalar/ndarray globals, all allowlisted. We assert the loaded
# value equals attacker-chosen bytes -> construction proceeded, not blocked.
print("\n=== Part 2: allowlisted-only pickle drives object construction ===")

with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    _sandbox.set_project_root(root)

    attacker_array = np.frombuffer(
        b"\xde\xad\xbe\xef\xca\xfe\xba\xbe", dtype=np.uint8
    ).copy()
    pkl_path = root / "payload.pkl"
    pkl_path.write_bytes(pickle.dumps(attacker_array, protocol=4))

    # Show the opcode stream only references allowlisted modules.
    used_globals: list[tuple[str, str]] = []
    for opcode, arg, _pos in pickletools.genops(pkl_path.read_bytes()):
        if opcode.name in ("STACK_GLOBAL", "GLOBAL") and arg is not None:
            # STACK_GLOBAL pulls (module, name) off the stack; for inspection we
            # re-derive via a quick disassemble of memo strings instead. Easier:
            # just confirm every global the real unpickler sees is allowlisted,
            # which we do below by loading successfully.
            used_globals.append((opcode.name, str(arg)))

    # The decisive test: load via the RESTRICTED unpickler. If the broad
    # allowlist truly contained nothing construction-capable, this would raise.
    # It does NOT raise — it returns the fully reconstructed attacker array.
    loaded = safe_unpickle(str(pkl_path))

    check(
        "construct::array-type",
        isinstance(loaded, np.ndarray),
        f"safe_unpickle returned np.ndarray (type={type(loaded).__name__})",
    )
    check(
        "construct::attacker-bytes-materialised",
        loaded.tobytes() == b"\xde\xad\xbe\xef\xca\xfe\xba\xbe",
        f"reconstructed bytes == attacker-chosen payload: {loaded.tobytes()!r}",
    )

    # Prove the constructor that ran is an allowlisted numpy global. Find the
    # actual (module, name) globals numpy emitted for this array.
    disasm_globals: list[tuple[str, str]] = []
    ops = list(pickletools.genops(pkl_path.read_bytes()))
    # SHORT_BINUNICODE / BINUNICODE args preceding STACK_GLOBAL are the
    # (module, name) pair. Walk and pair them up.
    pending: list[str] = []
    for opcode, arg, _pos in ops:
        if opcode.name in ("SHORT_BINUNICODE", "BINUNICODE", "BINUNICODE8"):
            pending.append(str(arg))
        elif opcode.name == "STACK_GLOBAL":
            if len(pending) >= 2:
                disasm_globals.append((pending[-2], pending[-1]))
        elif opcode.name not in ("MEMOIZE", "FRAME"):
            # Reset pending on structural ops so we only pair adjacent strings.
            if opcode.name in ("EMPTY_DICT", "MARK", "TUPLE", "TUPLE1", "TUPLE2", "TUPLE3"):
                pending = []

    reconstruct_globals = [g for g in disasm_globals if g[1] in ("_reconstruct", "ndarray", "scalar", "dtype")]
    all_allowlisted = all(_pickle_global_is_allowed(m, n) for m, n in disasm_globals)
    check(
        "construct::globals-are-allowlisted-numpy",
        len(reconstruct_globals) >= 1 and all_allowlisted,
        f"globals emitted by payload all allowlisted={all_allowlisted}; "
        f"reconstruct-family globals seen={reconstruct_globals}",
    )

# ---------------------------------------------------------------------------
# Part 3 — state injection into an allowlisted sklearn estimator
# ---------------------------------------------------------------------------
# An attacker fully controls a persisted estimator's __dict__/state. Loading via
# the restricted unpickler reconstructs the estimator AND applies attacker state
# (e.g. a forged coef_ that would silently change downstream predictions).
print("\n=== Part 3: attacker-controlled estimator state survives the allowlist ===")

with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    _sandbox.set_project_root(root)

    from sklearn.linear_model import LinearRegression

    forged = LinearRegression()
    # Inject state an attacker would choose — a coefficient vector and intercept
    # that were never produced by any fit() the operator ran.
    forged.coef_ = np.array([1234.5, -9999.0])
    forged.intercept_ = np.float64(42.0)
    forged.n_features_in_ = 2

    f = root / "model.joblib"
    import joblib

    joblib.dump(forged, str(f))

    loaded_model = safe_joblib_load(str(f))

    check(
        "state-inject::type-preserved",
        isinstance(loaded_model, LinearRegression),
        f"restricted load returned LinearRegression (type={type(loaded_model).__name__})",
    )
    check(
        "state-inject::forged-coef-survives",
        np.array_equal(getattr(loaded_model, "coef_", None), [1234.5, -9999.0]),
        f"attacker-forged coef_ materialised intact: {getattr(loaded_model, 'coef_', None)}",
    )
    check(
        "state-inject::forged-intercept-survives",
        float(getattr(loaded_model, "intercept_", -1)) == 42.0,
        f"attacker-forged intercept_ materialised intact: {getattr(loaded_model, 'intercept_', None)}",
    )
    # Decisive functional consequence: predictions are driven by forged state,
    # i.e. y = 1234.5*x0 - 9999*x1 + 42. This is silent mis-state injection that
    # the 'restriction' did nothing to prevent.
    pred = float(loaded_model.predict(np.array([[1.0, 0.0]]))[0])
    expected = 1234.5 * 1.0 - 9999.0 * 0.0 + 42.0
    check(
        "state-inject::prediction-uses-forged-state",
        abs(pred - expected) < 1e-6,
        f"predict([[1,0]]) -> {pred} (== forged 1234.5+42 = {expected})",
    )

# ---------------------------------------------------------------------------
# Verdict summary
# ---------------------------------------------------------------------------
print("\n=== SUMMARY ===")
if failures:
    print(f"REPRO RESULT: {len(failures)} assertion(s) did not hold:")
    for fmsg in failures:
        print(f"  - {fmsg}")
    print(
        "\nIf the FAILs are in Part 1 prefix breadth, the broad-allowlist premise "
        "is wrong. If Parts 2/3 PASS, the unpickler DID construct attacker state."
    )
else:
    print(
        "REPRO RESULT: ALL CHECKS PASSED.\n"
        "The allowlist permits arbitrary qualnames under whole-package entries, "
        "and the restricted unpickler reconstructed BOTH an attacker-chosen numpy "
        "array (Part 2) and a fully attacker-forged sklearn estimator with forged "
        "coef_/intercept_ that drive predictions (Part 3) — using ONLY allowlisted "
        "globals. This substantiates the finding's mechanism: the restriction "
        "blocks os.system-style RCE globals but does NOT prevent construction / "
        "state injection from allowlisted ML/data trees."
    )
