"""Adversarial reproduction for claimed bug B2.

CLAIM: _inject_contract_kwarg (src/haute/codegen.py:258-301) targets the FIRST
line whose lstrip() starts with '@pipeline.'/'@submodel.'.  The claim asserts
that when a node's *user code* (embedded by builders such as model_score /
rating_step / scenario_expander / polars transform) contains such a line
(a comment, a string/docstring literal line, or a nested decorated inner def),
the contract kwarg is injected into THAT user-body line instead of into the
real node decorator, producing a SyntaxError or syntactically-valid-but-wrong
output.

This script drives the REAL _inject_contract_kwarg with synthetic emitted
blocks that exactly mirror what the cited builders emit:

    @pipeline.<type>(<existing kwargs>)        <- the REAL decorator (FIRST line)
    def <func>(<params>) -> pl.LazyFrame:
        \"\"\"<description>\"\"\"
        <prelude>
        df = <first>
        <user body lines, indented 4 spaces by _wrap_user_code>
        return df

For each scenario the user body contains a line whose lstrip() starts with
'@pipeline.'.  We then parse the result and assert WHERE the injected
'contract=' kwarg landed.

If the claim is REAL: contract= lands inside the user body and/or the result
no longer parses / the real decorator lacks the kwarg.

If the claim is REFUTED: contract= lands on the real (physically-first)
decorator, the user-body lines are byte-for-byte unchanged, and the file
still parses.
"""

from __future__ import annotations

import ast
import sys

from haute.codegen import _inject_contract_kwarg

CONTRACT = 'contract={"inputs": ["age"], "outputs": ["age_band"]}'


def _decorator_kwargs_for(func_name: str, tree: ast.Module) -> list[str]:
    """Return the source-text of each decorator-call keyword on `func_name`."""
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == func_name:
            kws: list[str] = []
            for dec in stmt.decorator_list:
                if isinstance(dec, ast.Call):
                    kws.extend(kw.arg or "**" for kw in dec.keywords)
            return kws
    return []


def _body_contains_contract(emitted: str, func_name: str) -> bool:
    """True if 'contract=' textually appears on a *body* line (after the def)."""
    lines = emitted.split("\n")
    def_idx = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(f"def {func_name}"))
    return any("contract=" in ln for ln in lines[def_idx + 1 :])


def run_scenario(name: str, emitted: str, func_name: str, user_lines_before: list[str]) -> dict:
    result = _inject_contract_kwarg(emitted, CONTRACT)

    parses = True
    parse_err = ""
    try:
        tree = ast.parse(result)
    except SyntaxError as exc:
        parses = False
        parse_err = f"{type(exc).__name__}: {exc}"
        tree = None

    decorator_got_contract = False
    if tree is not None:
        decorator_got_contract = "contract" in _decorator_kwargs_for(func_name, tree)

    contract_in_body = _body_contains_contract(result, func_name)

    # Did any user line we put in the body get mutated?
    result_lines = result.split("\n")
    user_lines_after = [ln for ln in result_lines if "USERMARK" in ln]

    print(f"=== {name} ===")
    print(result)
    print("-" * 60)
    print(f"parses                 : {parses}{(' / ' + parse_err) if not parses else ''}")
    print(f"real decorator has contract kwarg : {decorator_got_contract}")
    print(f"'contract=' text on a body line   : {contract_in_body}")
    print(f"user-marked lines before          : {user_lines_before}")
    print(f"user-marked lines after           : {user_lines_after}")
    user_unchanged = user_lines_before == user_lines_after
    print(f"user lines unchanged              : {user_unchanged}")
    print()

    return {
        "name": name,
        "parses": parses,
        "decorator_got_contract": decorator_got_contract,
        "contract_in_body": contract_in_body,
        "user_unchanged": user_unchanged,
    }


def main() -> int:
    scenarios: list[dict] = []

    # ---- Scenario A: user-body COMMENT line that lstrips to '@pipeline.' ----
    # Mirrors _gen_transform (polars) user-code branch (_codegen_builders.py:1031-1038).
    # _wrap_user_code indents every user line by 4 spaces, so the comment becomes
    # '    # @pipeline.polars does X  # USERMARK' -> lstrip starts with '# @pipeline'
    # NOT '@pipeline.', so it is not even a candidate. Use a bare decorator-looking
    # comment whose lstrip *does* start with '@pipeline.' is impossible for a
    # '# ...' comment. The claim's worst case is a line that truly lstrips to
    # '@pipeline.' — that requires the '@' to be the first non-space char.
    func_a = "transform_node"
    user_a = [
        "    df = df.with_columns(pl.col('age') + 1)  # USERMARK comment uses @pipeline.polars",
    ]
    emitted_a = (
        f"@pipeline.polars(selected_columns=['age'])\n"
        f"def {func_a}(src) -> pl.LazyFrame:\n"
        f'    """doc"""\n'
        f"    df = src\n"
        f"{user_a[0]}\n"
        f"    return df\n"
    )
    scenarios.append(run_scenario("A: comment mentioning @pipeline.polars", emitted_a, func_a, user_a))

    # ---- Scenario B: user-body line that TRULY lstrips to '@pipeline.' ----
    # Worst case named by the claim: a string-literal / docstring line, or a
    # nested decorated inner def, whose first non-space char is '@'.
    # _wrap_user_code indents by 4 spaces, so a user line can only lstrip to
    # '@pipeline.' if, after the 4-space indent is stripped, '@pipeline.' leads.
    # That happens for a *nested decorated helper* the user pasted at column 0:
    #   @pipeline.something
    #   def helper(...): ...
    # After indentation it is '    @pipeline.something'. lstrip -> '@pipeline.something'.
    func_b = "rating_node"
    # The decorator line for the REAL node:
    real_dec_b = "@pipeline.rating_step(tables=[{'name': 't'}])"
    user_b = [
        "    @pipeline.polars  # USERMARK nested decorated helper pasted by user",
        "    def _helper(x):  # USERMARK",
        "        return x  # USERMARK",
    ]
    emitted_b = (
        f"{real_dec_b}\n"
        f"def {func_b}(src) -> pl.LazyFrame:\n"
        f'    """doc"""\n'
        f"    df = src\n"
        f"{chr(10).join(user_b)}\n"
        f"    return df\n"
    )
    scenarios.append(run_scenario("B: nested @pipeline.polars decorated helper in body", emitted_b, func_b, user_b))

    # ---- Scenario C: docstring/string-literal line that lstrips to '@pipeline.' ----
    # A triple-quoted string whose continuation line begins (after indent strip)
    # with '@pipeline.'. _wrap_user_code indents the *first* physical line only by
    # prefixing 4 spaces to each splitlines() line. A multi-line string literal's
    # interior newlines are real, so each interior line also gets 4 spaces.
    func_c = "score_node"
    real_dec_c = '@pipeline.model_score(source_type="run", run_id="r")'
    # User assigns a triple-quoted string. The 2nd physical line, after the
    # 4-space wrap indent, reads '    @pipeline.foo inside string  # USERMARK'.
    # NOTE: every line below carries USERMARK so the before/after filter is a
    # fair byte-for-byte comparison (the earlier omission of a USERMARK on the
    # trailing 'df = df' line caused a harness-only false positive).
    user_c = [
        "    note = '''first line  # USERMARK",
        "@pipeline.foo inside a string literal  # USERMARK",
        "'''  # USERMARK",
        "    df = df  # USERMARK",
    ]
    emitted_c = (
        f"{real_dec_c}\n"
        f"def {func_c}(src) -> pl.LazyFrame:\n"
        f'    """doc"""\n'
        f"    df = src\n"
        f"{chr(10).join(user_c)}\n"
        f"    return df\n"
    )
    scenarios.append(run_scenario("C: @pipeline.foo inside a triple-quoted string", emitted_c, func_c, user_c))

    # ----------------------------- verdict -----------------------------
    print("=" * 60)
    print("SUMMARY")
    for s in scenarios:
        print(s)
    print("=" * 60)

    # The claim is REAL iff for ANY scenario the contract kwarg failed to land
    # on the real decorator, OR landed in the body, OR a user line was mutated,
    # OR the output stopped parsing.
    claim_real_flags = []
    for s in scenarios:
        hijacked = (
            (not s["parses"])
            or (not s["decorator_got_contract"])
            or s["contract_in_body"]
            or (not s["user_unchanged"])
        )
        claim_real_flags.append(hijacked)
        verdict = "HIJACKED (claim supported)" if hijacked else "clean (claim refuted)"
        print(f"{s['name']}: {verdict}")

    if any(claim_real_flags):
        print("\nRESULT: claim B2 REPRODUCED in at least one scenario.")
        return 0
    print("\nRESULT: claim B2 NOT reproduced — real decorator always received "
          "the contract kwarg, user body untouched, output parsed.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
