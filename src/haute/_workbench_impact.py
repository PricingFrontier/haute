"""Impact on saved quotes: ``haute app impact`` (specs/workbench, Control flow 36).

Before production is approved, the workbench's CI prices a sample of the quotes production
keeps on both apps, the release staging runs and the release production runs, and compares
the answers on the output fields the two releases share, for whoever approves production. It
changes nothing: production's quotes are listed and opened, and each one's values are priced
through the apps' own routes (a pricing saves nothing), signed in as the CI service principal
``haute app rollout`` signs in as. A price that moves never fails the run; staging failing on
a quote production prices does, once the report is written.

The apps are reached through :class:`AppReader`, which
:class:`~haute._workbench_rollout.AzureContainerApps` implements, and their routes through
:func:`~haute._workbench_rollout.fetch` and :func:`~haute._workbench_rollout.post`, so every
step is tested against fakes of both.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeGuard

from haute import _workbench_rollout as rollouts
from haute._workbench_compare import (
    Move,
    SharedFields,
    compare_quote,
    is_number,
    shared_fields,
)
from haute._workbench_config import WorkbenchError
from haute._workbench_rollout import (
    _CLOCK,
    RECORD_TAGS,
    AppState,
    Clock,
    Fetch,
    HttpAnswer,
    Post,
)

#: The whole run, every request and wait included.
IMPACT_TIMEOUT_SECONDS = 600
#: The quotes priced unless ``--sample`` says otherwise, and the most it takes.
DEFAULT_SAMPLE = 100
MAX_SAMPLE = 1_000
#: Tries of a request a busy app answers 503, the first included.
BUSY_TRIES = 3
#: The report, in the working directory.
REPORT_FILE = "impact_report.md"
#: A pricing request's bound: longer than the app's own 60 seconds on a pricing
#: (``haute._workbench_app.PRICE_TIMEOUT_SECONDS``), so the app's 504 comes first.
PRICE_REQUEST_SECONDS = 75
#: The tag whose absence says production was never rolled out: there is no baseline.
RELEASE_TAG = RECORD_TAGS["release"]

#: Answers that say an app, not a quote, is failing, which stop the run: no answer, a
#: sign-in refused, a release other than the one the run read, or still busy or closed after
#: its tries; and any redirect, which is the host sending the request to its sign-in.
_APP_FAILURES = frozenset({0, 401, 403, 409, 503})
#: What an app's answer to one quote means, said without the app's own words.
_QUOTE_FAILURES: Mapping[int, str] = {
    413: "the quote is larger than the app takes",
    422: "the release refuses the quote's values",
    500: "the pipeline failed on the quote",
    504: "pricing the quote took too long",
    507: "the quote needed more memory than the app has",
}

#: The most quotes the quotes route lists in one page.
_PAGE_LIMIT = 200
#: The wait before a busy app is asked again: its ``Retry-After``, five seconds without one,
#: ten at most.
_BUSY_WAIT_SECONDS = 5.0
_MAX_BUSY_WAIT_SECONDS = 10.0

Verdict = Literal["compared", "same_release", "no_baseline", "stopped"]
Outcome = Literal[
    "moved",
    "unchanged",
    "no_longer_compares",
    "refused",
    "failed",
    "baseline_failed",
    "not_compared",
]


class ImpactError(WorkbenchError):
    """An app the impact cannot reach or use: what failed, naming the environment."""


class AppReader(Protocol):
    """A hosted app as the impact reads it: its state on the host and a token to sign in."""

    def state(self) -> AppState: ...

    def token(self, client_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Side:
    """An app as the impact reaches it: its environment, address, token, the release recorded
    on it, the release it answers and its form revision, and its form."""

    environment: str
    address: str
    token: str
    release: str
    form_revision: str
    form: Mapping[str, Any]
    commit: str | None = None

    def url(self, path: str) -> str:
        return f"https://{self.address}{path}"

    def signed(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass(frozen=True, slots=True)
class QuoteImpact:
    """A quote of the sample: its reference and option, its outcome, its moves and the reason
    for an outcome that is not a comparison."""

    reference: str
    option: int
    outcome: Outcome
    moves: tuple[Move, ...] = ()
    reason: str | None = None

    @property
    def label(self) -> str:
        return f"{self.reference}/{self.option}"


@dataclass(frozen=True, slots=True)
class Released:
    """An app's release as the report names it: its environment, release id and commit."""

    environment: str
    release: str | None
    commit: str | None


@dataclass(frozen=True, slots=True)
class ImpactReport:
    """What the impact found: each app's release, the sample asked for and listed, the shared
    fields, each quote's outcome, whether time ran out, and the verdict: ``compared``,
    ``same_release`` or ``no_baseline``, or ``stopped`` with why, an app that could not be
    reached or used."""

    baseline: Released
    candidate: Released
    sample: int
    verdict: Verdict = "compared"
    why: str | None = None
    listed: int = 0
    fields: SharedFields | None = None
    quotes: tuple[QuoteImpact, ...] = ()
    timed_out: bool = False

    def count(self, outcome: Outcome) -> int:
        return sum(1 for quote in self.quotes if quote.outcome == outcome)

    @property
    def failed(self) -> tuple[QuoteImpact, ...]:
        """The quotes staging failed on, which fail the run."""
        return tuple(quote for quote in self.quotes if quote.outcome == "failed")

    @property
    def passed(self) -> bool:
        """Whether the run passes: it did not stop and staging failed on no quote."""
        return self.verdict != "stopped" and not self.failed


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _body(answer: HttpAnswer) -> dict[str, Any]:
    try:
        payload = json.loads(answer.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _reason(answer: HttpAnswer) -> str:
    """Why an app answered as it did, for a failure of the app's own (never a quote's): its
    status and the reason it gave."""
    if answer.status == 0:
        return f"no answer: {answer.body.decode('utf-8', 'replace')}"
    detail = _body(answer).get("detail")
    if isinstance(detail, Mapping):
        detail = detail.get("message")
    return f"HTTP {answer.status}: {detail}" if isinstance(detail, str) else f"HTTP {answer.status}"


def _quote_reason(answer: HttpAnswer) -> str:
    """Why an app refused or failed a quote, said without the app's own words, which may
    quote the values typed: its status and what the status means."""
    return f"HTTP {answer.status}: " + _QUOTE_FAILURES.get(answer.status, "the app's own error")


class _StoppedError(WorkbenchError):
    """An app that stopped answering as the impact needs, mid-run."""


class _OutOfTimeError(Exception):
    """The run's deadline passed before a request or a wait."""


@dataclass(slots=True)
class _Budget:
    """The run's deadline, checked before each request and each wait."""

    clock: Clock
    deadline: float

    def check(self) -> None:
        """:class:`_OutOfTimeError` once the deadline has passed: asked before each of
        Azure's reads and tokens, which are bounded on their own."""
        if self.clock.now() >= self.deadline:
            raise _OutOfTimeError

    def ask(self, call: Callable[[], HttpAnswer]) -> HttpAnswer:
        """*call*'s answer, asked again after a busy app's wait (503), :data:`BUSY_TRIES`
        tries in all; :class:`_OutOfTimeError` when the deadline passes before a try or would
        pass during a wait."""
        for attempt in range(BUSY_TRIES):
            if self.clock.now() >= self.deadline:
                raise _OutOfTimeError
            answer = call()
            if answer.status != 503 or attempt == BUSY_TRIES - 1:
                return answer
            wait = _wait(answer.retry_after)
            if self.clock.now() + wait >= self.deadline:
                raise _OutOfTimeError
            self.clock.sleep(wait)
        raise AssertionError("unreachable")


def _wait(retry_after: str | None) -> float:
    """A busy app's ``Retry-After`` in seconds, :data:`_BUSY_WAIT_SECONDS` without one, at most
    :data:`_MAX_BUSY_WAIT_SECONDS`."""
    try:
        seconds = float(retry_after) if retry_after is not None else _BUSY_WAIT_SECONDS
    except ValueError:
        seconds = _BUSY_WAIT_SECONDS
    if not math.isfinite(seconds) or seconds < 0:
        seconds = _BUSY_WAIT_SECONDS
    return min(seconds, _MAX_BUSY_WAIT_SECONDS)


def _side(environment: str, host: AppReader, state: AppState, get: Fetch, budget: _Budget) -> Side:
    """*environment*'s app as the impact reaches it, signed in, its quotes open; refused,
    naming the environment, when it cannot be."""
    if state.address is None or state.client_id is None:
        raise ImpactError(
            f"{environment}'s app takes no requests at a public address signed in through "
            "Microsoft Entra ID: the impact prices quotes through it."
        )
    budget.check()
    signed = {"Authorization": f"Bearer {host.token(state.client_id)}"}
    base = f"https://{state.address}"
    release = budget.ask(lambda: get(f"{base}/api/app/release", signed))
    if release.status != 200:
        raise ImpactError(
            f"Signed in as the CI service principal, {environment}'s app answered "
            f"{_reason(release)}."
        )
    body = _body(release)
    if not _named(body.get("id")) or not _named(body.get("form_revision")):
        raise ImpactError(
            f"{environment}'s app answered its release without an id and a form revision."
        )
    quotes = body.get("quotes")
    if not isinstance(quotes, Mapping) or quotes.get("open") is not True:
        message = quotes.get("message") if isinstance(quotes, Mapping) else None
        raise ImpactError(
            f"{environment}'s app has its quotes closed: {message or 'no reason given'}"
        )
    form = budget.ask(lambda: get(f"{base}/api/app/form", signed))
    document = _body(form).get("form")
    if form.status != 200 or not isinstance(document, Mapping):
        raise ImpactError(f"{environment}'s app answered its form with {_reason(form)}.")
    recorded = state.recorded
    return Side(
        environment=environment,
        address=state.address,
        token=signed["Authorization"].removeprefix("Bearer "),
        release=body["id"],
        form_revision=body["form_revision"],
        form=document,
        commit=recorded.commit if recorded is not None else None,
    )


def _named(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _listed(
    side: Side, sample: int, get: Fetch, budget: _Budget, listed: list[tuple[str, int]]
) -> None:
    """Production's final quotes, archived ones left out, newest saved first, *sample* at
    most, by reference and option, added to *listed* page by page, so a run that stops keeps
    those listed before it stopped."""
    cursor: str | None = None
    while len(listed) < sample:
        query = {
            "view": "team",
            "stage": "final",
            "sort": "saved",
            "limit": str(min(_PAGE_LIMIT, sample - len(listed))),
        }
        if cursor is not None:
            query["cursor"] = cursor
        url = side.url("/api/app/quotes?" + urllib.parse.urlencode(query))
        answer = budget.ask(lambda url=url: get(url, side.signed()))  # type: ignore[misc]
        if answer.status != 200:
            raise ImpactError(f"prod's app listed its quotes with {_reason(answer)}.")
        body = _body(answer)
        page = body.get("quotes")
        cursor = body.get("next_cursor")
        if not isinstance(page, list) or not (cursor is None or isinstance(cursor, str)):
            raise ImpactError("prod's app answered a page of its quotes without its list.")
        for quote in page:
            reference = quote.get("reference") if isinstance(quote, Mapping) else None
            option = quote.get("option") if isinstance(quote, Mapping) else None
            if not _named(reference) or not isinstance(option, int) or isinstance(option, bool):
                raise ImpactError("prod's app listed a quote without its reference and option.")
            if len(listed) < sample:
                listed.append((reference, option))
        if not cursor:
            break


def _checked(side: Side, answer: HttpAnswer) -> HttpAnswer:
    """*answer*, unless it says *side*'s app, not the quote, is failing: no answer, a sign-in
    refused, another release, or still busy or closed after its tries."""
    if answer.status in _APP_FAILURES or 300 <= answer.status < 400:
        raise _StoppedError(f"{side.environment}'s app answered {_reason(answer)}.")
    return answer


def _priced(side: Side, values: Any, send: Post, budget: _Budget) -> HttpAnswer:
    body = json.dumps(
        {"form_revision": side.form_revision, "release": side.release, "values": values}
    ).encode("utf-8")
    return _checked(side, budget.ask(lambda: send(side.url("/api/app/price"), side.signed(), body)))


def _tables(side: Side, answer: HttpAnswer) -> Mapping[str, Any]:
    """A pricing's tables as the price route answers them, each a row or a list of rows; a
    200 that holds none stops the run, rather than reading as every value left empty."""
    tables = _body(answer).get("tables")
    if isinstance(tables, Mapping) and all(
        rows is None
        or isinstance(rows, Mapping)
        or (isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows))
        for rows in tables.values()
    ):
        return tables
    raise _StoppedError(f"{side.environment}'s app answered a pricing without its tables.")


def _quote(
    reference: str,
    option: int,
    baseline: Side,
    candidate: Side,
    fields: SharedFields,
    get: Fetch,
    send: Post,
    budget: _Budget,
) -> QuoteImpact:
    """One quote opened on production, priced on both apps and compared."""
    path = "/api/app/quotes/{}/{}".format(urllib.parse.quote(reference, safe=""), option)
    opened = _checked(baseline, budget.ask(lambda: get(baseline.url(path), baseline.signed())))
    if opened.status == 404:
        return QuoteImpact(reference, option, "not_compared", reason="gone since it was listed")
    if opened.status != 200:
        return QuoteImpact(reference, option, "baseline_failed", reason=_quote_reason(opened))
    values = _body(opened).get("values")
    if not isinstance(values, Mapping):
        raise _StoppedError("prod's app answered a quote it opened without its values.")
    before = _priced(baseline, values, send, budget)
    if before.status != 200:
        return QuoteImpact(reference, option, "baseline_failed", reason=_quote_reason(before))
    # Production's answer is held to its shape before staging is asked: whatever staging
    # answers, a malformed baseline stops the run.
    baseline_tables = _tables(baseline, before)
    after = _priced(candidate, values, send, budget)
    if after.status == 422:
        return QuoteImpact(reference, option, "refused", reason=_quote_reason(after))
    if after.status != 200:
        return QuoteImpact(reference, option, "failed", reason=_quote_reason(after))
    compared = compare_quote(
        fields, baseline_tables, _tables(candidate, after), sides=("on production", "on staging")
    )
    if compared.why is not None:
        return QuoteImpact(reference, option, "no_longer_compares", reason=compared.why)
    outcome: Outcome = "moved" if compared.moves else "unchanged"
    return QuoteImpact(reference, option, outcome, moves=compared.moves)


def _post_pricing(url: str, headers: Mapping[str, str], body: bytes) -> HttpAnswer:
    """A pricing's POST, given longer than the app's own bound on a pricing, so the app's
    504 arrives before the request gives up."""
    return rollouts.post(url, headers, body, timeout=PRICE_REQUEST_SECONDS)


@dataclass(slots=True)
class _Run:
    """What a run has found so far, which a run that stops still reports."""

    sample: int
    baseline: Released = Released("prod", None, None)
    candidate: Released = Released("staging", None, None)
    verdict: Verdict = "compared"
    why: str | None = None
    listed: list[tuple[str, int]] = field(default_factory=list)
    fields: SharedFields | None = None
    #: Whether the sample is listed whole: time running out before is a stop.
    listed_all: bool = False
    quotes: list[QuoteImpact] = field(default_factory=list)
    timed_out: bool = False

    def report(self) -> ImpactReport:
        reached = len(self.quotes)
        left = tuple(
            QuoteImpact(
                reference,
                option,
                "not_compared",
                reason="the impact ran out of time" if self.timed_out else "the run stopped",
            )
            for reference, option in self.listed[reached:]
        )
        return ImpactReport(
            baseline=self.baseline,
            candidate=self.candidate,
            sample=self.sample,
            verdict=self.verdict,
            why=self.why,
            listed=len(self.listed),
            fields=self.fields,
            quotes=(*self.quotes, *left),
            timed_out=self.timed_out,
        )


def impact(
    baseline: AppReader,
    candidate: AppReader,
    *,
    sample: int,
    say: Callable[[str], None],
    get: Fetch | None = None,
    send: Post | None = None,
    clock: Clock = _CLOCK,
) -> ImpactReport:
    """Production's app (*baseline*) and staging's (*candidate*) compared on up to *sample*
    of production's final quotes (specs/workbench, Control flow 36). Never raises for an app:
    one that cannot be reached or used stops the run, which the report's ``verdict`` says."""
    run = _Run(sample)
    budget = _Budget(clock, clock.now() + IMPACT_TIMEOUT_SECONDS)
    # The transport is looked up when the run starts, the rollout's own unless given.
    get = rollouts.fetch if get is None else get
    send = _post_pricing if send is None else send
    try:
        _measure(run, baseline, candidate, say, get, send, budget)
    except _OutOfTimeError:
        if run.verdict == "compared" and run.listed_all:
            run.timed_out = True
        else:
            run.verdict, run.why = "stopped", "the impact ran out of time before it priced a quote"
    except WorkbenchError as error:
        run.verdict, run.why = "stopped", str(error)
    return run.report()


def _measure(
    run: _Run,
    baseline: AppReader,
    candidate: AppReader,
    say: Callable[[str], None],
    get: Fetch,
    send: Post,
    budget: _Budget,
) -> None:
    budget.check()
    production_state = baseline.state()
    budget.check()
    staging = _side("staging", candidate, candidate.state(), get, budget)
    run.candidate = Released("staging", staging.release, staging.commit)
    say(f"  ✓ staging runs release {staging.release}")
    if RELEASE_TAG not in production_state.tag_names:
        run.verdict = "no_baseline"
        run.why = "production records no release: it has never been rolled out"
        return
    if production_state.recorded is None:
        raise ImpactError(
            "prod's app records its release incompletely: haute app rollout sets its "
            + ", ".join(RECORD_TAGS.values())
            + " tags together, so restore them or roll prod out again."
        )
    production = _side("prod", baseline, production_state, get, budget)
    run.baseline = Released("prod", production.release, production.commit)
    say(f"  ✓ prod runs release {production.release}")
    if production.release == staging.release:
        run.verdict = "same_release"
        return
    run.fields = fields = shared_fields(production.form, staging.form)
    _listed(production, run.sample, get, budget, run.listed)
    run.listed_all = True
    if not run.listed:
        run.verdict, run.why = "no_baseline", "production holds no final quote"
        return
    say(f"  ✓ {len(run.listed)} of prod's final quotes listed")
    for reference, option in run.listed:
        run.quotes.append(_quote(reference, option, production, staging, fields, get, send, budget))
    priced = sum(1 for quote in run.quotes if quote.outcome in _PRICED_ON_BOTH)
    say(f"  ✓ {priced} quotes priced on both apps")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    """A value as a report's table cell holds it."""
    if value is None:
        text = "empty"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = f"{value:,}"
    elif isinstance(value, float):
        text = f"{value:,.2f}" if value == 0 or abs(value) >= 1 else f"{value:.6g}"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _change(before: Any, after: Any) -> tuple[str, str]:
    """A number's move as its difference and as a percentage of production's."""
    if not (is_number(before) and is_number(after)):
        return "", ""
    difference = after - before
    percent = "" if before == 0 else f"{difference / abs(before) * 100:+.2f}%"
    return ("+" if difference > 0 else "") + _cell(difference), percent


def _percent(move: Move) -> float | None:
    if is_number(move.before) and is_number(move.after) and move.before != 0:
        return float((move.after - move.before) / abs(move.before) * 100)
    return None


def _field(move: Move) -> str:
    return move.table if move.column is None else f"{move.table}.{move.column}"


def _released(side: Released, title: str) -> str:
    release = f"`{side.release}`" if side.release else "none"
    commit = f"`{side.commit[:12]}`" if side.commit else "unrecorded"
    return f"| {title} | {release} | {commit} |"


#: The outcomes of a quote priced on both apps.
_PRICED_ON_BOTH = frozenset({"moved", "unchanged", "no_longer_compares", "refused", "failed"})


def render_report(report: ImpactReport) -> str:
    """The report as Markdown: quotes named by reference and option alone, no input value."""
    lines = [
        "# Impact on saved quotes",
        "",
        "| App | Release | Commit |",
        "|---|---|---|",
        _released(report.baseline, "Production (now)"),
        _released(report.candidate, "Staging (to approve)"),
        "",
    ]
    if report.verdict == "same_release":
        lines.append("Staging and production run the same release: nothing was priced.")
        return "\n".join(lines) + "\n"
    if report.verdict == "no_baseline":
        lines += [
            f"No baseline: {report.why}. Nothing was compared; the release answers only for "
            "the test quotes `haute app build` scored in the validate job.",
        ]
        return "\n".join(lines) + "\n"
    if report.verdict == "stopped":
        lines += [f"**The run stopped, and fails:** {report.why}", ""]
    if report.failed:
        lines += ["**The run fails:** staging failed on a quote production prices.", ""]
    priced = sum(1 for quote in report.quotes if quote.outcome in _PRICED_ON_BOTH)
    lines.append(
        f"Sample: up to {report.sample:,} of production's final quotes, newest saved first; "
        f"{report.listed:,} listed, {priced:,} priced on both apps."
    )
    if report.timed_out:
        lines.append(
            f"The impact ran out of time after {IMPACT_TIMEOUT_SECONDS // 60} minutes: the "
            "quotes left are not compared."
        )
    lines.append("")
    fields = report.fields
    if fields is not None:
        lines += ["## Fields only one release has", ""]
        if not (fields.added or fields.removed or fields.reshaped):
            lines.append("The two releases have the same output fields.")
        for title, names in (
            ("Added in staging", fields.added),
            ("Removed in staging", fields.removed),
            ("One row per quote in one release and many in the other", fields.reshaped),
        ):
            if names:
                lines.append(f"- {title}: " + ", ".join(f"`{name}`" for name in names))
        lines.append("")
    lines += ["## Outcomes", "", "| Outcome | Quotes |", "|---|---|"]
    for outcome, title in (
        ("moved", "Moved"),
        ("unchanged", "Unchanged"),
        ("no_longer_compares", "No longer compare"),
        ("refused", "Refused by the new release"),
        ("failed", "Staging failed on them"),
        ("baseline_failed", "Production could not price them"),
        ("not_compared", "Not compared"),
    ):
        lines.append(f"| {title} | {report.count(outcome):,} |")  # type: ignore[arg-type]
    lines.append("")
    _field_summary(report, lines)
    _moved_quotes(report, lines)
    for outcome, title in (
        ("no_longer_compares", "Quotes that no longer compare"),
        ("refused", "Quotes the new release refuses"),
        ("failed", "Quotes staging failed on"),
        ("baseline_failed", "Quotes production could not price"),
        ("not_compared", "Quotes not compared"),
    ):
        named = [quote for quote in report.quotes if quote.outcome == outcome]
        if named:
            lines += [f"## {title}", ""]
            lines += [f"- `{quote.label}`: {_cell(quote.reason)}" for quote in named]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _field_summary(report: ImpactReport, lines: list[str]) -> None:
    quotes_by_field: dict[str, set[str]] = {}
    percents: dict[str, list[float]] = {}
    for quote in report.quotes:
        for move in quote.moves:
            name = _field(move)
            quotes_by_field.setdefault(name, set()).add(quote.label)
            percent = _percent(move)
            if percent is not None:
                percents.setdefault(name, []).append(percent)
    if not quotes_by_field:
        return
    lines += [
        "## Fields that moved",
        "",
        "| Field | Quotes | Largest rise | Largest fall |",
        "|---|---|---|---|",
    ]
    for name, labels in quotes_by_field.items():
        found = percents.get(name, [])
        rises = [each for each in found if each > 0]
        falls = [each for each in found if each < 0]
        rise = f"{max(rises):+.2f}%" if rises else ""
        fall = f"{min(falls):+.2f}%" if falls else ""
        lines.append(f"| `{name}` | {len(labels):,} | {rise} | {fall} |")
    lines.append("")


def _moved_quotes(report: ImpactReport, lines: list[str]) -> None:
    moving = [quote for quote in report.quotes if quote.outcome == "moved"]
    if not moving:
        return
    lines += ["## Quotes that moved", ""]
    for quote in moving:
        lines += [
            f"### `{quote.label}`",
            "",
            "| Field | Row | Production | Staging | Change | Change % |",
            "|---|---|---|---|---|---|",
        ]
        for move in quote.moves:
            row = _cell(move.row) if move.row is not None else ""
            if move.column is None:
                held = "row added" if move.after else "row removed"
                lines.append(f"| `{move.table}` | {row} | {held} | | | |")
                continue
            difference, percent = _change(move.before, move.after)
            lines.append(
                f"| `{_field(move)}` | {row} | {_cell(move.before)} | {_cell(move.after)} "
                f"| {difference} | {percent} |"
            )
        lines.append("")
