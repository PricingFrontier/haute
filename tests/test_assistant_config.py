"""Tests for assistant configuration and readiness (``haute.assistant._config``).

Spec: specs/assistant/low-level.md — Key types (``AssistantConfig`` /
``AssistantReadiness``) and Control flow (Status): the readiness matrix names
exactly one missing piece; the output-token budget is strict (unset defaults,
malformed fails readiness — never warn-and-default); the mutation gate maps
every ``working_branch_status`` state plus the git-domain raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.assistant import _config
from haute.assistant._config import assistant_readiness, resolve_assistant_config
from haute.errors import ConfigError, HauteError
from haute.schemas import GitWorkingBranchResponse


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS", raising=False)
    return tmp_path


def _write_toml(root: Path, body: str) -> None:
    (root / "haute.toml").write_text(body, encoding="utf-8")


def _configured(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml(root, '[assistant]\nprovider = "anthropic"\nmodel = "m"\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


class TestReadinessMatrix:
    def test_absent_table_is_not_configured(self, project_root: Path):
        status = assistant_readiness()
        assert status.configured is False
        assert "[assistant]" in (status.reason or "")

    def test_unknown_provider(self, project_root: Path):
        _write_toml(project_root, '[assistant]\nprovider = "watson"\nmodel = "m"\n')
        status = assistant_readiness()
        assert status.configured is False
        assert "watson" in (status.reason or "")

    def test_missing_model(self, project_root: Path):
        _write_toml(project_root, '[assistant]\nprovider = "anthropic"\n')
        status = assistant_readiness()
        assert status.configured is False
        assert "model" in (status.reason or "").lower()

    def test_missing_api_key_names_the_env_var(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_toml(project_root, '[assistant]\nprovider = "anthropic"\nmodel = "m"\n')
        status = assistant_readiness()
        assert status.configured is False
        assert "ANTHROPIC_API_KEY" in (status.reason or "")

    def test_missing_sdk_names_broken_install(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The SDKs are core dependencies; their absence is a broken install,
        reported as a named readiness reason rather than an import crash."""
        _configured(project_root, monkeypatch)
        monkeypatch.setattr(_config, "_sdk_importable", lambda _provider: False)
        status = assistant_readiness()
        assert status.configured is False
        assert "anthropic SDK is missing" in (status.reason or "")
        assert "reinstall haute" in (status.reason or "")

    def test_fully_configured(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        _configured(project_root, monkeypatch)
        status = assistant_readiness()
        assert status.configured is True
        assert status.reason is None
        assert (status.provider, status.model) == ("anthropic", "m")

    def test_malformed_toml_raises_config_error(self, project_root: Path):
        _write_toml(project_root, "[assistant\nnot toml")
        with pytest.raises(ConfigError):
            assistant_readiness()

    def test_base_url_rejected_for_anthropic(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_toml(
            project_root,
            '[assistant]\nprovider = "anthropic"\nmodel = "m"\nbase_url = "https://x"\n',
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        status = assistant_readiness()
        assert status.configured is False
        assert "base_url" in (status.reason or "")

    def test_base_url_allowed_for_openai(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        _write_toml(
            project_root,
            '[assistant]\nprovider = "openai"\nmodel = "m"\nbase_url = "https://dbx"\n',
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        status = assistant_readiness()
        assert status.configured is True
        assert resolve_assistant_config().base_url == "https://dbx"

    @pytest.mark.parametrize(
        "base_url", ["http://localhost:8080/v1", "https://dbx.example/v1/chat"]
    )
    def test_openai_base_url_accepts_absolute_http_urls(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch, base_url: str
    ):
        _write_toml(
            project_root,
            f'[assistant]\nprovider = "openai"\nmodel = "m"\nbase_url = "{base_url}"\n',
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert resolve_assistant_config().base_url == base_url


class TestClosedTableAndBaseUrlValidation:
    def test_unknown_key_names_its_full_toml_path(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_toml(
            project_root,
            '[assistant]\nprovider = "openai"\nmodel = "m"\nendpoint = "secret"\n',
        )
        monkeypatch.setattr(
            _config,
            "_sdk_importable",
            lambda _provider: pytest.fail("SDK probe must not run for invalid configuration"),
        )
        with pytest.raises(ConfigError, match=r"\[assistant\]\.endpoint") as exc_info:
            resolve_assistant_config()
        assert "secret" not in str(exc_info.value)

    def test_multiple_unknown_keys_have_deterministic_full_paths(self, project_root: Path):
        _write_toml(
            project_root,
            '[assistant]\nmodel = "m"\nzebra = "value"\nalpha = "value"\n',
        )
        with pytest.raises(ConfigError) as exc_info:
            resolve_assistant_config()
        assert str(exc_info.value) == (
            "Unknown [assistant] configuration key(s): [assistant].alpha, [assistant].zebra."
        )

    @pytest.mark.parametrize(
        "base_url",
        [
            "",
            "/v1",
            "api.example/v1",
            "ftp://api.example/v1",
            "file:///tmp/model",
            "https:///v1",
            "http://[::1",
            "http://api.example:invalid",
            "http://api.example:70000",
            "http://api.example:",
            "https://user:password@api.example/v1",
            "https://api.example\\\\gateway/v1",
            "https://api.example/v1 with-space",
            "https://api.example/v1\t",
        ],
    )
    def test_invalid_openai_base_url_raises_redacted_field_error_before_sdk_probe(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch, base_url: str
    ):
        _write_toml(
            project_root,
            f'[assistant]\nprovider = "openai"\nmodel = "m"\nbase_url = "{base_url}"\n',
        )
        monkeypatch.setattr(
            _config,
            "_sdk_importable",
            lambda _provider: pytest.fail("SDK probe must not run for invalid base_url"),
        )
        with pytest.raises(ConfigError, match=r"\[assistant\]\.base_url") as exc_info:
            resolve_assistant_config()
        if base_url:
            assert base_url not in str(exc_info.value)

    def test_non_string_base_url_is_redacted_and_precedes_sdk_probe(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_toml(
            project_root,
            '[assistant]\nprovider = "openai"\nmodel = "m"\nbase_url = 123\n',
        )
        monkeypatch.setattr(
            _config,
            "_sdk_importable",
            lambda _provider: pytest.fail("SDK probe must not run for invalid base_url"),
        )
        with pytest.raises(ConfigError, match=r"\[assistant\]\.base_url") as exc_info:
            resolve_assistant_config()
        assert "123" not in str(exc_info.value)


class TestOutputTokenBudget:
    def test_unset_defaults_to_8192(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        _configured(project_root, monkeypatch)
        assert resolve_assistant_config().max_output_tokens == 8192

    def test_valid_value_is_used(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        _configured(project_root, monkeypatch)
        monkeypatch.setenv("HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS", "4096")
        assert resolve_assistant_config().max_output_tokens == 4096

    @pytest.mark.parametrize("raw", ["abc", "0", "-5", "4096.5", ""])
    def test_malformed_or_non_positive_fails_readiness(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch, raw: str
    ):
        """Never warn-and-default: a silently substituted cost ceiling is the
        wrong-fallback class the project forbids."""

        _configured(project_root, monkeypatch)
        monkeypatch.setenv("HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS", raw)
        status = assistant_readiness()
        assert status.configured is False
        assert "HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS" in (status.reason or "")


def _git_state(state: str, errors: list[str] | None = None) -> GitWorkingBranchResponse:
    return GitWorkingBranchResponse(state=state, errors=errors or [], current_branch="feature")


class TestMutationGate:
    @pytest.fixture()
    def patched_git(self, monkeypatch: pytest.MonkeyPatch):
        def patch(response: GitWorkingBranchResponse | Exception):
            def fake_status(_root, cwd=None):
                if isinstance(response, Exception):
                    raise response
                return response

            monkeypatch.setattr(_config._git, "working_branch_status", fake_status)

        return patch

    def test_ready_enables_mutations(self, project_root: Path, patched_git):
        patched_git(_git_state("ready"))
        enabled, reason = _config.mutations_readiness()
        assert (enabled, reason) == (True, None)

    def test_unset_directs_to_git_panel(self, project_root: Path, patched_git):
        patched_git(_git_state("unset"))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "working branch" in (reason or "").lower()
        assert "Git panel" in (reason or "")

    def test_no_repository_directs_to_git_initialisation(self, project_root: Path, patched_git):
        patched_git(_git_state("no-repository"))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "initialise git" in (reason or "").lower()

    def test_detached_head_directs_to_git_panel(self, project_root: Path, patched_git):
        patched_git(_git_state("detached"))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "attach head" in (reason or "").lower()
        assert "Git panel" in (reason or "")

    def test_divergent_directs_to_git_panel(self, project_root: Path, patched_git):
        patched_git(_git_state("divergent"))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "diverg" in (reason or "").lower()

    def test_invalid_joins_git_layer_errors(self, project_root: Path, patched_git):
        patched_git(_git_state("invalid", ["branch missing", "invariant violated"]))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "branch missing" in (reason or "")
        assert "invariant violated" in (reason or "")

    def test_git_domain_raise_maps_to_disabled_with_reason(self, project_root: Path, patched_git):
        patched_git(HauteError("Not a git repository. Run 'git init' first."))
        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert "git init" in (reason or "")

    def test_real_non_git_project_reports_disabled(self, project_root: Path):
        """No patching: the readiness endpoint path over a bare tmp dir."""

        enabled, reason = _config.mutations_readiness()
        assert enabled is False
        assert reason
