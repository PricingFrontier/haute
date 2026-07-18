"""Adversarial repro for claim:
  local-security-session-token-in-query-string

Two factual sub-claims from the repro_strategy:
 (A) websocket_rejection_reason(Headers({}), QueryParams({token: <valid>}))
     returns None (auth PASSES) despite NO session-token header and NO Origin
     header -> proves the token-in-query path authorizes the websocket.
 (B) _is_local_origin(Headers({})) is True (an absent Origin is treated as
     trusted).

This script asserts on the SPECIFIC expected-vs-actual values, then also
checks the wrong-token / foreign-origin negative paths so we can judge
whether the token is genuinely the "sole gate" the claim describes.

No disk / project I/O is required: _local_security only reads an env var and
the process boot token. We set HAUTE_LOCAL_SESSION_TOKEN to a deterministic
value so local_session_token() is predictable.
"""

from __future__ import annotations

import os

# Deterministic token BEFORE importing the module is not required (the function
# re-reads the env var each call), but set it explicitly for clarity.
os.environ["HAUTE_LOCAL_SESSION_TOKEN"] = "repro-deterministic-token"
# Make sure the disable-auth escape hatch is OFF so we exercise the real gate.
os.environ.pop("HAUTE_DISABLE_LOCAL_SESSION_AUTH", None)
os.environ.pop("HAUTE_TRUSTED_HOSTS", None)

from starlette.datastructures import Headers, QueryParams  # noqa: E402

from haute._local_security import (  # noqa: E402
    SESSION_TOKEN_HEADER,
    SESSION_TOKEN_QUERY_PARAM,
    _is_local_origin,
    local_session_auth_disabled,
    local_session_token,
    websocket_rejection_reason,
)

token = local_session_token()
assert token == "repro-deterministic-token", token
assert not local_session_auth_disabled()

failures: list[str] = []


def check(label: str, expected, actual) -> None:
    ok = expected == actual
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: expected={expected!r} actual={actual!r}")
    if not ok:
        failures.append(label)


# (A) Token-in-query authorizes the websocket with NO header and NO Origin.
no_headers = Headers({})
qp_valid = QueryParams({SESSION_TOKEN_QUERY_PARAM: token})
res_a = websocket_rejection_reason(no_headers, qp_valid)
check(
    "A: ws auth via query token, no header, no Origin -> None (authorized)",
    None,
    res_a,
)

# (B) Absent Origin is treated as local/trusted.
res_b = _is_local_origin(Headers({}))
check("B: _is_local_origin(no Origin) -> True (trusted)", True, res_b)

# --- Negative controls: is the token genuinely the SOLE gate? --------------

# (C) Wrong query token is rejected (token actually validated, not bypassed).
qp_wrong = QueryParams({SESSION_TOKEN_QUERY_PARAM: "wrong-token"})
res_c = websocket_rejection_reason(Headers({}), qp_wrong)
check(
    "C: wrong query token rejected",
    "Missing or invalid Haute session token",
    res_c,
)

# (D) No token at all (no header, no query) is rejected.
res_d = websocket_rejection_reason(Headers({}), QueryParams({}))
check(
    "D: no token anywhere rejected",
    "Missing or invalid Haute session token",
    res_d,
)

# (E) Foreign Origin is rejected BEFORE the token is even checked -> Origin is a
#     real second gate *for browser clients that send an Origin*.
foreign_headers = Headers({"origin": "https://attacker.example"})
res_e = websocket_rejection_reason(foreign_headers, qp_valid)
check(
    "E: foreign Origin rejected even WITH valid query token",
    "Origin is not trusted for the local Haute session",
    res_e,
)

# (F) Header token still works (header path also accepted) — sanity.
hdr_valid = Headers({SESSION_TOKEN_HEADER: token})
res_f = websocket_rejection_reason(hdr_valid, QueryParams({}))
check("F: header token authorizes ws -> None", None, res_f)

print()
if failures:
    raise SystemExit(f"REPRO: {len(failures)} assertion(s) did not match: {failures}")
print(
    "REPRO CONFIRMED (mechanics): the websocket accepts the session token from "
    "the URL query string, and an absent Origin is treated as trusted. "
    "Wrong/absent tokens are still rejected, and a *present* foreign Origin is "
    "still rejected, so the token is the sole gate ONLY for clients that omit "
    "the Origin header."
)
