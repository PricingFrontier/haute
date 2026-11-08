"""Credential detection and diagnostic redaction shared by data providers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from haute._validation_error import HauteValidationError

_CREDENTIAL_NAMES = frozenset(
    {
        "accesskey",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "credential",
        "oauth",
        "passwd",
        "password",
        "pwd",
        "sas",
        "se",
        "secret",
        "sig",
        "signature",
        "token",
    }
)
_CREDENTIAL_MARKERS = (
    "accesskey",
    "apikey",
    "authkey",
    "authtoken",
    "clientsecret",
    "credential",
    "password",
    "privatekey",
    "secretkey",
    "securitytoken",
    "sharedaccesssignature",
    "signature",
)
_URI_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)"
    # A key quoted as in JSON or a Python dict, which an HTTP error body embeds.
    r"(?P<quote>[\"']?)"
    r"(?P<separator>\s*[=:]\s*)"
    r'(?P<value>"[^"]*"|\'[^\']*\'|[^\s,;]+)'
)
# A query parameter outside a full URI: the relative signed URL in a storage
# error, whose ``?sig=...`` an enclosing ``url: ...`` assignment would swallow.
_QUERY_PARAMETER_RE = re.compile(
    r"(?P<lead>[?&])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)=(?P<value>[^&#\s\"'<>]*)"
)
_REDACTED = "<redacted>"


class CredentialMaterialError(HauteValidationError):
    """A URI or mapping key would persist inline credential material."""


def is_credential_name(name: str) -> bool:
    """Whether *name* is a recognised credential-bearing field or query key."""
    parts = re.findall(r"[a-z0-9]+", name.casefold())
    compact = "".join(parts)
    if compact in _CREDENTIAL_NAMES:
        return True
    if any(marker in compact for marker in _CREDENTIAL_MARKERS):
        return True
    return any(part in _CREDENTIAL_NAMES - {"se"} for part in parts)


def validate_credential_free_uri(uri: str) -> str:
    """Return *uri* when neither userinfo nor secret query parameters occur."""
    parsed = urlsplit(uri)
    if parsed.username is not None or parsed.password is not None:
        raise CredentialMaterialError("URI must not contain credential userinfo.")
    if any(is_credential_name(key) for key, _value in parse_qsl(parsed.query, True)):
        raise CredentialMaterialError("URI must not contain credential query parameters.")
    return uri


def _redact_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = f"{_REDACTED}@{netloc.rsplit('@', 1)[1]}"
    query = urlencode(
        [
            (key, _REDACTED if is_credential_name(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def _redact_assignments(text: str) -> str:
    """Replace the value of every credential-named assignment in *text*.

    A value that opens an object or a quoted string is scanned in turn, because
    it can hold the credential: ``Error: {"token": "abc"}``. That includes a
    credential key opening an object, whose first token is the next key and not
    a value. A bare scalar, such as a URL, is passed whole.
    """
    pieces: list[str] = []
    position = 0
    while (match := _ASSIGNMENT_RE.search(text, position)) is not None:
        value = match.group("value")
        if is_credential_name(match.group("key")) and value[:2] not in ('{"', "{'"):
            pieces += [text[position : match.start("value")], _REDACTED]
            position = match.end()
            continue
        end = match.end("separator") if value[0] in "{[\"'" else match.end()
        pieces.append(text[position:end])
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def redact_sensitive_text(text: str, *, known_secrets: Iterable[str] = ()) -> str:
    """Scrub credential-bearing URI/query/assignment values from diagnostic text."""
    redacted = text
    for secret in sorted(
        {secret for secret in known_secrets if secret},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret, _REDACTED)
    redacted = _URI_RE.sub(lambda match: _redact_uri(match.group(0)), redacted)

    def redact_query_parameter(match: re.Match[str]) -> str:
        if not is_credential_name(match.group("key")):
            return match.group(0)
        return f"{match.group('lead')}{match.group('key')}={_REDACTED}"

    redacted = _QUERY_PARAMETER_RE.sub(redact_query_parameter, redacted)

    return _redact_assignments(redacted)
