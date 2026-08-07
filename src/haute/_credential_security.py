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
    r"(?P<separator>\s*[=:]\s*)"
    r'(?P<value>"[^"]*"|\'[^\']*\'|[^\s,;]+)'
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

    def redact_assignment(match: re.Match[str]) -> str:
        if not is_credential_name(match.group("key")):
            return match.group(0)
        return f"{match.group('key')}{match.group('separator')}{_REDACTED}"

    return _ASSIGNMENT_RE.sub(redact_assignment, redacted)
