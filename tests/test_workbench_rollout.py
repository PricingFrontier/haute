"""``haute app rollout``: the hosted app rolled out, checked and recorded (specs/workbench,
Control flow 35), against a fake Container App and a fake app answering the check."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests

from haute import _workbench_rollout as rollouts
from haute._project_context import context_thread
from haute._workbench_config import AppEnvironment, WorkbenchConfig
from haute._workbench_rollout import (
    AppState,
    AzureSignIn,
    Clock,
    HttpAnswer,
    QuotesClosedError,
    Recorded,
    RevisionState,
    RolloutError,
    ServicePrincipal,
    app_environment,
    azure_sign_in,
    head_commit,
    hosted_check,
    promoted,
    rollout,
)
from tests._git_helpers import git_run, init_repo
from tests._project import use_project

OLD = "acr.example.io/pricing/motor@sha256:" + "0" * 64
NEW = "acr.example.io/pricing/motor@sha256:" + "a" * 64
COMMIT = "c0ffee" + "0" * 34
ADDRESS = "motor.example.azurecontainerapps.io"


class FakeHost:
    """A Container App in single revision mode running *image*, as Azure runs one: a new
    revision starts beside the one serving, which goes on taking the requests until the new
    one is ready, ``ready_after`` reads of the app's state later, and Azure then stops the
    old one itself. What the rollout reads and changes, each change listed in ``calls``."""

    def __init__(self, image: str = OLD, **state: Any) -> None:
        self.calls: list[tuple[str, ...]] = []
        old = RevisionState(
            "motor--old", image, True, "Provisioned", "Running", f"motor--old.{ADDRESS}"
        )
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
        #: What Azure reports first of a revision, one (provisioning, running) a read, before
        #: what it settled as: a container restarting is reported failed for a while. Keyed
        #: by the image of a revision an update makes, or by the name of one that exists.
        self.reports: dict[str, list[tuple[str, str]]] = {}
        self._made: set[str] = set()
        #: How many reads of the app's state pass before a new revision is its latest ready
        #: one: Azure calls a revision provisioned long before it serves.
        self.ready_after = 1
        self._becoming_ready: dict[str, int] = {}
        #: The revision that served before Azure's last switch.
        self.before: str | None = None
        #: Called after each change: a test's other writer.
        self.after: Any = None
        #: Called on each read of the app's state; and each set_image's timeout.
        self.on_state: Any = None
        self.timeouts: list[float] = []

    def _switch(self) -> None:
        """Azure's own switch: the latest revision, settled and ``ready_after`` reads in the
        making, becomes the latest ready one, and Azure stops every other revision."""
        name = self.latest
        if name is None or name not in self._becoming_ready:
            return
        if self._becoming_ready[name] > 0:
            self._becoming_ready[name] -= 1
            return
        del self._becoming_ready[name]
        self.before, self.ready = self.ready, name
        for other, revision in self.revisions.items():
            if other != name:
                self.revisions[other] = replace(revision, active=False)

    def state(self) -> AppState:
        if self.on_state is not None:
            self.on_state(self)
        self._switch()
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
        revision = self.revisions[name]
        first = self.reports.get((revision.image if name in self._made else name) or "")
        if first:
            provisioning, running = first.pop(0)
            return replace(revision, provisioning=provisioning, running=running)
        return revision

    def limit(self, seconds: float | None) -> None:
        self.limits.append(seconds)

    def deactivate(self, name: str) -> None:
        self.calls.append(("deactivate", name))
        self.revisions[name] = replace(self.revisions[name], active=False)
        self._becoming_ready.pop(name, None)

    def set_image(self, image: str, suffix: str, timeout: float) -> str:
        self.calls.append(("set_image", image, suffix))
        self.timeouts.append(timeout)
        if image in self.refuses:
            raise RolloutError("Azure refused the update.")
        name = f"motor--{suffix}"
        provisioning, running = self.settles.get(image, ("Provisioned", "Running"))
        # The revision before it stays active, and serving, until this one is ready.
        own = f"{name}.{self.settings['address']}"
        self.revisions[name] = RevisionState(name, image, True, provisioning, running, own)
        self._made.add(name)
        self.latest, self.image = name, image
        if (provisioning, running) == ("Provisioned", "Running"):
            self._becoming_ready[name] = self.ready_after
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
    """The hosted app as the check sees it: what each request is answered with, by the
    revision Azure serves (the app's latest ready one), which ``/health`` names."""

    release: str = "rel-1"
    #: The release each image is, where an app is rolled from one release to another.
    releases: dict[str, str] = field(default_factory=dict)
    #: The Container App at each address. Its own address is answered by the revision Azure
    #: serves; a revision's own (``<revision>.<address>``) by that revision alone.
    hosts: dict[str, FakeHost] = field(default_factory=dict)
    #: How many more answers at the app's own address come from the revision that served
    #: before Azure's switch, its routes following the switch.
    lagging: int = 0
    #: The revision that answered each request.
    answered: list[str | None] = field(default_factory=list)
    health: list[HttpAnswer] = field(default_factory=list)
    forged: HttpAnswer = field(default_factory=lambda: HttpAnswer(401))
    signed: HttpAnswer | None = None
    #: What the forged and the signed-in request are answered first, in order, before the
    #: answers above: a host restarting a container answers 503, or not at all.
    forged_first: list[HttpAnswer] = field(default_factory=list)
    signed_first: list[HttpAnswer] = field(default_factory=list)
    quotes: dict[str, Any] = field(
        default_factory=lambda: {
            "open": True,
            "error_code": None,
            "message": None,
            "differences": [],
        }
    )
    asked: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def _answering(self, url: str) -> tuple[str | None, str]:
        """The revision that answers a request for *url*, and the release it runs."""
        asked = urlsplit(url).hostname or ""
        if asked in self.hosts:
            host = self.hosts[asked]
            revision = host.ready
            if self.lagging and host.before is not None:
                self.lagging -= 1
                revision = host.before
        else:
            [host] = [each for name, each in self.hosts.items() if asked.endswith(f".{name}")]
            revision = asked.removesuffix(f".{host.settings['address']}")
            assert revision in host.revisions, f"no revision at {asked}"
        self.answered.append(revision)
        image = host.revisions[revision].image if revision is not None else None
        return revision, self.releases.get(image or "", self.release)

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpAnswer:
        self.asked.append((url, dict(headers)))
        signed_in = headers.get("Authorization") == "Bearer token-for-app-client-id"
        revision, release = self._answering(url)
        if url.endswith("/health"):
            assert signed_in, "the health check is signed in"
            if self.health:
                return self.health.pop(0)
            return _ok({"status": "ok", "release": release, "revision": revision})
        if not signed_in:
            assert "X-MS-CLIENT-PRINCIPAL" in headers
            return self.forged_first.pop(0) if self.forged_first else self.forged
        if self.signed_first:
            return self.signed_first.pop(0)
        if self.signed is not None:
            return self.signed
        return _ok({"id": release, "quotes": self.quotes})


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
    if host.settings["address"] is not None:
        app.hosts[host.settings["address"]] = host
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
        ({"address": None}, "no requests from outside its Container Apps environment"),
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


# ── The switch from one release to the next is Azure's ───────────────


def test_the_new_release_starts_beside_the_old_one_and_is_recorded_once_it_serves() -> None:
    host = FakeHost()
    app = FakeApp()

    release = _roll(host, app)

    assert release == "rel-1"
    # Nothing is stopped first: the image is set on a revision of its own, and that is all.
    assert host.changes() == [("set_image", NEW, "h261101120000000001")]
    assert host.calls[-1] == ("record", NEW, "rel-1", COMMIT)
    # Azure stopped the old revision itself, once the new one served.
    assert [name for name, revision in host.revisions.items() if revision.active] == [host.latest]
    assert "The revision motor--h261101120000000001 takes the app's requests" in " ".join(
        host.said  # type: ignore[attr-defined]
    )
    # The app's other tags are kept beside the record.
    assert host.tags["owner"] == "pricing"
    assert host.tags["haute-image"] == NEW
    # The check: the revision's own address asked its health and its release signed in, and
    # the forged principal without a token; then the app's own address asked its health.
    own = f"https://{host.latest}.{ADDRESS}"
    assert [(url, "Authorization" in headers) for url, headers in app.asked] == [
        (f"{own}/health", True),
        (f"{own}/api/app/release", False),
        (f"{own}/api/app/release", True),
        (f"https://{ADDRESS}/health", True),
    ]


def test_nothing_is_checked_until_azure_serves_the_new_revision() -> None:
    """Azure calls a revision provisioned half a minute before it sends it a request: until
    then the old revision answers, so nothing is asked of the app."""
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 4
    app = FakeApp()

    _roll(host, app, clock=ticks.clock())

    # Every request of the check was answered by the revision the rollout made, and none
    # was asked before Azure served it.
    assert len(app.answered) == 4 and set(app.answered) == {host.latest}
    assert ticks.t == 4 * rollouts.POLL_SECONDS


def test_a_release_rolled_out_over_another_is_checked_by_its_own_revisions_answer() -> None:
    """Rolling from one release to the next, the old revision can still answer as Azure's
    routes follow its switch: its answers say nothing of the new release and are asked again,
    never taken for the new one and never failed on."""
    host = FakeHost()
    app = FakeApp(releases={OLD: "rel-1", NEW: "rel-2"}, lagging=2)

    assert _roll(host, app) == "rel-2"

    # The revision's own address was answered by it alone; the app's own address by the
    # old revision twice more, then by the new one.
    assert app.answered == [host.latest] * 3 + ["motor--old", "motor--old", host.latest]
    assert host.calls[-1] == ("record", NEW, "rel-2", COMMIT)
    assert [call[0] for call in host.changes()] == ["set_image"]


def test_two_revisions_of_one_release_are_told_apart_by_their_names() -> None:
    """A release's id says what it prices, not which revision runs it: rolled out again on
    another image, the old revision's answers name the same release and still do not pass
    for the new one's."""
    host = FakeHost()
    app = FakeApp(releases={OLD: "rel-1", NEW: "rel-1"}, lagging=3)

    assert _roll(host, app) == "rel-1"

    assert app.answered[3:] == ["motor--old"] * 3 + [host.latest]


def test_an_address_the_new_revision_never_answers_fails_the_rollout() -> None:
    host = FakeHost()
    app = FakeApp(lagging=10_000)

    with pytest.raises(RolloutError, match="The app's own address was not answered") as failed:
        _roll(host, app)

    assert "from the revision motor--old" in str(failed.value)
    assert f"The previous release ({OLD}) runs again" in str(failed.value)


def test_an_app_is_updated_release_after_release() -> None:
    """The process an app's users rely on: one release rolled out over another, again and
    again, each recorded as the one that runs, one revision serving at the end of each."""
    host = FakeHost()
    images = [f"acr.example.io/pricing/motor@sha256:{n:064x}" for n in range(1, 7)]
    app = FakeApp(
        releases={OLD: "rel-0", **{image: f"rel-{n}" for n, image in enumerate(images, 1)}}
    )
    clock = FakeClock().clock()

    for number, image in enumerate(images, 1):
        assert _roll(host, app, image, clock=clock) == f"rel-{number}"

        assert host.tags["haute-release"] == f"rel-{number}"
        assert host.tags["haute-image"] == image
        active = [name for name, revision in host.revisions.items() if revision.active]
        assert active == [host.latest] and host.ready == host.latest
        if number == 3:
            # A release that fails between two that work: the one before it runs again,
            # still the one recorded, and the next release rolls out over it as ever.
            broken = "acr.example.io/pricing/motor@sha256:" + "b" * 64
            host.settles[broken] = ("Provisioned", "Degraded")
            with pytest.raises(RolloutError, match="runs again"):
                _roll(host, app, broken, clock=clock)
            assert host.tags["haute-image"] == image and host.image == image
            active = [name for name, revision in host.revisions.items() if revision.active]
            assert active == [host.latest] and host.ready == host.latest
    # One update a release, and two for the failed one (it, then the release before it);
    # nothing is ever stopped by the rollout.
    assert [call[0] for call in host.changes()] == ["set_image"] * (len(images) + 2)


def test_a_revision_that_is_never_ready_fails_with_the_old_one_still_serving() -> None:
    host = FakeHost()
    host.ready_after = 10_000
    app = FakeApp()

    with pytest.raises(RolloutError, match="was not ready to take the app's requests") as failed:
        _roll(host, app)

    assert "Azure still sends them to motor--old" in str(failed.value)
    # No request was asked of the app, which the old revision served throughout.
    assert app.asked == []
    assert [call[:2] for call in host.changes()] == [("set_image", NEW), ("set_image", OLD)]


def test_the_recovery_is_reported_only_once_azure_serves_the_previous_image_again() -> None:
    """A recovery's revision is provisioned long before it serves, as a rollout's is: the
    previous release is not said to run again until Azure sends it the requests."""
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 3
    app = FakeApp(signed=HttpAnswer(403))

    with pytest.raises(RolloutError, match="answered HTTP 403") as failed:
        _roll(host, app, clock=ticks.clock())

    assert f"The previous release ({OLD}) runs again" in str(failed.value)
    # The rollout's revision served after three polls, and the recovery's after three more.
    assert host.ready == host.latest and host.revisions[host.latest].image == OLD
    assert ticks.t == 6 * rollouts.POLL_SECONDS
    assert "Put the previous release back" in host.said[-1]  # type: ignore[attr-defined]
    assert not any(call[0] == "deactivate" for call in host.calls)


def test_the_revision_and_the_apps_address_share_one_deadline() -> None:
    """Five minutes hold the whole check: what the revision's own address spends of them
    the app's own address does not get again."""
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 0
    # The new revision would answer the app's address on its eleventh poll, as the five
    # minutes end: that poll is never made.
    app = FakeApp(health=[HttpAnswer(503)] * 20, lagging=10)

    with pytest.raises(RolloutError, match="The app's own address was not answered") as failed:
        _roll(host, app, clock=ticks.clock())

    waited = [ticks.t]
    # Twenty-one polls on the revision's address, ten on the app's: the check gave up at
    # five minutes in all, and the rest of the time is the recovery's.
    health = [url for url, _ in app.asked if url.endswith("/health")]
    assert len(health) == 21 + 10
    assert "from the revision motor--old" in str(failed.value)
    assert waited[0] >= rollouts.HEALTH_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("refusals", "health_seconds", "message"),
    [
        (8, 25.0, "The forged principal was not sent"),
        (29, 0.0, "The signed-in release was not asked"),
        (28, 0.0, "The app's own address was not asked"),
    ],
    ids=["forged-principal", "signed-in-release", "the-apps-address"],
)
def test_no_request_of_the_check_starts_once_its_time_is_spent(
    refusals: int, health_seconds: float, message: str
) -> None:
    """Requests take time as waits do. A request started within the five minutes may finish,
    but nothing of the check starts after them, however it would have been answered: the
    check fails saying what it did not ask, never passes past its deadline."""
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 0
    app = FakeApp(health=[HttpAnswer(503)] * refusals)
    app.hosts[ADDRESS] = host
    started: list[float] = []

    def taking_time(url: str, headers: Mapping[str, str]) -> HttpAnswer:
        started.append(ticks.t)
        ticks.t += health_seconds if url.endswith("/health") else 10.0
        return app(url, headers)

    with pytest.raises(RolloutError, match=message) as failed:
        rollout(
            host,
            environment="staging",
            image=NEW,
            commit=COMMIT,
            say=lambda _: None,
            get=taking_time,
            clock=ticks.clock(),
        )

    assert "the hosted check's 300 seconds were spent before it" in str(failed.value)
    assert max(started) < rollouts.HEALTH_TIMEOUT_SECONDS
    assert f"The previous release ({OLD}) runs again" in str(failed.value)


def test_a_re_run_is_held_to_the_revision_it_finds() -> None:
    """A revision already on the image is checked as a rollout's is: by its own answers."""
    host = FakeHost(image=NEW)
    another = _ok({"status": "ok", "release": "rel-1", "revision": "motor--another"})
    app = FakeApp(health=[another] * 40)

    with pytest.raises(RolloutError, match="not answered by the revision motor--old") as failed:
        _roll(host, app)

    assert "from the revision motor--another" in str(failed.value)
    assert "Nothing was changed" in str(failed.value)
    assert host.calls == []


# ── A transient is waited out, never taken for a verdict ──────────────


def _forged(app: FakeApp) -> list[str]:
    return [url for url, headers in app.asked if "X-MS-CLIENT-PRINCIPAL" in headers]


def _signed_release(app: FakeApp) -> list[str]:
    return [
        url
        for url, headers in app.asked
        if url.endswith("/api/app/release") and "Authorization" in headers
    ]


def test_a_revision_reported_failed_while_a_container_restarts_is_waited_on() -> None:
    """Azure reports a healthy revision failed for the seconds it restarts one of its
    containers, as it does when a health probe of its own did not complete, and running again
    after: the report alone fails nothing."""
    ticks = FakeClock()
    host = FakeHost()
    host.reports[NEW] = [("Failed", "Failed")] * 2

    assert _roll(host, FakeApp(), clock=ticks.clock()) == "rel-1"

    assert [call[0] for call in host.changes()] == ["set_image"]
    assert host.calls[-1] == ("record", NEW, "rel-1", COMMIT)
    assert ticks.t == 2 * rollouts.POLL_SECONDS


def test_a_revision_reported_failed_is_never_checked_though_azure_calls_it_ready() -> None:
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 0
    host.reports[NEW] = [("Failed", "Failed")] * 3
    app = FakeApp(hosts={ADDRESS: host})
    asked_at: list[float] = []

    def get(url: str, headers: Mapping[str, str]) -> HttpAnswer:
        asked_at.append(ticks.t)
        return app(url, headers)

    release = rollout(
        host,
        environment="staging",
        image=NEW,
        commit=COMMIT,
        say=lambda _: None,
        get=get,
        clock=ticks.clock(),
    )

    assert release == "rel-1"
    # Azure named it the latest ready revision at once: nothing was asked of it until it was
    # reported running again, three polls later.
    assert min(asked_at) == 3 * rollouts.POLL_SECONDS


def test_a_revision_still_reported_failed_at_the_deadline_fails_saying_what_azure_reports() -> None:
    host = FakeHost()
    host.ready_after = 0
    host.reports[NEW] = [("Failed", "Failed")] * 10_000
    app = FakeApp()

    with pytest.raises(RolloutError, match="takes the app's requests, but Azure still") as failed:
        _roll(host, app)

    message = str(failed.value)
    assert "unhealthy after 600 seconds (provisioning Failed, running Failed)" in message
    # It was never checked, and the release before it runs again.
    assert app.asked == []
    assert f"The previous release ({OLD}) runs again" in message


def test_the_recovery_waits_out_a_restart_of_its_own_revision() -> None:
    host = FakeHost()
    # The revision the recovery makes, on the previous image, is reported failed for two
    # reads: the recovery is not said to have failed too.
    host.reports[OLD] = [("Failed", "Failed")] * 2

    with pytest.raises(RolloutError, match="answered HTTP 403") as failed:
        _roll(host, FakeApp(signed=HttpAnswer(403)))

    assert f"The previous release ({OLD}) runs again" in str(failed.value)
    assert "failed too" not in str(failed.value)
    assert host.ready == host.latest and host.revisions[host.latest].image == OLD


def test_a_re_run_waits_out_a_restart_of_the_revision_it_finds() -> None:
    host = FakeHost(image=NEW)
    # Read as provisioned when the re-run is decided, then mid-restart for two reads.
    host.reports["motor--old"] = [("Provisioned", "Running")] + [("Failed", "Failed")] * 2

    assert _roll(host, FakeApp()) == "rel-1"

    assert host.calls == [("record", NEW, "rel-1", COMMIT)]


def test_a_dropped_forged_request_is_asked_again_and_the_rollout_completes() -> None:
    """One request a host restarting a container did not answer, or answered 503, says
    nothing of the app, and does not put a good release back."""
    host = FakeHost()
    app = FakeApp(forged_first=[HttpAnswer(0, b"connection reset"), HttpAnswer(503)])

    assert _roll(host, app) == "rel-1"

    assert len(_forged(app)) == 3
    assert host.calls[-1] == ("record", NEW, "rel-1", COMMIT)


def test_a_forged_request_never_answered_fails_as_an_app_that_gave_no_answer() -> None:
    host = FakeHost()
    app = FakeApp(forged=HttpAnswer(0, b"connection reset"))

    with pytest.raises(RolloutError, match="gave no answer to the request that forges") as failed:
        _roll(host, app)

    message = str(failed.value)
    assert "within the hosted check's 300 seconds" in message
    assert "last answer: no answer: connection reset" in message
    # Never said as an app that took the forged principal.
    assert "answered a request that forged" not in message
    assert f"The previous release ({OLD}) runs again" in message


def test_a_forged_request_let_through_fails_at_once_whatever_came_before() -> None:
    host = FakeHost()
    app = FakeApp(forged_first=[HttpAnswer(503)], forged=_ok({"id": "rel-1"}))

    with pytest.raises(RolloutError, match=r"forged a signed-in principal \(HTTP 200\)"):
        _roll(host, app)

    assert len(_forged(app)) == 2


def test_a_forged_request_let_through_is_never_asked_again() -> None:
    host = FakeHost()
    # The host would refuse the next one: it is not asked.
    app = FakeApp(forged_first=[_ok({"id": "rel-1"})])

    with pytest.raises(RolloutError, match=r"forged a signed-in principal \(HTTP 200\)"):
        _roll(host, app)

    assert len(_forged(app)) == 1


def test_a_signed_in_request_answered_503_is_asked_again() -> None:
    host = FakeHost()
    app = FakeApp(signed_first=[HttpAnswer(503), HttpAnswer(0, b"timed out")])

    assert _roll(host, app) == "rel-1"

    assert len(_signed_release(app)) == 3


def test_a_signed_in_request_never_answered_fails_without_the_sign_in_advice() -> None:
    host = FakeHost()
    app = FakeApp(signed=HttpAnswer(503))

    with pytest.raises(RolloutError, match="gave no answer to the signed-in request") as failed:
        _roll(host, app)

    message = str(failed.value)
    assert "last answer: HTTP 503" in message
    assert "your IT team" not in message
    assert f"The previous release ({OLD}) runs again" in message


def test_a_signed_in_request_refused_fails_on_its_first_answer() -> None:
    host = FakeHost()
    app = FakeApp(signed=HttpAnswer(401))

    with pytest.raises(RolloutError, match="answered HTTP 401"):
        _roll(host, app)

    assert len(_signed_release(app)) == 1


def test_a_request_asked_again_is_not_started_once_the_checks_time_is_spent() -> None:
    """Asking again takes time as waiting does: the last attempt starts within the check's
    five minutes, and none after them."""
    ticks = FakeClock()
    host = FakeHost()
    host.ready_after = 0
    app = FakeApp(forged=HttpAnswer(503), hosts={ADDRESS: host})
    started: list[float] = []

    def taking_time(url: str, headers: Mapping[str, str]) -> HttpAnswer:
        started.append(ticks.t)
        ticks.t += 25.0
        return app(url, headers)

    with pytest.raises(RolloutError, match="gave no answer to the request that forges"):
        rollout(
            host,
            environment="staging",
            image=NEW,
            commit=COMMIT,
            say=lambda _: None,
            get=taking_time,
            clock=ticks.clock(),
        )

    assert len(_forged(app)) > 1
    assert max(started) < rollouts.HEALTH_TIMEOUT_SECONDS


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
        (
            "Degraded",
            FakeApp(),
            r"still reported it unhealthy after 600 seconds \(provisioning Provisioned, "
            r"running Degraded\)",
        ),
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
    # The failed revision is never stopped by the rollout: Azure stops it once the previous
    # image's revision serves, and would start a stopped one again meanwhile.
    assert not any(call[0] == "deactivate" for call in host.calls)
    assert [name for name, revision in host.revisions.items() if revision.active] == [host.latest]


@pytest.mark.parametrize(
    ("settles", "app", "message"),
    [
        (
            ("Failed", "Failed"),
            FakeApp(),
            r"within 600 seconds \(provisioning Failed, running Failed\)",
        ),
        (
            ("Provisioned", "Degraded"),
            FakeApp(),
            r"within 600 seconds \(provisioning Provisioned, running Degraded\)",
        ),
        (
            ("Provisioning", "Processing"),
            FakeApp(),
            "was not ready to take the app's requests within 600 seconds",
        ),
        (
            None,
            FakeApp(health=[HttpAnswer(503)] * 40),
            "The revision's health check was not answered by the revision",
        ),
        (
            None,
            FakeApp(health=[_ok({"status": "ok", "release": "rel-1"})] * 40),
            "The revision's health check was not answered by the revision",
        ),
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
        "health-names-no-revision",
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


def test_an_update_azure_refuses_sets_the_previous_image_again() -> None:
    host = FakeHost()
    host.refuses.add(NEW)

    with pytest.raises(RolloutError, match="Azure refused the update") as failed:
        _roll(host, FakeApp())

    # No new revision was made, and nothing was stopped: the previous release served
    # throughout. Its image is set again all the same, since an update Azure answered late
    # may still land.
    assert [call[:2] for call in host.changes()] == [("set_image", NEW), ("set_image", OLD)]
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
    assert [call[0] for call in host.changes()] == ["set_image"]
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

    answer: Callable[[str, Mapping[str, str]], HttpAnswer] = app

    def checked_then_replaced(url: str, headers: Mapping[str, str]) -> HttpAnswer:
        # The revision serves and checks fine; another rollout lands before the record.
        answered = answer(url, headers)
        if url.endswith("/api/app/release") and "Authorization" in headers:
            another_rollout(host)
        return answered

    app.hosts[ADDRESS] = host
    with pytest.raises(RolloutError, match="before the release was recorded"):
        rollout(
            host,
            environment="staging",
            image=NEW,
            commit=COMMIT,
            say=lambda _: None,
            get=checked_then_replaced,
            clock=FakeClock().clock(),
        )

    assert not any(call[0] == "record" for call in host.calls)


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

    with pytest.raises(RolloutError, match="provisioning Failed, running Failed"):
        _roll(host, FakeApp())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


# ── The review's cases: what the app served, a lost answer, the deadline ──


def test_a_failure_puts_back_the_image_the_active_revision_served() -> None:
    """A failed update of Azure's own can leave the template naming an image no revision
    serves: the rollback restores the one that was serving."""
    host = FakeHost()
    host.image = "acr.example.io/pricing/motor@sha256:" + "b" * 64
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError, match="provisioning Failed, running Failed"):
        _roll(host, FakeApp())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


def test_a_revision_ready_past_the_deadline_fails_and_is_rolled_back() -> None:
    ticks = FakeClock()
    host = FakeHost()

    def slow(changed: FakeHost) -> None:
        # Azure's update takes longer than the whole deadline.
        changed.after = None
        ticks.t += rollouts.ROLLOUT_TIMEOUT_SECONDS + 1

    host.after = slow

    with pytest.raises(RolloutError, match="not ready to take the app's requests within 600"):
        _roll(host, FakeApp(), clock=ticks.clock())

    assert [call[1] for call in host.changes() if call[0] == "set_image"] == [NEW, OLD]


def test_with_no_revision_serving_a_failed_one_is_left_stopped() -> None:
    host = FakeHost()
    host.revisions["motor--old"] = replace(host.revisions["motor--old"], active=False)
    host.settles[NEW] = ("Failed", "Failed")

    with pytest.raises(RolloutError, match="No revision served before it") as failed:
        _roll(host, FakeApp())

    # No image to go back to: the failed revision is stopped, which holds since no revision
    # follows it, and nothing else is set.
    assert [call[:2] for call in host.changes()] == [
        ("set_image", NEW),
        ("deactivate", "motor--h261101120000000001"),
    ]
    assert not any(revision.active for revision in host.revisions.values())
    assert "provisioning Failed, running Failed" in str(failed.value)
    assert "so nothing runs: the failed revision is stopped" in str(failed.value)


def test_with_no_revision_serving_and_none_made_nothing_is_changed() -> None:
    host = FakeHost()
    host.revisions["motor--old"] = replace(host.revisions["motor--old"], active=False)
    host.refuses.add(NEW)

    with pytest.raises(RolloutError, match="Azure refused the update") as failed:
        _roll(host, FakeApp())

    assert "No revision served before it, and none was made: nothing was changed" in str(
        failed.value
    )
    assert [call[:2] for call in host.changes()] == [("set_image", NEW)]


def test_a_revision_address_the_rollout_cannot_reach_is_named() -> None:
    """Inside a company's network a revision's own address needs resolving and reaching as
    the app's does: a machine that cannot is told which address, not only that time passed."""
    host = FakeHost()
    app = FakeApp(health=[HttpAnswer(0, b"getaddrinfo failed")] * 40)

    with pytest.raises(RolloutError, match="no answer: getaddrinfo failed") as failed:
        _roll(host, app)

    assert (
        "The machine that runs the rollout must reach "
        f"https://motor--h261101120000000001.{ADDRESS}" in str(failed.value)
    )
    assert f"The previous release ({OLD}) runs again" in str(failed.value)


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
    # An update Azure refuses fails the rollout with no wait of its own, so the second read
    # of the app's state is the recovery's.
    host.refuses.add(NEW)
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
    assert "provisioning Failed, running Failed" in message
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
    import time as clock

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

        def do_GET(self) -> None:
            if self.path == "/stall":
                # The client has given up by now; writing to its closed socket
                # would print a broken pipe into whichever test runs next.
                clock.sleep(3)
                return
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
    thread = context_thread(httpd.serve_forever, daemon=True)
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
        (_config(app_host=None), "reach an app on Azure Container Apps"),
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


#: The four variables a job gives for the sign-in.
_SIGN_IN_VARIABLES = (rollouts.SUBSCRIPTION_VARIABLE, *rollouts.SERVICE_PRINCIPAL_VARIABLES)


@pytest.fixture
def job(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A job that gives nothing yet for the sign-in, with the Azure CLI on its path."""
    for name in _SIGN_IN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    _azure_cli(monkeypatch, found=True)
    return monkeypatch


def _azure_cli(monkeypatch: pytest.MonkeyPatch, *, found: bool) -> None:
    monkeypatch.setattr(
        rollouts.shutil, "which", lambda name: "/usr/bin/az" if found and name == "az" else None
    )


def test_all_three_variables_sign_in_as_the_service_principal(job: pytest.MonkeyPatch) -> None:
    job.setenv("AZURE_SUBSCRIPTION_ID", "subscription-1")
    job.setenv("AZURE_TENANT_ID", "tenant")
    job.setenv("AZURE_CLIENT_ID", "client")
    job.setenv("AZURE_CLIENT_SECRET", "s3cret")

    sign_in = azure_sign_in()

    # The Azure CLI on the path changes nothing: what the job gives is the sign-in.
    assert sign_in == AzureSignIn("subscription-1", ServicePrincipal("tenant", "client", "s3cret"))
    assert "s3cret" not in repr(sign_in)


def test_none_of_the_three_signs_in_through_the_azure_cli(job: pytest.MonkeyPatch) -> None:
    job.setenv("AZURE_SUBSCRIPTION_ID", "subscription-1")

    assert azure_sign_in() == AzureSignIn("subscription-1", None)


def test_with_nothing_given_the_subscription_and_both_ways_to_sign_in_are_named(
    job: pytest.MonkeyPatch,
) -> None:
    """A job whose secrets were never set, on a runner that has the Azure CLI: one refusal
    names every variable, not the subscription alone."""
    with pytest.raises(RolloutError) as refused:
        azure_sign_in()

    assert "set AZURE_SUBSCRIPTION_ID" in str(refused.value)
    assert "set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET to sign in" in str(
        refused.value
    )
    assert "az login" in str(refused.value)


def test_none_of_the_three_and_no_azure_cli_is_refused_naming_both_ways(
    job: pytest.MonkeyPatch,
) -> None:
    job.setenv("AZURE_SUBSCRIPTION_ID", "subscription-1")
    _azure_cli(job, found=False)

    with pytest.raises(RolloutError) as refused:
        azure_sign_in()

    assert "set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET to sign in" in str(
        refused.value
    )
    assert "az login" in str(refused.value) and "not on the path" in str(refused.value)


@pytest.mark.parametrize(
    ("given", "unset"),
    [
        (("AZURE_TENANT_ID",), "AZURE_CLIENT_ID, AZURE_CLIENT_SECRET"),
        (("AZURE_TENANT_ID", "AZURE_CLIENT_ID"), "AZURE_CLIENT_SECRET"),
        (("AZURE_CLIENT_SECRET",), "AZURE_TENANT_ID, AZURE_CLIENT_ID"),
    ],
)
def test_a_service_principal_given_in_part_is_refused_never_read_as_the_azure_clis(
    job: pytest.MonkeyPatch, given: tuple[str, ...], unset: str
) -> None:
    job.setenv("AZURE_SUBSCRIPTION_ID", "subscription-1")
    for name in given:
        job.setenv(name, "given")

    with pytest.raises(RolloutError) as refused:
        azure_sign_in()

    assert f"set {unset} too, to sign in as the CI service principal" in str(refused.value)


def test_the_subscription_unset_is_named_beside_whatever_else_is_missing(
    job: pytest.MonkeyPatch,
) -> None:
    job.setenv("AZURE_TENANT_ID", "tenant")

    with pytest.raises(RolloutError) as refused:
        azure_sign_in()

    assert "set AZURE_SUBSCRIPTION_ID, the subscription the apps are in" in str(refused.value)
    assert "set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET too" in str(refused.value)


def test_each_sign_in_takes_tokens_from_its_own_credential_alone() -> None:
    """A sign-in the job gives is never set aside for the other: its credential is that one
    kind, no chain that tries another when Azure refuses it."""
    from azure.identity import AzureCliCredential, ClientSecretCredential

    environment = AppEnvironment("staging", "pricing-rg", "motor-staging")
    principal = ServicePrincipal("00000000-0000-0000-0000-000000000000", "client", "wrong")

    given = rollouts.AzureContainerApps(environment, AzureSignIn("subscription-1", principal))
    cli = rollouts.AzureContainerApps(environment, AzureSignIn("subscription-1"))

    assert type(given._credential) is ClientSecretCredential
    assert type(cli._credential) is AzureCliCredential


def test_the_azure_cli_is_asked_for_the_apps_token_in_the_subscriptions_account_within_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token request as ``az`` gets it: the app registration as its resource, the
    account of the subscription the run reads, whatever ``az``'s current one is, and the
    request's bound as the process's."""
    from azure.identity._credentials import azure_cli

    asked: list[tuple[list[str], float]] = []

    def answer(args: list[str], **options: Any) -> str:
        asked.append((args[1:], options["timeout"]))
        return json.dumps({"accessToken": "a-token", "expiresOn": "2099-01-01 00:00:00.000000"})

    monkeypatch.setattr(azure_cli.shutil, "which", lambda name: "az")
    monkeypatch.setattr(azure_cli.subprocess, "check_output", answer)
    host = rollouts.AzureContainerApps(
        AppEnvironment("staging", "pricing-rg", "motor-staging"),
        AzureSignIn("subscription-1"),
        client=object(),  # type: ignore[arg-type]
    )

    assert host.token("app-client-id") == "a-token"
    arguments, bound = asked[0]
    assert arguments[:2] == ["account", "get-access-token"]
    assert arguments[arguments.index("--resource") + 1] == "app-client-id"
    assert arguments[arguments.index("--subscription") + 1] == "subscription-1"
    assert bound == rollouts.REQUEST_TIMEOUT_SECONDS


def test_an_azure_cli_that_is_not_signed_in_is_said_as_the_rollouts_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from azure.identity._credentials import azure_cli

    def refuse(args: list[str], **options: Any) -> str:
        raise subprocess.CalledProcessError(
            1, args, stderr="Please run 'az login' to setup account."
        )

    monkeypatch.setattr(azure_cli.shutil, "which", lambda name: "az")
    monkeypatch.setattr(azure_cli.subprocess, "check_output", refuse)
    host = rollouts.AzureContainerApps(
        AppEnvironment("staging", "pricing-rg", "motor-staging"),
        AzureSignIn("subscription-1"),
        client=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(RolloutError) as refused:
        host.token("app-client-id")

    # Both ways are named: the variables a job forgot to give are the likelier cause.
    assert "the Azure CLI, which gave it no sign-in" in str(refused.value)
    assert "set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET to sign in" in str(
        refused.value
    )
    assert "az login" in str(refused.value)


def test_the_commit_is_the_one_checked_out_and_none_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RolloutError, match="git names none"):
        head_commit(tmp_path)

    init_repo(tmp_path)
    assert head_commit(tmp_path) == git_run(tmp_path, "rev-parse", "HEAD")


def test_the_hosted_check_answers_the_release_its_revision_runs() -> None:
    host = FakeHost()
    app = FakeApp(release="rel-9", hosts={ADDRESS: host})
    clock = FakeClock().clock()
    serving = host.revisions["motor--old"]

    assert hosted_check(app, ADDRESS, "token-for-app-client-id", clock, revision=serving) == "rel-9"

    # A revision the app's address is not answered by never passes on another's answers.
    other = replace(serving, name="motor--other")
    with pytest.raises(RolloutError, match="not answered by the revision motor--other") as failed:
        hosted_check(app, ADDRESS, "token-for-app-client-id", clock, revision=other)
    assert "HTTP 200 from the revision motor--old" in str(failed.value)
    # And one Azure names no address for cannot be asked at all.
    with pytest.raises(RolloutError, match="names no address for the revision motor--old"):
        hosted_check(app, ADDRESS, "token", clock, revision=replace(serving, address=None))


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
                self, group: str, name: str, envelope: Any, *, timeout: float, polling: Any
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
                        fqdn=f"{revision}.{ADDRESS}",
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
        AzureSignIn("subscription-1"),
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
    # A revision's own address is Azure's: the hosted check asks the revision there.
    assert host.revision("motor--new") == RevisionState(
        "motor--new", NEW, True, "Provisioned", "Running", f"motor--new.{ADDRESS}"
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


# ── Through the real Azure SDK ─────────────────────────────────────────────────────

_POLL = "https://management.azure.com/operations/update-1"


class _ResourceManager(requests.adapters.BaseAdapter):
    """Azure Resource Manager as the real SDK reaches it over HTTP: the Container App, and
    an update answered 202 with a Location to poll, done on its first poll."""

    def __init__(self) -> None:
        super().__init__()
        self.app: dict[str, Any] = {
            "location": "uksouth",
            "tags": {"owner": "pricing"},
            "properties": {
                "latestRevisionName": "motor-staging--old",
                "configuration": {
                    "activeRevisionsMode": "Single",
                    "ingress": {"external": True, "fqdn": ADDRESS},
                },
                "template": {
                    "containers": [{"name": "app", "image": OLD}],
                    "revisionSuffix": "old",
                },
            },
        }
        self.updates: list[dict[str, Any]] = []
        self.reads: list[str] = []

    def send(self, request: Any, **kwargs: Any) -> requests.Response:
        if request.method == "PATCH":
            body = json.loads(request.body)
            self.updates.append(body)
            template = body.get("properties", {}).get("template")
            if template is not None:
                self.app["properties"]["template"] = template
                suffix = template["revisionSuffix"]
                self.app["properties"]["latestRevisionName"] = f"motor-staging--{suffix}"
            if "tags" in body:
                self.app["tags"] = body["tags"]
            return self._answer(request, 202, None, {"Location": _POLL, "Retry-After": "0"})
        # Only the app's own address and the update's Location answer: a poll anywhere else
        # is the SDK reading the wrong URL.
        address = request.url.split("?", 1)[0]
        self.reads.append(address)
        if address == _POLL or address.endswith("/containerApps/motor-staging"):
            return self._answer(request, 200, self.app)
        return self._answer(request, 404, {"error": {"code": "NotFound", "message": address}})

    def close(self) -> None:
        pass

    @staticmethod
    def _answer(
        request: Any, status: int, body: Any, headers: Mapping[str, str] | None = None
    ) -> requests.Response:
        import io

        from urllib3.response import HTTPResponse

        content = json.dumps(body).encode("utf-8") if body is not None else b""
        sent = {"Content-Type": "application/json", **(headers or {})}
        answer = requests.Response()
        answer.status_code = status
        answer.headers.update(sent)
        # azure-core reads the urllib3 response beneath, as a real transport fills it.
        answer.raw = HTTPResponse(
            body=io.BytesIO(content), headers=sent, status=status, preload_content=False
        )
        answer._content = content
        answer.encoding = "utf-8"
        answer.request = request
        answer.url = request.url
        return answer


class _SdkCredential:
    """A token as azure-core's bearer policy asks one."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        return AccessToken("a-token", 4_102_444_800)


def test_the_real_sdk_takes_the_rollouts_updates() -> None:
    # The SDK hands an update's keyword arguments to the request and to the poller it
    # builds, whose own first parameter is `timeout`: the rollout's request bound reached it
    # twice, failing after Azure had taken the image, before the check or the record.
    from azure.core.pipeline.transport import RequestsTransport
    from azure.mgmt.appcontainers import ContainerAppsAPIClient

    manager = _ResourceManager()
    session = requests.Session()
    session.mount("https://", manager)
    client = ContainerAppsAPIClient(
        _SdkCredential(),  # type: ignore[arg-type]
        "subscription-1",
        transport=RequestsTransport(session=session),
    )
    host = rollouts.AzureContainerApps(
        AppEnvironment("staging", "pricing-rg", "motor-staging"),
        AzureSignIn("subscription-1"),
        client=client,
        credential=_SdkCredential(),  # type: ignore[arg-type]
    )

    made = host.set_image(NEW, "h261102190710b9a4d9", 60.0)
    host.record(Recorded(NEW, "rel-1", COMMIT))

    assert made == "motor-staging--h261102190710b9a4d9"
    # Each update was followed to its end at the Location Azure answered it with.
    assert manager.reads.count(_POLL) == 2
    assert manager.updates[0]["properties"]["template"]["containers"][0]["image"] == NEW
    assert manager.app["tags"] == {
        "owner": "pricing",
        "haute-image": NEW,
        "haute-release": "rel-1",
        "haute-commit": COMMIT,
    }


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

    use_project(tmp_path)
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
    for name in _SIGN_IN_VARIABLES:
        monkeypatch.setenv(name, f"value-of-{name.lower()}")
    return tmp_path


def _hosts(monkeypatch: pytest.MonkeyPatch, app: FakeApp | None = None, **hosts: FakeHost) -> None:
    """Each environment's Container App, at an address of its own, and the app that answers."""

    def make(environment: AppEnvironment, sign_in: AzureSignIn) -> FakeHost:
        assert sign_in.subscription == "value-of-azure_subscription_id"
        assert sign_in.service_principal is not None
        assert sign_in.service_principal.client_secret == "value-of-azure_client_secret"
        return hosts[environment.name]

    app = FakeApp() if app is None else app
    for name, host in hosts.items():
        host.settings["address"] = f"{name}.{ADDRESS}"
        app.hosts[host.settings["address"]] = host
    monkeypatch.setattr(rollouts, "AzureContainerApps", make)
    monkeypatch.setattr(rollouts, "fetch", app)


def test_the_cli_rolls_staging_out_and_prints_the_release_last(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    staging = FakeHost()
    _hosts(monkeypatch, staging=staging)

    result = runner.invoke(cli, ["app", "rollout", "staging", "--image", NEW])

    assert result.exit_code == 0, result.output
    assert "Stopped the revision" not in result.output
    assert "takes the app's requests" in result.output
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

    closed = FakeApp(
        quotes={
            "open": False,
            "error_code": "quote_store_mismatch",
            "message": "the tables do not fit",
            "differences": ["policy: missing column age"],
        }
    )
    _hosts(monkeypatch, closed, staging=FakeHost())

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
