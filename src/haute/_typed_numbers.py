"""Text typed as a number, read by one grammar in two ways (specs/frontend-shared, "Typed numbers").

The browser's reader, ``frontend/src/utils/typedNumbers.ts``, is the same rule in the other
language; both are held to the case file
``frontend/src/utils/__tests__/fixtures/typedText.json``. No other code reads typed text with
``float``, ``int`` or ``Decimal``: the mypy plugin ``scripts/mypy_typed_numbers.py`` refuses such a
call outside this module unless it is a named exception.

- A field declared numeric reads text as a number or refuses it (:func:`read_typed_number`,
  :func:`read_typed_whole`): a sign and a currency sign, digits grouped in threes by commas or
  by spaces, a point as the only decimal mark, an exponent, the value finite. Text that could
  be two numbers (``1,5``) is refused, never guessed.
- A Constant's value, which has no declared type, is a number only when it is canonical
  finite decimal text (:func:`constant_value`): a whole number stays an integer, a boolean a
  boolean, anything else text, and a value that is not finite is refused.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

__all__ = [
    "TYPED_SPACES",
    "canonical_number",
    "canonical_text",
    "ConstantValueError",
    "constant_value",
    "number_text",
    "read_typed_number",
    "read_typed_whole",
    "setting_number",
    "trim_typed",
]

#: The characters trimmed from either end of typed text, and the only ones a value holding
#: nothing else counts as empty for: one list in both languages, in place of each language's
#: own notion of whitespace (Python's ``str.strip`` also takes the separator controls U+001C
#: to U+001F and U+0085, and leaves the byte-order mark; JavaScript's ``trim`` the reverse).
TYPED_SPACES = (
    "\t\n\v\f\r \u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

#: The spaces that group digits in threes, as a comma does.
_GROUP_SPACES = " \u00a0\u2009\u202f"

_NUMBER = re.compile(
    r"(?P<sign1>[+-]?)(?:[$£€][" + _GROUP_SPACES + r"]*)?(?P<sign2>[+-]?)"
    r"(?P<whole>[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?:["
    + _GROUP_SPACES
    + r"][0-9]{3})+)?"
    r"(?P<fraction>\.[0-9]*)?(?P<exponent>[eE][+-]?[0-9]+)?"
)

#: A Constant's whole number as it is written: no leading zero, no ``+``, and not ``-0``.
_CANONICAL_WHOLE = re.compile(r"0|-?[1-9][0-9]*")
#: A Constant's float as it is written, before its digits are held to the float's own.
_CANONICAL_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")
#: Text that reads as a number that is not finite, in any letter case and with any sign.
_NOT_FINITE = re.compile(r"[+-]?(?:nan|inf|infinity)", re.IGNORECASE)

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def trim_typed(text: str) -> str:
    """*text* without the typed spaces at either end."""
    return text.strip(TYPED_SPACES)


def _parts(text: str) -> tuple[str, bool] | None:
    """The number *text* is, as plain digits for the language to read, and whether it holds
    only digits (no fraction or exponent); or None when it is not a number."""
    match = _NUMBER.fullmatch(trim_typed(text))
    if match is None:
        return None
    sign1, sign2, whole, fraction, exponent = match.group(
        "sign1", "sign2", "whole", "fraction", "exponent"
    )
    if sign1 and sign2:
        return None
    if whole is None and (fraction is None or fraction == "."):
        return None
    digits = re.sub("[," + _GROUP_SPACES + "]", "", whole or "")
    plain = f"{sign1 or sign2}{digits}{fraction or ''}{exponent or ''}"
    return plain, fraction is None and exponent is None


def read_typed_number(text: str) -> float | None:
    """The number *text* is, read by the grammar, or None when it is not one (finite)."""
    parts = _parts(text)
    if parts is None:
        return None
    number = float(parts[0])
    return number if math.isfinite(number) else None


def read_typed_whole(text: str) -> int | None:
    """The whole number *text* is, exactly however large, or None when it is not a number or
    not whole: digits alone read as digits, any other number when its value is whole."""
    parts = _parts(text)
    if parts is None:
        return None
    plain, digits_only = parts
    if digits_only:
        return int(plain)
    number = read_typed_number(text)
    return int(number) if number is not None and number.is_integer() else None


def setting_number(value: object) -> float | None:
    """A numeric setting a settings file holds as a JSON number or as typed text, as a finite
    float, or None when it is neither: a boolean, a number that is not finite, or text that is
    not a number by the grammar (:func:`read_typed_number`)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    return read_typed_number(value) if isinstance(value, str) else None


def number_text(number: float) -> str:
    """A float's canonical text: its shortest digits, without an exponent, with a point."""
    text = format(Decimal(repr(number)), "f")
    return text if "." in text else f"{text}.0"


class ConstantValueError(ValueError):
    """A Constant's value that is a number nothing can hold: not finite, or a whole number
    outside a 64-bit integer's range. Its message says what the value is, to follow
    "which is"."""


def canonical_number(text: str) -> int | float | None:
    """The number *text* is written as, or None when it is not canonical finite decimal text:
    a whole number with an optional ``-``, no leading zero and not ``-0``, as an integer; or
    digits, a point and digits that are a float's shortest digits without an exponent
    (:func:`number_text`), as that float. A settings file holds such text as a JSON number,
    which reads back as the same text."""
    if _CANONICAL_WHOLE.fullmatch(text):
        return int(text)
    if _CANONICAL_DECIMAL.fullmatch(text):
        number = float(text)
        if math.isfinite(number) and number_text(number) == text:
            return number
    return None


def canonical_text(number: int | float) -> str:
    """The canonical text of a whole number or a finite float, which
    :func:`canonical_number` reads back as it."""
    return str(number) if isinstance(number, int) else number_text(number)


def constant_value(value: object) -> bool | int | float | str | None:
    """A Constant's value as it runs and as its settings file holds it.

    A boolean stays a boolean and a null a null; a whole number, or canonical whole-number
    text, an integer; a finite float, or canonical decimal text, a float; any other text
    stays text (:func:`canonical_number`). A value that is not finite (``nan``, ``inf``,
    ``infinity`` as text, a float that is not finite) and a whole number outside a 64-bit
    integer's range raise :class:`ConstantValueError`.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _held_whole(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConstantValueError("not a finite number")
        return value
    if not isinstance(value, str):
        raise ConstantValueError("neither a number, a boolean nor text")
    if _NOT_FINITE.fullmatch(trim_typed(value)):
        raise ConstantValueError("not a finite number")
    number = canonical_number(value)
    if number is None:
        return value
    return _held_whole(number) if isinstance(number, int) else number


def _held_whole(value: int) -> int:
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise ConstantValueError("a whole number too large for a 64-bit integer")
    return value
