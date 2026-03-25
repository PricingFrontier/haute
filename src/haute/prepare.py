"""Schema-driven column preparation for flattened JSON DataFrames.

Transforms dot-notation columns (produced by :func:`haute._json_flatten.flatten`)
into clean snake_case names and generates counting columns for arrays and
boolean-group patterns.

Usage::

    import haute

    df = haute.clean_columns(lazy_frame)
"""

from __future__ import annotations

import re
from collections import defaultdict

import polars as pl

from haute._logging import get_logger

logger = get_logger(component="prepare")


# ---------------------------------------------------------------------------
# Singularisation (used only for count-column naming)
# ---------------------------------------------------------------------------

_IRREGULAR_PLURALS: dict[str, str] = {
    "children": "child",
    "people": "person",
    "men": "man",
    "women": "woman",
    "mice": "mouse",
    "teeth": "tooth",
    "feet": "foot",
    "geese": "goose",
    "oxen": "ox",
    "indices": "index",
    "matrices": "matrix",
    "vertices": "vertex",
    "analyses": "analysis",
    "diagnoses": "diagnosis",
    "crises": "crisis",
    "theses": "thesis",
    "buses": "bus",
}

_UNCOUNTABLE: frozenset[str] = frozenset(
    {
        "series",
        "species",
        "news",
        "lens",
        "data",
        "status",
        "analysis",
        "basis",
        "crisis",
        "diagnosis",
        "thesis",
    }
)

_BOOL_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "selected",
        "active",
        "included",
        "enabled",
        "opted_in",
        "opted_out",
    }
)


def _singularise(word: str) -> str:
    """Naively singularise an English plural for count-column naming."""
    lower = word.lower()
    if lower in _UNCOUNTABLE:
        return word
    if lower in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[lower]
    if lower.endswith("ies") and len(lower) > 3:
        return word[:-3] + "y"
    if lower.endswith("sses"):
        return word[:-2]
    if lower.endswith(("shes", "ches", "xes", "zes")):
        return word[:-2]
    if lower.endswith("ves"):
        return word[:-3] + "f"
    if lower.endswith("s") and not lower.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


# ---------------------------------------------------------------------------
# Column-name pattern detection (no schema needed)
# ---------------------------------------------------------------------------

# Matches a segment that is a pure integer (array index)
_INDEX_RE = re.compile(r"^\d+$")


def _detect_arrays(columns: list[str]) -> dict[str, list[str]]:
    """Detect array patterns from column names alone.

    Scans for columns containing numeric segments (e.g.
    ``additional_drivers.1.gender``) and groups them by array prefix.

    Returns ``{array_prefix: [indicator_col_1, indicator_col_2, ...]}``
    where each indicator column is the first field of the first slot.
    """
    # Group columns by their array prefix (everything before the first numeric segment)
    # e.g. "additional_drivers.1.gender" -> prefix "additional_drivers", index 1
    arrays: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))

    for col in columns:
        parts = col.split(".")
        # Find the first numeric segment
        for i, seg in enumerate(parts):
            if _INDEX_RE.match(seg):
                prefix = ".".join(parts[:i])
                idx = int(seg)
                arrays[prefix][idx].append(col)
                break

    if not arrays:
        return {}

    # For each array, find the indicator column (first field of the first slot)
    col_set = set(columns)
    result: dict[str, list[str]] = {}
    for prefix, slots in arrays.items():
        if not slots:
            continue
        # Get the first field name from slot 1 (or the lowest slot)
        min_slot = min(slots)
        if not slots[min_slot]:
            continue
        # The indicator is the first column in each slot
        first_col = slots[min_slot][0]
        # Extract the suffix after "prefix.N"
        suffix = first_col[len(prefix) + len(str(min_slot)) + 2 :]  # +2 for the two dots

        # Collect indicator columns across all slots
        indicator_cols = []
        for idx in sorted(slots):
            indicator = f"{prefix}.{idx}.{suffix}" if suffix else f"{prefix}.{idx}"
            if indicator in col_set:
                indicator_cols.append(indicator)

        result[prefix] = indicator_cols

    return result


def _detect_boolean_groups(columns: list[str]) -> dict[str, list[str]]:
    """Detect boolean-group patterns from column names alone.

    Looks for patterns like ``section.child_a.field``, ``section.child_b.field``
    where the same leaf field appears across multiple sibling children under
    the same parent, and the leaf name suggests a boolean (``selected``,
    ``active``, ``included``, ``enabled``, or any field that appears in all
    siblings at the same depth).

    Returns ``{section_prefix: [col_1, col_2, ...]}`` with the boolean columns.
    """
    # Group 3-part dot-paths by (parent, leaf)
    # e.g. "add_ons.breakdown_cover.selected"
    #   -> parent="add_ons", child="breakdown_cover", leaf="selected"
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)

    for col in columns:
        parts = col.split(".")
        # Only consider 3+ segment paths where no segment is numeric (not arrays)
        if len(parts) < 3:
            continue
        if any(_INDEX_RE.match(p) for p in parts):
            continue
        # parent = everything except last two, child = second-to-last, leaf = last
        parent = ".".join(parts[:-2])
        leaf = parts[-1]
        if leaf in _BOOL_FIELD_NAMES:
            groups[(parent, leaf)].append(col)

    # Only keep groups with 2+ children (a single child isn't a "group")
    result: dict[str, list[str]] = {}
    for (parent, _leaf), cols in groups.items():
        if len(cols) >= 2:
            result[parent] = cols

    return result


# ---------------------------------------------------------------------------
# Count-column generation (from column names, no schema needed)
# ---------------------------------------------------------------------------


def _array_count_exprs(
    columns: list[str],
) -> tuple[list[pl.Expr], set[str]]:
    """Build ``number_of_*`` and ``has_*`` expressions from detected arrays."""
    exprs: list[pl.Expr] = []
    generated: set[str] = set()
    arrays = _detect_arrays(columns)

    for prefix, indicator_cols in arrays.items():
        if not indicator_cols:
            continue

        path_parts = prefix.split(".")
        array_name = path_parts[-1]
        singular = _singularise(array_name)

        if len(path_parts) > 1:
            parent_parts = [_singularise(p) for p in path_parts[:-1]]
            count_name = f"number_of_{'_'.join(parent_parts)}_{array_name}"
            has_name = f"has_{'_'.join(parent_parts)}_{singular}"
        else:
            count_name = f"number_of_{array_name}"
            has_name = f"has_{singular}"

        non_null_sum = pl.sum_horizontal(
            *(pl.col(c).is_not_null().cast(pl.Int32) for c in indicator_cols)
        )
        exprs.append(non_null_sum.alias(count_name))
        exprs.append((non_null_sum > 0).alias(has_name))
        generated.add(count_name)
        generated.add(has_name)

    return exprs, generated


def _boolean_group_count_exprs(
    columns: list[str],
) -> tuple[list[pl.Expr], set[str]]:
    """Build ``number_of_*`` expressions from detected boolean groups."""
    exprs: list[pl.Expr] = []
    generated: set[str] = set()
    groups = _detect_boolean_groups(columns)

    for parent, bool_cols in groups.items():
        section_name = parent.replace(".", "_")
        count_name = f"number_of_{section_name}"
        count_expr = pl.sum_horizontal(
            *(pl.col(c).fill_null(False).cast(pl.Int32) for c in bool_cols)
        ).alias(count_name)
        exprs.append(count_expr)
        generated.add(count_name)

    return exprs, generated


# ---------------------------------------------------------------------------
# Rename: mechanical dot -> underscore
# ---------------------------------------------------------------------------


def _build_rename_map(df_columns: list[str]) -> dict[str, str]:
    """Replace every ``.`` with ``_``.  Fully mechanical, no heuristics."""
    return {col: col.replace(".", "_") for col in df_columns if "." in col}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clean_columns(
    df: pl.LazyFrame,
    *,
    rename: dict[str, str] | None = None,
    max_array_expand: int = 10,
) -> pl.LazyFrame:
    """Transform a flattened JSON LazyFrame into clean, model-ready columns.

    ``clean_columns()`` does three mechanical things:

    1. **Renames** every dot-notation column by replacing ``.`` with ``_``.
    2. **Counts array items** -- adds ``number_of_*`` and ``has_*`` columns
       for every detected array pattern (columns with numeric segments).
    3. **Counts boolean groups** -- for columns matching patterns like
       ``section.child_a.selected``, ``section.child_b.selected``, adds a
       ``number_of_*`` column.

    No schema file is needed -- patterns are detected from column names.

    Parameters
    ----------
    df
        A Polars LazyFrame with dot-notation column names.
    rename
        Override specific column names.  Keys are the dot-notation source name
        or the mechanical underscore name; values are the desired output name.
    max_array_expand
        Arrays with more than this many detected slots only receive count
        columns; per-slot columns are dropped.

    Returns
    -------
    pl.LazyFrame
        A LazyFrame with underscore column names and counting columns.

    Raises
    ------
    ValueError
        If two source columns would map to the same target name.
    """
    df_columns = df.collect_schema().names()
    dot_cols = sum(1 for c in df_columns if "." in c)

    # 1. Mechanical rename: dot -> underscore
    auto_rename = _build_rename_map(df_columns)

    # 2. Apply user overrides
    if rename:
        for src, dst in rename.items():
            if src in auto_rename:
                auto_rename[src] = dst
            else:
                for orig, clean in list(auto_rename.items()):
                    if clean == src:
                        auto_rename[orig] = dst
                        break
                else:
                    auto_rename[src] = dst

    # 3. Collision detection
    seen: dict[str, str] = {}
    # Seed with columns that won't be renamed (they already occupy their name)
    for col in df_columns:
        if col not in auto_rename:
            seen[col] = col
    for src, dst in auto_rename.items():
        if dst in seen:
            raise ValueError(
                f"Column name collision: both '{seen[dst]}' and '{src}' map to '{dst}'"
            )
        seen[dst] = src

    # 4. Detect arrays and determine which to drop (large arrays)
    arrays = _detect_arrays(df_columns)
    drop_for_expand: set[str] = set()
    for prefix, indicator_cols in arrays.items():
        slot_count = len(indicator_cols)
        if slot_count > max_array_expand:
            # Drop all columns belonging to this array
            prefix_dot = prefix + "."
            for col in df_columns:
                if col.startswith(prefix_dot):
                    drop_for_expand.add(col)

    # 5. Count columns (computed before rename, using original column names)
    array_exprs, array_names = _array_count_exprs(df_columns)
    group_exprs, group_names = _boolean_group_count_exprs(df_columns)
    count_exprs = array_exprs + group_exprs

    if count_exprs:
        df = df.with_columns(count_exprs)

    # 6. Drop large-array per-slot columns
    for col in drop_for_expand:
        auto_rename.pop(col, None)
    if drop_for_expand:
        existing = set(df.collect_schema().names())
        to_drop = drop_for_expand & existing
        if to_drop:
            df = df.drop(list(to_drop))

    # 7. Apply rename
    current_cols = set(df.collect_schema().names())
    final_rename = {k: v for k, v in auto_rename.items() if k in current_cols}
    if final_rename:
        df = df.rename(final_rename)

    count_total = len(array_names) + len(group_names)
    logger.info(
        "columns_cleaned",
        renamed=dot_cols,
        counts_added=count_total,
        dropped=len(drop_for_expand),
        total_output=len(df.collect_schema().names()),
    )

    return df
