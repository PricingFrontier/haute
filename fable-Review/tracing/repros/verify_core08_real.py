"""CORE-08 end-to-end: cold trace over a REAL multi-frame v2 apiInput.

Builds a genuine per-port parquet cache (root + drivers frames), wires an
apiInput node that emits a dict[label, DataFrame], and calls the public
execute_trace() with NO preview injection (cold path) — exactly the
production trace-route flow minus the HTTP layer. Records whether it
returns or raises, for:

  Case 1: trace target IS the multi-frame apiInput node.
  Case 2: trace target is a DOWNSTREAM node (apiInput is an ancestor).
"""

from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path
from typing import Any

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute.trace import execute_trace
from tests.conftest import make_graph as _g


def _col(name: str, path: str) -> dict[str, Any]:
    return {"name": name, "path": path, "type": "int", "status": "Confirmed",
            "selected": True, "levels": None}


def _table(path: str, label: str, cols: list[dict[str, Any]]) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": True, "row_id_column": None, "columns": cols}


def build_api(tmp: Path) -> tuple[str, dict[str, Any]]:
    data = tmp / "data.json"
    data.write_text(json.dumps([{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}]), encoding="utf-8")
    tables = [
        _table("$[:]", "root", [_col("id", "$[:].id")]),
        _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
    ]
    cfg = {"tables": tables}
    build_per_port_cache(str(data), cfg, _json_cache_dir(str(data), "working"))
    node_config = {"path": str(data), "tables": tables}
    return str(data), node_config


def run_case(title: str, graph, **kw) -> None:
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)
    try:
        result = execute_trace(graph, **kw)
        print("RETURNED TraceResult; steps:", [s.node_id for s in result.steps])
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"RAISED: {type(exc).__name__}: {exc}")
        for line in tb.strip().splitlines()[-5:]:
            print("   " + line)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    _, node_config = build_api(tmp)

    # Case 1: target IS the multi-frame apiInput
    g1 = _g({
        "nodes": [{"id": "api", "data": {"label": "api", "nodeType": "apiInput",
                                          "config": node_config}}],
        "edges": [],
    })
    run_case("Case 1: target = multi-frame apiInput (no row_values)", g1,
             target_node_id="api")
    run_case("Case 1b: target = multi-frame apiInput (WITH row_values)", g1,
             target_node_id="api", row_values={"id": 1})

    # Case 2: downstream consumer of the 'root' port; apiInput is an ANCESTOR
    g2 = _g({
        "nodes": [
            {"id": "api", "data": {"label": "api", "nodeType": "apiInput",
                                   "config": node_config}},
            {"id": "consumer", "data": {"label": "consumer", "nodeType": "polars",
                                        "config": {"code": "df = df.with_columns(id2=pl.col('id'))"}}},
        ],
        "edges": [{"id": "e1", "source": "api", "target": "consumer", "sourceHandle": "root"}],
    })
    run_case("Case 2: target = downstream consumer (apiInput is ancestor)", g2,
             target_node_id="consumer")


if __name__ == "__main__":
    main()
