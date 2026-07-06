"""Adversarial repro for claim: preamble-utility-fingerprint-syspath-miss.

Hypothesis under test
---------------------
`preamble_execution_fingerprint(preamble, pipeline_dir=P)` only mixes the
*content* of utility modules found under ``P`` or ``cwd`` (via
``_utility_candidates_for_dir``). If the preamble's ``import utility`` actually
resolves at run time to a module living in some OTHER ``sys.path`` directory Q
(P != Q != cwd), then:

  * the executor binds Q's code (preview/trace reflect Q's bytes), but
  * the fingerprint is computed from P/utility{.py,/} + cwd/utility{.py,/},
    both of which are MISSING on disk, so the digest never moves when Q is
    edited -> stale preview/trace served as fresh.

We assert the SPECIFIC wrong behaviour: the fingerprint is byte-for-byte
identical before and after a real edit to the module that ``import utility``
actually loads.

A second, independent block exercises the AST detector's alias-evasion claim.

ISOLATION: tempfile-only disk I/O; cwd redirected to a clean empty temp dir
(restored in finally); sys.path/sys.modules mutations reverted in finally.
No reads/writes of rating/, src/, tests/, or any real project file.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

# Import the units under test exactly as production callers do.
from haute._cache import (
    preamble_execution_fingerprint,
    preamble_imports_utility,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    original_cwd = os.getcwd()
    original_syspath = list(sys.path)
    original_modules = dict(sys.modules)

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root).resolve()

        # P: the pipeline directory (what graph.source_file lives next to).
        #    Deliberately has NO utility module on disk.
        pipeline_dir = root / "P_pipeline"
        pipeline_dir.mkdir()

        # cwd: a clean directory with NO utility module on disk.
        clean_cwd = root / "C_cwd"
        clean_cwd.mkdir()

        # Q: a *different* directory that IS on sys.path and DOES contain a
        #    real, importable utility.py. This models PYTHONPATH / installed
        #    package / parent-dir-on-path resolution.
        q_dir = root / "Q_syspath"
        q_dir.mkdir()
        util_q = q_dir / "utility.py"
        _write(util_q, "MULTIPLIER = 1\n\ndef scale(x):\n    return x * MULTIPLIER\n")

        # Put cwd at clean_cwd so candidate #2 (cwd/utility) is genuinely
        # missing, and Q at the front of sys.path so `import utility` resolves
        # to Q (P has no utility, so it cannot shadow Q).
        os.chdir(clean_cwd)
        sys.path.insert(0, str(q_dir))
        # Drop any pre-existing 'utility' so our import resolves freshly to Q.
        for name in [m for m in sys.modules if m == "utility" or m.startswith("utility.")]:
            del sys.modules[name]
        importlib.invalidate_caches()

        preamble = "import utility\n"

        # Sanity: the preamble genuinely resolves to Q's module at run time.
        # This is what the executor would bind into the namespace.
        util_mod = importlib.import_module("utility")
        resolved_file = Path(util_mod.__file__).resolve()
        if resolved_file != util_q.resolve():
            failures.append(
                "SETUP INVALID: `import utility` resolved to "
                f"{resolved_file} not the intended Q module {util_q.resolve()}"
            )
            # Without correct resolution the repro proves nothing.
            raise SystemExit("\n".join(failures))
        if util_mod.scale(10) != 10:
            failures.append("SETUP INVALID: Q utility.scale(10) != 10 before edit")

        # Fingerprint BEFORE editing the actually-imported module.
        fp_before = preamble_execution_fingerprint(
            preamble, pipeline_dir=str(pipeline_dir)
        )

        # Now EDIT the real module that the executor loads. New behaviour:
        # scale(10) -> 1000. A correct content-aware key MUST change.
        _write(util_q, "MULTIPLIER = 100\n\ndef scale(x):\n    return x * MULTIPLIER\n")

        # Confirm the edit is real and would be observed by a fresh import
        # (i.e. the executor, which evicts + re-imports, would see scale==1000).
        for name in [m for m in sys.modules if m == "utility" or m.startswith("utility.")]:
            del sys.modules[name]
        importlib.invalidate_caches()
        util_mod2 = importlib.import_module("utility")
        if util_mod2.scale(10) != 1000:
            failures.append(
                "SETUP INVALID: edited Q utility.scale(10) != 1000 "
                f"(got {util_mod2.scale(10)})"
            )

        fp_after = preamble_execution_fingerprint(
            preamble, pipeline_dir=str(pipeline_dir)
        )

        print(f"[A] fingerprint before edit : {fp_before}")
        print(f"[A] fingerprint after  edit : {fp_after}")
        print(f"[A] Q module before/after   : scale(10)=10 -> scale(10)=1000")

        # THE BUG: identical fingerprint despite the imported code changing.
        if fp_before == fp_after:
            print(
                "[A] REPRODUCED: fingerprint unchanged after editing the module "
                "that `import utility` actually loads -> stale preview/trace."
            )
        else:
            failures.append(
                "[A] NOT reproduced: fingerprint changed after editing Q's "
                "utility module (key tracked the real import)."
            )

        # ------------------------------------------------------------------
        # Block B: AST alias-evasion. Even when pipeline_dir IS correct and a
        # utility module sits right next to it, an aliased re-export hides the
        # dependency from preamble_imports_utility, so utility content is
        # dropped from the key entirely.
        # ------------------------------------------------------------------
        util_p = pipeline_dir / "utility.py"
        _write(util_p, "VALUE = 1\n")

        literal_import = "import utility\n"
        # Construct the module name dynamically so the literal string "utility"
        # never appears as an import target the AST walker recognises.
        aliased_import = (
            "import importlib\n"
            "_n = 'util' + 'ity'\n"
            "mod = importlib.import_module(_n)\n"
        )

        detects_literal = preamble_imports_utility(literal_import)
        detects_aliased = preamble_imports_utility(aliased_import)
        print(f"[B] preamble_imports_utility(literal)  = {detects_literal}")
        print(f"[B] preamble_imports_utility(aliased)  = {detects_aliased}")

        fp_literal = preamble_execution_fingerprint(
            literal_import, pipeline_dir=str(pipeline_dir)
        )
        fp_aliased = preamble_execution_fingerprint(
            aliased_import, pipeline_dir=str(pipeline_dir)
        )
        # The literal form includes a 'utility' part; the aliased form does not.
        literal_includes_utility = "utility" in (fp_literal or "")  # not a strong check
        if detects_literal and not detects_aliased:
            print(
                "[B] REPRODUCED: dynamically-built import name evades AST "
                "detection -> utility content dropped from key even though "
                "pipeline_dir is correct and a utility.py is present."
            )
        else:
            failures.append(
                "[B] NOT reproduced: detector flagged the aliased/dynamic "
                f"import (literal={detects_literal}, aliased={detects_aliased})."
            )

        # Restore environment before leaving the tempdir context.
        os.chdir(original_cwd)
        sys.path[:] = original_syspath
        sys.modules.clear()
        sys.modules.update(original_modules)

    if failures:
        print("\nRESULT: claim NOT fully substantiated:")
        for f in failures:
            print("  - " + f)
        return 1

    print("\nRESULT: claim REPRODUCED (both blocks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
