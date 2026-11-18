"""What a test asserts of a failure answer (``haute._failure_answers``)."""

from __future__ import annotations

import re

#: A parametrized case's expected detail when the failure is one nothing names.
UNNAMED = object()


def assert_detail(detail: object, expected: object) -> None:
    """*detail* is *expected*, or an unnamed failure's answer when *expected* is
    :data:`UNNAMED`."""
    if expected is UNNAMED:
        assert_unnamed(detail)
    else:
        assert detail == expected


def assert_unnamed(detail: object) -> None:
    """A failure nothing names: words and an incident id, never the exception's text or a
    pointer to a server log."""
    said = detail["message"] if isinstance(detail, dict) else detail
    assert isinstance(said, str), detail
    assert "incident " in said, said
    assert "server log" not in said


def incident_in(detail: object) -> str:
    """The incident id an unnamed failure's answer names."""
    said = detail["message"] if isinstance(detail, dict) else detail
    found = re.search(r"incident ([0-9a-f]{12})", str(said))
    assert found is not None, detail
    return found.group(1)


def without_message(detail: object) -> object:
    """A structured detail without the ``message`` every answer words it with, for a test of
    its other fields; the message is asserted present."""
    assert isinstance(detail, dict) and isinstance(detail.get("message"), str), detail
    return {key: value for key, value in detail.items() if key != "message"}


def message_of(detail: object) -> object:
    """What *detail* says: its message when it is structured, as a job's ``error_detail``
    always is."""
    return detail["message"] if isinstance(detail, dict) else detail
