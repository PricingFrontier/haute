"""Shared Databricks credential-resolution contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from haute._databricks_credentials import (
    DatabricksConfigError,
    DatabricksSignInError,
    credentials_rejected,
    databricks_sdk_config,
    databricks_workspace_client,
    resolve_databricks_credentials,
)
from haute._databricks_io import DatabricksConfigError as LegacyDatabricksConfigError


@pytest.fixture(autouse=True)
def _clean_databricks_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_CLI_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    # Neither haute nor the SDK may read the developer's own ~/.databrickscfg.
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "no-profiles.cfg"))


def _profiles_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    path = tmp_path / "databrickscfg"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(path))
    return path


def test_pat_precedence_and_host_projections_are_resolved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "  https://workspace.example/  ")
    monkeypatch.setenv("DATABRICKS_TOKEN", " pat-secret-value ")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "ignored-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ignored-secret")

    credentials = resolve_databricks_credentials()

    assert credentials.workspace_host == "https://workspace.example"
    assert credentials.server_hostname == "workspace.example"
    assert credentials.auth_mode == "pat"
    assert credentials.token == " pat-secret-value "
    assert credentials.client_id is None
    assert credentials.client_secret is None


def test_service_principal_pair_is_selected_without_a_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example/")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-value")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-value")

    credentials = resolve_databricks_credentials()

    assert credentials.workspace_host == "workspace.example"
    assert credentials.server_hostname == "workspace.example"
    assert credentials.auth_mode == "service_principal"
    assert credentials.token is None
    assert credentials.client_id == "client-value"
    assert credentials.client_secret == "secret-value"


def test_error_aggregates_named_requirements_without_credential_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "exposed-client-value")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials(
            additional_missing=("http_path on the Data Input node",),
        )

    message = str(exc_info.value)
    assert "DATABRICKS_TOKEN" in message
    assert "DATABRICKS_CLIENT_SECRET" in message
    assert "http_path on the Data Input node" in message
    assert "exposed-client-value" not in message


def test_credential_representation_redacts_all_auth_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-value")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-value")

    rendered = repr(resolve_databricks_credentials())

    assert "service_principal" in rendered
    assert "client-value" not in rendered
    assert "secret-value" not in rendered


def test_sql_io_reexports_the_shared_error_class() -> None:
    assert LegacyDatabricksConfigError is DatabricksConfigError


def _rejections() -> list[tuple[BaseException, bool]]:
    from databricks.sdk.errors import PermissionDenied, Unauthenticated

    return [
        (Unauthenticated("Invalid access token."), True),
        # The SDK's OAuth token exchange, refusing a service principal.
        (ValueError("invalid_client: Client authentication failed"), True),
        (ValueError("unauthorized_client: Client is not allowed this grant"), True),
        (ValueError("invalid_grant: The grant has expired"), True),
        (ValueError("invalid_request: Missing grant_type"), False),
        (ValueError("invalid literal for int() with base 10: 'x'"), False),
        (PermissionDenied("Missing USE CATALOG."), False),
        (RuntimeError("invalid_client: not from the token endpoint's exchange"), False),
    ]


def test_only_a_refusal_of_the_credentials_counts_as_one() -> None:
    assert [credentials_rejected(exc) for exc, _ in _rejections()] == [
        expected for _, expected in _rejections()
    ]


# ---------------------------------------------------------------------------
# User sign-ins: a named CLI profile, or an auth type with the host
# ---------------------------------------------------------------------------


def test_a_named_profile_is_read_from_the_cli_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profiles_file(
        tmp_path,
        monkeypatch,
        "[DEFAULT]\n\n[work]\nhost = https://adb-1.azuredatabricks.net/\n"
        "auth_type = databricks-cli\n",
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    credentials = resolve_databricks_credentials()

    assert credentials.auth_mode == "profile"
    assert credentials.profile == "work"
    assert credentials.workspace_host == "https://adb-1.azuredatabricks.net"
    assert credentials.server_hostname == "adb-1.azuredatabricks.net"
    assert credentials.sdk_auth_type == "databricks-cli"
    assert credentials.token is None
    assert credentials.client_id is None
    assert credentials.client_secret is None
    assert credentials.signs_in_as_user


def test_a_profile_does_not_inherit_the_default_section_as_the_sdk_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``configparser`` would hand ``[team]`` the ``[DEFAULT]`` host; the SDK reads each
    profile's own keys only, so haute must refuse what the SDK would fail on."""
    _profiles_file(
        tmp_path,
        monkeypatch,
        "[DEFAULT]\nhost = shared.example\n\n[team]\nauth_type = databricks-cli\n",
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "team")

    with pytest.raises(DatabricksConfigError, match="Profile 'team' .* has no host"):
        resolve_databricks_credentials()


def test_the_default_section_is_the_profile_named_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profiles_file(
        tmp_path, monkeypatch, "[DEFAULT]\nhost = shared.example\nauth_type = databricks-cli\n"
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT")

    credentials = resolve_databricks_credentials()

    assert credentials.profile == "DEFAULT"
    assert credentials.workspace_host == "shared.example"
    assert credentials.sdk_auth_type == "databricks-cli"


@pytest.mark.parametrize(
    ("section", "pinned"),
    [
        ("token = dapi-in-file", "pat"),
        ("auth_type = pat\ntoken = dapi-in-file", "pat"),
        ("client_id = app-id\nclient_secret = app-secret", "oauth-m2m"),
        ("auth_type = oauth-m2m\nclient_id = app-id\nclient_secret = app-secret", "oauth-m2m"),
        ("auth_type = databricks-cli", "databricks-cli"),
    ],
)
def test_a_profile_s_sign_in_is_pinned_from_what_it_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, pinned: str
) -> None:
    _profiles_file(tmp_path, monkeypatch, f"[work]\nhost = https://workspace.example\n{section}\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    credentials = resolve_databricks_credentials()

    assert credentials.auth_mode == "profile"
    assert credentials.sdk_auth_type == pinned
    # The file's own credentials stay in the file: the SDK reads them, haute never does.
    assert credentials.token is None
    assert credentials.client_secret is None


@pytest.mark.parametrize(
    ("section", "said"),
    [
        ("auth_type = azure-cli", "does not use for data access"),
        ("auth_type = basic", "does not use for data access"),
        ("auth_type = pat", "holds no token"),
        ("auth_type = oauth-m2m\nclient_id = app-id", "lacks client_id or client_secret"),
    ],
)
def test_a_profile_pinned_to_a_sign_in_it_does_not_hold_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, said: str
) -> None:
    """Any other type would let the SDK merge ambient credentials (an Azure CLI login,
    DATABRICKS_USERNAME/DATABRICKS_PASSWORD) under a name the profile gave."""
    _profiles_file(tmp_path, monkeypatch, f"[work]\nhost = https://workspace.example\n{section}\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")
    monkeypatch.setenv("DATABRICKS_USERNAME", "someone")
    monkeypatch.setenv("DATABRICKS_PASSWORD", "ambient-secret")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "Profile 'work'" in message
    assert said in message
    assert "ambient-secret" not in message


def test_only_a_missing_or_expired_login_earns_the_login_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI's own message decides: a locked keychain or a proxy is not cured by
    `databricks auth login`, so those failures point at the server log instead."""
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")
    credentials = resolve_databricks_credentials()

    expired = (
        "The Databricks CLI sign-in failed or has expired",
        "run `databricks auth login --host <your DATABRICKS_HOST>` and try again",
    )
    assert credentials.sign_in_failure() == expired
    assert (
        credentials.sign_in_failure(
            "cannot get access token: Error: A new access token "
            "could not be retrieved because the refresh token is "
            "invalid."
        )
        == expired
    )
    assert credentials.sign_in_failure("Error: not logged in") == expired

    problem, remedy = credentials.sign_in_failure(
        "cannot get access token: Error: keychain is locked: user interaction required"
    )
    assert problem == "The Databricks CLI could not produce a token for DATABRICKS_HOST"
    assert "server log" in remedy
    assert "keychain" in remedy
    assert "expired" not in problem


def test_a_profile_naming_only_a_host_is_refused_even_with_ambient_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left unpinned, the SDK's default chain would sign in with whatever the machine
    has, basic auth from the environment or an Azure CLI login, before the CLI's."""
    path = _profiles_file(tmp_path, monkeypatch, "[work]\nhost = https://workspace.example\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")
    monkeypatch.setenv("DATABRICKS_USERNAME", "someone")
    monkeypatch.setenv("DATABRICKS_PASSWORD", "ambient-secret")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "Profile 'work'" in message
    assert str(path) in message
    assert "names only a host" in message
    assert "databricks auth login --host <workspace URL> --profile work" in message
    assert "ambient-secret" not in message


def test_a_profile_declaring_the_browser_sign_in_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profiles_file(
        tmp_path,
        monkeypatch,
        "[work]\nhost = https://workspace.example\nauth_type = external-browser\n",
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "Profile 'work'" in message
    assert "external-browser" in message
    assert "databricks auth login --host <workspace URL> --profile work" in message


def test_a_missing_config_file_is_refused_naming_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "DATABRICKS_CONFIG_PROFILE" in message
    assert "profile 'work'" in message
    assert str(tmp_path / "no-profiles.cfg") in message
    assert "databricks auth login --host <workspace URL> --profile work" in message


def test_a_profile_the_config_file_lacks_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _profiles_file(tmp_path, monkeypatch, "[other]\nhost = https://workspace.example\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "profile 'work'" in message
    assert str(path) in message
    assert "databricks auth login --host <workspace URL> --profile work" in message


def test_a_profile_without_a_host_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profiles_file(tmp_path, monkeypatch, "[work]\nauth_type = databricks-cli\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    with pytest.raises(DatabricksConfigError, match="Profile 'work' .* has no host"):
        resolve_databricks_credentials()


def test_an_unparseable_config_file_is_refused_without_echoing_its_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _profiles_file(
        tmp_path, monkeypatch, "[work]\nhost = https://x\ndapi-secret-line-without-a-key\n"
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert str(path) in message
    assert "dapi-secret" not in message


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABRICKS_HOST", "https://other.example"),
        ("DATABRICKS_TOKEN", "pat-secret-value"),
        ("DATABRICKS_CLIENT_ID", "client-value"),
        ("DATABRICKS_CLIENT_SECRET", "secret-value"),
        ("DATABRICKS_AUTH_TYPE", "databricks-cli"),
    ],
)
def test_a_profile_is_refused_beside_any_other_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _profiles_file(tmp_path, monkeypatch, "[work]\nhost = https://workspace.example\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")
    monkeypatch.setenv(name, value)

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "DATABRICKS_CONFIG_PROFILE" in message
    assert name in message
    assert value not in message


def test_the_cli_auth_type_selects_the_cli_sign_in_with_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", " https://workspace.example/ ")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")

    credentials = resolve_databricks_credentials()

    assert credentials.auth_mode == "cli_login"
    assert credentials.sdk_auth_type == "databricks-cli"
    assert credentials.workspace_host == "https://workspace.example"
    assert credentials.server_hostname == "workspace.example"
    assert credentials.profile is None
    assert credentials.token is None
    assert credentials.client_id is None
    assert credentials.client_secret is None
    assert credentials.signs_in_as_user


def test_an_auth_type_without_a_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials(additional_missing=("http_path on the Data Input node",))

    message = str(exc_info.value)
    assert "DATABRICKS_HOST" in message
    assert "http_path on the Data Input node" in message
    assert "DATABRICKS_TOKEN" not in message


@pytest.mark.parametrize("auth_type", ["oauth-m2m", "external-browser", "pat"])
def test_any_other_auth_type_is_refused_naming_the_accepted_one(
    monkeypatch: pytest.MonkeyPatch, auth_type: str
) -> None:
    """Only the CLI's sign-in is offered through the auth type: a token or pair has
    its own variables, and the SDK's browser sign-in cannot be timed out or cancelled."""
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", auth_type)

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "DATABRICKS_AUTH_TYPE" in message
    assert "databricks-cli" in message


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABRICKS_TOKEN", "pat-secret-value"),
        ("DATABRICKS_CLIENT_ID", "client-value"),
        ("DATABRICKS_CLIENT_SECRET", "secret-value"),
    ],
)
def test_an_auth_type_is_refused_beside_a_token_or_pair(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")
    monkeypatch.setenv(name, value)

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    assert "DATABRICKS_AUTH_TYPE" in message
    assert name in message
    assert value not in message


def test_the_missing_message_names_every_sign_in_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials()

    message = str(exc_info.value)
    for name in (
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CONFIG_PROFILE",
    ):
        assert name in message
    assert "DATABRICKS_HOST" not in message.split(":", 1)[1].split("DATABRICKS_AUTH_TYPE")[0]


def test_user_sign_ins_have_no_credential_fields_to_redact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profiles_file(
        tmp_path,
        monkeypatch,
        "[work]\nhost = https://workspace.example\nauth_type = databricks-cli\n",
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

    rendered = repr(resolve_databricks_credentials())

    assert "profile='work'" in rendered
    assert "token" not in rendered
    assert "client_secret" not in rendered


# ---------------------------------------------------------------------------
# The SDK handoff, through a fake Databricks CLI
# ---------------------------------------------------------------------------

_CLI_TOKEN_JSON = (
    '{"access_token":"cli-token-123","token_type":"Bearer","expiry":"2099-01-01T00:00:00Z"}'
)


def _fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    """A ``databricks`` stand-in the SDK runs for ``auth token`` (``DATABRICKS_CLI_PATH``)."""
    path = tmp_path / "databricks"
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("DATABRICKS_CLI_PATH", str(path))
    return path


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake Databricks CLI is a POSIX shell script; the SDK's handoff is "
    "platform-neutral and the resolver's own tests above run everywhere",
)
class TestSdkHandoffThroughTheCli:
    """The real SDK, driven through a fake ``databricks`` executable.

    Observed under databricks-sdk 0.88 (pyproject caps it below 0.89): auth type
    ``databricks-cli`` runs ``<cli> auth token --host <host>`` and reads the JSON's
    ``access_token``, ``token_type`` and ``expiry``; the header is the token verbatim.
    """

    def test_cli_sign_in_hands_the_sdk_the_token_the_cli_prints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(tmp_path, monkeypatch, f"printf '%s' '{_CLI_TOKEN_JSON}'\n")
        monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
        monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")
        credentials = resolve_databricks_credentials()

        config = databricks_sdk_config(credentials)

        assert config.authenticate() == {"Authorization": "Bearer cli-token-123"}
        assert config.auth_type == "databricks-cli"
        assert databricks_workspace_client(credentials).config.auth_type == "databricks-cli"

    def test_a_profile_s_pinned_sign_in_wins_over_ambient_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK loads the environment before the profile and, left to its default
        chain, would sign in with basic auth; the pinned auth type forces the CLI."""
        _fake_cli(tmp_path, monkeypatch, f"printf '%s' '{_CLI_TOKEN_JSON}'\n")
        _profiles_file(
            tmp_path,
            monkeypatch,
            "[work]\nhost = https://workspace.example\nauth_type = databricks-cli\n",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")
        monkeypatch.setenv("DATABRICKS_USERNAME", "someone")
        monkeypatch.setenv("DATABRICKS_PASSWORD", "ambient-secret")

        config = databricks_sdk_config(resolve_databricks_credentials())

        assert config.auth_type == "databricks-cli"
        assert config.authenticate() == {"Authorization": "Bearer cli-token-123"}

    def test_a_profile_signs_in_through_the_cli_with_the_profile_s_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = tmp_path / "cli-args"
        _fake_cli(
            tmp_path, monkeypatch, f"echo \"$@\" > \"{seen}\"\nprintf '%s' '{_CLI_TOKEN_JSON}'\n"
        )
        _profiles_file(
            tmp_path,
            monkeypatch,
            "[work]\nhost = https://workspace.example\nauth_type = databricks-cli\n",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

        config = databricks_sdk_config(resolve_databricks_credentials())

        assert config.authenticate() == {"Authorization": "Bearer cli-token-123"}
        assert seen.read_text(encoding="utf-8").split() == [
            "auth",
            "token",
            "--host",
            "https://workspace.example",
        ]

    def test_an_expired_cli_sign_in_becomes_a_sign_in_error_with_the_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(
            tmp_path,
            monkeypatch,
            "echo 'Error: A new access token could not be retrieved because the refresh token"
            " is invalid. To reauthenticate, run the following command:' >&2\n"
            "echo '  $ databricks auth login --profile work' >&2\nexit 1\n",
        )
        _profiles_file(
            tmp_path,
            monkeypatch,
            "[work]\nhost = https://workspace.example\nauth_type = databricks-cli\n",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")

        with pytest.raises(DatabricksSignInError) as exc_info:
            databricks_sdk_config(resolve_databricks_credentials())

        assert str(exc_info.value) == (
            "Databricks sign-in for profile 'work' failed or has expired: "
            "run `databricks auth login --profile work` and try again."
        )
        assert exc_info.value.problem == (
            "Databricks sign-in for profile 'work' failed or has expired"
        )
        assert "refresh token is invalid" in str(exc_info.value.__cause__)
        assert credentials_rejected(exc_info.value)
        assert isinstance(exc_info.value, DatabricksConfigError)

    def test_a_failed_cli_sign_in_by_auth_type_names_the_host_variable_not_the_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(tmp_path, monkeypatch, "echo 'not logged in' >&2\nexit 1\n")
        monkeypatch.setenv("DATABRICKS_HOST", "https://secret-corp.example")
        monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")

        with pytest.raises(DatabricksSignInError) as exc_info:
            databricks_sdk_config(resolve_databricks_credentials())

        assert str(exc_info.value) == (
            "The Databricks CLI sign-in failed or has expired: "
            "run `databricks auth login --host <your DATABRICKS_HOST>` and try again."
        )
        assert "secret-corp" not in str(exc_info.value)

    def test_a_cli_path_that_does_not_exist_is_reported_as_the_cli_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_CLI_PATH", str(tmp_path / "no-such" / "databricks"))
        monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
        monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")

        with pytest.raises(DatabricksSignInError) as exc_info:
            databricks_sdk_config(resolve_databricks_credentials())

        assert exc_info.value.problem == "The Databricks CLI was not found by the server"
        assert "DATABRICKS_CLI_PATH" in exc_info.value.remedy
        assert "expired" not in str(exc_info.value)

    def test_a_cli_missing_from_the_path_is_reported_as_the_cli_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without DATABRICKS_CLI_PATH the SDK looks along PATH; finding nothing, its
        CLI provider declines and the SDK reports no credentials at all."""
        monkeypatch.delenv("DATABRICKS_CLI_PATH", raising=False)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
        monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "databricks-cli")

        with pytest.raises(DatabricksSignInError) as exc_info:
            databricks_sdk_config(resolve_databricks_credentials())

        assert exc_info.value.problem == "The Databricks CLI was not found by the server"


def test_a_token_or_service_principal_needs_no_sdk_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "pat")

    with pytest.raises(ValueError, match="pat"):
        databricks_sdk_config(resolve_databricks_credentials())
