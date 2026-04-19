"""Back-compat re-export shim for the pipeline-parser helpers.

The implementation was split into four focused modules (Phase 2 Wave 4
package 4A — items #52 and #61 of ``docs/CODEBASE_REVIEW.md``):

* :mod:`haute._ast_helpers`      — pure AST / source utilities
* :mod:`haute._code_extraction`  — user-code extraction engine + shims
* :mod:`haute._config_builder`   — node-config dict construction
* :mod:`haute._graph_builders`   — GraphNode / GraphEdge construction

This module re-exports every name that used to live here so existing
callers (``parser.py``, ``_parser_regex.py``, ``_parser_submodels.py``,
plus tests) keep working without modification.  New code should import
directly from the thematic module instead.

``warn_unrecognized_config_keys`` and ``load_node_config`` are also
re-exported here as the canonical *patch target* for legacy tests —
``_config_builder`` looks them up through this shim at call time, so
``patch("haute._parser_helpers.warn_unrecognized_config_keys")`` still
reaches the real call-site after the split.
"""

from __future__ import annotations

from haute._ast_helpers import (
    _dedent,
    _eval_ast_literal,
    _extract_connect_calls,
    _extract_function_bodies,
    _extract_meta,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _extract_submodel_meta,
    _get_decorator_kwargs,
    _get_decorator_node_type,
    _get_docstring,
    _is_pipeline_node_decorator,
    _is_submodel_node_decorator,
    _strip_docstring,
)
from haute._code_extraction import (
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_sentinel_user_code,
    _extract_source_user_code,
    _extract_user_code,
    _unwrap_chain_assignment,
)
from haute._config_builder import (
    _build_node_config,
    _copy_config_keys,
    _resolve_node_config,
)
from haute._config_io import load_node_config
from haute._config_validation import warn_unrecognized_config_keys
from haute._graph_builders import (
    _build_edges,
    _build_rf_nodes,
    _extract_decorated_nodes,
)

__all__ = [
    # AST / source utilities
    "_eval_ast_literal",
    "_get_decorator_kwargs",
    "_is_pipeline_node_decorator",
    "_is_submodel_node_decorator",
    "_get_decorator_node_type",
    "_get_docstring",
    "_strip_docstring",
    "_dedent",
    "_extract_function_bodies",
    "_extract_connect_calls",
    "_extract_meta",
    "_extract_pipeline_meta",
    "_extract_submodel_meta",
    "_extract_preamble",
    "_extract_preserved_blocks",
    # Code extraction
    "_extract_user_code",
    "_extract_sentinel_user_code",
    "_extract_source_user_code",
    "_extract_model_score_user_code",
    "_extract_external_user_code",
    "_unwrap_chain_assignment",
    # Config construction
    "_build_node_config",
    "_copy_config_keys",
    "_resolve_node_config",
    # Graph building
    "_extract_decorated_nodes",
    "_build_edges",
    "_build_rf_nodes",
    # Patch-target re-exports (legacy test seams)
    "load_node_config",
    "warn_unrecognized_config_keys",
]
