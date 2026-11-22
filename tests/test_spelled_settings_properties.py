"""Edited settings keep the spelling of every number the edit left, and change no value
(CGEN-09; codegen high-level, "The same place").

``haute._edited_parts.spelled_as`` is given the settings a browser sent and the saved
settings the edit began from. Whatever it pairs, what it reads is, as a browser holds it,
what the browser sent: only how a number the browser cannot hold is spelt changes. And an
element only moved, or left as it was, is the saved element, its numbers spelt as saved.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from haute._edited_parts import spelled_as
from tests._property_budget import pr_budget
from tests._unedited_parts import as_javascript_holds_it

#: Numbers a browser holds otherwise as well as ones it holds exactly.
_NUMBERS = st.one_of(
    st.integers(min_value=-(2**63), max_value=2**63),
    st.sampled_from([1.0, -0.0, 0.0, 2.5, 1e20, 1e21, 100.0, 9007199254740993, 2**53]),
    st.floats(allow_nan=False, allow_infinity=False),
)
_SCALARS = st.one_of(_NUMBERS, st.booleans(), st.none(), st.text(max_size=3))
_KEYS = st.sampled_from(["a", "b", "rows", "default_value", "output_column"])
_SETTINGS = st.recursive(
    _SCALARS,
    lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(_KEYS, inner, max_size=4)),
    max_leaves=16,
)


@st.composite
def _edited(draw: st.DrawFn, value: Any) -> Any:
    """*value* after an edit: elements moved, removed or added, values changed or kept."""
    if isinstance(value, list):
        items = [draw(_edited(item)) for item in value]
        if len(items) > 1 and draw(st.booleans()):
            items = list(draw(st.permutations(items)))
        if items and draw(st.booleans()):
            del items[draw(st.integers(0, len(items) - 1))]
        if draw(st.booleans()):
            items.insert(draw(st.integers(0, len(items))), draw(_SETTINGS))
        return items
    if isinstance(value, dict):
        edited = {key: draw(_edited(item)) for key, item in value.items()}
        if draw(st.booleans()):
            edited[draw(_KEYS)] = draw(_SETTINGS)
        return edited
    return draw(st.one_of(st.just(value), _SCALARS))


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


@pr_budget(max_examples=300)
@given(data=st.data())
def test_what_is_read_is_what_the_browser_sent(data: st.DataObject) -> None:
    saved = data.draw(_SETTINGS)
    sent = as_javascript_holds_it(data.draw(_edited(saved)))

    read = spelled_as(sent, saved)

    assert _text(as_javascript_holds_it(read)) == _text(sent)


@pr_budget(max_examples=200)
@given(data=st.data())
def test_an_element_only_moved_is_the_saved_element(data: st.DataObject) -> None:
    saved = data.draw(st.lists(_SETTINGS, min_size=1, max_size=5, unique_by=_text))
    order = data.draw(st.permutations(range(len(saved))))
    held = [as_javascript_holds_it(saved[index]) for index in order]
    if len({_text(item) for item in held}) < len(held):
        # Two elements a browser holds alike are one to it: either may stand for either.
        return

    read = spelled_as(held, saved)

    assert _text(read) == _text([saved[index] for index in order])
