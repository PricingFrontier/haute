"""``haute app rollout``: the hosted app rolled out, checked and recorded (specs/workbench,
Control flow 35), against a fake Container App and a fake app answering the check."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from haute import _workbench_rollout as rollouts
from haute._workbench_config import AppEnvironment, WorkbenchConfig
from haute._workbench_rollout import (
    AppState,
    Clock,
    HttpAnswer,
    QuotesClosedError,
    Recorded,
    RevisionState,
    RolloutError,
    app_environment,
    credentials,
    head_commit,
    hosted_check,
    promoted,
    rollout,
)
from tests._git_helpers import git_run, init_repo

OLD = "acr.example.io/pricing/motor@sha256:" + "0" * 64
NEW = "acr.example.io/pricing/motor@sha256:" + "a" * 64
COMMIT = "c0ffee" + "0" * 34
ADDRESS = "motor.example.azurecontainerapps.io"


class FakeHost:
    """A Container App in single revision mode running *image*: what the rollout reads and
    changes, each change listed in ``calls``."""

    def __init__(self, image: str = OLD, **state: Any) -> None:
        self.calls: list[tuple[str, ...]] = []
        old = RevisionState("motor--old", image, True, "Provisioned", "Running")
        self.revisions: dict[str, RevisionState] = {old.name: old}
        self.latest: str | None = old.name
        self.ready: str | None = old.name
        self.limits: list[float | None] = []
        self.image = image
        self.tags: dict[str, str] = {"owner": "pricing"}
        self.settings: dict[str, Any] = {
            "single_revision": True,
            "containers": 1,
            "address": ADDRESS,
            "client_id": "app-client-id",
        }
        self.settings.update(state)
        #: What each new revision of an image settles as: (provisioning, running).
        self.settles: dict[str, tuple[str, str]] = {}
        #: Images whose update Azure refuses, or does not finish in time.
        self.refuses: set[str] = set()
        self.token_refused = False
        #: Revisions whose deactivation Azure applies, then answers with a failure.
        self.deactivation_lost: set[str] = set()
        #: Called after each change: a test's other writer.
        self.after: Any = None
        #: Called on each read of the app's state; and each set_image's timeout.
        self.on_state: Any = None
        self.timeouts: list[float] = []

    def state(self) -> AppState:
        if self.on_state is not None:
            self.on_state(self)
        return AppState(
            single_revision=self.settings["single_revision"],
            images=(self.image,) * self.settings["containers"],
            address=self.settings["address"],
            latest_revision=self.latest,
            active_revisions=tuple(n for n, r in self.revisions.items() if r.active),
            client_id=self.settings["client_id"],
            tag_names=frozenset(self.tags),
            recorded=rollouts._recorded(self.tags),
            ready_revision=self.ready,
        )

    def revision(self, name: str) -> RevisionState:
        return self.revisions[name]

    def limit(self, seconds: float | None) -> None:
        self.limits.append(seconds)

    def deactivate(self, name: str) -> None:
        self.calls.append(("deactivate", name))
        self.revisions[name] = replace(self.revisions[name], active=False)
        if name in self.deactivation_lost:
            raise RolloutError(f"Azure's answer to stopping {name} was lost.")

    def set_image(self, image: str, suffix: str, timeout: float) -> str:
        self.calls.append(("set_image", image, suffix))
        self.timeouts.append(timeout)
        if image in self.refuses:
            raise RolloutError("Azure refused the update.")
        name = f"motor--{suffix}"
        provisioning, running = self.settles.get(image, ("Provisioned", "Running"))
        for other, revision in self.revisions.items():
            self.revisions[other] = replace(revision, active=False)
        self.revisions[name] = RevisionState(name, image, True, provisioning, running)
        self.latest, self.image = name, image
        if provisioning == "Provisioned":
            self.ready = name
        if self.after is not None:
            self.after(self)
        return name

    def record(self, recorded: Recorded) -> None:
        self.calls.append(("record", recorded.image, recorded.release, recorded.commit))
        self.tags.update(
            {
                "haute-image": recorded.image,
                "haute-release": recorded.release,
                "haute-commit": recorded.commit,
            }
        )

    def token(self, client_id: str) -> str:
        if self.token_refused:
            raise RolloutError("Azure gave no token.")
        return f"token-for-{client_id}"

    def changes(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] != "record"]


@dataclass
class FakeApp:
    """The hosted app as the check sees it: what each request is answered with."""

    release: str = "rel-1"
    health: list[HttpAnswer] = field(default_factory=list)
    forged: HttpAnswer = field(default_factory=lambda: HttpAnswer(401))
    signed: HttpAnswer | None = None
    quotes: dict[str, Any] = field(
        default_factory=lambda: {
            "open": True,
            "error_code": None,
            "message": None,
            "differences": [],
        }
    )
    asked: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpAnswer:
        self.asked.append((url, dict(headers)))
        signed_in = headers.get("Authorization") == "Bearer token-for-app-client-id"
        if url.endswith("/health"):
            assert signed_in, "the health check is signed in"
            if self.health:
                return self.health.pop(0)
            return _ok({"status": "ok", "release": self.release})
        if not signed_in:
            assert "X-MS-CLIENT-PRINCIPAL" in headers
            return self.forged
        if self.signed is not None:
            return self.signed
        return _ok({"id": self.release, "quotes": self.quotes})


def _ok(body: dict[str, Any]) -> HttpAnswer:
    return HttpAnswer(200, json.dumps(body).encode("utf-8"))


class FakeClock:
    """Time that passes only as the rollout sleeps, one second fixed for every suffix."""

    def __init__(self) -> None:
        self.t = 0.0
        self.numbers = iter(f"{n:06x}" for n in range(1, 10_000))

    def clock(self) -> Clock:
        return Clock(
            now=lambda: self.t,
            sleep=self._sleep,
            utc=lambda: datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC),
            random=lambda: next(self.numbers),
        )

    def _sleep(self, seconds: float) -> None:
        self.t += seconds


def _roll(host: FakeHost, app: FakeApp, image: str = NEW, **options: Any) -> str:
    said: list[str] = []
    try:
        return rollout(
            host,
            environment="staging",
            image=image,
            commit=COMMIT,
            say=said.append,
            get=app,
            clock=options.pop("clock", FakeClock().clock()),
        )
    finally:
        host.said = said  # type: ignore[attr-defined]


# ── The preflight: refused before anything changes ─────────────────────


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"single_revision": False}, "keeps several revisions active"),
        ({"containers": 2}, "runs 2 containers"),
        ({"address": None}, "no requests at a public address"),
        ({"client_id": None}, "signs no one in through Microsoft Entra ID"),
    ],
    ids=["multiple-revision-mode", "two-containers", "no-address", "no-entra-id"],
)
def test_an_app_the_rollout_cannot_roll_out_is_refused_changing_nothing(
    change: dict[str, Any], message: str
) -> None:
    host = FakeHost(**change)

    with pytest.raises(RolloutError, match=message):
        _roll(host, FakeApp())

    assert host.calls == []


def test_an_image_named_by_tag_is_refused_changing_nothing() -> None:
    host = FakeHost()

    with pytest.raises(RolloutError, match="names no image by digest"):
        _roll(host, FakeApp(), image="acr.example.io/pricing/motor:latest")

    assert host.calls == []


def test_a_token_azure_will_not_give_is_refused_changing_nothing() -> None:
    host = FakeHost()
    host.token_refused = True

    with pytest.raises(RolloutError, match="Azure gave no token"):
        _roll(host, FakeApp())

    assert host.calls == []


def test_an_image_past_azures_tag_value_limit_is_refused_changing_nothing() -> None:
    host = FakeHost()
    long_image = "acr.example.io/" + "p" * 200 + "/motor@sha256:" + "a" * 64

    with pytest.raises(RolloutError, match="past the 256 an Azure tag holds"):
        _roll(host, FakeApp(), image=long_image)

    assert host.calls == []


def test_an_app_with_no_room_for_the_records_tags_is_refused_changing_nothing() -> None:
    host = FakeHost()
    host.tags = {f"tag-{n}": "x" for n in range(48)}

    with pytest.raises(RolloutError, match="the release's record needs 3 of the 50"):
        _roll(host, FakeApp())

    assert host.calls == []


# ── The maintenance window ────────────────────────────────────────────


def test_the_old_release_stops_before_the_new_one_starts_and_the_release_is_recorded() -> None:
    host = FakeHost()
    app = FakeApp()

    release = _roll(host, app)

    assert release == "rel-1"
    # Every active revision deactivated, then the image set on a revision of its own.
    assert host.changes() == [
        ("deactivate", "motor--old"),
        ("set_image", NEW, "h261101120000000001"),
    ]
    assert host.calls[-1] == ("record", NEW, "rel-1", COMMIT)
    # The app's other tags are kept beside the record.
    assert host.tags["owner"] == "pricing"
    assert host.tags["haute-image"] == NEW
    # The check: health and the signed-in release asked with the token, the forged
    # principal asked without one.
    asked = [(url.removeprefix(f"https://{ADDRESS}"), "Authorization" in h) for url, h in app.asked]
    assert asked == [("/health", True), ("/api/app/release", False), ("/api/app/release", True)]


def test_two_rollouts_in_one_second_get_two_suffixes() -> None:
    clock = FakeClock().clock()
    host = FakeHost()
    _roll(host, FakeApp(), clock=clock)
    other = FakeHost()
    _roll(other, FakeApp(), clock=clock)

    first = host.changes()[-1][2]
    second = other.changes()[-1][2]
    assert first != second
    assert first[:13] == second[:13] == "h261101120000"


def test_a_re_run_on_the_image_rolls_nothing_and_records_it() -> None:
    host = FakeHost(image=NEW)

    assert _roll(host, FakeApp()) == "rel-1"

    assert host.changes() == []
    assert host.calls == [("record", NEW, "rel-1", COMMIT)]


@pytest.mark.parametrize(
    ("running", "app", "message"),
    [
        ("Degraded", FakeApp(), "is degraded"),
        ("Running", FakeApp(signed=HttpAnswer(401)), "answered HTTP 401"),
    ],
    ids=["degraded", "check-fails"],
)
def test_a_re_run_that_fails_changes_nothing(running: str, app: FakeApp, message: str) -> None:
    host = FakeHost(image=NEW)
    host.revisions["motor--old"] = replace(host.revisions["motor--old"], running=running)

    with pytest.raises(RolloutError, match=message) as refused:
        _roll(host, app)

    assert "Nothing was changed" in str(refused.value)
    assert host.calls == []


def test_an_inactive_revision_on_the_image_is_rolled_again_to_a_new_revision() -> None:
    host = FakeHost(image=NEW)
    host.revisions["motor--old"] = replace(host.revisions["motor--old"], active=False)

    _roll(host, FakeApp())

    assert [call[0] for call in host.changes()] == ["set_image"]
    assert host.latest != "motor--old"


# ── A failed release is replaced by the previous one ──────────────────


def _rolled_back(host: FakeHost) -> None:
    sets = [call for call in host.changes() if call[0] == "set_image"]
    assert [call[1] for call in sets] == [NEW, OLD]
    # The rollback's revision has a suffix of its own.
    assert sets[0][2] != sets[1][2]
    assert host.image == OLD
    assert not any(call[0] == "record" for call in host.calls)


@pytest.mark.parametrize(
    ("settles", "app", "message"),
    [
        (("Failed", "Failed"), FakeApp(), "failed to provision"),
        (("Provisioned", "Degraded"), FakeApp(), "is degraded"),
        (("Provisioning", "Processing"), FakeApp(), "was not provisioned within 600 seconds"),
        (None, FakeApp(health=[HttpAnswer(503)] * 40), "did not answer its health check"),
        (None, FakeApp(health=[HttpAnswer(0, b"timed out")] * 40), "no answer: timed out"),
        (None, FakeApp(forged=_ok({"id": "rel-1"})), r"forged a signed-in principal \(HTTP 200\)"),
        (
            None,
            FakeApp(forged=HttpAnswer(302, location="https://elsewhere.example/")),
            r"forged a signed-in principal \(HTTP 302\)",
        ),
        (None, FakeApp(signed=HttpAnswer(403)), "answered HTTP 403"),
        (None, FakeApp(signed=_ok({"id": "rel-0", "quotes": {"open": True}})), "release 'rel-0'"),
        (
            None,
            FakeApp(
                quotes={"open": False, "error_code": "quote_store_unreachable", "message": "down"}
            ),
            "quotes are closed: down",
        ),
    ],
    ids=[
        "provisioning-failed",
        "degraded",
        "provisioning-past-deadline",
        "health-never-answers",
        "stalled-request",
        "forged-answered",
        "forged-redirected-elsewhere",
        "signed-in-refused",
        "another-release",
        "closed-for-another-reason",
    ],
)
def test_a_failed_release_is_replaced_by_the_previous_one(
    settles: tuple[str, str] | None, app: FakeApp, message: str
) -> None:
    host = FakeHost()
    if settles is not None:
        host.settles[NEW] = settles

    with pytest.raises(RolloutError, match=message) as failed:
        _roll(host, app)

    assert f"The previous release ({OLD}) runs again" in str(failed.value)
    _rolled_back(host)


def test_a_redirect_to_the_hosts_sign_in_is_a_refusal() -> None:
    app = FakeApp(forged=HttpAnswer(302, location=f"https://{ADDRESS}/.auth/login/aad"))

    assert _roll(FakeHost(), app) == "rel-1"


def test_an_update_refused_after_the_old_revision_stopped_puts_it_back() -> None:
    host = FakeHost()
    host.refuses.add(NEW)

    with pytest.raises(RolloutError, match="Azure refused the update") as failed:
        _roll(host, FakeApp())

    # No new revision was made: the one the rollout started from is still the latest.
    assert host.changes()[0] == ("deactivate", "motor--old")
    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]
    assert host.image == OLD
    assert "runs again" in str(failed.value)


def test_a_rollback_that_fails_names_the_apps_state() -> None:
    host = FakeHost()
    host.settles[NEW] = ("Failed", "Failed")
    host.refuses.add(OLD)

    with pytest.raises(RolloutError, match="Putting the previous release") as failed:
        _roll(host, FakeApp())

    assert "latest revision is motor--h261101120000000001" in str(failed.value)


def test_quotes_closed_by_a_store_mismatch_stay_recorded_and_fail_naming_each_difference() -> None:
    host = FakeHost()
    differences = ["policy: missing column age", "pricing: premium is text, not float"]
    app = FakeApp(
        quotes={
            "open": False,
            "error_code": "quote_store_mismatch",
            "message": "the tables do not fit the form",
            "differences": differences,
        }
    )

    with pytest.raises(QuotesClosedError) as closed:
        _roll(host, app)

    assert closed.value.differences == tuple(differences)
    assert all(each in str(closed.value) for each in differences)
    # The release stays, recorded; nothing is rolled back.
    assert [call[0] for call in host.changes()] == ["deactivate", "set_image"]
    assert host.calls[-1] == ("record", NEW, "rel-1", COMMIT)
    assert host.image == NEW


def test_another_change_made_latest_before_the_guard_looks_is_never_undone() -> None:
    host = FakeHost()
    host.settles[NEW] = ("Failed", "Failed")

    def another_rollout(changed: FakeHost) -> None:
        # Another writer's revision becomes the latest right after this one's.
        changed.after = None
        theirs = RevisionState("motor--theirs", NEW, True, "Provisioned", "Running")
        changed.revisions[theirs.name] = theirs
        changed.latest = theirs.name

    host.after = another_rollout

    with pytest.raises(RolloutError, match="Another change made motor--theirs") as failed:
        _roll(host, FakeApp())

    assert "nothing was undone" in str(failed.value)
    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW]
    assert not any(call[0] == "record" for call in host.calls)


def test_the_release_is_not_recorded_over_another_change() -> None:
    host = FakeHost()
    app = FakeApp()

    def another_rollout(changed: FakeHost) -> None:
        changed.after = None
        changed.latest = "motor--theirs"
        changed.revisions["motor--theirs"] = RevisionState(
            "motor--theirs", NEW, True, "Provisioned", "Running"
        )

    original = host.set_image

    def set_then_replaced(image: str, suffix: str, timeout: float) -> str:
        made = original(image, suffix, timeout)
        # The revision provisions and checks fine; another rollout lands before the record.
        host.after = another_rollout
        return made

    host.set_image = set_then_replaced  # type: ignore[method-assign]
    host.revision = _then(host.revision, lambda: host.after and host.after(host))  # type: ignore[method-assign]

    with pytest.raises(RolloutError, match="before the release was recorded"):
        _roll(host, app)

    assert not any(call[0] == "record" for call in host.calls)


def _then(read: Any, side: Any) -> Any:
    def wrapped(name: str) -> RevisionState:
        answer = read(name)
        side()
        return answer

    return wrapped


def test_azure_never_takes_another_writers_revision_for_its_own(azure: Any) -> None:
    host, sdk, _ = azure
    sdk.answers = "motor--theirs"

    with pytest.raises(RolloutError, match="answered the revision motor--theirs, not this"):
        host.set_image(NEW, "h261101120000abcdef", 412.0)


def test_azure_spends_the_deadline_from_its_first_request(azure: Any) -> None:
    host, sdk, _ = azure
    sdk.start_takes = 100.0

    host.set_image(NEW, "h261101120000abcdef", 412.0)

    # The update's start took 100 of the 412 seconds: the wait has what is left.
    assert sdk.pollers[0].waited == 312.0


def test_azure_bounds_every_request_to_what_is_left_and_refuses_once_spent(azure: Any) -> None:
    host, sdk, _ = azure
    host.state()
    # Outside a deadline each request has a minute, its retries and their waits included.
    assert {timeout for _, timeout in sdk.timeouts} == {60.0}

    host.limit(5.0)
    sdk.clock[0] += 2.0
    sdk.timeouts.clear()
    host.deactivate("motor--old")
    assert sdk.timeouts == [("deactivate_revision", 3.0)]

    sdk.clock[0] += 4.0
    with pytest.raises(RolloutError, match="deadline passed before Azure was asked to stop"):
        host.deactivate("motor--old")
    # Nothing was asked once the deadline passed.
    assert sdk.deactivated == ["motor--old"]


def test_the_sdk_never_waits_longer_than_ten_seconds_before_a_retry() -> None:
    """azure-core checks a request's budget only between attempts, so the retry policy caps
    the wait a ``Retry-After`` asks for."""
    from types import SimpleNamespace

    policy = rollouts._retry_policy()
    slept: list[float] = []
    transport = SimpleNamespace(sleep=slept.append)
    throttled = SimpleNamespace(http_response=SimpleNamespace(headers={"Retry-After": "300"}))

    assert policy._sleep_for_retry(throttled, transport) is True
    assert slept == [10]
    assert policy.total_retries == 2


def test_the_client_is_built_with_the_bounded_retry_policy(azure: Any) -> None:
    client = rollouts._client(_Credential(), "subscription")

    assert type(client._config.retry_policy).__name__ == "_BoundedRetry"
    assert client._config.retry_policy.total_retries == 2


def test_a_rollout_bounds_azure_to_its_deadline_and_its_recovery_to_its_own() -> None:
    host = FakeHost()
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError):
        _roll(host, FakeApp())

    assert host.limits == [600, 600, None]


def test_a_failure_puts_back_the_serving_revision_not_another_active_one() -> None:
    """In single revision mode a revision on its way out can stay active beside the one
    serving, taking no traffic: the image put back is Azure's latest ready revision's."""
    host = FakeHost()
    leaving = RevisionState(
        "motor--leaving",
        "acr.example.io/pricing/motor@sha256:" + "c" * 64,
        True,
        "Deprovisioning",
        "Running",
    )
    host.revisions = {leaving.name: leaving, **host.revisions}
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError, match="failed to provision"):
        _roll(host, FakeApp())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


# ── The review's cases: what the app served, a lost answer, the deadline ──


def test_a_failure_puts_back_the_image_the_active_revision_served() -> None:
    """A failed update of Azure's own can leave the template naming an image no revision
    serves: the rollback restores the one that was serving."""
    host = FakeHost()
    host.image = "acr.example.io/pricing/motor@sha256:" + "b" * 64
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError, match="failed to provision"):
        _roll(host, FakeApp())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


def test_a_stop_azure_applied_but_lost_the_answer_to_is_put_back() -> None:
    host = FakeHost()
    host.deactivation_lost.add("motor--old")

    with pytest.raises(RolloutError, match="was lost") as failed:
        _roll(host, FakeApp())

    # Nothing new was made: the previous image runs again on a revision of its own.
    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [OLD]
    assert host.image == OLD and host.revisions[host.latest].active
    assert "runs again" in str(failed.value)


def test_a_revision_provisioned_past_the_deadline_fails_and_is_rolled_back() -> None:
    ticks = FakeClock()
    host = FakeHost()

    def slow(changed: FakeHost) -> None:
        # Azure's update takes longer than the whole deadline.
        changed.after = None
        ticks.t += rollouts.ROLLOUT_TIMEOUT_SECONDS + 1

    host.after = slow

    with pytest.raises(RolloutError, match="not provisioned within 600 seconds"):
        _roll(host, FakeApp(), clock=ticks.clock())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


def test_with_no_revision_serving_a_failed_one_is_left_stopped() -> None:
    host = FakeHost()
    host.revisions["motor--old"] = replace(host.revisions["motor--old"], active=False)
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError, match="No revision served before it") as failed:
        _roll(host, FakeApp())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW]
    assert not any(revision.active for revision in host.revisions.values())
    assert "failed to provision" in str(failed.value)


@pytest.mark.parametrize(
    ("location", "refused"),
    [
        (f"https://{ADDRESS}/.auth/login/aad?post_login_redirect_uri=/", True),
        ("/.auth/login/aad", True),
        ("https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize", True),
        ("https://unrelated.example/.auth/login", False),
        ("https://unrelated.example/?next=login.microsoftonline.com", False),
        (f"https://{ADDRESS}/.auth/loginx", False),
        (f"http://{ADDRESS}/.auth/login", False),
        ("http://login.microsoftonline.com/x", False),
    ],
    ids=[
        "own-login",
        "relative-login",
        "microsoft-login",
        "another-host-login-path",
        "another-host-naming-microsoft",
        "another-path",
        "own-login-over-http",
        "microsoft-over-http",
    ],
)
def test_only_a_redirect_to_the_hosts_sign_in_is_a_refusal(location: str, refused: bool) -> None:
    app = FakeApp(forged=HttpAnswer(302, location=location))
    host = FakeHost()

    if refused:
        assert _roll(host, app) == "rel-1"
    else:
        with pytest.raises(RolloutError, match=r"forged a signed-in principal \(HTTP 302\)"):
            _roll(host, app)


def test_the_recovery_keeps_one_deadline_through_its_ownership_read() -> None:
    ticks = FakeClock()
    host = FakeHost()
    host.settles[NEW] = ("Failed", "Failed")
    reads = []

    def slow_read(read: FakeHost) -> None:
        reads.append(ticks.t)
        if len(reads) == 2:
            # The recovery's ownership read takes 90 seconds.
            ticks.t += 90

    host.on_state = slow_read

    with pytest.raises(RolloutError, match="runs again"):
        _roll(host, FakeApp(), clock=ticks.clock())

    # The rollback's update gets what is left of the one recovery deadline.
    assert host.timeouts[-1] == rollouts.RECOVERY_TIMEOUT_SECONDS - 90


def test_a_recovery_that_fails_names_both_failures_and_why_the_state_is_unknown() -> None:
    host = FakeHost()
    host.settles[NEW] = ("Failed", "Failed")
    host.refuses.add(OLD)

    def unreadable_after_the_rollback(read: FakeHost) -> None:
        if any(call[:2] == ("set_image", OLD) for call in read.calls):
            raise RolloutError("Azure could not read the app: throttled")

    host.on_state = unreadable_after_the_rollback

    with pytest.raises(RolloutError) as failed:
        _roll(host, FakeApp())

    message = str(failed.value)
    assert "failed to provision" in message
    assert "Putting the previous release" in message and "Azure refused the update" in message
    assert "could not be read afterwards: Azure could not read the app: throttled" in message


def test_azure_never_waits_on_an_update_past_the_bound_set(azure: Any) -> None:
    host, sdk, _ = azure
    host.limit(100.0)

    host.set_image(NEW, "h261101120000abcdef", 412.0)

    assert sdk.pollers[0].waited == 100.0


# ── fetch, the real transport, against a server on loopback ───────────


@pytest.fixture
def server() -> Any:
    """A server on loopback answering each path as the transport's failures look."""
    import http.server
    import threading
    import time as clock

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

        def do_GET(self) -> None:
            if self.path == "/stall":
                clock.sleep(3)
            if self.path in ("/truncated", "/error-truncated"):
                self.send_response(200 if self.path == "/truncated" else 500)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"0123456789")
                self.wfile.flush()
                self.connection.close()
                return
            if self.path == "/sign-in":
                self.send_response(302)
                self.send_header("Location", "/.auth/login/aad")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_fetch_answers_a_stalled_request_within_its_timeout(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rollouts, "REQUEST_TIMEOUT_SECONDS", 0.5)

    answer = rollouts.fetch(f"{server}/stall", {})

    assert answer.status == 0


def test_fetch_answers_a_body_that_ends_early_as_no_answer(server: str) -> None:
    assert rollouts.fetch(f"{server}/truncated", {}).status == 0


def test_fetch_keeps_an_errors_status_when_its_body_ends_early(server: str) -> None:
    answer = rollouts.fetch(f"{server}/error-truncated", {})

    assert (answer.status, answer.body) == (500, b"")


def test_fetch_answers_a_redirect_without_following_it(server: str) -> None:
    answer = rollouts.fetch(f"{server}/sign-in", {})

    assert (answer.status, answer.location) == (302, "/.auth/login/aad")
    assert rollouts.fetch(f"{server}/health", {}).status == 200


# ── --from: the release another environment runs ─────────────────────


def _staged(image: str = NEW, commit: str = COMMIT) -> FakeHost:
    host = FakeHost(image=image)
    host.record(Recorded(image, "rel-1", commit))
    host.calls.clear()
    return host


def test_from_takes_the_image_the_source_runs_for_the_commit() -> None:
    assert promoted(_staged(), "staging", COMMIT) == NEW


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (FakeHost(image=NEW), "records no release"),
        (_staged(image="acr.example.io/pricing/motor:latest"), "names no image by digest"),
        (_staged(commit="d" * 40), "runs commit dddddddddddd, not c0ffee000000"),
        (_staged(image=OLD), "does not run the release it records"),
    ],
    ids=["no-record", "tag-named", "another-commit", "not-running-it"],
)
def test_from_refuses_a_record_it_cannot_promote(source: FakeHost, message: str) -> None:
    if source.tags.get("haute-image") == OLD:
        # Recorded OLD, but the app runs NEW since.
        source.image = NEW
        source.revisions["motor--old"] = replace(source.revisions["motor--old"], image=NEW)
    with pytest.raises(RolloutError, match=message):
        promoted(source, "staging", COMMIT)


def test_a_partial_record_is_no_record() -> None:
    host = FakeHost(image=NEW)
    host.tags.update({"haute-image": NEW, "haute-release": "rel-1"})

    assert host.state().recorded is None


# ── The project's side: environment, commit, credentials ─────────────


def _config(**changes: Any) -> WorkbenchConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "project_root": Path(),
        "app_host": "azure-container-apps",
        "app_environments": (AppEnvironment("staging", "rg", "motor-staging"),),
    }
    base.update(changes)
    return WorkbenchConfig(**base)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_config(enabled=False), "has no workbench"),
        (_config(app_host=None), "rolls out an app on Azure Container Apps"),
        (_config(app_environments=()), "names no [workbench.app.staging]"),
        (
            _config(app_environments=(AppEnvironment("staging", "", ""),)),
            "leaves resource_group and container_app empty",
        ),
    ],
    ids=["no-workbench", "no-host", "not-named", "left-empty"],
)
def test_an_environment_the_project_does_not_name_is_refused(
    config: WorkbenchConfig, message: str
) -> None:
    with pytest.raises(RolloutError, match=message.replace("[", r"\[").replace("]", r"\]")):
        app_environment(config, "staging")


def test_each_unset_credential_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in rollouts.CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")

    with pytest.raises(RolloutError) as refused:
        credentials()

    assert "AZURE_SUBSCRIPTION_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET" in str(refused.value)


def test_the_commit_is_the_one_checked_out_and_none_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RolloutError, match="git names none"):
        head_commit(tmp_path)

    init_repo(tmp_path)
    assert head_commit(tmp_path) == git_run(tmp_path, "rev-parse", "HEAD")


def test_the_hosted_check_answers_the_release_it_finds() -> None:
    app = FakeApp(release="rel-9")

    assert hosted_check(app, ADDRESS, "token-for-app-client-id", FakeClock().clock()) == "rel-9"


# ── AzureContainerApps, held to the SDK ───────────────────────────────


class _Poller:
    def __init__(self, result: Any, done: bool = True) -> None:
        self._result = result
        self._done = done
        self.waited: float | None = None

    def wait(self, timeout: float | None = None) -> None:
        self.waited = timeout

    def done(self) -> bool:
        return self._done

    def result(self) -> Any:
        return self._result


class _Sdk:
    """A ``ContainerAppsAPIClient`` with the operations the rollout calls."""

    def __init__(self, models: Any, *, done: bool = True) -> None:
        self.models = models
        self.done = done
        #: The revision Azure answers an update with, else the one its suffix names.
        self.answers: str | None = None
        #: Seconds the update's start spends, on the adapter's clock.
        self.start_takes = 0.0
        self.clock = [0.0]
        #: Each request's budget, the SDK's timeout, by operation.
        self.timeouts: list[tuple[str, float]] = []
        self.updates: list[Any] = []
        self.pollers: list[_Poller] = []
        self.deactivated: list[str] = []
        self.app = models.ContainerApp(
            location="uksouth",
            tags={"owner": "pricing"},
            properties=models.ContainerAppProperties(
                configuration=models.Configuration(
                    active_revisions_mode="Single",
                    ingress=models.Ingress(external=True, fqdn=ADDRESS),
                ),
                template=models.Template(
                    containers=[models.Container(name="app", image=OLD)],
                    revision_suffix="hold",
                ),
                latest_revision_name="motor--old",
            ),
        )
        sdk = self

        class Apps:
            def get(self, group: str, name: str, *, timeout: float) -> Any:
                sdk.timeouts.append(("get", timeout))
                return sdk.app

            def begin_update(
                self, group: str, name: str, envelope: Any, *, timeout: float
            ) -> _Poller:
                sdk.timeouts.append(("begin_update", timeout))
                sdk.updates.append(envelope)
                sdk.clock[0] += sdk.start_takes
                template = envelope.properties.template if envelope.get("properties") else None
                answered = sdk.answers or f"motor--{template.revision_suffix if template else 'x'}"
                updated = models.ContainerApp(
                    location="uksouth",
                    properties=models.ContainerAppProperties(latest_revision_name=answered),
                )
                poller = _Poller(updated, sdk.done)
                sdk.pollers.append(poller)
                return poller

        class Revisions:
            def list_revisions(self, group: str, name: str, *, timeout: float) -> list[Any]:
                sdk.timeouts.append(("list_revisions", timeout))
                return [
                    models.Revision(
                        name="motor--old", properties=models.RevisionProperties(active=True)
                    ),
                    models.Revision(
                        name="motor--gone", properties=models.RevisionProperties(active=False)
                    ),
                ]

            def get_revision(self, group: str, name: str, revision: str, *, timeout: float) -> Any:
                sdk.timeouts.append(("get_revision", timeout))
                return models.Revision(
                    name=revision,
                    properties=models.RevisionProperties(
                        active=True,
                        provisioning_state="Provisioned",
                        running_state="Running",
                        template=models.Template(
                            containers=[models.Container(name="app", image=NEW)]
                        ),
                    ),
                )

            def deactivate_revision(
                self, group: str, name: str, revision: str, *, timeout: float
            ) -> None:
                sdk.timeouts.append(("deactivate_revision", timeout))
                sdk.deactivated.append(revision)

        class Auth:
            def get(self, group: str, name: str, config: str, *, timeout: float) -> Any:
                assert config == "current"
                sdk.timeouts.append(("auth", timeout))
                return models.AuthConfig(
                    properties=models.AuthConfigProperties(
                        identity_providers=models.IdentityProviders(
                            azure_active_directory=models.AzureActiveDirectory(
                                enabled=True,
                                registration=models.AzureActiveDirectoryRegistration(
                                    client_id="app-client-id"
                                ),
                            )
                        )
                    )
                )

        self.container_apps = Apps()
        self.container_apps_revisions = Revisions()
        self.container_apps_auth_configs = Auth()


class _Credential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, scope: str) -> Any:
        self.scopes.append(scope)

        @dataclass
        class Token:
            token: str

        return Token("a-token")


@pytest.fixture
def azure() -> tuple[rollouts.AzureContainerApps, _Sdk, _Credential]:
    from azure.mgmt.appcontainers import models

    sdk = _Sdk(models)
    credential = _Credential()
    host = rollouts.AzureContainerApps(
        AppEnvironment("staging", "pricing-rg", "motor-staging"),
        {},
        client=sdk,  # type: ignore[arg-type]
        credential=credential,  # type: ignore[arg-type]
        now=lambda: sdk.clock[0],
    )
    return host, sdk, credential


def test_azure_reads_the_state_the_rollout_needs(azure: Any) -> None:
    host, _, _ = azure

    state = host.state()

    assert state == AppState(
        single_revision=True,
        images=(OLD,),
        address=ADDRESS,
        latest_revision="motor--old",
        active_revisions=("motor--old",),
        client_id="app-client-id",
        tag_names=frozenset({"owner"}),
        recorded=None,
    )
    assert host.revision("motor--new") == RevisionState(
        "motor--new", NEW, True, "Provisioned", "Running"
    )


def test_azure_sets_the_image_on_a_revision_of_its_own_within_the_deadline(azure: Any) -> None:
    host, sdk, _ = azure

    made = host.set_image(NEW, "h261101120000abcdef", 412.0)

    assert made == "motor--h261101120000abcdef"
    template = sdk.updates[0].properties.template
    assert template.containers[0].image == NEW
    assert template.revision_suffix == "h261101120000abcdef"
    assert sdk.pollers[0].waited == 412.0
    # What Azure receives: the template alone, the new image and suffix in it.
    sent = sdk.updates[0].as_dict()
    assert set(sent["properties"]) == {"template"}
    assert sent["properties"]["template"]["containers"][0]["image"] == NEW
    assert sent["properties"]["template"]["revisionSuffix"] == "h261101120000abcdef"


def test_azure_fails_an_update_past_its_deadline(azure: Any) -> None:
    host, sdk, _ = azure
    sdk.done = False

    with pytest.raises(RolloutError, match="did not finish within 30 seconds"):
        host.set_image(NEW, "h261101120000abcdef", 30.0)


def test_azure_records_the_release_keeping_the_apps_other_tags(azure: Any) -> None:
    host, sdk, _ = azure

    host.record(Recorded(NEW, "rel-1", COMMIT))

    assert dict(sdk.updates[0].tags) == {
        "owner": "pricing",
        "haute-image": NEW,
        "haute-release": "rel-1",
        "haute-commit": COMMIT,
    }
    # Tags alone: no template, so no revision is made.
    assert set(sdk.updates[0].as_dict()) == {"location", "tags"}


def test_azure_asks_a_token_for_the_app_registrations_client_id(azure: Any) -> None:
    host, _, credential = azure

    assert host.token("app-client-id") == "a-token"
    assert credential.scopes == ["app-client-id/.default"]


def test_azure_deactivates_a_revision(azure: Any) -> None:
    host, sdk, _ = azure

    host.deactivate("motor--old")

    assert sdk.deactivated == ["motor--old"]


# ── haute app rollout, end to end through the CLI ──────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Path:
    """A workbench project on Azure Container Apps, its hosted environments named, committed,
    with the CI service principal's variables set."""
    from haute.cli import cli

    monkeypatch.chdir(tmp_path)
    initialised = runner.invoke(
        cli, ["init", "--workbench", "--store", "sqlserver", "--sign-in", "password"]
    )
    assert initialised.exit_code == 0, initialised.output
    toml = tmp_path / "haute.toml"
    text = toml.read_text(encoding="utf-8")
    for name in ("staging", "prod"):
        text = text.replace(
            f'[workbench.app.{name}]\nresource_group = ""\ncontainer_app = ""\n',
            f'[workbench.app.{name}]\nresource_group = "pricing-rg"\n'
            f'container_app = "motor-{name}"\n',
        )
    toml.write_text(text, encoding="utf-8")
    init_repo(tmp_path)
    for name in rollouts.CREDENTIAL_VARIABLES:
        monkeypatch.setenv(name, f"value-of-{name.lower()}")
    return tmp_path


def _hosts(monkeypatch: pytest.MonkeyPatch, **hosts: FakeHost) -> None:
    def make(environment: AppEnvironment, values: Mapping[str, str]) -> FakeHost:
        assert values["AZURE_CLIENT_SECRET"] == "value-of-azure_client_secret"
        return hosts[environment.name]

    monkeypatch.setattr(rollouts, "AzureContainerApps", make)
    monkeypatch.setattr(rollouts, "fetch", FakeApp())


def test_the_cli_rolls_staging_out_and_prints_the_release_last(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    staging = FakeHost()
    _hosts(monkeypatch, staging=staging)

    result = runner.invoke(cli, ["app", "rollout", "staging", "--image", NEW])

    assert result.exit_code == 0, result.output
    assert "Stopped the revision motor--old" in result.output
    assert result.output.rstrip().splitlines()[-1] == "rel-1"
    assert staging.tags["haute-commit"] == git_run(project, "rev-parse", "HEAD")


def test_the_cli_promotes_the_release_staging_runs(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    commit = git_run(project, "rev-parse", "HEAD")
    staging = FakeHost(image=NEW)
    staging.record(Recorded(NEW, "rel-1", commit))
    prod = FakeHost()
    _hosts(monkeypatch, staging=staging, prod=prod)

    result = runner.invoke(cli, ["app", "rollout", "prod", "--from", "staging"])

    assert result.exit_code == 0, result.output
    assert prod.image == NEW
    assert prod.tags["haute-image"] == NEW


def test_the_cli_fails_on_closed_quotes_naming_each_difference(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    _hosts(monkeypatch, staging=FakeHost())
    monkeypatch.setattr(
        rollouts,
        "fetch",
        FakeApp(
            quotes={
                "open": False,
                "error_code": "quote_store_mismatch",
                "message": "the tables do not fit",
                "differences": ["policy: missing column age"],
            }
        ),
    )

    result = runner.invoke(cli, ["app", "rollout", "staging", "--image", NEW])

    assert result.exit_code == 1
    assert "policy: missing column age" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "args", [[], ["--image", NEW, "--from", "staging"]], ids=["neither", "both"]
)
def test_the_cli_takes_image_or_from_exactly_one(
    project: Path, runner: Any, args: list[str]
) -> None:
    from haute.cli import cli

    result = runner.invoke(cli, ["app", "rollout", "prod", *args])

    assert result.exit_code == 2
    assert "Pass --image or --from, one of them" in result.output
