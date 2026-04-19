"""Tests for haute._mlflow_utils — shared MLflow helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from haute._mlflow_utils import resolve_version, search_versions

# ---------------------------------------------------------------------------
# search_versions
# ---------------------------------------------------------------------------


class TestSearchVersions:
    def test_calls_client_with_quoted_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        result = search_versions(client, "my_model")
        assert result == []
        client.search_model_versions.assert_called_once_with("name='my_model'")

    def test_escapes_single_quotes_in_model_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        result = search_versions(client, "model's_name")
        assert result == []
        call_arg = client.search_model_versions.call_args[0][0]
        assert "\\'" in call_arg

    def test_returns_client_result(self):
        client = MagicMock()
        v1 = MagicMock(version="1")
        client.search_model_versions.return_value = [v1]
        result = search_versions(client, "model")
        assert result == [v1]


# ---------------------------------------------------------------------------
# resolve_version
# ---------------------------------------------------------------------------


class TestResolveVersion:
    def test_explicit_version_returned_as_is(self):
        client = MagicMock()
        assert resolve_version(client, "model", "3") == "3"

    def test_explicit_version_does_not_call_search(self):
        client = MagicMock()
        result = resolve_version(client, "model", "3")
        assert result == "3"
        client.search_model_versions.assert_not_called()

    def test_latest_resolves_to_highest(self):
        client = MagicMock()
        v1 = MagicMock(version="1")
        v2 = MagicMock(version="2")
        v3 = MagicMock(version="3")
        client.search_model_versions.return_value = [v1, v3, v2]
        result = resolve_version(client, "model", "latest")
        assert result == "3"

    def test_empty_version_resolves_to_latest(self):
        client = MagicMock()
        v = MagicMock(version="5")
        client.search_model_versions.return_value = [v]
        result = resolve_version(client, "model", "")
        assert result == "5"

    def test_no_versions_raises(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        with pytest.raises(ValueError, match="No versions found"):
            resolve_version(client, "model", "latest")

    def test_no_versions_error_includes_model_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        with pytest.raises(ValueError, match="my-special-model"):
            resolve_version(client, "my-special-model", "")

    def test_sorts_by_integer_not_string(self):
        """Versions '10' and '9': string sort would put '9' > '10'."""
        client = MagicMock()
        v9 = MagicMock(version="9")
        v10 = MagicMock(version="10")
        client.search_model_versions.return_value = [v9, v10]
        result = resolve_version(client, "model", "latest")
        assert result == "10"
