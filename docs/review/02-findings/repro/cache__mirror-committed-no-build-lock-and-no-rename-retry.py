"""Adversarial reproduction for claim
`mirror-committed-no-build-lock-and-no-rename-retry`.

CLAIM (two distinct parts):
  Part A (concurrency / torn read): `mirror_cache_to_committed` (save path,
    in `haute._json_flatten`) copytrees `working/<hash>/` -> a `.tmp` sibling
    WITHOUT taking the per-dir `_BUILD_LOCKS` lock that `build_per_port_cache`
    (in `haute._json_shred`) holds while it atomically swaps `working/<hash>/`.
    `shutil.copytree` snapshots the source dir listing ONCE (os.scandir) then
    copies each entry. A concurrent build whose `_swap_dir_into_place` renames
    the live `working/<hash>/` aside mid-walk makes a still-pending entry's
    source path dangle -> `FileNotFoundError`, OR copies a torn mix
    (meta.json from one build + a parquet from another). The two functions live
    in different modules and the lock is `_json_shred`-private, so mirror does
    NOT serialise against the build.

  Part B (Windows rename retry asymmetry): mirror uses BARE `.rename()`
    (no PermissionError retry) where `_swap_dir_into_place` uses
    `_rename_dir_with_retry`. This part is a static diff; asserted below by
    inspecting the byte-code/source of both functions (platform-independent
    evidence that the retry wrapper is present in one and absent in the other).

This script ISOLATES everything under a tempfile dir + os.chdir (the cache
root is `Path.cwd()/.haute_cache/...`, NOT the sandbox root). It NEVER touches
real project files.

Part A is proven DETERMINISTICALLY: we drive the real `shutil.copytree` and,
via its `copy_function` hook (invoked per-entry AFTER copytree has already
snapshotted the directory listing), we trigger the concurrent build's atomic
swap between the first and second file copy. We then assert the SPECIFIC wrong
behaviour: copytree raises FileNotFoundError reading a source path that the
concurrent swap renamed away -- i.e. the mirror tore.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


def _col(name: str, path: str, *, type_: str = "int") -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_,
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _root_cfg(*cols: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": list(cols),
            }
        ]
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haute_mirror_repro_"))
    prev_cwd = Path.cwd()
    os.chdir(tmp)
    try:
        from haute import _json_flatten
        from haute._json_flatten import (
            _clear_session,
            _json_cache_dir,
            _mark_working_consulted,
            mirror_cache_to_committed,
        )
        from haute import _json_shred
        from haute._json_shred import build_per_port_cache

        _clear_session()

        data = tmp / "data.json"
        data.write_text(json.dumps([{"id": 1, "amt": 10}]), encoding="utf-8")
        data_str = str(data.resolve())

        # Schema A: two emit columns -> the build writes one parquet (single
        # root table) + meta.json. We need >=2 files in working/ so copytree's
        # per-entry copy_function fires more than once (meta.json THEN parquet,
        # or vice versa); the swap is injected between the two.
        cfg_a = _root_cfg(_col("id", "$[*].id"), _col("amt", "$[*].amt"))
        # cfg_b must be a VALID schema (so the concurrent build's strict-typed
        # shred succeeds and actually swaps working/) yet have a DIFFERENT
        # fingerprint (so it is a real swap, not the no-op trapdoor). Same
        # int type, but a single column -> different config -> different fp.
        cfg_b = _root_cfg(_col("id", "$[*].id"))

        working_dir = _json_cache_dir(data_str, "working")

        # 1) Build the initial working/ cache and arm the mirror (the route
        #    does _mark_working_consulted on success).
        build_per_port_cache(data_str, cfg_a, working_dir)
        _mark_working_consulted(data_str)

        entries_before = sorted(p.name for p in working_dir.iterdir())
        print(f"[setup] working/ contains: {entries_before}")
        assert len(entries_before) >= 2, (
            "need >=2 files in working/ so copytree walks multiple entries; "
            f"got {entries_before}"
        )

        committed_dir = _json_cache_dir(data_str, "committed")
        assert not committed_dir.exists(), "committed/ should not exist yet"

        # 2) Sanity: confirm mirror really does NOT acquire the build lock.
        #    `_swap_dir_into_place` renames live_dir -> a UNIQUE .build-old-<uuid>
        #    sibling. The build lock `_BUILD_LOCKS` is keyed on the resolved
        #    cache dir. If mirror held it, the concurrent build below would
        #    block instead of swapping mid-copy. We assert the source of
        #    mirror_cache_to_committed references neither `_build_lock_for` nor
        #    `_BUILD_LOCKS` (it is in a different module and cannot).
        mirror_src = inspect.getsource(mirror_cache_to_committed)
        assert "_build_lock_for" not in mirror_src and "_BUILD_LOCKS" not in mirror_src, (
            "REFUTED: mirror_cache_to_committed appears to take the build lock"
        )
        print("[trace] mirror_cache_to_committed takes NO _BUILD_LOCKS / _build_lock_for")

        # 3) Drive the real shutil.copytree through mirror, but hook its
        #    per-entry copy_function so that AFTER the first file is copied
        #    (copytree has already snapshotted the dir listing), a concurrent
        #    build atomically swaps working/ — renaming the live dir aside.
        #    The remaining snapshotted source entries then dangle.
        real_copytree = shutil.copytree
        swap_done = {"n": 0}
        copied_names: list[str] = []

        def _instrumented_copytree(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
            real_copy_function = kwargs.pop("copy_function", shutil.copy2)

            def _copy_function(s: str, d: str) -> Any:
                copied_names.append(Path(s).name)
                # Copy THIS entry first (capturing build A's bytes for entry 0),
                # THEN inject the concurrent build's atomic swap exactly once.
                # copytree has already snapshotted the dir listing, so the next
                # entry's source path string is unchanged but now resolves into
                # whatever the swap renamed into working/ (build B) -> torn mix
                # (or dangles if the file name set differs -> FileNotFoundError).
                result = real_copy_function(s, d)
                if swap_done["n"] == 0:
                    swap_done["n"] += 1
                    # A different schema fingerprint forces a REAL swap
                    # (not the no-op trapdoor). This renames the live
                    # working/ dir aside via _swap_dir_into_place.
                    build_per_port_cache(data_str, cfg_b, working_dir)
                return result

            kwargs["copy_function"] = _copy_function
            return real_copytree(src, dst, *args, **kwargs)

        _json_flatten.shutil.copytree = _instrumented_copytree  # type: ignore[assignment]

        raised: BaseException | None = None
        try:
            mirror_cache_to_committed(data_str)
        except BaseException as exc:  # noqa: BLE001 - we classify it below
            raised = exc
        finally:
            _json_flatten.shutil.copytree = real_copytree  # type: ignore[assignment]

        print(f"[run] copy_function fired for entries: {copied_names}")
        print(f"[run] concurrent build swap injected: {swap_done['n']} time(s)")
        print(f"[run] mirror raised: {type(raised).__name__ if raised else None}: {raised}")

        # ---- Part A verdict ------------------------------------------------
        # The predicted failure: a snapshotted source entry vanished because
        # the concurrent swap renamed working/ aside -> FileNotFoundError on a
        # source path INSIDE the (now-renamed) working dir.
        part_a_reproduced = False
        if isinstance(raised, FileNotFoundError):
            missing = str(getattr(raised, "filename", "") or raised)
            # The dangling path must be a source entry under the original
            # working/ dir (not the .tmp destination), proving the torn read.
            if os.path.normcase(str(working_dir)) in os.path.normcase(missing):
                part_a_reproduced = True
                print(
                    "[PART A] REPRODUCED: copytree tore -- a snapshotted source "
                    f"entry vanished mid-walk: {missing}"
                )

        # Alternative tearing manifestation: mirror 'succeeds' but committed/
        # ends up INCOHERENT -- meta.json describes build A (its column_count)
        # while the co-located parquet bytes are build B's (different column
        # count). The mirror is supposed to copy ONE self-consistent snapshot.
        # We read the meta's declared column_count for `root` and compare it to
        # the actual parquet's column count: a mismatch is the torn mix.
        import pyarrow.parquet as pq

        if not part_a_reproduced and raised is None and committed_dir.exists():
            meta_path = committed_dir / "meta.json"
            if meta_path.exists():
                meta_obj = json.loads(meta_path.read_text(encoding="utf-8"))
                fp = meta_obj.get("schema_fingerprint", "")
                tables = {t.get("label"): t for t in meta_obj.get("tables", [])}
                meta_cols = tables.get("root", {}).get("column_count")
                root_parquet = committed_dir / "root.parquet"
                # 1) meta lists a parquet that is not on disk -> torn.
                listed = {t.get("parquet") for t in meta_obj.get("tables", [])}
                on_disk = {p.name for p in committed_dir.glob("*.parquet")}
                actual_cols = None
                if root_parquet.exists():
                    actual_cols = pq.read_schema(root_parquet).names
                print(
                    f"[PART A] committed/ meta fp={fp[:8]} "
                    f"meta.column_count(root)={meta_cols} "
                    f"actual parquet columns={actual_cols} "
                    f"meta-listed parquets={listed} on-disk={on_disk}"
                )
                if listed - on_disk:
                    part_a_reproduced = True
                    print(
                        "[PART A] REPRODUCED (torn mix): committed/ meta lists "
                        f"parquets {listed} but on-disk has {on_disk}"
                    )
                elif (
                    meta_cols is not None
                    and actual_cols is not None
                    and meta_cols != len(actual_cols)
                ):
                    part_a_reproduced = True
                    print(
                        "[PART A] REPRODUCED (torn mix): committed/meta.json "
                        f"declares column_count={meta_cols} for `root` but the "
                        f"co-located root.parquet actually has {len(actual_cols)} "
                        f"columns {actual_cols} -- meta from one build, parquet "
                        "from another."
                    )

        # ---- Part B: static rename-retry asymmetry -------------------------
        flatten_src = inspect.getsource(mirror_cache_to_committed)
        swap_src = inspect.getsource(_json_shred._swap_dir_into_place)
        retry_src = inspect.getsource(_json_shred._rename_dir_with_retry)

        mirror_uses_retry = "_rename_dir_with_retry" in flatten_src
        mirror_uses_bare_rename = ".rename(" in flatten_src
        swap_uses_retry = "_rename_dir_with_retry" in swap_src
        retry_catches_permerror = "PermissionError" in retry_src

        print(
            f"[PART B] mirror uses _rename_dir_with_retry={mirror_uses_retry}, "
            f"mirror uses bare .rename={mirror_uses_bare_rename}, "
            f"swap uses _rename_dir_with_retry={swap_uses_retry}, "
            f"retry catches PermissionError={retry_catches_permerror}"
        )
        part_b_reproduced = (
            (not mirror_uses_retry)
            and mirror_uses_bare_rename
            and swap_uses_retry
            and retry_catches_permerror
        )
        if part_b_reproduced:
            print(
                "[PART B] CONFIRMED: mirror uses bare .rename with no Windows "
                "PermissionError retry; _swap_dir_into_place uses the retry wrapper."
            )

        print()
        print(f"PART A (torn read, no build lock) reproduced: {part_a_reproduced}")
        print(f"PART B (bare rename, no retry)     confirmed:  {part_b_reproduced}")

        # The claim is substantiated if EITHER hazard is demonstrated; both
        # are asserted distinctly. Part A is the executable concurrency proof.
        assert part_a_reproduced, (
            "Part A NOT reproduced: mirror's copytree did not tear under a "
            "concurrent atomic swap of working/."
        )
        assert part_b_reproduced, (
            "Part B NOT confirmed: rename-retry asymmetry not present as claimed."
        )
        print("\nRESULT: CLAIM SUBSTANTIATED (both parts).")
        return 0
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
