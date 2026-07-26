"""Regression tests for lazy environment-variable accessors.

Defect class (Maginot: import-time environment capture): timeout/limit knobs
used to be frozen into module-level constants at import, so an override set
*after* the module was imported — the common case for a programmatic server
start or a pytest ``monkeypatch.setenv`` — was silently ignored, and a
malformed value crashed module import instead of the request.

The fix (mirroring PR #64's ``HAUTE_MEM_LOG`` treatment) resolves each knob per
call via ``haute._env``. Every test below sets the env var AFTER the module is
imported and proves the new value takes effect. Explicitly invalid configuration
fails loudly so it cannot silently disable a safety limit.
"""

from __future__ import annotations

import pytest

from haute import _env


class TestEnvHelpers:
    """The shared parse-with-fallback helpers in ``haute._env``."""

    def test_float_env_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.float_env("HAUTE_TEST_KNOB", 12.5) == 12.5

    def test_float_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "3.5")
        assert _env.float_env("HAUTE_TEST_KNOB", 12.5) == 3.5

    def test_float_env_malformed_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "not-a-float")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a finite number"):
            _env.float_env("HAUTE_TEST_KNOB", 12.5)

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-0.1"])
    def test_float_env_rejects_non_finite_or_non_positive_values(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(
            RuntimeError, match="HAUTE_TEST_KNOB must be a finite number greater than 0"
        ):
            _env.float_env("HAUTE_TEST_KNOB", 12.5)

    def test_int_env_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.int_env("HAUTE_TEST_KNOB", 42) == 42

    def test_int_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "7")
        assert _env.int_env("HAUTE_TEST_KNOB", 42) == 7

    def test_int_env_malformed_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "3.5")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.int_env("HAUTE_TEST_KNOB", 42)

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_int_env_rejects_non_positive_values(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.int_env("HAUTE_TEST_KNOB", 42)

    def test_optional_int_env_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.optional_int_env("HAUTE_TEST_KNOB") is None

    def test_optional_int_env_empty_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.optional_int_env("HAUTE_TEST_KNOB")

    def test_optional_int_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "99")
        assert _env.optional_int_env("HAUTE_TEST_KNOB") == 99

    @pytest.mark.parametrize("raw", ["not-an-int", "0", "-1"])
    def test_optional_int_env_invalid_value_fails_loudly(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.optional_int_env("HAUTE_TEST_KNOB")


# (accessor, env var, override string, expected parsed value, default) for every
# knob that used to be captured at import. Setting the env var AFTER import must
# change the accessor's result — that is exactly the frozen-constant regression.
_ACCESSOR_CASES = [
    ("haute.routes.pipeline", "_trace_timeout", "HAUTE_TRACE_TIMEOUT", "5", 5.0, 120.0),
    ("haute.routes.pipeline", "_preview_timeout", "HAUTE_PREVIEW_TIMEOUT", "5", 5.0, 120.0),
    ("haute.routes.pipeline", "_sink_timeout", "HAUTE_SINK_TIMEOUT", "5", 5.0, 300.0),
    ("haute.routes.json_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT", "5", 5.0, 1800.0),
    (
        "haute.routes.output_assemble",
        "_dry_run_timeout",
        "HAUTE_OUTPUT_DRY_RUN_TIMEOUT",
        "5",
        5.0,
        120.0,
    ),
    ("haute.routes.input_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT", "5", 5.0, 1800.0),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_timeout",
        "HAUTE_AUTO_RANGE_TIMEOUT",
        "60",
        60,
        1800,
    ),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_chunk_size",
        "HAUTE_AUTO_RANGE_CHUNK_SIZE",
        "111",
        111,
        2_000_000,
    ),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_partitions",
        "HAUTE_AUTO_RANGE_PARTITIONS",
        "8",
        8,
        16,
    ),
    (
        "haute.routes._train_service",
        "_default_train_timeout",
        "HAUTE_TRAIN_TIMEOUT",
        "60",
        60,
        3600,
    ),
    (
        "haute.routes._train_service",
        "_max_train_loss_history",
        "HAUTE_TRAIN_LOSS_HISTORY_LIMIT",
        "10",
        10,
        200,
    ),
]


def _resolve(module_name: str, accessor: str):
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, accessor)


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var", "override", "expected", "default"),
    _ACCESSOR_CASES,
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a, *_ in _ACCESSOR_CASES],
)
def test_accessor_override_takes_effect_after_import(
    module_name, accessor, env_var, override, expected, default, monkeypatch
):
    """An env override set after import is honoured (was frozen before)."""
    fn = _resolve(module_name, accessor)
    monkeypatch.delenv(env_var, raising=False)
    assert fn() == default
    monkeypatch.setenv(env_var, override)
    assert fn() == expected


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var", "override", "expected", "default"),
    _ACCESSOR_CASES,
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a, *_ in _ACCESSOR_CASES],
)
def test_accessor_malformed_value_fails_loudly(
    module_name, accessor, env_var, override, expected, default, monkeypatch
):
    """A malformed explicit override is a configuration error."""
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv(env_var, "not-a-number")
    with pytest.raises(RuntimeError, match=env_var):
        fn()


def test_solver_timeout_optional_semantics(monkeypatch):
    """The optional timeout is absent by default and strict when configured."""
    from haute.routes import _optimiser_service as opt

    monkeypatch.delenv("HAUTE_SOLVER_TIMEOUT", raising=False)
    assert opt._default_solver_timeout() is None
    monkeypatch.setenv("HAUTE_SOLVER_TIMEOUT", "42")
    assert opt._default_solver_timeout() == 42
    # Malformed must not silently remove the timeout.
    monkeypatch.setenv("HAUTE_SOLVER_TIMEOUT", "not-an-int")
    with pytest.raises(RuntimeError, match="HAUTE_SOLVER_TIMEOUT.*positive integer"):
        opt._default_solver_timeout()


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var"),
    [
        ("haute.routes.json_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT"),
        ("haute.routes.input_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT"),
    ],
)
@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf"])
def test_build_timeout_has_one_positive_finite_policy(
    module_name, accessor, env_var, raw, monkeypatch
):
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv(env_var, raw)
    with pytest.raises(RuntimeError, match=env_var):
        fn()


@pytest.mark.parametrize(
    ("module_name", "accessor"),
    [
        ("haute.routes.json_cache", "_build_timeout"),
        ("haute.routes.input_cache", "_build_timeout"),
    ],
)
def test_build_timeout_accepts_positive_values_below_old_clamp(module_name, accessor, monkeypatch):
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv("HAUTE_BUILD_TIMEOUT", "0.0005")
    assert fn() == 0.0005


def test_auto_range_context_default_reflects_env(monkeypatch):
    """The frozen dataclass default is a ``default_factory``, so a per-test
    env override reaches ``FrontierAutoRangeContext()`` — proving the fix also
    covers the dataclass-default capture, not just the direct accessor call.
    """
    from haute.routes._optimiser_service import FrontierAutoRangeContext

    monkeypatch.setenv("HAUTE_AUTO_RANGE_CHUNK_SIZE", "333")
    monkeypatch.setenv("HAUTE_AUTO_RANGE_PARTITIONS", "9")
    ctx = FrontierAutoRangeContext()
    assert ctx.chunk_size == 333
    assert ctx.partition_count == 9
