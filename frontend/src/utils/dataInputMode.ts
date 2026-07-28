/**
 * Mirrors the backend derivation (`data_input_is_direct` in
 * `_polars_io_registry.py`): a file-backed Parquet scan reads directly from
 * its configured source; every other Data Input executes from a published
 * snapshot. An absent or blank mode means the format default, which for
 * Parquet is `scan` — the same unset rule as the backend's
 * `resolve_input_mode`.
 */
export function dataInputIsDirect(config: Record<string, unknown>): boolean {
  return (
    config.inputType === "file" &&
    config.format === "parquet" &&
    (config.mode == null || config.mode === "" || config.mode === "scan")
  )
}
