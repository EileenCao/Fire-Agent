from pathlib import Path

from mcp_server.services.historical_data import (
    HistoricalDataResult,
    WorkspaceHistoricalDataProvider,
)
from mcp_server.workspace import initialize_workspace


class _Skill:
    name = "a-stock-data"
    version = "3.6.0"


class _FakeCache:
    def __init__(self, root):
        self.root = Path(root)
        self.calls = []

    def write(self, code, bars, provenance):
        self.calls.append((code, list(bars), dict(provenance)))
        path = self.root / (code + ".parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake parquet", encoding="utf-8")
        return path


class _ReusableCache:
    def __init__(self, root):
        self.root = Path(root)
        self.read_calls = 0

    def path_for(self, code, source_version):
        return self.root / (code + ".parquet")

    def read_metadata(self, code, source_version):
        return {
            "requested_start": "2025-01-01",
            "requested_end": "2026-12-31",
            "source_version": source_version,
        }

    def read(self, code, source_version):
        self.read_calls += 1
        return [
            {"date": "2026-01-01", "open": 10, "high": 11, "low": 9, "close": 10},
            {"date": "2026-01-02", "open": 10, "high": 11, "low": 9, "close": 10},
        ]


def test_provider_normalizes_filters_and_records_provenance(tmp_path):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace = initialize_workspace(project_root, tmp_path / "FireAgentWorkspace")
    cache = _FakeCache(workspace.parquet_dir)

    def fetcher(code, market, start_date, end_date):
        assert code == "512890"
        assert market == "SH"
        return [
            {"date": "2025-12-31", "open": 9, "high": 9, "low": 9, "close": 9},
            {"date": "2026-01-01", "open": 10, "high": 11, "low": 9, "close": 10},
            {"date": "2026-01-03", "open": 11, "high": 12, "low": 10, "close": 11},
        ]

    provider = WorkspaceHistoricalDataProvider(
        workspace=workspace,
        skill=_Skill(),
        fetcher=fetcher,
        cache=cache,
    )

    result = provider.fetch(["SH512890"], "2026-01-01", "2026-01-02")

    assert isinstance(result, HistoricalDataResult)
    assert list(result.data) == ["512890"]
    assert [bar["date"] for bar in result.data["512890"]] == ["2026-01-01"]
    assert result.provenance["skill_version"] == "3.6.0"
    assert result.provenance["price_basis"] == "adjusted"
    assert result.cache_paths["512890"].endswith("512890.parquet")
    assert (workspace.raw_dir / "512890.json").exists()


def test_provider_returns_explicit_missing_symbols_and_errors(tmp_path):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace = initialize_workspace(project_root, tmp_path / "FireAgentWorkspace")

    def fetcher(code, market, start_date, end_date):
        raise RuntimeError("source unavailable")

    provider = WorkspaceHistoricalDataProvider(
        workspace=workspace,
        skill=_Skill(),
        fetcher=fetcher,
        cache=None,
    )

    result = provider.fetch(["512890"], "2026-01-01", "2026-01-02")

    assert result.data == {}
    assert result.missing_symbols == ["512890"]
    assert "512890" in result.errors
    assert "source unavailable" in result.errors["512890"]


def test_provider_reuses_a_matching_parquet_snapshot(tmp_path):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace = initialize_workspace(project_root, tmp_path / "FireAgentWorkspace")
    cache = _ReusableCache(workspace.parquet_dir)

    def fail_if_called(code, market, start_date, end_date):
        raise AssertionError("matching cache should avoid a source call")

    provider = WorkspaceHistoricalDataProvider(
        workspace=workspace,
        skill=_Skill(),
        fetcher=fail_if_called,
        cache=cache,
    )

    result = provider.fetch(["512890"], "2026-01-01", "2026-01-02")

    assert result.data["512890"][0]["date"] == "2026-01-01"
    assert result.provenance["per_symbol"]["512890"]["cache_hit"] is True
    assert cache.read_calls == 1
