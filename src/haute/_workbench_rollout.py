"""The hosted app rolled out and checked: ``haute app rollout`` (specs/workbench, Control flow 35).

A workbench project's CI rolls the Container App ``[workbench.app.<environment>]`` names to
one image, by digest, in a maintenance window: the release running stops, then the new one
starts on a revision of its own, never two at once. The hosted check then holds the app to the
release: it answers, the host's sign-in stands in front of it, and its quotes are open. A
release that fails is replaced by the one before; one whose quotes are closed because its
environment's tables do not fit its form yet stays, since the people who own the database
change them in SQL and the app opens its quotes once they fit. The release that stays is
recorded on the Container App as Azure tags, which ``--from`` reads to promote it.

The host is reached through the :class:`ContainerAppHost` protocol, which
:class:`AzureContainerApps` implements with the Azure SDK, and the app through :func:`fetch`,
so every step is tested against fakes of both. Everything that can be refused is refused
before anything changes. The guard against undoing another rollout is a read before each
write, best effort: Azure takes no conditional update of a Container App, so the generated
jobs, serialised per environment, are what keep two rollouts of one app apart.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from haute._workbench_config import AppEnvironment, WorkbenchConfig, WorkbenchError

if TYPE_CHECKING:
    from azure.identity import ClientSecretCredential
    from azure.mgmt.appcontainers import ContainerAppsAPIClient

#: The variables the CI service principal and its subscription are named in: the four secrets
#: a workbench project's CI is given for its host.
CREDENTIAL_VARIABLES: tuple[str, ...] = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
)
#: An image named by digest: a tag can be pointed at other bytes, a digest cannot.
DIGEST_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
#: Azure's update and the new revision's provisioning, together.
ROLLOUT_TIMEOUT_SECONDS = 600
#: How long a provisioned revision's app may take to answer its health check.
HEALTH_TIMEOUT_SECONDS = 300
#: A rollback's update and provisioning, together.
RECOVERY_TIMEOUT_SECONDS = 600
#: Each request the hosted check sends.
REQUEST_TIMEOUT_SECONDS = 30
#: How often a revision and the app's health are asked again while waiting.
POLL_SECONDS = 10
#: How many times the Azure SDK retries a request, each within the request timeout.
_SDK_RETRIES = 2
#: The longest a request, its retries and their waits included, may take outside a deadline.
_CALL_TIMEOUT_SECONDS = 60
#: The longest the Azure SDK waits before a retry, whatever ``Retry-After`` Azure asks for:
#: a request's budget is checked only between its attempts.
_MAX_RETRY_WAIT_SECONDS = 10
#: Azure's limits on a resource's tags: how many it holds, and how long a value may be.
TAG_LIMIT = 50
TAG_VALUE_LIMIT = 256
#: The error code the app's quotes are closed with when the store's tables do not fit its form.
STORE_MISMATCH = "quote_store_mismatch"
#: The Azure tags a rollout records the release it leaves running under, on the Container App.
RECORD_TAGS: Mapping[str, str] = {
    "image": "haute-image",
    "release": "haute-release",
    "commit": "haute-commit",
}
#: Where a host's sign-in redirects a request it refuses: its own login path on the app's
#: address, or Microsoft's login host.
_SIGN_IN_PATH = "/.auth/login"
_MICROSOFT_LOGIN = "login.microsoftonline.com"
#: A revision's states that say it is provisioned, and that it cannot serve.
_PROVISIONED = "Provisioned"
_FAILED_PROVISIONING = frozenset({"Failed"})
_FAILED_RUNNING = frozenset({"Failed", "Degraded"})


class RolloutError(WorkbenchError):
    """The rollout cannot go ahead, or failed: the message names what, and what was done."""


class QuotesClosedError(RolloutError):
    """The release stays, its quotes closed because its environment's tables do not fit its
    form: the release and each difference, for the database's owners to change in SQL."""

    def __init__(self, message: str, release: str, differences: tuple[str, ...]) -> None:
        super().__init__(message)
        self.release = release
        self.differences = differences


@dataclass(frozen=True, slots=True)
class Recorded:
    """The release a rollout recorded on an app: its image by digest, its id and its commit."""

    image: str
    release: str
    commit: str


@dataclass(frozen=True, slots=True)
class RevisionState:
    """A revision of the app as the host reports it."""

    name: str
    image: str | None
    active: bool
    provisioning: str
    running: str


@dataclass(frozen=True, slots=True)
class AppState:
    """The Container App as the rollout needs it: whether it keeps one revision active at a
    time, its containers' images, its public address (None without one), its latest revision
    and every active one, its Microsoft Entra ID registration's client ID (None without one),
    the names of its Azure tags and the release recorded on it (None without a whole record)."""

    single_revision: bool
    images: tuple[str, ...]
    address: str | None
    latest_revision: str | None
    active_revisions: tuple[str, ...]
    client_id: str | None
    tag_names: frozenset[str] = frozenset()
    recorded: Recorded | None = None
    #: Azure's latest ready revision: in single revision mode the one serving, while it is
    #: active. A revision on its way out can stay active beside it, taking no traffic.
    ready_revision: str | None = None


class ContainerAppHost(Protocol):
    """One Container App, as the rollout reads and changes it. Every failure is a
    :class:`RolloutError` naming the app."""

    def state(self) -> AppState: ...

    def revision(self, name: str) -> RevisionState: ...

    def deactivate(self, name: str) -> None: ...

    def limit(self, seconds: float | None) -> None:
        """Bound every request from now on, retries and their waits included, to end within
        *seconds* from now, refusing one asked after; None lifts the bound."""
        ...

    def set_image(self, image: str, suffix: str, timeout: float) -> str:
        """Set the app's one container to *image* on a revision suffixed *suffix*, waiting on
        the host's update no longer than *timeout* seconds, and answer the revision made."""
        ...

    def record(self, recorded: Recorded) -> None:
        """Set the record's Azure tags on the app, keeping its other tags."""
        ...

    def token(self, client_id: str) -> str:
        """A bearer token, as the CI service principal, for the app registration *client_id*."""
        ...


@dataclass(frozen=True, slots=True)
class HttpAnswer:
    """One answer of the hosted app: its status (0 when no server answered), its body and
    where a redirect points."""

    status: int
    body: bytes = b""
    location: str | None = None


Fetch = Callable[[str, Mapping[str, str]], HttpAnswer]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch(url: str, headers: Mapping[str, str]) -> HttpAnswer:
    """One GET of *url* with *headers* within :data:`REQUEST_TIMEOUT_SECONDS`, redirects not
    followed: a sign-in's redirect is an answer of its own. A request no server answers, or
    one that stalls, answers status 0 with the reason as its body."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return HttpAnswer(response.status, response.read(), response.headers.get("Location"))
    except urllib.error.HTTPError as error:
        # An error's status is its answer; a body that stalls or ends early is none.
        try:
            body = error.read()
        except (http.client.HTTPException, OSError):
            body = b""
        return HttpAnswer(error.code, body, error.headers.get("Location"))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        # No server, a stall (a timeout is an OSError) or a body that ends early.
        return HttpAnswer(0, (str(error) or type(error).__name__).encode("utf-8"))


def forged_principal() -> dict[str, str]:
    """The headers a host's sign-in would set for a signed-in principal, roles included: the
    app accepts them when they reach it unchanged, so a host whose sign-in stands in front of
    the app must refuse a request carrying them unsigned."""
    claims = {
        "auth_typ": "aad",
        "name_typ": "name",
        "role_typ": "roles",
        "claims": [
            {"typ": "name", "val": "haute-rollout-check"},
            {"typ": "roles", "val": "haute-rollout-check"},
        ],
    }
    return {
        "X-MS-CLIENT-PRINCIPAL-NAME": "haute-rollout-check",
        "X-MS-CLIENT-PRINCIPAL-ID": "00000000-0000-0000-0000-000000000000",
        "X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(claims).encode("utf-8")).decode(),
    }


def credentials() -> dict[str, str]:
    """The CI service principal and its subscription, by variable, from the environment;
    :class:`RolloutError` naming every one of :data:`CREDENTIAL_VARIABLES` that is unset."""
    values = {name: os.environ.get(name, "").strip() for name in CREDENTIAL_VARIABLES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RolloutError(
            "The rollout signs in to Azure as the CI service principal: set "
            + ", ".join(missing)
            + "."
        )
    return values


def app_environment(config: WorkbenchConfig, name: str) -> AppEnvironment:
    """The hosted environment *name*'s app, as ``[workbench.app.<name>]`` names it: refused for
    a project without a workbench, with an app host the rollout does not reach, or naming no
    such environment or leaving its resource group or Container App empty."""
    if not config.enabled:
        raise RolloutError("This project has no workbench: there is no app to roll out.")
    if config.app_host != "azure-container-apps":
        raise RolloutError(
            "haute app rollout rolls out an app on Azure Container Apps: set [workbench.app] "
            f'host = "azure-container-apps" in haute.toml (it is {config.app_host!r}).'
        )
    found = next((each for each in config.app_environments if each.name == name), None)
    if found is None:
        raise RolloutError(
            f"haute.toml names no [workbench.app.{name}]: name the resource group and the "
            f"Container App {name}'s app runs as there."
        )
    empty = [key for key in ("resource_group", "container_app") if not getattr(found, key)]
    if empty:
        raise RolloutError(
            f"[workbench.app.{name}] leaves {' and '.join(empty)} empty: fill them in with the "
            "Container App your IT team runs the app as."
        )
    return found


def head_commit(project_dir: Path) -> str:
    """The commit checked out in *project_dir*, which the rollout records; refused when git
    names none."""
    from haute._git_core import _run_git_ok, git_binary_available

    named, commit = (
        _run_git_ok("rev-parse", "HEAD", cwd=project_dir) if git_binary_available() else (False, "")
    )
    if not named or not commit:
        raise RolloutError(
            "A rollout records the commit it rolls out, and git names none here: run it in a "
            "clone of the project with git installed and a commit checked out."
        )
    return commit


def promoted(source: ContainerAppHost, source_name: str, commit: str) -> str:
    """The image ``--from source_name`` rolls out: the one *source*'s record names, refused
    when the record is missing, names an image other than by digest or another commit than
    *commit*, or *source* does not run it."""
    state = source.state()
    recorded = state.recorded
    if recorded is None:
        raise RolloutError(
            f"{source_name}'s app records no release: roll {source_name} out first, with "
            "haute app rollout."
        )
    if not DIGEST_REFERENCE.fullmatch(recorded.image):
        raise RolloutError(
            f"{source_name}'s app records {recorded.image!r}, which names no image by digest: "
            f"roll {source_name} out again."
        )
    if recorded.commit != commit:
        raise RolloutError(
            f"{source_name} runs commit {recorded.commit[:12]}, not {commit[:12]}, the commit "
            f"checked out: promote the commit {source_name} runs, or roll {source_name} out to "
            "this one first."
        )
    latest = source.revision(state.latest_revision) if state.latest_revision else None
    if latest is None or not latest.active or latest.image != recorded.image:
        raise RolloutError(
            f"{source_name}'s app does not run the release it records ({recorded.image}): roll "
            f"{source_name} out again."
        )
    return recorded.image


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_hex() -> str:
    return secrets.token_hex(3)


@dataclass(frozen=True, slots=True)
class Clock:
    """Time as the rollout waits on it: a monotonic reading, a sleep, the UTC time and the
    random part a revision's suffix is named with."""

    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    utc: Callable[[], datetime] = _utc_now
    random: Callable[[], str] = _random_hex


#: The clock a rollout runs on outside tests.
_CLOCK = Clock()


def _suffix(clock: Clock) -> str:
    """A revision suffix of a rollout's own: ``h``, the UTC time to the second and six random
    hex digits, so two rollouts in one second never share one."""
    return "h" + clock.utc().strftime("%y%m%d%H%M%S") + clock.random()


def _wait_provisioned(
    host: ContainerAppHost, name: str, deadline: float, clock: Clock, budget: int
) -> None:
    """Wait until revision *name* is provisioned, failing one whose provisioning failed or
    whose running state is failed or degraded, or the *deadline* (*budget* seconds)."""
    while True:
        revision = host.revision(name)
        if revision.provisioning in _FAILED_PROVISIONING:
            raise RolloutError(f"The revision {name} failed to provision.")
        if revision.running in _FAILED_RUNNING:
            raise RolloutError(f"The revision {name} is {revision.running.lower()}.")
        if clock.now() >= deadline:
            raise RolloutError(
                f"The revision {name} was not provisioned within {budget} seconds "
                f"(provisioning {revision.provisioning}, running {revision.running})."
            )
        if revision.provisioning == _PROVISIONED:
            return
        clock.sleep(POLL_SECONDS)


def hosted_check(get: Fetch, address: str, token: str, clock: Clock = _CLOCK) -> str:
    """Hold the app at *address* to the release: ``/health`` answers signed in, a forged
    principal is refused by the host, and the signed-in app answers the same release with its
    quotes open. Answers the release id; :class:`QuotesClosedError` when the quotes are closed
    because the store's tables do not fit the form, :class:`RolloutError` otherwise."""
    base = f"https://{address}"
    signed = {"Authorization": f"Bearer {token}"}
    deadline = clock.now() + HEALTH_TIMEOUT_SECONDS
    while True:
        health = get(f"{base}/health", signed)
        release = _json(health).get("release") if health.status == 200 else None
        if isinstance(release, str) and release:
            break
        if clock.now() >= deadline:
            raise RolloutError(
                f"The app did not answer its health check within {HEALTH_TIMEOUT_SECONDS} "
                f"seconds (last answer: {_said(health)})."
            )
        clock.sleep(POLL_SECONDS)
    forged = get(f"{base}/api/app/release", forged_principal())
    if not _refused(forged, address):
        raise RolloutError(
            "The app answered a request that forged a signed-in principal "
            f"({_said(forged)}): its host's built-in authentication must stand in front of it."
        )
    answer = get(f"{base}/api/app/release", signed)
    if answer.status != 200:
        raise RolloutError(
            f"Signed in as the CI service principal, the app answered {_said(answer)}: your IT "
            "team lets the service principal sign in to it through the built-in authentication."
        )
    body = _json(answer)
    if body.get("id") != release:
        raise RolloutError(
            f"The app answered release {body.get('id')!r} after its health check named {release!r}."
        )
    quotes = body.get("quotes")
    if not isinstance(quotes, dict):
        raise RolloutError("The app's release answered no state of its quotes.")
    if quotes.get("open") is not True:
        message = str(quotes.get("message") or "no reason given")
        if quotes.get("error_code") == STORE_MISMATCH:
            differences = tuple(str(each) for each in quotes.get("differences") or ())
            raise QuotesClosedError(
                f"Release {release} runs with its quotes closed: {message}"
                + "".join(f"\n  - {each}" for each in differences),
                release,
                differences,
            )
        raise RolloutError(f"The app's quotes are closed: {message}")
    return release


def _refused(answer: HttpAnswer, address: str) -> bool:
    """Whether the host refused a request: 401, 403, or a redirect to its sign-in, the app's
    own address (*address*, or a relative location) under ``/.auth/login``, or Microsoft's
    login host over https."""
    if answer.status in (401, 403):
        return True
    if not 300 <= answer.status < 400 or not answer.location:
        return False
    where = urllib.parse.urlsplit(answer.location)
    host = (where.hostname or "").lower()
    if host == _MICROSOFT_LOGIN:
        return where.scheme == "https"
    own = (not where.netloc and not where.scheme) or (
        where.scheme == "https" and host == address.lower()
    )
    return own and (where.path == _SIGN_IN_PATH or where.path.startswith(_SIGN_IN_PATH + "/"))


def _json(answer: HttpAnswer) -> dict[str, Any]:
    try:
        payload = json.loads(answer.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _said(answer: HttpAnswer) -> str:
    if answer.status == 0:
        return f"no answer: {answer.body.decode('utf-8', 'replace')}"
    return f"HTTP {answer.status}"


def _preflight(state: AppState, environment: str, image: str) -> str:
    """Refuse an app the rollout cannot roll out and check, before anything changes; answers
    the Entra ID client ID the check's token is asked for."""
    if not state.single_revision:
        raise RolloutError(
            f"{environment}'s app keeps several revisions active: a rollout needs single "
            "revision mode, so two releases never take traffic at once."
        )
    if len(state.images) != 1:
        raise RolloutError(
            f"{environment}'s app runs {len(state.images)} containers: a rollout sets the one "
            "container the app's release runs in."
        )
    if state.address is None:
        raise RolloutError(
            f"{environment}'s app takes no requests at a public address: the hosted check "
            "needs its external ingress."
        )
    if state.client_id is None:
        raise RolloutError(
            f"{environment}'s app signs no one in through Microsoft Entra ID: the app needs "
            "its built-in authentication, and the hosted check signs in through it."
        )
    if len(image) > TAG_VALUE_LIMIT:
        raise RolloutError(
            f"The image reference is {len(image)} characters, past the {TAG_VALUE_LIMIT} an "
            "Azure tag holds, so the release could not be recorded: use a shorter registry path."
        )
    if len(state.tag_names | set(RECORD_TAGS.values())) > TAG_LIMIT:
        raise RolloutError(
            f"{environment}'s app has {len(state.tag_names)} Azure tags, and the release's "
            f"record needs {len(RECORD_TAGS)} of the {TAG_LIMIT} Azure allows: remove some."
        )
    return state.client_id


@dataclass(slots=True)
class _Run:
    """One rollout: the host, the app's address, the token, the transport and the clock."""

    host: ContainerAppHost
    environment: str
    address: str
    token: str
    get: Fetch
    clock: Clock
    say: Callable[[str], None]
    steps: list[str] = field(default_factory=list)

    def check(self) -> str:
        return hosted_check(self.get, self.address, self.token, self.clock)

    def record(self, ours: str | None, recorded: Recorded) -> None:
        """Record the release on the app, while *ours* is still its latest revision."""
        latest = self.host.state().latest_revision
        if latest != ours:
            raise RolloutError(
                f"Another change made {latest} the app's latest revision before the release was "
                "recorded: nothing was recorded or undone."
            )
        self.host.record(recorded)
        self.say(f"  ✓ Recorded release {recorded.release} on {self.environment}'s app")


def rollout(
    host: ContainerAppHost,
    *,
    environment: str,
    image: str,
    commit: str,
    say: Callable[[str], None],
    get: Fetch = fetch,
    clock: Clock = _CLOCK,
) -> str:
    """Roll *host* to *image* for *environment*'s app, check it, and record the release that
    stays; answers its id (specs/workbench, Control flow 35)."""
    if not DIGEST_REFERENCE.fullmatch(image):
        raise RolloutError(
            f"{image!r} names no image by digest (<repository>@sha256:...): a tag can be "
            "pointed at other bytes, so a rollout takes the digest haute app build --push "
            "printed."
        )
    state = host.state()
    client_id = _preflight(state, environment, image)
    assert state.address is not None
    run = _Run(host, environment, state.address, host.token(client_id), get, clock, say)
    previous = state.latest_revision
    latest = host.revision(previous) if previous else None
    if (
        latest is not None
        and latest.active
        and latest.provisioning == _PROVISIONED
        and latest.image == image
    ):
        return _recheck(run, latest, image, commit)

    # The image the app served, which a failure puts back: its serving revision's, Azure's
    # latest ready one while it is active, not its template's (which a failed update of
    # Azure's own can leave naming another) nor another active revision's on its way out.
    serving = state.ready_revision if state.ready_revision in state.active_revisions else None
    kept = host.revision(serving).image if serving is not None else None
    suffix = _suffix(clock)
    deadline = clock.now() + ROLLOUT_TIMEOUT_SECONDS
    host.limit(ROLLOUT_TIMEOUT_SECONDS)
    made: str | None = None
    try:
        for name in state.active_revisions:
            host.deactivate(name)
            say(f"  ✓ Stopped the revision {name}")
        made = host.set_image(image, suffix, max(0.0, deadline - clock.now()))
        say(f"  ✓ Started the revision {made} on {image}")
        _wait_provisioned(host, made, deadline, clock, ROLLOUT_TIMEOUT_SECONDS)
        say(f"  ✓ The revision {made} is provisioned")
        release = run.check()
    except QuotesClosedError as closed:
        host.limit(None)
        run.record(made, Recorded(image, closed.release, commit))
        raise
    except RolloutError as failure:
        host.limit(RECOVERY_TIMEOUT_SECONDS)
        try:
            undone = _roll_back(host, suffix, previous, kept, clock, say)
        finally:
            host.limit(None)
        raise RolloutError(f"{failure} {undone}") from failure
    host.limit(None)
    say(f"  ✓ Release {release} runs on {environment}, its quotes open")
    run.record(made, Recorded(image, release, commit))
    return release


def _recheck(run: _Run, latest: RevisionState, image: str, commit: str) -> str:
    """A re-run: the app's latest revision already runs *image*, so nothing is rolled; it is
    checked and recorded, and a failure undoes nothing."""
    run.say(f"  ✓ {run.environment} already runs {image}: checking it, changing nothing")
    unchanged = f"Nothing was changed: {run.environment} runs as it did."
    if latest.running in _FAILED_RUNNING:
        raise RolloutError(f"The revision {latest.name} is {latest.running.lower()}. {unchanged}")
    try:
        release = run.check()
    except QuotesClosedError as closed:
        run.record(latest.name, Recorded(image, closed.release, commit))
        raise
    except RolloutError as failure:
        raise RolloutError(f"{failure} {unchanged}") from failure
    run.record(latest.name, Recorded(image, release, commit))
    return release


def _roll_back(
    host: ContainerAppHost,
    suffix: str,
    previous: str | None,
    kept: str | None,
    clock: Clock,
    say: Callable[[str], None],
) -> str:
    """Put the image the app ran before back on a revision of its own (the image alone: the
    template is the app's as it stands), unless another change has made a revision
    other than this rollout's (suffixed *suffix*) or the one it started from (*previous*) the
    app's latest; answers what was done, for the failure's message."""
    latest = host.state().latest_revision
    ours = latest if latest is not None and latest.endswith(f"--{suffix}") else None
    if ours is None and latest != previous:
        return (
            f"Another change made {latest} the app's latest revision meanwhile, so nothing "
            "was undone."
        )
    deadline = clock.now() + RECOVERY_TIMEOUT_SECONDS
    try:
        if ours is not None:
            host.deactivate(ours)
            say(f"  ✓ Stopped the failed revision {ours}")
        if kept is None:
            return "No revision served before it, so nothing runs: the failed revision is stopped."
        back = host.set_image(kept, _suffix(clock), max(0.0, deadline - clock.now()))
        _wait_provisioned(host, back, deadline, clock, RECOVERY_TIMEOUT_SECONDS)
    except RolloutError as failure:
        last = host.state()
        return (
            f"Putting the previous release ({kept}) back failed too: {failure} The app's "
            f"latest revision is {last.latest_revision}; active: "
            f"{', '.join(last.active_revisions) or 'none'}."
        )
    say(f"  ✓ Put the previous release back on the revision {back}")
    return f"The previous release ({kept}) runs again, on the revision {back}."


@contextmanager
def _azure(what: str) -> Iterator[None]:
    """An Azure SDK failure while doing *what*, said as a :class:`RolloutError`."""
    from azure.core.exceptions import AzureError

    try:
        yield
    except AzureError as error:
        message = getattr(error, "message", None) or str(error)
        raise RolloutError(f"Azure could not {what}: {message}") from error


class AzureContainerApps:
    """A Container App on Azure, through ``azure-mgmt-appcontainers``, signed in as the CI
    service principal *values* name."""

    def __init__(
        self,
        environment: AppEnvironment,
        values: Mapping[str, str],
        *,
        client: ContainerAppsAPIClient | None = None,
        credential: ClientSecretCredential | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if credential is None:
            from azure.identity import ClientSecretCredential

            credential = ClientSecretCredential(
                values["AZURE_TENANT_ID"], values["AZURE_CLIENT_ID"], values["AZURE_CLIENT_SECRET"]
            )
        if client is None:
            client = _client(credential, values["AZURE_SUBSCRIPTION_ID"])
        self._now = now
        self._end: float | None = None
        self._credential = credential
        self._client = client
        self._group = environment.resource_group
        self._name = environment.container_app
        self._where = f"the Container App {self._name} in {self._group}"

    def limit(self, seconds: float | None) -> None:
        self._end = None if seconds is None else self._now() + seconds

    def _budget(self, what: str, end: float | None = None) -> float:
        """The SDK's ``timeout`` for a request to do *what*, its retries and their waits
        included: what is left of the bound (and of *end*), at most
        :data:`_CALL_TIMEOUT_SECONDS`; refused when nothing is left."""
        ends = [each for each in (self._end, end) if each is not None]
        left = min([each - self._now() for each in ends], default=float(_CALL_TIMEOUT_SECONDS))
        if left <= 0:
            raise RolloutError(f"The rollout's deadline passed before Azure was asked to {what}.")
        return min(left, float(_CALL_TIMEOUT_SECONDS))

    def _app(self, end: float | None = None) -> Any:
        what = f"read {self._where}"
        with _azure(what):
            app = self._client.container_apps.get(
                self._group, self._name, timeout=self._budget(what, end)
            )
        if app.properties is None or app.properties.template is None:
            raise RolloutError(f"Azure answered {self._where} with no template.")
        return app

    def state(self) -> AppState:
        app = self._app()
        properties = app.properties
        configuration = properties.configuration
        # Azure's default is single revision mode.
        mode = _named(getattr(configuration, "active_revisions_mode", None)) or "Single"
        ingress = getattr(configuration, "ingress", None)
        address = ingress.fqdn if ingress is not None and ingress.external else None
        what = f"list the revisions of {self._where}"
        with _azure(what):
            revisions = list(
                self._client.container_apps_revisions.list_revisions(
                    self._group, self._name, timeout=self._budget(what)
                )
            )
        active = tuple(
            str(each.name)
            for each in revisions
            if each.name and each.properties is not None and each.properties.active
        )
        tags = dict(app.tags or {})
        return AppState(
            single_revision=mode.lower() == "single",
            images=tuple(str(each.image) for each in properties.template.containers or ()),
            address=address or None,
            latest_revision=properties.latest_revision_name or None,
            active_revisions=active,
            client_id=self._client_id(),
            tag_names=frozenset(tags),
            recorded=_recorded(tags),
            ready_revision=properties.latest_ready_revision_name or None,
        )

    def _client_id(self) -> str | None:
        from azure.core.exceptions import ResourceNotFoundError

        what = f"read the authentication of {self._where}"
        try:
            auth = self._client.container_apps_auth_configs.get(
                self._group, self._name, "current", timeout=self._budget(what)
            )
        except ResourceNotFoundError:
            return None
        except Exception as error:
            from azure.core.exceptions import AzureError

            if isinstance(error, AzureError):
                raise RolloutError(
                    f"Azure could not read the authentication of {self._where}: {error}"
                ) from error
            raise
        providers = auth.properties.identity_providers if auth.properties else None
        entra = providers.azure_active_directory if providers is not None else None
        if entra is None or entra.enabled is False or entra.registration is None:
            return None
        return entra.registration.client_id or None

    def revision(self, name: str) -> RevisionState:
        what = f"read the revision {name} of {self._where}"
        with _azure(what):
            revision = self._client.container_apps_revisions.get_revision(
                self._group, self._name, name, timeout=self._budget(what)
            )
        properties = revision.properties
        if properties is None:
            raise RolloutError(f"Azure answered the revision {name} of {self._where} empty.")
        containers = properties.template.containers if properties.template else None
        return RevisionState(
            name=name,
            image=str(containers[0].image) if containers else None,
            active=bool(properties.active),
            provisioning=_named(properties.provisioning_state),
            running=_named(properties.running_state),
        )

    def deactivate(self, name: str) -> None:
        what = f"stop the revision {name} of {self._where}"
        with _azure(what):
            self._client.container_apps_revisions.deactivate_revision(
                self._group, self._name, name, timeout=self._budget(what)
            )

    def set_image(self, image: str, suffix: str, timeout: float) -> str:
        from azure.mgmt.appcontainers.models import ContainerApp, ContainerAppProperties

        # The deadline holds from here: the read and the update's start spend it too.
        end = self._now() + timeout
        app = self._app(end)
        template = app.properties.template
        template.containers[0].image = image
        template.revision_suffix = suffix
        envelope = ContainerApp(
            location=app.location, properties=ContainerAppProperties(template=template)
        )
        what = f"set the image of {self._where}"
        with _azure(what):
            poller = self._client.container_apps.begin_update(
                self._group, self._name, envelope, timeout=self._budget(what, end)
            )
            poller.wait(timeout=max(0.0, end - self._now()))
            if not poller.done():
                raise RolloutError(
                    f"Azure's update of {self._where} did not finish within {int(timeout)} seconds."
                )
            updated = poller.result()
        latest = updated.properties.latest_revision_name if updated.properties else None
        if not latest:
            raise RolloutError(f"Azure's update of {self._where} named no revision.")
        if not str(latest).endswith(f"--{suffix}"):
            # The resource Azure answers may already hold another change: never take its
            # revision for this rollout's.
            raise RolloutError(
                f"Azure's update of {self._where} answered the revision {latest}, not this "
                f"rollout's (suffixed {suffix}): another change landed meanwhile."
            )
        return str(latest)

    def record(self, recorded: Recorded) -> None:
        from azure.mgmt.appcontainers.models import ContainerApp

        app = self._app()
        tags = {
            **(app.tags or {}),
            RECORD_TAGS["image"]: recorded.image,
            RECORD_TAGS["release"]: recorded.release,
            RECORD_TAGS["commit"]: recorded.commit,
        }
        what = f"record the release on {self._where}"
        with _azure(what):
            poller = self._client.container_apps.begin_update(
                self._group,
                self._name,
                ContainerApp(location=app.location, tags=tags),
                timeout=self._budget(what),
            )
            poller.wait(timeout=REQUEST_TIMEOUT_SECONDS * 4)
            if not poller.done():
                raise RolloutError(f"Azure did not record the release on {self._where} in time.")
            poller.result()

    def token(self, client_id: str) -> str:
        with _azure(f"give the CI service principal a token for the app {client_id}"):
            return str(self._credential.get_token(f"{client_id}/.default").token)


def _client(credential: Any, subscription: str, **options: Any) -> ContainerAppsAPIClient:
    """The Container Apps client a rollout reaches Azure through: each request bounded by
    :data:`REQUEST_TIMEOUT_SECONDS`, retried at most :data:`_SDK_RETRIES` times, never waiting
    longer than :data:`_MAX_RETRY_WAIT_SECONDS` between attempts (*options*, a test's
    transport, say, passed on)."""
    from azure.mgmt.appcontainers import ContainerAppsAPIClient

    return ContainerAppsAPIClient(
        credential,
        subscription,
        connection_timeout=REQUEST_TIMEOUT_SECONDS,
        read_timeout=REQUEST_TIMEOUT_SECONDS,
        retry_policy=_retry_policy(),
        **options,
    )


def _retry_policy() -> Any:
    """azure-core's retry policy, :data:`_SDK_RETRIES` retries, its ``Retry-After`` waits capped
    at :data:`_MAX_RETRY_WAIT_SECONDS`: the policy checks a request's budget only between
    attempts, so an uncapped wait could hold a stopped app past its deadline."""
    from azure.core.pipeline.policies import RetryPolicy

    class _BoundedRetry(RetryPolicy):
        def _sleep_for_retry(self, response: Any, transport: Any) -> bool:
            retry_after = self.get_retry_after(response)
            if retry_after:
                transport.sleep(min(retry_after, _MAX_RETRY_WAIT_SECONDS))
                return True
            return False

    return _BoundedRetry(retry_total=_SDK_RETRIES, retry_backoff_max=_MAX_RETRY_WAIT_SECONDS)


def _named(value: object) -> str:
    """A state the SDK answers, as Azure names it: an enum's value (``Provisioned``), not its
    ``str()`` (``RevisionProvisioningState.PROVISIONED``); empty for none."""
    if value is None:
        return ""
    return str(value.value) if isinstance(value, Enum) else str(value)


def _recorded(tags: Mapping[str, str]) -> Recorded | None:
    values = {key: str(tags.get(tag) or "") for key, tag in RECORD_TAGS.items()}
    if not all(values.values()):
        return None
    return Recorded(**values)
