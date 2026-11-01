"""``haute app impact``: staging's release compared with production's on production's saved
quotes (specs/workbench, Control flow 36), against fake hosts and fake apps, and once through
two real apps serving releases of different forms."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from haute import _workbench_impact as impacts
from haute import _workbench_rollout as rollouts
from haute._workbench_config import AppEnvironment
from haute._workbench_impact import (
    IMPACT_TIMEOUT_SECONDS,
    ImpactReport,
    Move,
    SharedFields,
    SharedTable,
    compare_quote,
    impact,
    moved,
    render_report,
    shared_fields,
)
from haute._workbench_rollout import AppState, Clock, HttpAnswer

COMMIT = "c0ffee" + "0" * 34
PROD = "motor-prod.example.azurecontainerapps.io"
STAGING = "motor-staging.example.azurecontainerapps.io"


# ── Fakes ─────────────────────────────────────────────────────────────────


def _table(
    name: str, columns: list[str], *, rows: str = "one", keys: tuple[str, ...] = ()
) -> dict[str, Any]:
    """An output table as a form document holds it."""
    return {
        "id": f"t_{name}",
        "name": name,
        "role": "output",
        "rows": rows,
        "columns": [
            {"id": f"c_{column}", "name": column, "type": "float", "key": column in keys}
            for column in columns
        ],
    }


def _form(*tables: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "id": "t1",
        "name": "policy_details",
        "role": "input",
        "rows": "one",
        "columns": [{"id": "c_exposure", "name": "exposure", "type": "float"}],
    }
    return {"version": 1, "name": "x", "schema": {"tables": [policy, *tables]}}


PRICING = _table("pricing_output", ["premium"])


def _ok(body: Mapping[str, Any]) -> HttpAnswer:
    return HttpAnswer(200, json.dumps(body).encode("utf-8"))


def _refused(status: int, detail: Any = None, retry_after: str | None = None) -> HttpAnswer:
    body = json.dumps({"detail": detail}).encode("utf-8") if detail is not None else b""
    return HttpAnswer(status, body, retry_after=retry_after)


@dataclass
class FakeApp:
    """A hosted app as the impact reaches it: its release, its form, the final quotes it
    lists newest first, each quote's values, and how it prices values."""

    release: str
    form: dict[str, Any] = field(default_factory=lambda: _form(PRICING))
    form_revision: str = ""
    quotes: list[tuple[str, int]] = field(default_factory=list)
    values: dict[tuple[str, int], Any] = field(default_factory=dict)
    #: Values to the answer's tables; the default prices half the exposure.
    pricing: Callable[[Any], HttpAnswer] | None = None
    #: Answers queued per path, taken before the app's own.
    queued: dict[str, list[HttpAnswer]] = field(default_factory=dict)
    quotes_open: bool = True
    asked: list[tuple[str, str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.form_revision = self.form_revision or f"rev-{self.release}"

    def answer(self, method: str, path: str, body: Any) -> HttpAnswer:
        self.asked.append((method, path, body))
        route = urllib.parse.urlsplit(path).path
        if self.queued.get(route):
            return self.queued[route].pop(0)
        if route == "/api/app/release":
            return _ok(
                {
                    "id": self.release,
                    "form_revision": self.form_revision,
                    "quotes": {"open": self.quotes_open, "message": "the tables do not fit"},
                }
            )
        if route == "/api/app/form":
            return _ok({"form": self.form, "revision": self.form_revision})
        if route == "/api/app/quotes":
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))
            start = int(query.get("cursor", "0"))
            end = start + int(query["limit"])
            page = self.quotes[start:end]
            return _ok(
                {
                    "quotes": [{"reference": r, "option": o} for r, o in page],
                    "next_cursor": str(end) if end < len(self.quotes) else None,
                    "counts": {},
                }
            )
        if route.startswith("/api/app/quotes/"):
            _, _, _, _, reference, option = route.split("/")
            key = (urllib.parse.unquote(reference), int(option))
            if key not in self.values:
                return _refused(404, {"message": "no such quote"})
            return _ok({"values": self.values[key]})
        if route == "/api/app/price":
            assert method == "POST"
            if body["release"] != self.release or body["form_revision"] != self.form_revision:
                return _refused(409, "This page was drawn for another release of the app")
            if self.pricing is not None:
                return self.pricing(body["values"])
            exposure = body["values"]["t1"][0]["c_exposure"]
            return _ok({"tables": {"pricing_output": [{"premium": exposure / 2}]}, "request": {}})
        raise AssertionError(f"unexpected request {method} {path}")

    def priced(self) -> list[Any]:
        """The values each pricing request carried, in order."""
        return [body["values"] for method, _, body in self.asked if method == "POST"]


@dataclass
class Apps:
    """The two apps behind one transport, by address, each request signed in."""

    prod: FakeApp
    staging: FakeApp

    def _app(self, url: str, headers: Mapping[str, str]) -> tuple[FakeApp, str]:
        parts = urllib.parse.urlsplit(url)
        app = {PROD: self.prod, STAGING: self.staging}[parts.netloc]
        assert parts.scheme == "https"
        assert headers["Authorization"] == f"Bearer token-for-{parts.netloc}"
        return app, parts.path + (f"?{parts.query}" if parts.query else "")

    def get(self, url: str, headers: Mapping[str, str]) -> HttpAnswer:
        app, path = self._app(url, headers)
        return app.answer("GET", path, None)

    def send(self, url: str, headers: Mapping[str, str], body: bytes) -> HttpAnswer:
        app, path = self._app(url, headers)
        return app.answer("POST", path, json.loads(body))


class FakeHost:
    """A Container App as the impact reads it: its address, its Entra ID client, its tags."""

    def __init__(
        self, address: str, *, recorded: bool = True, tags: dict[str, str] | None = None
    ) -> None:
        self.address = address
        self.client_id: str | None = address
        self.tags = (
            tags
            if tags is not None
            else (
                {
                    "haute-image": "img@sha256:" + "0" * 64,
                    "haute-release": "x",
                    "haute-commit": COMMIT,
                }
                if recorded
                else {}
            )
        )
        self.tokens = 0

    def state(self) -> AppState:
        return AppState(
            single_revision=True,
            images=(),
            address=self.address,
            latest_revision=None,
            active_revisions=(),
            client_id=self.client_id,
            tag_names=frozenset(self.tags),
            recorded=rollouts._recorded(self.tags),
        )

    def token(self, client_id: str) -> str:
        self.tokens += 1
        return f"token-for-{client_id}"


class FakeClock:
    """Time that passes as the run sleeps, and as each request takes *per_request* seconds."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def clock(self) -> Clock:
        return Clock(now=lambda: self.t, sleep=self._sleep)

    def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _values(exposure: float) -> dict[str, Any]:
    return {"t1": [{"c_exposure": exposure}], "quote_details": [{"qd_insured": "Acme Ltd"}]}


def _apps(count: int = 2, **staging: Any) -> Apps:
    """Production on release p1 with *count* final quotes, staging on release s1 pricing a
    quarter of the exposure."""
    quotes = [(f"Q-{n:06d}", 1) for n in range(1, count + 1)]
    prod = FakeApp(
        "p1",
        quotes=quotes,
        values={quote: _values(1000.0 * n) for n, quote in enumerate(quotes, 1)},
    )
    staging.setdefault(
        "pricing",
        lambda values: _ok(
            {"tables": {"pricing_output": [{"premium": values["t1"][0]["c_exposure"] / 4}]}}
        ),
    )
    return Apps(prod, FakeApp("s1", **staging))


def _run(
    apps: Apps,
    *,
    sample: int = 100,
    prod: FakeHost | None = None,
    staging: FakeHost | None = None,
    clock: FakeClock | None = None,
) -> ImpactReport:
    return impact(
        prod or FakeHost(PROD),
        staging or FakeHost(STAGING),
        sample=sample,
        say=lambda _: None,
        get=apps.get,
        send=apps.send,
        clock=(clock or FakeClock()).clock(),
    )


# ── Baselines ─────────────────────────────────────────────────────────────


def test_production_never_rolled_out_has_no_baseline_and_is_not_reached() -> None:
    apps = _apps()

    report = _run(apps, prod=FakeHost(PROD, recorded=False))

    assert (report.verdict, report.passed) == ("no_baseline", True)
    assert "never been rolled out" in str(report.why)
    assert apps.prod.asked == []
    assert report.candidate.release == "s1"
    text = render_report(report)
    assert "No baseline" in text
    assert "the test quotes `haute app build` scored" in text


def test_an_incomplete_record_on_production_stops_the_run() -> None:
    apps = _apps()
    host = FakeHost(PROD, tags={"haute-release": "p1"})

    report = _run(apps, prod=host)

    assert (report.verdict, report.passed) == ("stopped", False)
    assert "records its release incompletely" in str(report.why)
    assert apps.prod.asked == []


def test_the_same_release_on_both_prices_nothing() -> None:
    apps = _apps()
    apps.staging.release = "p1"
    apps.staging.form_revision = apps.prod.form_revision

    report = _run(apps)

    assert (report.verdict, report.passed) == ("same_release", True)
    assert apps.prod.priced() == [] and apps.staging.priced() == []
    assert "the same release" in render_report(report)


def test_production_holding_no_final_quote_has_no_baseline() -> None:
    report = _run(_apps(count=0))

    assert (report.verdict, report.why) == ("no_baseline", "production holds no final quote")


@pytest.mark.parametrize("which", ["prod", "staging"])
def test_an_app_with_its_quotes_closed_stops_the_run(which: str) -> None:
    apps = _apps()
    getattr(apps, which).quotes_open = False

    report = _run(apps)

    assert (report.verdict, report.passed) == ("stopped", False)
    assert f"{which}'s app has its quotes closed: the tables do not fit" == report.why


def test_an_app_without_a_sign_in_stops_the_run() -> None:
    host = FakeHost(STAGING)
    host.client_id = None

    report = _run(_apps(), staging=host)

    assert report.verdict == "stopped"
    assert "staging's app takes no requests" in str(report.why)


# ── The sample ────────────────────────────────────────────────────────────


def test_the_sample_is_production_final_quotes_listed_across_pages_and_cut() -> None:
    apps = _apps(count=300)

    report = _run(apps, sample=250)

    listed = [
        dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))
        for method, path, _ in apps.prod.asked
        if path.startswith("/api/app/quotes?")
    ]
    assert listed == [
        {"view": "team", "stage": "final", "sort": "saved", "limit": "200"},
        {"view": "team", "stage": "final", "sort": "saved", "limit": "50", "cursor": "200"},
    ]
    assert (report.listed, len(report.quotes)) == (250, 250)
    assert report.quotes[0].label == "Q-000001/1"


def test_each_app_prices_the_values_unchanged_with_its_own_release() -> None:
    apps = _apps(count=1)

    report = _run(apps)

    assert apps.prod.priced() == apps.staging.priced() == [_values(1000.0)]
    for app in (apps.prod, apps.staging):
        [body] = [body for method, _, body in app.asked if method == "POST"]
        assert (body["release"], body["form_revision"]) == (app.release, app.form_revision)
    assert [quote.outcome for quote in report.quotes] == ["moved"]


def test_a_busy_app_is_asked_again_after_a_capped_wait() -> None:
    apps = _apps(count=1)
    apps.staging.queued["/api/app/price"] = [_refused(503, "busy", retry_after="60")]
    clock = FakeClock()

    report = _run(apps, clock=clock)

    assert clock.slept == [10]
    assert report.quotes[0].outcome == "moved"


def test_an_app_still_busy_after_its_tries_stops_the_run() -> None:
    apps = _apps(count=2)
    apps.staging.queued["/api/app/price"] = [_refused(503, "busy")] * impacts.BUSY_TRIES
    clock = FakeClock()

    report = _run(apps, clock=clock)

    assert clock.slept == [5, 5]
    assert (report.verdict, report.passed) == ("stopped", False)
    assert "staging's app answered HTTP 503: busy" in str(report.why)
    assert [quote.outcome for quote in report.quotes] == ["not_compared"] * 2


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (HttpAnswer(0, b"timed out"), "no answer: timed out"),
        (_refused(401, "Sign in"), "HTTP 401: Sign in"),
        (_refused(409, "drawn for another release"), "HTTP 409: drawn for another release"),
        (HttpAnswer(302, b"", "/.auth/login/aad"), "HTTP 302"),
        (_refused(503, "busy"), "HTTP 503: busy"),
    ],
    ids=["no-answer", "sign-in-refused", "release-changed", "sign-in-redirect", "busy"],
)
@pytest.mark.parametrize("route", ["/api/app/quotes/Q-000002/1", "/api/app/price"])
def test_production_failing_mid_run_stops_the_run_and_the_report_is_written(
    answer: HttpAnswer, said: str, route: str
) -> None:
    apps = _apps(count=3)
    # A busy answer stops the run only once it is still busy after its tries.
    failing = [answer] * (impacts.BUSY_TRIES if answer.status == 503 else 1)
    if route == "/api/app/price":
        # The first quote's pricing answers; the second's fails.
        first = apps.prod.answer(
            "POST", route, {"release": "p1", "form_revision": "rev-p1", "values": _values(1.0)}
        )
        apps.prod.asked.clear()
        apps.prod.queued[route] = [first, *failing]
    else:
        apps.prod.queued[route] = failing

    report = _run(apps)

    assert (report.verdict, report.passed) == ("stopped", False)
    assert report.why == f"prod's app answered {said}."
    assert [quote.outcome for quote in report.quotes] == ["moved", "not_compared", "not_compared"]
    text = render_report(report)
    assert "**The run stopped, and fails:**" in text
    assert "`Q-000003/1`: the run stopped" in text


def test_quotes_listed_before_a_later_page_fails_are_kept_not_compared() -> None:
    apps = _apps(count=300)
    query = "/api/app/quotes?view=team&stage=final&sort=saved&limit=200"
    first = apps.prod.answer("GET", query, None)
    apps.prod.asked.clear()
    apps.prod.queued["/api/app/quotes"] = [first, _refused(500, "boom")]

    report = _run(apps, sample=250)

    assert (report.verdict, report.listed) == ("stopped", 200)
    assert {quote.outcome for quote in report.quotes} == {"not_compared"}
    assert len(report.quotes) == 200
    assert apps.prod.priced() == []


@pytest.mark.parametrize(
    "page",
    [
        {"quotes": "Q-000001"},
        {"quotes": [{"reference": "Q-000001"}]},
        {"quotes": [{"reference": "Q-000001", "option": True}]},
        {"quotes": [], "next_cursor": 200},
    ],
    ids=["no-list", "no-option", "option-not-a-number", "cursor-not-text"],
)
def test_a_page_of_quotes_in_an_unknown_shape_stops_the_run(page: dict[str, Any]) -> None:
    apps = _apps()
    apps.prod.queued["/api/app/quotes"] = [_ok(page)]

    report = _run(apps)

    assert report.verdict == "stopped"
    assert "prod's app" in str(report.why)


@pytest.mark.parametrize(
    "answer",
    [
        HttpAnswer(200, b"<html>signed out</html>"),
        _ok({"request": {}}),
        _ok({"tables": []}),
        _ok({"tables": {"pricing_output": "500"}}),
        _ok({"tables": {"pricing_output": [500.0]}}),
    ],
    ids=["not-json", "no-tables", "tables-a-list", "rows-text", "row-not-a-row"],
)
@pytest.mark.parametrize("which", ["prod", "staging"])
def test_a_pricing_answered_without_its_tables_stops_the_run(
    answer: HttpAnswer, which: str
) -> None:
    apps = _apps(count=2)
    getattr(apps, which).queued["/api/app/price"] = [answer]

    report = _run(apps)

    assert (report.verdict, report.passed) == ("stopped", False)
    assert report.why == f"{which}'s app answered a pricing without its tables."
    assert [quote.outcome for quote in report.quotes] == ["not_compared"] * 2


@pytest.mark.parametrize(
    "staged", [_refused(422, "values refused"), _refused(500, "boom")], ids=["refused", "failed"]
)
def test_a_malformed_production_pricing_stops_the_run_before_staging_is_asked(
    staged: HttpAnswer,
) -> None:
    apps = _apps(count=1)
    apps.prod.queued["/api/app/price"] = [_ok({})]
    apps.staging.queued["/api/app/price"] = [staged]

    report = _run(apps)

    assert (report.verdict, report.passed) == ("stopped", False)
    assert report.why == "prod's app answered a pricing without its tables."
    assert apps.staging.priced() == []


def test_a_quote_opened_without_its_values_stops_the_run() -> None:
    apps = _apps(count=1)
    apps.prod.queued["/api/app/quotes/Q-000001/1"] = [_ok({"quote": {}})]

    report = _run(apps)

    assert report.verdict == "stopped"
    assert report.why == "prod's app answered a quote it opened without its values."
    assert apps.prod.priced() == []


def test_a_release_answered_without_its_id_stops_the_run() -> None:
    apps = _apps()
    apps.staging.queued["/api/app/release"] = [_ok({"quotes": {"open": True}})]

    report = _run(apps)

    assert report.verdict == "stopped"
    assert report.why == "staging's app answered its release without an id and a form revision."


def test_a_quotes_page_production_does_not_answer_stops_the_run() -> None:
    apps = _apps()
    apps.prod.queued["/api/app/quotes"] = [_refused(500, "boom")]

    report = _run(apps)

    assert report.verdict == "stopped"
    assert "listed its quotes with HTTP 500" in str(report.why)


# ── Outcomes of a quote ───────────────────────────────────────────────────

DISTINCTIVE = "ZEBRA-CROSSING-9931"


def test_each_quote_outcome_is_named_and_a_staging_failure_fails_the_run() -> None:
    apps = _apps(count=5)
    refusal = _refused(422, f"exposure: {DISTINCTIVE!r} is not a number")
    apps.prod.values.pop(("Q-000005", 1))
    apps.prod.queued["/api/app/price"] = [
        _ok({"tables": {"pricing_output": [{"premium": 500.0}]}}),
        _refused(500, f"The pipeline failed on this quote: {DISTINCTIVE}"),
        _ok({"tables": {"pricing_output": [{"premium": 1500.0}]}}),
        _ok({"tables": {"pricing_output": [{"premium": 2000.0}]}}),
    ]
    apps.staging.queued["/api/app/price"] = [
        _ok({"tables": {"pricing_output": [{"premium": 500.0}]}}),
        refusal,
        _refused(500, f"The pipeline failed on this quote: {DISTINCTIVE}"),
    ]

    report = _run(apps)

    assert [(quote.label, quote.outcome) for quote in report.quotes] == [
        ("Q-000001/1", "unchanged"),
        ("Q-000002/1", "baseline_failed"),
        ("Q-000003/1", "refused"),
        ("Q-000004/1", "failed"),
        ("Q-000005/1", "not_compared"),
    ]
    assert report.passed is False
    assert report.verdict == "compared"
    assert [quote.label for quote in report.failed] == ["Q-000004/1"]
    text = render_report(report)
    assert "**The run fails:** staging failed on a quote production prices." in text
    assert "`Q-000003/1`: HTTP 422: the release refuses the quote's values" in text
    assert "`Q-000004/1`: HTTP 500: the pipeline failed on the quote" in text
    assert "`Q-000002/1`: HTTP 500: the pipeline failed on the quote" in text
    assert "`Q-000005/1`: gone since it was listed" in text
    # No app's own words, which may quote the values typed, and no input value.
    assert DISTINCTIVE not in text
    assert "Acme" not in text and "1,000" not in text


# ── The deadline ──────────────────────────────────────────────────────────


def test_time_running_out_during_a_request_leaves_the_rest_not_compared() -> None:
    apps = _apps(count=4)
    clock = FakeClock()
    staged = apps.staging.pricing
    assert staged is not None

    def slow(values: Any) -> HttpAnswer:
        clock.t += 350
        return staged(values)

    apps.staging.pricing = slow
    apps.staging.queued["/api/app/price"] = [_refused(500, "boom")]

    report = _run(apps, clock=clock)

    assert report.timed_out and report.verdict == "compared"
    assert [quote.outcome for quote in report.quotes] == [
        "failed",
        "moved",
        "moved",
        "not_compared",
    ]
    # The earlier failure still fails the run.
    assert report.passed is False
    assert "ran out of time" in render_report(report)


def test_a_wait_that_would_pass_the_deadline_is_not_taken() -> None:
    apps = _apps(count=2)
    clock = FakeClock()
    clock.t = 0.0
    apps.staging.queued["/api/app/price"] = [_refused(503, "busy", retry_after="10")]

    def late(values: Any) -> HttpAnswer:
        clock.t = IMPACT_TIMEOUT_SECONDS - 5
        return _ok({"tables": {"pricing_output": [{"premium": 1.0}]}})

    apps.prod.pricing = late

    report = _run(apps, clock=clock)

    assert clock.slept == []
    assert report.timed_out and report.passed
    assert [quote.outcome for quote in report.quotes] == ["not_compared", "not_compared"]


def test_time_running_out_before_the_quotes_are_listed_stops_the_run() -> None:
    apps = _apps()
    clock = FakeClock()
    clock.t = 0.0
    real = apps.prod.answer

    def slow(method: str, path: str, body: Any) -> HttpAnswer:
        if path.startswith("/api/app/form"):
            clock.t = IMPACT_TIMEOUT_SECONDS + 1
        return real(method, path, body)

    apps.prod.answer = slow  # type: ignore[method-assign]

    report = _run(apps, clock=clock)

    assert report.verdict == "stopped" and not report.timed_out
    assert "ran out of time before it priced a quote" in str(report.why)


# ── The fields compared ───────────────────────────────────────────────────


def test_time_running_out_before_productions_token_asks_azure_for_none() -> None:
    apps = _apps()
    clock = FakeClock()
    real = apps.staging.answer

    def slow(method: str, path: str, body: Any) -> HttpAnswer:
        if path.startswith("/api/app/form"):
            clock.t = IMPACT_TIMEOUT_SECONDS
        return real(method, path, body)

    apps.staging.answer = slow  # type: ignore[method-assign]
    production = FakeHost(PROD)

    report = _run(apps, prod=production, clock=clock)

    assert report.verdict == "stopped"
    assert production.tokens == 0
    assert apps.prod.asked == []


def test_shared_fields_are_those_both_releases_name_with_the_same_rows() -> None:
    production = _form(
        _table("pricing_output", ["premium", "commission"]),
        _table("schedule", ["item", "rate"], rows="many", keys=("item",)),
        _table("legacy", ["x"]),
        _table("covers", ["cover", "price"], rows="many", keys=("cover",)),
        _table("one_to_many", ["v"]),
        _table("many_to_one", ["v"], rows="many"),
    )
    candidate = _form(
        _table("pricing_output", ["premium", "fee"]),
        _table("schedule", ["item", "rate", "excess"], rows="many", keys=("item", "rate")),
        _table("covers", ["cover", "price"], rows="many"),
        _table("one_to_many", ["v"], rows="many"),
        _table("many_to_one", ["v"]),
        _table("extras", ["y"]),
    )

    fields = shared_fields(production, candidate)

    assert fields.tables == (
        SharedTable("pricing_output", False, ("premium",), ()),
        # A key marked on one side alone is no key.
        SharedTable("schedule", True, ("item", "rate"), ("item",)),
        SharedTable("covers", True, ("cover", "price"), ()),
    )
    assert fields.added == ("extras", "pricing_output.fee", "schedule.excess")
    assert fields.removed == ("legacy", "pricing_output.commission")
    assert fields.reshaped == ("one_to_many", "many_to_one")


def test_a_reshaped_table_is_listed_once_and_never_compared() -> None:
    fields = shared_fields(
        _form(_table("covers", ["price"], rows="many")), _form(_table("covers", ["price"]))
    )
    # A many-row answer holding one row is still not compared against a one-row table.
    compared = compare_quote(fields, {"covers": [{"price": 1.0}]}, {"covers": [{"price": 2.0}]})

    assert compared.moves == () and compared.why is None
    assert fields.reshaped == ("covers",)


ONE = SharedFields((SharedTable("pricing_output", False, ("premium", "decline"), ()),))
KEYED = SharedFields((SharedTable("schedule", True, ("item", "rate"), ("item",)),))
PLACED = SharedFields((SharedTable("schedule", True, ("item", "rate"), ()),))


def test_a_one_row_table_compares_column_by_column() -> None:
    compared = compare_quote(
        ONE,
        {"pricing_output": [{"premium": 100.0, "decline": False}]},
        {"pricing_output": [{"premium": 110.0, "decline": False}], "other": [{"x": 1}]},
    )

    assert compared.moves == (Move("pricing_output", None, "premium", 100.0, 110.0),)


def test_a_one_row_table_one_side_leaves_out_is_empty() -> None:
    compared = compare_quote(ONE, {}, {"pricing_output": [{"premium": 1.0}]})

    assert compared.moves == (Move("pricing_output", None, "premium", None, 1.0),)


def test_a_many_row_table_matches_rows_by_key_and_names_rows_added_and_removed() -> None:
    compared = compare_quote(
        KEYED,
        {"schedule": [{"item": "a", "rate": 1.0}, {"item": "b", "rate": 2.0}]},
        {"schedule": [{"item": "c", "rate": 3.0}, {"item": "a", "rate": 1.5}]},
    )

    assert compared.moves == (
        Move("schedule", "item=a", "rate", 1.0, 1.5),
        Move("schedule", "item=b", None, True, False),
        Move("schedule", "item=c", None, False, True),
    )


def test_a_numeric_key_matches_whether_it_is_written_whole_or_decimal() -> None:
    compared = compare_quote(
        KEYED,
        {"schedule": [{"item": 1, "rate": 100.0}, {"item": True, "rate": 1.0}]},
        {"schedule": [{"item": 1.0, "rate": 125.0}, {"item": 2, "rate": 1.0}]},
    )

    assert compared.moves == (
        Move("schedule", "item=1", "rate", 100.0, 125.0),
        # True is no number: its row is not the row keyed 1.
        Move("schedule", "item=True", None, True, False),
        Move("schedule", "item=2", None, False, True),
    )


@pytest.mark.parametrize(
    ("before", "after", "why"),
    [
        (
            [{"item": "a", "rate": 1.0}, {"item": "a", "rate": 2.0}],
            [{"item": "a", "rate": 1.0}],
            "schedule on production holds the key item=a twice",
        ),
        (
            [{"item": "a", "rate": 1.0}],
            [{"rate": 1.0}],
            "a row of schedule on staging leaves its key item empty",
        ),
    ],
    ids=["key-twice", "key-empty"],
)
def test_rows_that_cannot_be_matched_no_longer_compare(
    before: list[Any], after: list[Any], why: str
) -> None:
    compared = compare_quote(KEYED, {"schedule": before}, {"schedule": after})

    assert (compared.moves, compared.why) == ((), why)


def test_rows_without_a_shared_key_compare_by_position_when_counts_match() -> None:
    rows = [{"item": "a", "rate": 1.0}, {"item": "b", "rate": 2.0}]

    compared = compare_quote(
        PLACED, {"schedule": rows}, {"schedule": [rows[0], {"item": "b", "rate": 2.5}]}
    )
    unequal = compare_quote(PLACED, {"schedule": rows}, {"schedule": rows[:1]})

    assert compared.moves == (Move("schedule", "row 2", "rate", 2.0, 2.5),)
    assert unequal.why == (
        "schedule has 2 rows on production and 1 on staging, and no key both releases mark "
        "matches them"
    )


@pytest.mark.parametrize(
    ("before", "after", "has_moved"),
    [
        (100.0, 100.0 + 100.0 * 1e-9, False),
        (100.0, 100.0 + 100.0 * 1e-7, True),
        (100, 100.0, False),
        (0, 0.0, False),
        (0, 1e-12, True),
        (True, 1, True),
        (False, 0, True),
        (None, 0, True),
        (None, None, False),
        ("NY", "NY", False),
        ("NY", "CA", True),
        (-50.0, -50.0, False),
    ],
)
def test_a_value_moves_by_the_rule(before: Any, after: Any, has_moved: bool) -> None:
    assert moved(before, after) is has_moved


# ── The report ────────────────────────────────────────────────────────────


def test_the_report_gives_percentages_only_between_numbers_from_a_non_zero_value() -> None:
    fields = SharedFields(
        (SharedTable("pricing_output", False, ("premium", "loading", "note", "credit"), ()),)
    )
    quote = impacts.QuoteImpact(
        "Q-1",
        1,
        "moved",
        moves=compare_quote(
            fields,
            {"pricing_output": [{"premium": 0.0, "loading": -200.0, "note": 5.0, "credit": None}]},
            {"pricing_output": [{"premium": 12.5, "loading": -100.0, "note": "n/a", "credit": 3}]},
        ).moves,
    )
    report = ImpactReport(
        impacts.Released("prod", "p1", COMMIT),
        impacts.Released("staging", "s1", None),
        100,
        listed=1,
        fields=fields,
        quotes=(quote,),
    )

    text = render_report(report)

    # From zero: the difference, no percent. A negative value's size is the denominator.
    assert "| `pricing_output.premium` |  | 0.00 | 12.50 | +12.50 |  |" in text
    assert "| `pricing_output.loading` |  | -200.00 | -100.00 | +100.00 | +50.00% |" in text
    # Across kinds, or from empty: neither.
    assert "| `pricing_output.note` |  | 5.00 | n/a |  |  |" in text
    assert "| `pricing_output.credit` |  | empty | 3 |  |  |" in text
    assert "| `pricing_output.loading` | 1 | +50.00% |  |" in text
    assert "| `pricing_output.premium` | 1 |  |  |" in text
    assert "| Production (now) | `p1` | `c0ffee000000` |" in text
    assert "| Staging (to approve) | `s1` | unrecorded |" in text


def test_the_report_lists_the_fields_only_one_release_has_once() -> None:
    apps = _apps(count=2)
    apps.staging.form = _form(_table("pricing_output", ["premium", "fee"]))

    text = render_report(_run(apps))

    assert text.count("`pricing_output.fee`") == 1
    assert "- Added in staging: `pricing_output.fee`" in text
    assert "| `pricing_output.premium` | 2 |  | -50.00% |" in text


def test_a_cell_keeps_its_table_whole() -> None:
    fields = SharedFields((SharedTable("t", False, ("why",), ()),))
    quote = impacts.QuoteImpact(
        "Q-1", 1, "moved", moves=compare_quote(fields, {}, {"t": [{"why": "a|b\nc"}]}).moves
    )
    report = ImpactReport(
        impacts.Released("prod", "p1", None),
        impacts.Released("staging", "s1", None),
        1,
        listed=1,
        fields=fields,
        quotes=(quote,),
    )

    assert "| `t.why` |  | empty | a\\|b c |  |  |" in render_report(report)


# ── The credential ────────────────────────────────────────────────────────


def test_the_credential_is_bounded_as_the_client_is() -> None:
    host = rollouts.AzureContainerApps(
        AppEnvironment("prod", "rg", "motor-prod"),
        {
            "AZURE_TENANT_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_CLIENT_ID": "client",
            "AZURE_CLIENT_SECRET": "secret",
            "AZURE_SUBSCRIPTION_ID": "subscription",
        },
    )
    found: dict[str, Any] = {}

    def walk(value: Any, seen: set[int]) -> None:
        if id(value) in seen or not hasattr(value, "__dict__"):
            return
        seen.add(id(value))
        kind = type(value).__name__
        if kind == "_BoundedRetry":
            found["retry"] = value
        if kind == "ConnectionConfiguration":
            found["connection"] = value
        for each in vars(value).values():
            for item in each if isinstance(each, list) else [each]:
                walk(item, seen)

    walk(host._credential, set())

    assert (found["connection"].timeout, found["connection"].read_timeout) == (30, 30)
    assert (found["retry"].total_retries, found["retry"].backoff_max) == (2, 10)


# ── The command ───────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Path:
    """A workbench project on Azure Container Apps, both hosted environments named, with the
    CI service principal's variables set."""
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
    for name in rollouts.CREDENTIAL_VARIABLES:
        monkeypatch.setenv(name, f"value-of-{name.lower()}")
    return tmp_path


def _serve(monkeypatch: pytest.MonkeyPatch, apps: Apps, **hosts: FakeHost) -> None:
    made = {"prod": FakeHost(PROD), "staging": FakeHost(STAGING), **hosts}

    def make(environment: AppEnvironment, values: Mapping[str, str]) -> FakeHost:
        assert values["AZURE_CLIENT_SECRET"] == "value-of-azure_client_secret"
        return made[environment.name]

    monkeypatch.setattr(rollouts, "AzureContainerApps", make)
    monkeypatch.setattr(rollouts, "fetch", apps.get)
    monkeypatch.setattr(
        rollouts, "post", lambda url, headers, body, timeout: apps.send(url, headers, body)
    )


def test_the_cli_writes_the_report_and_passes_on_moved_prices(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    _serve(monkeypatch, _apps(count=3))

    result = runner.invoke(cli, ["app", "impact", "--sample", "2"])

    assert result.exit_code == 0, result.output
    assert "✓ 2 of prod's final quotes listed" in result.output
    report = (project / "impact_report.md").read_bytes()
    assert b"\r\n" not in report
    assert "## Quotes that moved" in report.decode("utf-8")


def test_the_cli_writes_the_report_for_a_first_release(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    _serve(monkeypatch, _apps(), prod=FakeHost(PROD, recorded=False))

    result = runner.invoke(cli, ["app", "impact"])

    assert result.exit_code == 0, result.output
    assert "No baseline" in (project / "impact_report.md").read_text(encoding="utf-8")


def test_the_cli_fails_after_writing_the_report_when_staging_fails_a_quote(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    apps = _apps(count=2)
    apps.staging.queued["/api/app/price"] = [_refused(500, "boom")]
    _serve(monkeypatch, apps)

    result = runner.invoke(cli, ["app", "impact"])

    assert result.exit_code == 1
    assert "Q-000001/1: HTTP 500: the pipeline failed on the quote" in result.output
    text = (project / "impact_report.md").read_text(encoding="utf-8")
    assert "## Quotes staging failed on" in text and "## Quotes that moved" in text


def test_the_cli_fails_after_writing_the_report_when_the_run_stops(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    apps = _apps()
    apps.staging.quotes_open = False
    _serve(monkeypatch, apps)

    result = runner.invoke(cli, ["app", "impact"])

    assert result.exit_code == 1
    assert "The impact stopped: staging's app has its quotes closed" in result.output
    assert (project / "impact_report.md").is_file()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda text: text.replace('host = "azure-container-apps"\n', ""),
            "[workbench.app].host must be one of azure-container-apps",
        ),
        (
            lambda text: text.replace('container_app = "motor-prod"', 'container_app = ""'),
            "[workbench.app.prod] leaves container_app empty",
        ),
    ],
    ids=["no-host", "prod-empty"],
)
def test_the_cli_refuses_before_any_host_and_writes_no_report(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: Any,
    change: Callable[[str], str],
    message: str,
) -> None:
    from haute.cli import cli

    toml = project / "haute.toml"
    toml.write_text(change(toml.read_text(encoding="utf-8")), encoding="utf-8")
    _serve(monkeypatch, _apps())

    result = runner.invoke(cli, ["app", "impact"])

    assert result.exit_code == 1
    assert message in result.output
    assert not (project / "impact_report.md").exists()


def test_the_cli_refuses_unset_credentials_naming_each(
    project: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    from haute.cli import cli

    monkeypatch.delenv("AZURE_CLIENT_SECRET")
    _serve(monkeypatch, _apps())

    result = runner.invoke(cli, ["app", "impact"])

    assert result.exit_code == 1
    assert "set AZURE_CLIENT_SECRET." in result.output
    assert not (project / "impact_report.md").exists()


@pytest.mark.parametrize("sample", ["0", "1001"])
def test_the_cli_takes_a_sample_from_one_to_a_thousand(
    project: Path, runner: Any, sample: str
) -> None:
    from haute.cli import cli

    result = runner.invoke(cli, ["app", "impact", "--sample", sample])

    assert result.exit_code == 2


# ── Two real apps ─────────────────────────────────────────────────────────


def test_saved_quotes_priced_on_two_real_apps_of_different_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production's form keeps a note and takes the vehicles as text; staging's drops the
    note, adds an age, takes the vehicles as a number and prices a quarter of the exposure,
    not half: a quote whose vehicles staging cannot read is refused, the rest move."""
    from tests._workbench_impact_apps import TwoApps

    with TwoApps(tmp_path, monkeypatch) as apps:
        apps.final_quote("Q-000001", vehicles="3", note="private")
        apps.final_quote("Q-000002", vehicles="three", note="private")

        report = impact(
            apps.prod_host,
            apps.staging_host,
            sample=10,
            say=lambda _: None,
            get=apps.get,
            send=apps.send,
        )

    outcomes = {quote.label: quote for quote in report.quotes}
    assert report.verdict == "compared", report.why
    assert outcomes["Q-000001/1"].outcome == "moved"
    assert outcomes["Q-000001/1"].moves == (Move("pricing_output", None, "premium", 500.0, 250.0),)
    assert outcomes["Q-000002/1"].outcome == "refused"
    assert outcomes["Q-000002/1"].reason == "HTTP 422: the release refuses the quote's values"
    assert report.fields is not None and report.fields.added == ("pricing_output.fee",)
    assert "private" not in render_report(report)
