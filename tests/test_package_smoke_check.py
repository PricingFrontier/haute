"""The package smoke reads each engine's range from the installed wheel's requirements."""

from __future__ import annotations

from importlib.metadata import version

import pytest

from scripts import package_smoke_check as smoke


@pytest.mark.parametrize("distribution", ["lightgbm", "interpret-core"])
def test_the_installed_engines_are_inside_the_ranges_haute_declares(distribution: str) -> None:
    smoke._assert_within_declared_range(distribution, version(distribution))


def test_a_version_past_the_declared_cap_is_refused() -> None:
    with pytest.raises(AssertionError, match=r"lightgbm 5\.0\.0 is outside the range haute"):
        smoke._assert_within_declared_range("lightgbm", "5.0.0")


def test_a_distribution_haute_does_not_declare_is_refused() -> None:
    with pytest.raises(AssertionError, match="one requirement on not-a-dependency"):
        smoke._assert_within_declared_range("not-a-dependency", "1.0")
