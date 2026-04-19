"""Extraction of user code from pipeline function bodies.

Houses the four per-node extractors plus the consolidated engine that
backs them.  Each extractor (``_extract_user_code``,
``_extract_source_user_code``, ``_extract_model_score_user_code``,
``_extract_external_user_code``) differs only in its *boilerplate
matcher* — the logic that decides how much of the function body is
auto-generated boilerplate to be stripped before user code begins.

Design (item #52 of CODEBASE_REVIEW.md):

* ``BOILERPLATE_MATCHERS`` is a registry mapping a kind string to a
  ``BoilerplateMatcher`` that returns the *start index* of the user
  code within the cleaned body lines, plus the *return variable* whose
  trailing ``return <var>`` should be stripped.
* ``extract_user_code(body_source, *, kind, param_names)`` is the
  single consolidated entrypoint — it does the shared work (docstring
  strip, dedent, trailing-return strip) and dispatches the
  kind-specific logic through the registry.
* The four legacy ``_extract_*_user_code`` functions remain as thin
  shims over the engine, keeping the existing public surface intact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from haute._ast_helpers import _dedent, _strip_docstring

__all__ = [
    "_extract_user_code",
    "_extract_sentinel_user_code",
    "_extract_source_user_code",
    "_extract_model_score_user_code",
    "_extract_external_user_code",
    "_unwrap_chain_assignment",
    "extract_user_code",
    "BOILERPLATE_MATCHERS",
    "BoilerplateMatcher",
    "MatcherResult",
]


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


def _unwrap_chain_assignment(
    code: str,
    param_names: list[str] | None = None,
) -> str | None:
    """Unwrap ``df = (\\n...\\n)`` and strip the leading source variable name.

    When a leading identifier matches a known *param_name* it is kept
    (it's part of the user's code, e.g. ``source.filter(...)``).  Only
    codegen-injected variable names (not in param_names) are stripped to
    prevent accumulation on save/reload roundtrips.

    Returns the extracted chain code, or ``None`` if the pattern doesn't match.
    """
    if not (code.startswith("df = (") or code.startswith("df=(")):
        return None
    inner = code.split("(", 1)[1]
    if inner.rstrip().endswith(")"):
        inner = inner.rstrip()[:-1]
    extracted = _dedent(inner).strip()
    # Strip leading source variable name to prevent accumulation on
    # save/reload roundtrips (e.g. "source_name\n.filter()")
    lines = extracted.splitlines()
    if (
        len(lines) > 1
        and lines[1].lstrip().startswith(".")
        and lines[0].strip().isidentifier()
        and lines[0].strip() not in (param_names or [])
    ):
        extracted = "\n".join(lines[1:])
    return extracted


def _extract_sentinel_user_code(body_source: str, return_var: str = "result") -> str:
    """Extract user code between ``# -- user code --`` sentinel and trailing return.

    **Legacy support** — older pipeline files use a sentinel comment to
    delimit auto-generated boilerplate from user code.  New codegen no
    longer writes the sentinel; the ``_extract_source_user_code`` and
    ``_extract_model_score_user_code`` functions handle both old and new
    formats.

    If no sentinel is found returns an empty string (caller should try
    the non-sentinel extraction path).
    """
    sentinel = "# -- user code --"
    if sentinel not in body_source:
        return ""

    # Take everything after the sentinel
    _, _, after = body_source.partition(sentinel)
    lines = after.strip().splitlines()
    if not lines:
        return ""

    # Strip trailing auto-generated return
    while lines and lines[-1].strip() in (f"return {return_var}", ""):
        lines.pop()

    if not lines:
        return ""

    return _dedent("\n".join(lines)).strip()


# ---------------------------------------------------------------------------
# Consolidated engine — pluggable boilerplate matchers
# ---------------------------------------------------------------------------


class MatcherResult(NamedTuple):
    """Result of a boilerplate matcher.

    ``start_idx`` is the first index in ``cleaned_lines`` that is
    considered user code (everything before it is boilerplate to skip).
    ``return_var`` is the variable whose trailing ``return <var>`` should
    be stripped from the extracted tail.
    """

    start_idx: int
    return_var: str


# A matcher inspects the *cleaned* (docstring-stripped) body lines plus
# the node's declared parameter names and decides where user code begins.
BoilerplateMatcher = Callable[[list[str], tuple[str, ...]], MatcherResult]


def _match_polars(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """Polars/transform nodes: keep everything — matcher is a no-op.

    Post-processing (strip ``df = <param>`` alias, unwrap chain, convert
    ``return <expr>``) is applied by ``_finalise_polars``.
    """
    return MatcherResult(start_idx=0, return_var="df")


def _match_source(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """DataSource / scenarioExpander nodes: skip leading imports + first statement.

    The first statement is always the auto-generated data-load
    (``df = pl.scan_parquet(...)`` or similar).  It may span multiple
    lines — we walk parentheses to find its end.
    """
    # Skip leading import lines (from/import) codegen may have added
    idx = 0
    while idx < len(cleaned) and cleaned[idx].strip().startswith(("from ", "import ")):
        idx += 1

    if idx >= len(cleaned):
        return MatcherResult(start_idx=idx, return_var="df")

    # Skip the first statement — walk parens to handle multi-line assignments
    first_end = idx
    depth = 0
    for i in range(idx, len(cleaned)):
        depth += cleaned[i].count("(") - cleaned[i].count(")")
        if depth <= 0 and i >= first_end:
            first_end = i + 1
            break
    return MatcherResult(start_idx=first_end, return_var="df")


def _match_model_score(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ModelScore nodes: skip up to and including the ``score_from_config(...)`` call.

    The call may span multiple lines — we walk parentheses to find the
    closing ``)``.  If no score_from_config call is found, return an
    out-of-range start index so the engine yields an empty result.
    """
    for i, line in enumerate(cleaned):
        stripped = line.strip()
        if "score_from_config(" in stripped and not stripped.startswith(("from ", "import ")):
            # Walk forward to balance the opening paren(s) of this call
            depth = line.count("(") - line.count(")")
            j = i
            while depth > 0 and j + 1 < len(cleaned):
                j += 1
                depth += cleaned[j].count("(") - cleaned[j].count(")")
            return MatcherResult(start_idx=j + 1, return_var="result")

    # No call found — treat the whole body as boilerplate (empty user code)
    return MatcherResult(start_idx=len(cleaned) + 1, return_var="result")


def _match_external(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ExternalFile nodes: skip imports, ``with open(...)`` blocks, and ``obj = …`` lines.

    This is the most heuristic of the four — user code starts at the
    first line that is *not* recognised file-loading boilerplate.
    """
    if not cleaned:
        return MatcherResult(start_idx=0, return_var="df")

    # Determine the base indentation from the first non-blank line
    base_indent = 0
    for line in cleaned:
        if line.strip():
            base_indent = len(line) - len(line.lstrip())
            break

    i = 0
    in_with = False
    while i < len(cleaned):
        s = cleaned[i].strip()
        line_indent = len(cleaned[i]) - len(cleaned[i].lstrip()) if s else 0

        if not s:
            i += 1
            continue

        # Inside a with-block: skip indented body lines
        if in_with:
            if line_indent > base_indent:
                i += 1
                continue
            in_with = False
            # fall through to check this line normally

        if s.startswith("import ") or s.startswith("from "):
            i += 1
            continue
        if s.startswith("with open("):
            in_with = True
            i += 1
            continue
        if s.startswith("obj = ") or s.startswith("obj."):
            i += 1
            continue

        break  # first user-code line

    return MatcherResult(start_idx=i, return_var="df")


# Registry of boilerplate matchers, keyed by *kind*.  The kind string
# is the public key callers use to select a matcher; aliases for the
# same matcher are allowed so callers can use either the NodeType style
# ("dataSource") or the Python-snake style ("data_source", "source").
BOILERPLATE_MATCHERS: dict[str, BoilerplateMatcher] = {
    "polars": _match_polars,
    "transform": _match_polars,
    "source": _match_source,
    "dataSource": _match_source,
    "data_source": _match_source,
    "model_score": _match_model_score,
    "modelScore": _match_model_score,
    "external": _match_external,
    "externalFile": _match_external,
    "external_file": _match_external,
}


def _strip_trailing_return(code_lines: list[str], return_var: str) -> list[str]:
    """Strip trailing blank lines and the codegen ``return <return_var>`` line."""
    stripped = list(code_lines)
    while stripped and stripped[-1].strip() in (f"return {return_var}", ""):
        stripped.pop()
    return stripped


def _finalise_polars(code: str, param_names: tuple[str, ...]) -> str:
    """Apply polars-specific post-processing: strip df=<param>, unwrap chain, convert return."""
    # Strip codegen-prepended "df = <param_name>" alias to prevent
    # accumulation on save/reload roundtrips.
    first_line = code.splitlines()[0].strip() if code else ""
    if first_line.startswith("df = ") or first_line.startswith("df="):
        alias_target = first_line.split("=", 1)[1].strip()
        if alias_target in param_names:
            remaining = "\n".join(code.splitlines()[1:]).strip()
            if remaining:
                code = remaining

    # Pattern 1: codegen chain style "df = (\n...\n)" — unwrap to inner
    chain = _unwrap_chain_assignment(code, param_names=list(param_names))
    if chain is not None:
        return chain

    # Pattern 2: hand-written "return <expr>" — convert to "df = <expr>"
    stripped_lines: list[str] = []
    in_return = False
    for line in code.splitlines():
        s = line.strip()
        is_return = s == "return" or (s.startswith("return ") and not s.startswith("return_"))
        if is_return and not in_return:
            stripped_lines.append(line.replace("return ", "df = ", 1) if "return " in line else "")
            in_return = True
        elif in_return:
            stripped_lines.append(line)
        elif not is_return:
            stripped_lines.append(line)

    return _dedent("\n".join(stripped_lines)).strip()


def _finalise_source(code: str, param_names: tuple[str, ...]) -> str:
    """Source-specific post-processing: unwrap chain-assignment pattern if present."""
    chain = _unwrap_chain_assignment(code)
    if chain is not None:
        return chain
    return code


def _finalise_external(code: str, param_names: tuple[str, ...]) -> str:
    """External-specific post-processing: handle the edge case of ``return df`` only."""
    # _strip_trailing_return has already removed the newline-prefixed
    # version; if the sole remaining content is ``return df`` (e.g. the
    # body was just boilerplate + return), wipe it.
    if code.strip() == "return df":
        return ""
    return code


# Per-kind finaliser registry.  The finaliser runs after the shared
# "strip docstring → dedent → skip boilerplate → strip trailing return"
# engine pass.  Defaults to no-op for kinds without special handling.
def _finalise_noop(code: str, param_names: tuple[str, ...]) -> str:
    return code


_FINALISERS: dict[str, Callable[[str, tuple[str, ...]], str]] = {
    "polars": _finalise_polars,
    "transform": _finalise_polars,
    "source": _finalise_source,
    "dataSource": _finalise_source,
    "data_source": _finalise_source,
    "model_score": _finalise_noop,
    "modelScore": _finalise_noop,
    "external": _finalise_external,
    "externalFile": _finalise_external,
    "external_file": _finalise_external,
}


def extract_user_code(
    body_source: str,
    *,
    kind: str,
    param_names: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Extract user code from a function body, dispatching via the matcher registry.

    This is the consolidated engine that backs the four legacy
    ``_extract_*_user_code`` extractors (item #52).  It performs the
    shared work (strip docstring → dedent → skip matcher-specific
    boilerplate → strip trailing ``return <var>``) and delegates the
    kind-specific pre- and post-processing to the registry.

    Args:
        body_source: The raw function body (as produced by
            ``_extract_function_bodies``).
        kind: The matcher key — one of the entries in
            ``BOILERPLATE_MATCHERS`` (``"polars"``, ``"source"``,
            ``"model_score"``, ``"external"`` and their aliases).
        param_names: Parameter names of the decorated function, used by
            polars post-processing to distinguish user identifiers from
            codegen-injected ones.

    Returns:
        The extracted user code as a string (empty string if the body
        contains only boilerplate).

    Raises:
        KeyError: If *kind* is not in the registry.
    """
    if kind not in BOILERPLATE_MATCHERS:
        raise KeyError(
            f"Unknown boilerplate matcher kind: {kind!r}. "
            f"Available kinds: {sorted(BOILERPLATE_MATCHERS)!r}"
        )
    matcher = BOILERPLATE_MATCHERS[kind]
    finaliser = _FINALISERS[kind]
    params = tuple(param_names or ())

    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    result = matcher(cleaned, params)
    rest = cleaned[result.start_idx :]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()
    code_lines = _strip_trailing_return(code.splitlines(), result.return_var)
    if not code_lines:
        return ""

    code = "\n".join(code_lines).strip()
    return finaliser(code, params)


# ---------------------------------------------------------------------------
# Legacy per-kind shims — thin wrappers over the consolidated engine
# ---------------------------------------------------------------------------


def _extract_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract the meaningful user code from a polars/transform function body.

    Strips the docstring and the codegen-appended ``return df``.
    For codegen chain style ``df = (...)`` it unwraps the inner expression.
    For hand-written ``return expr`` it strips the ``return`` keyword.
    For multi-statement bodies (assignments, comments) it returns as-is.
    """
    return extract_user_code(body_source, kind="polars", param_names=tuple(param_names))


def _extract_source_user_code(body_source: str) -> str:
    """Extract user code from a DATA_SOURCE or SCENARIO_EXPANDER body.

    The auto-generated boilerplate is a single assignment line at the
    top (e.g. ``df = pl.scan_parquet("...")``).  Everything after that
    assignment — minus the trailing ``return df`` — is user code.

    Supports both the legacy sentinel format (``# -- user code --``)
    and the new sentinel-free format where user code follows the
    boilerplate directly.
    """
    legacy = _extract_sentinel_user_code(body_source, "df")
    if legacy:
        return legacy
    return extract_user_code(body_source, kind="source")


def _extract_model_score_user_code(body_source: str) -> str:
    """Extract user post-processing code from a MODEL_SCORE function body.

    The auto-generated boilerplate is the ``from pathlib ...`` /
    ``score_from_config(...)`` block.  Everything after the
    ``result = score_from_config(...)`` line (minus ``return result``)
    is user code.

    Supports both the legacy sentinel format and the new sentinel-free
    format.
    """
    legacy = _extract_sentinel_user_code(body_source, "result")
    if legacy:
        return legacy
    return extract_user_code(body_source, kind="model_score")


def _extract_external_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract user code from an externalFile function body.

    Strips the docstring, then scans forward to skip the file-loading
    boilerplate (import statements, with-open blocks, obj assignments /
    method calls).  Everything between the boilerplate and a trailing
    ``return df`` is the user code.
    """
    return extract_user_code(body_source, kind="external", param_names=tuple(param_names))
