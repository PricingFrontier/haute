"""Adversarial repro: cache_state_signature_for_graph ms-bucket coarseness.

CLAIM: cache_state_signature_for_graph keys preview/trace invalidation on
int(meta.mtime*1000). Two meta.json rebuilds in the same ms bucket yield an
identical json_cache= fragment, so a same-data/same-schema cache mutation
(e.g. clear+rebuild, mirror) within one ms tick produces a byte-identical
fragment and a dependent preview/trace entry can be served stale.

This script proves the claim by:
  (A) Demonstrating empirically that distinct meta.json rewrites
      (distinct st_mtime_ns) collapse into the same int(st_mtime*1000)
      bucket -- i.e. the resolution loss is real on this platform.
  (B) Asserting the public behaviour: cache_state_signature_for_graph
      returns a BYTE-IDENTICAL fragment across a *genuine* working/
      meta.json mutation (different bytes, different schema_fingerprint,
      different st_mtime_ns) when both writes land in the same ms bucket.
      A correct invalidation key MUST differ; equality is the bug.

ISOLATION: all disk I/O is under tempfile.TemporaryDirectory; we os.chdir
into it (the cache dir is resolved from Path.cwd()) and restore cwd + never
touch src/, tests/, rating/, or any real project file.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import orjson

# Import the real production code under test.
from haute import _json_flatten as jf
from haute._types import NodeType


# --- tiny duck-typed graph: cache_state_signature_for_graph only reads
#     node.id, node.data.nodeType, node.data.config.get("path") and
#     iterates `graph.nodes`. No real PipelineGraph needed. ---------------
class _Data:
    def __init__(self, node_type: NodeType, config: dict) -> None:
        self.nodeType = node_type
        self.config = config


class _Node:
    def __init__(self, node_id: str, data: _Data) -> None:
        self.id = node_id
        self.data = data


class _Graph:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = nodes


def _api_input_graph(data_path: str) -> _Graph:
    return _Graph(
        [_Node("n1", _Data(NodeType.API_INPUT, {"path": data_path}))]
    )


def _write_meta(meta_path: Path, payload: dict) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_bytes(orjson.dumps(payload))


def _force_mtime_ns(p: Path, mtime_ns: int) -> None:
    """Pin a file's mtime to an exact ns value via os.utime(ns=...)."""
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, mtime_ns))


def part_a_resolution_loss() -> bool:
    """Show distinct mtime_ns rewrites collapsing into one int(mtime*1000) bucket.

    Robust to fast/coarse filesystem clocks: we PIN two mtimes that are
    1 microsecond apart inside the same millisecond, then read them back the
    way _maybe_meta_mtime_ms does. They are distinct at ns resolution but
    identical after int(st_mtime*1000).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "probe.json"
        f.write_bytes(b"{}")
        # Two timestamps 1 microsecond apart, both in ms bucket 1_700_000_000_123.
        base_ms = 1_700_000_000_123  # arbitrary fixed ms since epoch
        ns_a = base_ms * 1_000_000 + 100_000  # +100us into the ms
        ns_b = base_ms * 1_000_000 + 900_000  # +900us into the ms (distinct ns)

        _force_mtime_ns(f, ns_a)
        seen_ns_a = f.stat().st_mtime_ns
        bucket_a = jf._maybe_meta_mtime_ms(f)

        _force_mtime_ns(f, ns_b)
        seen_ns_b = f.stat().st_mtime_ns
        bucket_b = jf._maybe_meta_mtime_ms(f)

        print(f"[A] mtime_ns A = {seen_ns_a}  -> ms bucket {bucket_a}")
        print(f"[A] mtime_ns B = {seen_ns_b}  -> ms bucket {bucket_b}")
        distinct_ns = seen_ns_a != seen_ns_b
        same_bucket = bucket_a == bucket_b
        print(f"[A] distinct ns? {distinct_ns}   same ms bucket? {same_bucket}")
        return distinct_ns and same_bucket


def part_b_signature_collision() -> tuple[str, str]:
    """Two genuinely different working/ meta.json states, same ms bucket,
    produce a byte-identical json_cache= fragment.

    Returns (sig_before, sig_after); the bug is sig_before == sig_after.
    """
    import tempfile

    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as td:
        try:
            os.chdir(td)  # _json_cache_dir() resolves from Path.cwd()

            # A data file path inside the sandbox. It need not contain real
            # data: cache_state_signature_for_graph signs the meta.json
            # sidecar, and _path_hash only normalises the path string.
            data_path = str(Path(td) / "input.jsonl")
            Path(data_path).write_bytes(b'{"a":1}\n')

            graph = _api_input_graph(data_path)

            working_dir = jf._json_cache_dir(data_path, jf._LAYER_WORKING)
            meta_path = jf._json_cache_meta_path(working_dir)

            # ---- State 1: a built cache (schema fingerprint AAAA) --------
            _write_meta(
                meta_path,
                {
                    "schema_mode": "infer",
                    "schema_fingerprint": "AAAAAAAA",
                    "data_file": {"size": 8, "mtime_ns": 111},
                    "tables": ["main"],
                },
            )
            # Pin mtime into a chosen ms bucket, low end.
            base_ms = 1_700_000_000_500
            _force_mtime_ns(meta_path, base_ms * 1_000_000 + 50_000)  # +50us
            sig_before = jf.cache_state_signature_for_graph(graph)
            ns_before = meta_path.stat().st_mtime_ns

            # ---- Genuine cache mutation: clear + rebuild with DIFFERENT ---
            # schema/content (fingerprint BBBB, different data_file sig).
            # This is exactly the "clear+rebuild of different schema, or a
            # mirror that rewrites meta.json" case the claim targets. The
            # bytes differ; only the ms bucket is shared.
            _write_meta(
                meta_path,
                {
                    "schema_mode": "explicit",
                    "schema_fingerprint": "BBBBBBBB",
                    "data_file": {"size": 999, "mtime_ns": 222},
                    "tables": ["main", "extra"],
                },
            )
            _force_mtime_ns(meta_path, base_ms * 1_000_000 + 950_000)  # +950us, same ms
            sig_after = jf.cache_state_signature_for_graph(graph)
            ns_after = meta_path.stat().st_mtime_ns

            print(f"[B] meta bytes changed; schema_fingerprint AAAA -> BBBB")
            print(f"[B] mtime_ns before = {ns_before}")
            print(f"[B] mtime_ns after  = {ns_after}  (distinct: {ns_before != ns_after})")
            print(f"[B] sig_before = {sig_before!r}")
            print(f"[B] sig_after  = {sig_after!r}")
            return sig_before, sig_after
        finally:
            os.chdir(original_cwd)


def main() -> int:
    print("=== Part A: ms-bucket resolution loss is real ===")
    a_ok = part_a_resolution_loss()

    print("\n=== Part B: signature collision across a real cache mutation ===")
    sig_before, sig_after = part_b_signature_collision()

    print("\n=== VERDICT ===")
    # Sanity: the fragment must actually be the json_cache= material, and
    # the mtime must actually be part of it (otherwise the test is vacuous).
    assert sig_before.startswith("json_cache="), sig_before
    assert ":" in sig_before, "expected node=hash:working_ms:committed_ms shape"

    # Part A must hold for the claim's premise to be meaningful.
    assert a_ok, "ms-bucket collision premise did not hold on this platform"

    # THE BUG: a genuine, byte-changing, fingerprint-changing cache mutation
    # within one ms tick yields an IDENTICAL invalidation fragment.
    if sig_before == sig_after:
        print(
            "REPRODUCED: cache_state_signature_for_graph returned a BYTE-"
            "IDENTICAL json_cache= fragment across a real cache mutation\n"
            "(schema_fingerprint AAAA->BBBB, distinct mtime_ns) because both\n"
            "meta.json writes shared one int(mtime*1000) bucket. A preview/\n"
            "trace entry keyed on this fragment for invalidation is served stale."
        )
        return 0

    print(
        "NOT REPRODUCED: fragment differed across the mutation -- the ms "
        "bucket did not collide for these writes."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
