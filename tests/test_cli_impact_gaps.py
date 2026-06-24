"""Coverage-gap tests for haute.cli._impact.

Covers the branches the broader ``test_cli_impact.py`` suite leaves untested:

* unsupported transport target → warning + early return (lines 119→127, 131);
* ``_impact_databricks`` when ``databricks-sdk`` is not installed (268-273);
* ``_impact_databricks`` scoring production when the prod endpoint exists
  (294→295, 296).
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli._helpers import TransportInfo
from haute.cli._impact import ImpactConfig, _impact_databricks, handle_impact

if TYPE_CHECKING:
    from pathlib import Path


def _write_min_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minimal haute.toml + impact dataset rooted at *tmp_path*."""
    import polars as pl

    monkeypatch.chdir(tmp_path)
    toml = (
        '[project]\nname = "t"\npipeline = "main.py"\n'
        '[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        'target = "databricks"\n'
        '[safety]\nimpact_dataset = "data/impact.parquet"\n'
        '[ci]\nprovider = "github"\n'
        '[ci.staging]\nendpoint_suffix = "-staging"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)
    (tmp_path / ".git").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame({"VehPower": [5, 6], "premium": [100.0, 200.0]}).write_parquet(
        data_dir / "impact.parquet"
    )


class TestUnsupportedTarget:
    def test_unsupported_transport_warns_and_returns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A transport with an unknown ``kind`` should warn and return early
        without writing any report."""
        _write_min_project(tmp_path, monkeypatch)

        with patch(
            "haute.cli._impact.resolve_transport",
            return_value=TransportInfo(kind="unsupported"),
        ):
            result = handle_impact(ImpactConfig(endpoint_suffix=None, sample=0, batch_size=500))

        assert result is None
        captured = capsys.readouterr()
        assert "not yet implemented" in captured.err
        # Early return → no report artifact written.
        assert not (tmp_path / "impact_report.md").exists()


class TestImpactDatabricksImportError:
    def test_missing_sdk_exits(self) -> None:
        """When ``databricks.sdk`` cannot be imported the helper should echo
        an install hint and raise SystemExit(1)."""
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "databricks.sdk" or name.startswith("databricks.sdk"):
                raise ImportError("no databricks")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("haute.deploy._config._load_env"),
            patch("haute.deploy._impact.score_endpoint_batched", return_value=[]),
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(SystemExit) as excinfo,
        ):
            _impact_databricks("stg", "prod", [{"x": 1}], 100)

        assert excinfo.value.code == 1


class TestImpactDatabricksProdScoring:
    def test_prod_exists_scores_production(self) -> None:
        """When the prod endpoint exists, production should also be scored and
        returned (the prod-scoring branch)."""
        mock_ws = MagicMock()
        # serving_endpoints.get succeeds → prod_exists stays True.
        mock_ws.serving_endpoints.get.return_value = MagicMock()

        staging_preds = [{"p": 1.0}]
        prod_preds = [{"p": 0.9}]

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                side_effect=[staging_preds, prod_preds],
            ) as mock_score,
        ):
            staging, prod, exists = _impact_databricks("stg", "prod", [{"x": 1}], 100)

        assert exists is True
        assert staging == staging_preds
        assert prod == prod_preds
        # Both staging and production were scored.
        assert mock_score.call_count == 2
