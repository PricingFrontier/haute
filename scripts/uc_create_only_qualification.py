"""Verify create-only upload semantics for a Unity Catalog volume path."""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from typing import Any

from haute._uc_transport import _is_already_exists, _uc_volume_path, validate_uc_url


def run_round(
    client: Any,
    round_path: str,
    num_writers: int,
) -> tuple[bool, str, list[str]]:
    barrier = threading.Barrier(num_writers)
    results: list[tuple[bool, str]] = [(False, "")] * num_writers

    def worker(idx: int) -> None:
        payload = json.dumps({"writer": idx}).encode("utf-8")
        barrier.wait()
        try:
            client.files.upload(round_path, io.BytesIO(payload), overwrite=False)
            results[idx] = (True, f"writer {idx}")
        except Exception as exc:
            if _is_already_exists(exc):
                results[idx] = (False, "already_exists (409)")
            else:
                results[idx] = (False, f"error: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [i for i, (ok, _) in enumerate(results) if ok]
    details = [f"w{i}: {msg}" for i, (_, msg) in enumerate(results)]

    if len(winners) != 1:
        return False, f"expected 1 winner, got {len(winners)}: {winners}", details

    winner = winners[0]
    expected_payload = json.dumps({"writer": winner}).encode("utf-8")
    try:
        downloaded = client.files.download(round_path).contents.read()
        if downloaded.strip() != expected_payload.strip():
            return False, f"stored bytes mismatch: got {downloaded!r}, expected w{winner}", details
    except Exception as exc:
        return False, f"failed to download stored object: {exc}", details

    return True, f"winner w{winner}", details


def qualify(url: str, writers: int, rounds: int) -> int:
    validate_uc_url(url)
    root = _uc_volume_path(url).rstrip("/")

    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient()

    print(f"Qualifying create-only semantics on: {url}")
    print(f"Volume path: {root} | writers: {writers} | rounds: {rounds}\n")

    anomalies: list[str] = []
    print(f"{'Round':<8} {'Status':<10} {'Outcome':<30}")
    print("-" * 50)

    for r in range(1, rounds + 1):
        round_path = f"{root}/round-{r}/pointer.json"
        try:
            ok, outcome, _ = run_round(client, round_path, writers)
            status = "PASS" if ok else "FAIL"
            print(f"{r:<8} {status:<10} {outcome:<30}")
            if not ok:
                anomalies.append(f"Round {r}: {outcome}")
        finally:
            try:
                client.files.delete(round_path)
            except Exception:
                pass

    print("-" * 50)
    if anomalies:
        print("VERDICT: NOT QUALIFIED")
        for a in anomalies:
            print(f"  - {a}")
        return 1

    print("VERDICT: QUALIFIED")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify create-only upload semantics on UC volume.",
    )
    parser.add_argument("url", help="Unity Catalog location (uc://catalog.schema.volume/path)")
    parser.add_argument("--writers", type=int, default=8, help="Concurrent writers (default: 8)")
    parser.add_argument("--rounds", type=int, default=5, help="Rounds to test (default: 5)")
    args = parser.parse_args()
    sys.exit(qualify(args.url, args.writers, args.rounds))


if __name__ == "__main__":
    main()
