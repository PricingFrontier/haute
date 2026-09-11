"""Structured-input engine: v2 per-port JSON/JSONL/XML shred and cache.

The package decomposes the engine by concern; every submodule is imported by
its concern name and there are no aggregating re-exports:

- ``_records`` — streaming JSON/JSONL/XML record iteration and range tiling.
- ``_shred`` — table specs and the single-pass record walk.
- ``_writer`` — bounded Parquet row-group emission and spill bundles.
- ``_publication`` — cross-process locking, staging, atomic swap, recovery.
- ``_source_proof`` — strong native revisions and SHA-256 content signatures.
- ``_runtime_storage`` — disk budget, spill leases, and runtime snapshots.
- ``_inference`` — v2 schema inference from data.
- ``_inference_filter`` — bounded native checks for already-observed structures.
- ``_inference_cache`` — complete schema reuse behind strong source revisions.
- ``_cache`` — cache build/validate/load lifecycle and the apiInput loader.
"""
