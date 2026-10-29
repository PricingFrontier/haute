"""Qualify create-only upload semantics on an isolated Unity Catalog volume path.

The release gate for hosted publication (specs/hosted-project-storage): each
round races at least two writers to create one object with ``overwrite=False``.
A round qualifies only when exactly one writer stored its bytes, every other
writer was refused with the Files API's already-exists conflict, and the object
then holds the winner's bytes. A writer that failed any other way (permission,
timeout, transport) never contended at the create, so its round proves nothing
and the run is not qualified.

The run owns only what its rounds create: a round starts only on a path that
holds no object, and afterwards deletes whatever object its writers left there.
A path that already held an object is never deleted, and a cleanup that fails
fails the run, naming the path to remove by hand.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from dataclasses import dataclass
from typing import Any, Literal

from haute._uc_transport import _is_already_exists, _is_not_found, _uc_volume_path, validate_uc_url

MIN_WRITERS = 2
MIN_ROUNDS = 1

WriterKind = Literal["stored", "conflict", "error"]


@dataclass(frozen=True)
class WriterOutcome:
    """What one writer's create-only upload did."""

    writer: int
    kind: WriterKind
    error: str = ""

    def describe(self) -> str:
        if self.kind == "stored":
            return f"w{self.writer}: stored its bytes"
        if self.kind == "conflict":
            return f"w{self.writer}: refused, already exists"
        return f"w{self.writer}: failed before contention: {self.error}"


@dataclass(frozen=True)
class RoundResult:
    qualified: bool
    outcome: str
    writers: tuple[WriterOutcome, ...]


def _payload(writer: int) -> bytes:
    return json.dumps({"writer": writer}).encode("utf-8")


def _race(client: Any, round_path: str, num_writers: int) -> tuple[WriterOutcome, ...]:
    """Start every writer's create at once and record what each one did."""
    barrier = threading.Barrier(num_writers)
    outcomes: list[WriterOutcome | None] = [None] * num_writers

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            client.files.upload(round_path, io.BytesIO(_payload(idx)), overwrite=False)
        except Exception as exc:
            if _is_already_exists(exc):
                outcomes[idx] = WriterOutcome(idx, "conflict")
            else:
                outcomes[idx] = WriterOutcome(idx, "error", f"{type(exc).__name__}: {exc}")
        else:
            outcomes[idx] = WriterOutcome(idx, "stored")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    recorded: list[WriterOutcome] = []
    for idx, outcome in enumerate(outcomes):
        if outcome is None:
            raise RuntimeError(f"writer {idx} finished without recording an outcome")
        recorded.append(outcome)
    return tuple(recorded)


def _judge(client: Any, round_path: str, writers: tuple[WriterOutcome, ...]) -> RoundResult:
    failed = [w for w in writers if w.kind == "error"]
    if failed:
        return RoundResult(
            False,
            f"{len(failed)} writer(s) failed before contention; their creates prove nothing",
            writers,
        )
    winners = [w.writer for w in writers if w.kind == "stored"]
    if len(winners) != 1:
        return RoundResult(False, f"expected 1 winner, got {len(winners)}: {winners}", writers)
    winner = winners[0]
    try:
        stored = client.files.download(round_path).contents.read()
    except Exception as exc:
        return RoundResult(
            False, f"could not read the stored object: {type(exc).__name__}: {exc}", writers
        )
    if stored != _payload(winner):
        return RoundResult(False, f"stored bytes {stored!r} are not winner w{winner}'s", writers)
    return RoundResult(True, f"winner w{winner}, every other writer refused", writers)


def _holds_object(client: Any, path: str) -> bool:
    try:
        client.files.get_metadata(path)
    except Exception as exc:
        if _is_not_found(exc):
            return False
        raise
    return True


def _remove_round_object(client: Any, round_path: str) -> str | None:
    """Delete what this round's writers left at *round_path*; the failure, if any."""
    try:
        client.files.delete(round_path)
    except Exception as exc:
        if _is_not_found(exc):
            return None
        return f"could not delete {round_path}: {type(exc).__name__}: {exc}; remove it by hand"
    return None


def run_round(client: Any, round_path: str, num_writers: int) -> RoundResult:
    """Race *num_writers* creates of *round_path* and judge what they did."""
    writers = _race(client, round_path, num_writers)
    return _judge(client, round_path, writers)


def qualify(url: str, writers: int, rounds: int, client: Any) -> int:
    """Run the rounds; 0 when qualified, 1 when not, 2 when refused before any round."""
    if writers < MIN_WRITERS or rounds < MIN_ROUNDS:
        print(
            f"Refused: qualification needs at least {MIN_WRITERS} writers and "
            f"{MIN_ROUNDS} round (got {writers} writers, {rounds} rounds)."
        )
        return 2
    validate_uc_url(url)
    root = _uc_volume_path(url).rstrip("/")

    print(f"Qualifying create-only semantics on: {url}")
    print(f"Volume path: {root} | writers: {writers} | rounds: {rounds}\n")

    anomalies: list[str] = []
    print(f"{'Round':<8} {'Status':<10} Outcome")
    print("-" * 60)
    for number in range(1, rounds + 1):
        round_path = f"{root}/round-{number}/pointer.json"
        if _holds_object(client, round_path):
            print(f"{number:<8} {'REFUSED':<10} {round_path} already holds an object")
            anomalies.append(
                f"Round {number}: {round_path} already held an object this run did not "
                "create; it was left in place. Qualify on an empty path."
            )
            continue
        try:
            result = run_round(client, round_path, writers)
        finally:
            cleanup_failure = _remove_round_object(client, round_path)
        print(f"{number:<8} {'PASS' if result.qualified else 'FAIL':<10} {result.outcome}")
        if not result.qualified:
            anomalies.append(f"Round {number}: {result.outcome}")
            anomalies.extend(f"    {writer.describe()}" for writer in result.writers)
        if cleanup_failure is not None:
            anomalies.append(f"Round {number}: {cleanup_failure}")

    print("-" * 60)
    if anomalies:
        print("VERDICT: NOT QUALIFIED")
        for anomaly in anomalies:
            print(f"  - {anomaly}")
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

    from databricks.sdk import WorkspaceClient

    sys.exit(qualify(args.url, args.writers, args.rounds, WorkspaceClient()))


if __name__ == "__main__":
    main()
