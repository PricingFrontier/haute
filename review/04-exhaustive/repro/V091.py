"""Repro for V091: frontend sanitizeName() omits the Python-keyword guard
that backend _sanitize_func_name() applies, so the two diverge for any node
label that sanitises to a reserved Python keyword (e.g. "class").

This is a frontend(TS)/backend(Python) wire-contract mismatch. The backend
half (_sanitize_func_name) is imported and run directly. The frontend half is
reimplemented here as a *faithful, line-for-line* port of
frontend/src/utils/sanitizeName.ts (commit on branch wave-2-cache-integrity),
INCLUDING its omission of the keyword branch, so the divergence demonstrated
is the genuine one shipped in the frontend, not a strawman.

Downstream impact proven structurally:
  - Backend writes the api_input config to
    config/quote_input/<_sanitize_func_name(label)>.json
    (_config_io.py:346-347 -> config_path_for_node, folder "quote_input").
  - Frontend requests config/quote_input/<sanitizeName(label)>.json
    (NodePanel.tsx:708 -> ApiInputEditor config_path -> json_cache GET).
  - For a keyword label these paths differ, so the cache-status read in
    json_cache._read_v2_config hits a non-existent path and returns None
    (silent cached=false).

The repro ASSERTS the specific wrong values:
  backend("class")  == "node_class"   (correct, keyword-guarded)
  frontend("class") == "class"        (WRONG: no keyword prefix)
and that the resulting on-disk config paths differ.
"""

from __future__ import annotations

from haute._graph_utils import _sanitize_func_name as backend_sanitize


def frontend_sanitize(label: str) -> str:
    """Faithful port of frontend/src/utils/sanitizeName.ts.

    Mirrors every step of the TS source EXACTLY, including the fact that it
    only guards the leading-digit and empty-string cases and has NO Python
    keyword branch. JS String iteration is by code point, matching the TS
    ``for (const c of name)`` loop; Python ``for c in s`` iterates code points
    too, so the behaviour matches for the BMP/astral chars used here.
    """
    # let name = label.trim().replace(/[\s-]/g, "_")
    import re

    name = re.sub(r"[\s-]", "_", label.strip())
    encoded: list[str] = []
    for c in name:
        code = ord(c)
        if code < 128:
            # ASCII: keep alnum/underscore, drop everything else.
            if re.fullmatch(r"[a-zA-Z0-9_]", c):
                encoded.append(c)
        else:
            # Non-ASCII: reversibly encode as _x<hex>_  (lowercase hex, as
            # JS Number.prototype.toString(16) produces).
            encoded.append(f"_x{format(code, 'x')}_")
    name = "".join(encoded)
    # if (name && /^[0-9]/.test(name)) name = `node_${name}`
    if name and re.match(r"^[0-9]", name):
        name = f"node_{name}"
    # return name || "unnamed_node"
    return name or "unnamed_node"


def main() -> None:
    # ---- 0. Sanity: the two ports AGREE on non-keyword inputs (so any
    #         divergence below is specifically the keyword guard, not a
    #         broken port). ----
    for ok_label in ["my node", "My-Node", "café", "123abc", "  padded  ", ""]:
        b = backend_sanitize(ok_label)
        f = frontend_sanitize(ok_label)
        assert b == f, (
            f"PORT MISMATCH on non-keyword input {ok_label!r}: "
            f"backend={b!r} frontend={f!r} (the frontend port is unfaithful; "
            f"fix the repro, not the finding)"
        )

    # ---- 1. The core divergence on the canonical keyword "class". ----
    backend_class = backend_sanitize("class")
    frontend_class = frontend_sanitize("class")

    assert backend_class == "node_class", (
        f"expected backend _sanitize_func_name('class') == 'node_class', "
        f"got {backend_class!r}"
    )
    # This is the bug: the frontend computes a name the backend never wrote.
    assert frontend_class == "class", (
        f"expected frontend sanitizeName('class') == 'class' (no keyword "
        f"guard), got {frontend_class!r}"
    )
    assert backend_class != frontend_class, (
        "expected backend and frontend to DISAGREE for label 'class'"
    )

    # ---- 2. Generalise across the full reserved-keyword set. Every keyword
    #         that is itself a bare identifier (no transformation) must show
    #         the node_ prefix on the backend and NOT on the frontend. ----
    import keyword

    diverging: list[tuple[str, str, str]] = []
    for kw in keyword.kwlist:
        # Restrict to labels that survive sanitisation unchanged as a bare
        # identifier (all of kwlist are ASCII identifiers, so they do).
        b = backend_sanitize(kw)
        f = frontend_sanitize(kw)
        if b != f:
            diverging.append((kw, b, f))
    # Sanity: a broad, non-trivial set diverges (not just one).
    assert len(diverging) >= 20, (
        f"expected many keywords to diverge, only {len(diverging)} did: "
        f"{diverging!r}"
    )
    # Spot-check representative ones called out in the finding.
    for kw in ["class", "return", "import", "for", "if", "def", "lambda",
               "while", "with", "yield", "global", "None", "True", "False",
               "and", "or", "not", "in", "is"]:
        assert backend_sanitize(kw) == f"node_{kw}", kw
        assert frontend_sanitize(kw) == kw, kw

    # ---- 3. Concrete downstream artefact: the api_input config path the two
    #         sides build for a node labelled "class". The backend WRITES the
    #         first; the frontend cache-status GET asks for the second. ----
    backend_path = f"config/quote_input/{backend_sanitize('class')}.json"
    frontend_path = f"config/quote_input/{frontend_sanitize('class')}.json"
    assert backend_path == "config/quote_input/node_class.json", backend_path
    assert frontend_path == "config/quote_input/class.json", frontend_path
    assert backend_path != frontend_path, (
        "expected the on-disk config path the backend writes to differ from "
        "the path the frontend requests for a keyword-labelled node"
    )

    print("V091 REPRO: CONFIRMED")
    print(f"  backend  _sanitize_func_name('class') = {backend_class!r}")
    print(f"  frontend sanitizeName('class')        = {frontend_class!r}  <-- WRONG (no keyword guard)")
    print(f"  keywords that diverge: {len(diverging)} of {len(keyword.kwlist)}")
    print(f"  backend writes : {backend_path}")
    print(f"  frontend reads : {frontend_path}  <-- misses -> cached=false")


if __name__ == "__main__":
    main()
