"""Adversarial repro for:
  read-v2-config-orjson-duplicate-key-divergence

Claim: the cache-build route's config funnel (`_read_v2_config`, orjson)
silently keeps the LAST occurrence of a duplicate JSON key, while the
parser/executor funnel (`load_node_config` -> `_load_json_object`,
json + object_pairs_hook) REJECTS the SAME on-disk file with a ValueError.
Same bytes -> two outcomes.

Both funnels are documented (see _config_io.py:103-110 and the
_read_v2_config docstring) as having to AGREE on the in-memory shape they
materialise from a disk-resident apiInput config. They disagree on
duplicate keys.

This repro:
  1. Writes ONE real on-disk apiInput v2 config under
     config/quote_input/<name>.json (the folder the parser maps to
     NodeType.API_INPUT) containing a duplicate top-level `path` key
     (path="FIRST.json" then path="OTHER.json") plus a valid tables[].
  2. Drives `_read_v2_config` (cache-build route) on those bytes and
     asserts it ACCEPTS, keeping path == "OTHER.json" (last-wins).
  3. Drives `load_node_config` (parser/executor) on the SAME file and
     asserts it RAISES ValueError("duplicate JSON key 'path'").

ISOLATION: all I/O under tempfile; project root set via
haute._sandbox.set_project_root. No real project file touched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute._config_io import load_node_config
from haute.routes.json_cache import _read_v2_config

# A single on-disk file body with a DUPLICATE top-level `path` key.
# orjson keeps the last; json+object_pairs_hook rejects it.
DUP_CONFIG_BYTES = (
    b'{\n'
    b'  "path": "FIRST.json",\n'
    b'  "tables": [\n'
    b'    {\n'
    b'      "path": "$[*]",\n'
    b'      "label": "root",\n'
    b'      "emit": true,\n'
    b'      "columns": [\n'
    b'        {"name": "a", "path": "$[*].a", "type": "str", "status": "Confirmed", "selected": true}\n'
    b'      ]\n'
    b'    }\n'
    b'  ],\n'
    b'  "path": "OTHER.json"\n'
    b'}\n'
)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _sandbox.set_project_root(root)

        # Write the SAME file under the apiInput config folder so the
        # parser path resolves its node type to API_INPUT (quote_input).
        cfg_dir = root / "config" / "quote_input"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "quotes.json"
        cfg_path.write_bytes(DUP_CONFIG_BYTES)

        # ---- Funnel 1: cache-build route (_read_v2_config / orjson) ----
        cache_view = _read_v2_config(str(cfg_path))

        assert cache_view is not None, (
            "EXPECTED cache route to ACCEPT the duplicate-key config "
            "(orjson keeps last); got None (rejected/migration)."
        )
        cache_path_value = cache_view.get("path")
        # orjson semantics: the LAST `path` wins.
        assert cache_path_value == "OTHER.json", (
            "EXPECTED cache route to silently keep the LAST duplicate "
            f"(path='OTHER.json'); got path={cache_path_value!r}."
        )

        # ---- Funnel 2: parser/executor (load_node_config) on SAME file ----
        parser_outcome: str
        parser_path_value: object = "<never-loaded>"
        try:
            parsed = load_node_config(cfg_path, base_dir=root)
            parser_outcome = "accepted"
            parser_path_value = parsed.get("path")
        except ValueError as exc:
            parser_outcome = f"rejected: {exc}"

        # The bug: same file, parser REJECTS what the cache ACCEPTED.
        assert parser_outcome.startswith("rejected"), (
            "EXPECTED parser/executor funnel to REJECT the duplicate-key "
            f"config with ValueError; instead it {parser_outcome} "
            f"(path={parser_path_value!r}). If this fires, the two funnels "
            "AGREE and the divergence claim is REFUTED."
        )
        assert "duplicate JSON key" in parser_outcome, (
            "EXPECTED the rejection to be the duplicate-key guard; got: "
            f"{parser_outcome}"
        )

        # ---- Divergence summary (the 'must not happen' the comments warn of) ----
        print("REPRO RESULT: divergence CONFIRMED on identical on-disk bytes")
        print(f"  file: {cfg_path}")
        print(f"  cache-build route (_read_v2_config / orjson) -> ACCEPTED, path={cache_path_value!r}")
        print(f"  parser/executor   (load_node_config / json)  -> {parser_outcome}")
        print(
            "  => cache can be built keyed on path='OTHER.json' while the "
            "executor's parse path refuses to load the same file."
        )


if __name__ == "__main__":
    main()
