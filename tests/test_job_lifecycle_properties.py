"""Property-based tests for background job lifecycle and concurrency contracts.

Contracts covered from specs/background-jobs/high-level.md:
- One-way terminal transition with precedence-based tie-breaking (Model A).
- Single-flight mutual exclusion by key (Model B).
- Registry latest-job supersession and publication guard (Model C).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hypothesis import find, given
from hypothesis import strategies as st

from haute.routes._background_jobs import (
    CancellableJobRegistry,
    SingleFlightConflictError,
    SingleFlightCoordinator,
)
from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore
from tests._property_budget import pr_budget

# ---------------------------------------------------------------------------
# Independent Reference Model A: Lifecycle State Machine
# ---------------------------------------------------------------------------

ORDERED_TERMINAL_REASONS = (
    "error",
    "contract_error",
    "memory_limited",
    "cancelled",
    "timed_out",
    "superseded",
)
ALL_TERMINAL_REASONS = ("completed",) + ORDERED_TERMINAL_REASONS
REASON_RANKS = {reason: index for index, reason in enumerate(ORDERED_TERMINAL_REASONS, start=1)}


class SpecModelALifecycle:
    """Independent reference model for one job's lifecycle transitions.

    Written directly from the specification:
    error < contract_error < memory_limited < cancelled < timed_out < superseded.
    completed is reachable only from running and is immutable except the explicit
    completed-to-error correction with expected_status='completed'.
    """

    def __init__(self) -> None:
        self.status: str = "running"
        self.reason: str | None = None
        self.ended_at: float | None = None
        self.completed_at: float | None = None

    def transition(self, to: str, expected_status: str, now: float) -> bool:
        if expected_status == "completed" and to != "error":
            raise ValueError("A completed lifecycle record may only be corrected to 'error'")

        if self.status == expected_status:
            self.status = to
            self.reason = to
            self.ended_at = now
            if to == "completed":
                self.completed_at = now
            return True
        elif self.status == "running":
            # expected_status was "completed" but the job is running
            return False
        elif self.status == "completed" or to == "completed":
            return False
        else:
            if REASON_RANKS[to] > REASON_RANKS[self.status]:
                self.status = to
                self.reason = to
                self.ended_at = now
                return True
            return False

    def publish(self, now: float) -> bool:
        if self.status == "running":
            self.status = "completed"
            self.reason = "completed"
            self.ended_at = now
            self.completed_at = now
            return True
        return False


# ---------------------------------------------------------------------------
# Independent Reference Model B: Single-Flight Coordinator
# ---------------------------------------------------------------------------


class SpecModelBSingleFlight:
    """Independent reference model for single-flight key ownership.

    A key has at most one owner. Acquire succeeds if key is unowned or already
    owned by caller (idempotent). Release clears ownership only when called by
    the current owner.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self.owners: dict[str, str | None] = {k: None for k in keys}

    def acquire(self, key: str, job_id: str) -> str:
        curr = self.owners[key]
        if curr is None or curr == job_id:
            self.owners[key] = job_id
            return job_id
        raise SingleFlightConflictError(key=key, active_job_id=curr, active_kind="task")

    def release(self, key: str, job_id: str) -> None:
        if self.owners[key] == job_id:
            self.owners[key] = None


# ---------------------------------------------------------------------------
# Independent Reference Model C: Cancellable Registry Supersession
# ---------------------------------------------------------------------------


class SpecModelCRegistry:
    """Independent reference model for latest-job registry supersession.

    Registering J for K cancels previous job for K (if registered) with
    reason 'superseded'. cancel(job) marks job cancelled with 'cancelled'
    (JobCancellation.cancel overwrites previous reason). release(job) clears
    registration and latest pointer if it still points to that job.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self.latest_by_key: dict[str, str | None] = {k: None for k in keys}
        self.tokens: dict[str, dict[str, Any]] = {}

    def register_latest(self, key: str, job_id: str) -> None:
        prev = self.latest_by_key.get(key)
        if prev is not None and prev in self.tokens:
            self.tokens[prev]["cancelled"] = True
            self.tokens[prev]["reason"] = "superseded"
        self.latest_by_key[key] = job_id
        self.tokens[job_id] = {"key": key, "cancelled": False, "reason": None}

    def cancel(self, job_id: str) -> bool:
        if job_id in self.tokens:
            self.tokens[job_id]["cancelled"] = True
            # In JobCancellation.cancel, a new reason overwrites
            self.tokens[job_id]["reason"] = "cancelled"
            return True
        return False

    def release(self, job_id: str) -> None:
        if job_id in self.tokens:
            key = self.tokens[job_id]["key"]
            del self.tokens[job_id]
            if self.latest_by_key.get(key) == job_id:
                self.latest_by_key[key] = None

    def is_cancelled(self, job_id: str) -> bool:
        return self.tokens[job_id]["cancelled"] if job_id in self.tokens else False

    def cancellation_reason(self, job_id: str) -> str | None:
        if job_id in self.tokens and self.tokens[job_id]["cancelled"]:
            return self.tokens[job_id]["reason"]
        return None

    def latest_publication(self, job_id: str) -> bool:
        return bool(
            job_id in self.tokens
            and not self.tokens[job_id]["cancelled"]
            and self.latest_by_key.get(self.tokens[job_id]["key"]) == job_id
        )


# ---------------------------------------------------------------------------
# Strategies for Operations
# ---------------------------------------------------------------------------

st_model_a_ops = st.lists(
    st.one_of(
        st.tuples(
            st.just("transition"),
            st.sampled_from(ALL_TERMINAL_REASONS),
            st.sampled_from(["running", "completed"]),
        ),
        st.tuples(st.just("publish")),
    ),
    min_size=1,
    max_size=8,
)

st_keys_b = ["k1", "k2"]
st_jobs_b = ["j1", "j2", "j3"]
st_model_b_ops = st.lists(
    st.tuples(
        st.sampled_from(["acquire", "release"]),
        st.sampled_from(st_keys_b),
        st.sampled_from(st_jobs_b),
    ),
    min_size=1,
    max_size=10,
)

st_keys_c = ["k1", "k2"]
st_jobs_c = ["j1", "j2", "j3", "j4"]
st_model_c_ops = st.lists(
    st.one_of(
        st.tuples(
            st.just("register_latest"),
            st.sampled_from(st_keys_c),
            st.sampled_from(st_jobs_c),
        ),
        st.tuples(st.just("cancel"), st.sampled_from(st_jobs_c)),
        st.tuples(st.just("release"), st.sampled_from(st_jobs_c)),
    ),
    min_size=1,
    max_size=10,
)


# ---------------------------------------------------------------------------
# Property: Model A Lifecycle Store Agreement
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(ops=st_model_a_ops)
def test_lifecycle_store_agrees_with_the_spec_model(ops: list[tuple[Any, ...]]) -> None:
    """Drive JobStore and SpecModelALifecycle in lockstep; assert agreement on every step."""
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    model = SpecModelALifecycle()
    ever_terminal = False

    for idx, op in enumerate(ops):
        now = float(idx + 1)
        if op[0] == "transition":
            _, to, expected_status = op
            model_error: type[Exception] | None = None
            store_error: type[Exception] | None = None
            model_accepted: bool = False
            store_accepted: bool = False

            try:
                model_accepted = model.transition(to, expected_status, now)
            except ValueError as exc:
                model_error = type(exc)

            try:
                res = lifecycle.transition(
                    job_id,
                    to=to,
                    expected_status=expected_status,
                    now=now,
                )
                store_accepted = res is not None
            except ValueError as exc:
                store_error = type(exc)

            assert model_error == store_error, (
                f"Step {idx} ({op!r}): error mismatch model={model_error} store={store_error}"
            )
            if model_error is None:
                assert model_accepted == store_accepted, (
                    f"Step {idx} ({op!r}): acceptance mismatch "
                    f"model={model_accepted} store={store_accepted}"
                )
        else:
            model_accepted = model.publish(now)
            res = lifecycle.publish_completion(job_id, publish=lambda: {}, now=now)
            store_accepted = res is not None
            assert model_accepted == store_accepted, (
                f"Step {idx} (publish): acceptance mismatch "
                f"model={model_accepted} store={store_accepted}"
            )

        job = store.require_job(job_id)
        assert job["status"] == model.status, (
            f"Step {idx}: status mismatch store={job['status']!r} model={model.status!r}"
        )

        if model.status == "running":
            assert "terminal_reason" not in job
        else:
            ever_terminal = True
            assert job.get("terminal_reason") == model.reason, (
                f"Step {idx}: terminal_reason mismatch "
                f"store={job.get('terminal_reason')!r} model={model.reason!r}"
            )
            assert job.get("ended_at") == model.ended_at, (
                f"Step {idx}: ended_at mismatch store={job.get('ended_at')} model={model.ended_at}"
            )

        assert job.get("completed_at") == model.completed_at, (
            f"Step {idx}: completed_at mismatch "
            f"store={job.get('completed_at')} model={model.completed_at}"
        )

        if ever_terminal:
            assert job["status"] != "running", (
                f"Step {idx}: job status returned to 'running' after becoming terminal"
            )


# ---------------------------------------------------------------------------
# Negative Controls: Model A
# ---------------------------------------------------------------------------


def test_first_write_wins_model_is_refuted() -> None:
    """Negative control: a model variant that refuses every write once terminal
    disagrees with the store on some sequence."""

    def fww_disagrees(ops: list[tuple[Any, ...]]) -> bool:
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})
        fww_status = "running"

        for idx, op in enumerate(ops):
            now = float(idx + 1)
            if op[0] == "transition":
                _, to, expected_status = op
                if expected_status == "completed" and to != "error":
                    continue
                # First-write-wins: refuses all transitions once terminal
                if fww_status == "running":
                    if expected_status == "running":
                        fww_status = to
                        fww_accepted = True
                    else:
                        fww_accepted = False
                else:
                    fww_accepted = False

                store_accepted = (
                    lifecycle.transition(job_id, to=to, expected_status=expected_status, now=now)
                    is not None
                )
                if fww_accepted != store_accepted:
                    return True
            else:
                if fww_status == "running":
                    fww_status = "completed"
                    fww_accepted = True
                else:
                    fww_accepted = False

                store_accepted = (
                    lifecycle.publish_completion(job_id, publish=lambda: {}, now=now) is not None
                )
                if fww_accepted != store_accepted:
                    return True
        return False

    found = find(st_model_a_ops, fww_disagrees, settings=pr_budget(60))
    assert fww_disagrees(found), f"First-write-wins model was not refuted by sequence: {found!r}"


def test_last_write_wins_model_is_refuted() -> None:
    """Negative control: a model variant that accepts every terminal write
    disagrees with the store on some sequence."""

    def lww_disagrees(ops: list[tuple[Any, ...]]) -> bool:
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        for idx, op in enumerate(ops):
            now = float(idx + 1)
            if op[0] == "transition":
                _, to, expected_status = op
                if expected_status == "completed" and to != "error":
                    continue
                # Last-write-wins accepts every terminal transition
                lww_accepted = True
                store_accepted = (
                    lifecycle.transition(job_id, to=to, expected_status=expected_status, now=now)
                    is not None
                )
                if lww_accepted != store_accepted:
                    return True
            else:
                lww_accepted = False
                store_accepted = (
                    lifecycle.publish_completion(job_id, publish=lambda: {}, now=now) is not None
                )
                if lww_accepted != store_accepted:
                    return True
        return False

    found = find(st_model_a_ops, lww_disagrees, settings=pr_budget(60))
    assert lww_disagrees(found), f"Last-write-wins model was not refuted by sequence: {found!r}"


# ---------------------------------------------------------------------------
# Property: Model B Single-Flight Coordinator Agreement
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(ops=st_model_b_ops)
def test_single_flight_coordinator_agrees_with_spec_model(
    ops: list[tuple[str, str, str]],
) -> None:
    """Drive SingleFlightCoordinator and SpecModelBSingleFlight in lockstep."""
    coord = SingleFlightCoordinator()
    model = SpecModelBSingleFlight(st_keys_b)

    for op, key, job_id in ops:
        if op == "acquire":
            coord_conflict: str | None = None
            model_conflict: str | None = None

            try:
                coord.acquire(key, job_id=job_id, kind="task")
            except SingleFlightConflictError as exc:
                coord_conflict = exc.active_job_id

            try:
                model.acquire(key, job_id)
            except SingleFlightConflictError as exc:
                model_conflict = exc.active_job_id

            assert coord_conflict == model_conflict, (
                f"Op acquire({key}, {job_id}): conflict mismatch "
                f"coord={coord_conflict} model={model_conflict}"
            )
        else:
            coord.release(key, job_id=job_id)
            model.release(key, job_id)

        active_handle = coord.active(key)
        coord_owner = active_handle.job_id if active_handle is not None else None
        assert coord_owner == model.owners[key], (
            f"Active owner mismatch for {key}: coord={coord_owner} model={model.owners[key]}"
        )


def test_release_by_non_owner_frees_key_model_is_refuted() -> None:
    """Negative control: a model where release by a non-owner also frees the key
    is refuted by find."""

    def buggy_release_disagrees(ops: list[tuple[str, str, str]]) -> bool:
        coord = SingleFlightCoordinator()
        owners: dict[str, str | None] = {k: None for k in st_keys_b}

        for op, k, j in ops:
            if op == "acquire":
                c_exc = False
                try:
                    coord.acquire(k, job_id=j, kind="task")
                except SingleFlightConflictError:
                    c_exc = True

                m_exc = owners[k] is not None and owners[k] != j
                if c_exc != m_exc:
                    return True
                if not m_exc:
                    owners[k] = j
            else:
                coord.release(k, job_id=j)
                # Buggy release: unconditionally clears ownership
                owners[k] = None

            active_h = coord.active(k)
            c_active = active_h.job_id if active_h else None
            if c_active != owners[k]:
                return True
        return False

    found = find(st_model_b_ops, buggy_release_disagrees, settings=pr_budget(60))
    assert buggy_release_disagrees(found), (
        f"Buggy release model was not refuted by sequence: {found!r}"
    )


# ---------------------------------------------------------------------------
# Property: Model C Registry Supersession Agreement
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(ops=st_model_c_ops)
def test_registry_supersession_agrees_with_spec_model(ops: list[tuple[Any, ...]]) -> None:
    """Drive CancellableJobRegistry and SpecModelCRegistry in lockstep."""
    reg = CancellableJobRegistry()
    model = SpecModelCRegistry(st_keys_c)

    for op in ops:
        if op[0] == "register_latest":
            _, key, job_id = op
            reg.register_latest(key, job_id)
            model.register_latest(key, job_id)
        elif op[0] == "cancel":
            _, job_id = op
            reg_res = reg.cancel(job_id)
            model_res = model.cancel(job_id)
            assert reg_res == model_res, f"Cancel mismatch on {job_id}"
        else:
            _, job_id = op
            reg.release(job_id)
            model.release(job_id)

        for j in st_jobs_c:
            assert reg.is_cancelled(j) == model.is_cancelled(j), (
                f"is_cancelled mismatch for {j}: "
                f"reg={reg.is_cancelled(j)} model={model.is_cancelled(j)}"
            )
            assert reg.cancellation_reason(j) == model.cancellation_reason(j), (
                f"cancellation_reason mismatch for {j}: "
                f"reg={reg.cancellation_reason(j)} model={model.cancellation_reason(j)}"
            )
            with reg.latest_publication(j) as reg_pub:
                assert reg_pub == model.latest_publication(j), (
                    f"latest_publication mismatch for {j}: "
                    f"reg={reg_pub} model={model.latest_publication(j)}"
                )


def test_superseded_job_can_publish_model_is_refuted() -> None:
    """Negative control: a model that lets a superseded job publish is refuted by find."""

    def buggy_superseded_disagrees(ops: list[tuple[Any, ...]]) -> bool:
        reg = CancellableJobRegistry()
        for op in ops:
            if op[0] == "register_latest":
                reg.register_latest(op[1], op[2])
            elif op[0] == "cancel":
                reg.cancel(op[1])
            else:
                reg.release(op[1])

        for j in st_jobs_c:
            with reg.latest_publication(j) as reg_pub:
                # Buggy model lets superseded jobs publish if still registered
                is_superseded = reg.cancellation_reason(j) == "superseded"
                if is_superseded and not reg_pub:
                    return True
        return False

    found = find(st_model_c_ops, buggy_superseded_disagrees, settings=pr_budget(60))
    assert buggy_superseded_disagrees(found), (
        f"Buggy superseded-can-publish model was not refuted by sequence: {found!r}"
    )
