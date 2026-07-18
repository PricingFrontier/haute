"""Adversarial repro for claim:
  request-id-header-reflection-unbounded

Claim mechanics (src/haute/server.py):
  370  rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
  372  structlog.contextvars.bind_contextvars(request_id=rid)
  396  response.headers["x-request-id"] = rid

Two factual sub-claims to prove (no sanitisation / no length bound):
 (A) An attacker-supplied x-request-id is bound VERBATIM into structlog
     contextvars and therefore appears verbatim in every emitted log event's
     ``request_id`` field — for BOTH an oversized 100_000-char value AND a
     newline-bearing value.  This is the structured-log-injection half.
 (B) The same verbatim value is reflected back into the response
     ``x-request-id`` header — for the oversized value (the header-reflection
     half).  We also probe the CR/LF value to record whether the transport
     mitigates raw CR/LF at send time (the claim concedes h11 does).

Isolation: this test instantiates ONLY ``_RequestIdMiddleware`` from
``haute.server`` on a throwaway in-memory Starlette app with a trivial
endpoint.  No project root, no disk I/O, no real ``/api`` route, no session
token gate (the middleware under test runs regardless of auth).

NOTE on capture method: ``structlog.testing.capture_logs`` replaces the WHOLE
processor chain with a single ``LogCapture`` sink, which BYPASSES
``merge_contextvars`` — so captured events do NOT carry contextvar-bound keys.
That is a test-harness artifact, not the behaviour of the real server.  To
faithfully prove the log-injection half we instead run the REAL
``configure_logging()`` chain (JSON mode) whose first processor is
``merge_contextvars`` (see src/haute/_logging.py:72) and capture the rendered
stderr line, asserting the verbatim value appears in the emitted JSON.

A FAIL here means the predicted *value* did NOT propagate (claim refuted);
a clean run means the verbatim/oversized value DID propagate (claim's
mechanics confirmed).
"""

from __future__ import annotations

import io
import json
import os

# JSON log mode so the rendered line is machine-checkable. Set BEFORE the
# logging config runs.
os.environ["HAUTE_LOG_FORMAT"] = "json"

import structlog  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from haute._logging import configure_logging  # noqa: E402
from haute.server import _RequestIdMiddleware  # noqa: E402

# Configure the REAL structlog chain (first processor is merge_contextvars),
# so contextvar-bound request_id is rendered onto every emitted line.
configure_logging()

OVERSIZED = "a" * 100_000
NEWLINE_VALUE = "line1\nline2 forged=evil"

failures: list[str] = []


def check(label: str, expected, actual) -> None:
    ok = expected == actual
    shown_exp = expected if not isinstance(expected, str) or len(expected) <= 60 else f"<{len(expected)} chars>"
    shown_act = actual if not isinstance(actual, str) or len(actual) <= 60 else f"<{len(actual)} chars>"
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: expected={shown_exp!r} actual={shown_act!r}")
    if not ok:
        failures.append(label)


import logging  # noqa: E402


def _emitted_request_id() -> object:
    """Emit a structlog event through the REAL chain and capture stderr.

    Returns the parsed ``request_id`` from the rendered JSON line, so we can
    assert exactly what the server would write to its log aggregator. Swaps the
    root handler's stream to an in-memory buffer (the handler captured a
    reference to the original ``sys.stderr`` at configure time, so we patch the
    handler.stream directly rather than redirecting ``sys.stderr``).
    """
    root = logging.getLogger()
    buf = io.StringIO()
    saved_streams = []
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            saved_streams.append((h, h.stream))
            h.stream = buf
    try:
        structlog.get_logger().info("repro_probe_event")
    finally:
        for h, s in saved_streams:
            h.stream = s
    out = buf.getvalue()
    for line in out.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "repro_probe_event":
            return rec.get("request_id", "<absent>")
    return f"<no JSON probe line; raw={out!r}>"


async def _endpoint(request):  # noqa: ANN001, ANN202
    # Snapshot what the middleware bound into contextvars for THIS request and
    # also emit a real log event through the configured chain so the rendered
    # JSON carries the merged request_id.
    bound = structlog.contextvars.get_contextvars()
    emitted_rid = _emitted_request_id()
    return JSONResponse(
        {
            "bound_request_id": bound.get("request_id"),
            "emitted_request_id": emitted_rid,
        }
    )


def _build_client() -> TestClient:
    app = Starlette(routes=[Route("/probe", _endpoint, methods=["GET"])])
    app.add_middleware(_RequestIdMiddleware)
    # raise_server_exceptions=False so a transport-level header rejection (for
    # the CR/LF case) surfaces as a response we can observe rather than
    # aborting the whole script.
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# (A) OVERSIZED value: log-context binding + header reflection
# --------------------------------------------------------------------------
client = _build_client()
resp = client.get("/probe", headers={"x-request-id": OVERSIZED})

# A2: emitted log line carries the oversized value verbatim in request_id.
check(
    "A2: emitted log request_id == raw 100_000-char value (no length bound)",
    OVERSIZED,
    resp.json().get("emitted_request_id"),
)

# A3: the value the middleware bound (seen inside the endpoint) is verbatim.
check(
    "A3: contextvar bound inside request == raw oversized value",
    OVERSIZED,
    resp.json().get("bound_request_id"),
)

# A4: response header echoes the oversized value verbatim (reflection half).
check(
    "A4: response x-request-id header == raw oversized value (reflected)",
    OVERSIZED,
    resp.headers.get("x-request-id"),
)

# --------------------------------------------------------------------------
# (B) NEWLINE value: structured-log-injection half. The raw newline must
#     survive into the bound contextvar; in JSON mode it is escaped as \n in
#     the rendered line (still the same Python str value after parse).
# --------------------------------------------------------------------------
client2 = _build_client()
resp2 = client2.get("/probe", headers={"x-request-id": NEWLINE_VALUE})
check(
    "B1: emitted log request_id contains raw newline (no sanitisation)",
    NEWLINE_VALUE,
    resp2.json().get("emitted_request_id"),
)
check(
    "B2: newline survives into bound contextvar inside request",
    NEWLINE_VALUE,
    resp2.json().get("bound_request_id"),
)

# B3: DOCUMENT (do not gate on) what the transport does with the CR/LF header
#     reflection — the claim concedes h11/starlette may reject it at send time.
hdr_echo = resp2.headers.get("x-request-id")
print(
    f"[INFO] B3: response x-request-id for newline value -> {hdr_echo!r} "
    f"(status={resp2.status_code}); claim concedes raw CR/LF response "
    f"splitting is mitigated by the transport, so this is informational."
)

print()
if failures:
    raise SystemExit(f"REPRO FAILED: {len(failures)} assertion(s) did not match: {failures}")
print(
    "REPRO CONFIRMED (mechanics): x-request-id is bound into structlog "
    "contextvars and emitted on every log line VERBATIM, with no length "
    "bound (100_000 chars survive) and no charset sanitisation (raw newline "
    "survives) — the structured-log-injection half is real and unmitigated. "
    "The oversized value is also reflected verbatim into the response "
    "x-request-id header. Severity remains LOW under the loopback/single-user "
    "trust model (server defaults to 127.0.0.1)."
)
