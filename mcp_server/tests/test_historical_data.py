from pathlib import Path

from mcp_server.services.historical_data import (
    AStockDailyBarsFetcher,
    HistoricalDataResult,
    WorkspaceHistoricalDataProvider,
)
from mcp_server.workspace import initialize_workspace


class _Skill:
    name = "a-stock-data"
    version = "3.6.0"


def test_tencent_daily_bars_fetcher_bypasses_environment_proxy_by_default():
    import requests

    session = requests.Session()
    AStockDailyBarsFetcher(session=session)

    assert session.trust_env is False


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


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _TencentSession:
    def __init__(self):
        self.calls = []
        self.headers = {}

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        parts = params["param"].split(",")
        start, end = parts[2], parts[3]
        rows = [
            [start, "10", "10", "11", "9", "100"],
            ["2026-01-03", "11", "11", "12", "10", "101"],
            [end, "12", "12", "13", "11", "102"],
        ]
        return _FakeResponse({"data": {"sh512890": {"qfqday": rows}}})


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


def test_tencent_fetcher_segments_date_ranges_and_deduplicates_rows():
    session = _TencentSession()
    fetcher = AStockDailyBarsFetcher(session=session, segment_days=2)

    bars = fetcher("512890", "SH", "2026-01-01", "2026-01-05")

    assert len(session.calls) == 3
    for _, params, _ in session.calls:
        parts = params["param"].split(",")
        assert parts[0] == "sh512890"
        assert parts[1] == "day"
        assert len(parts[2]) == 10 and parts[2][4] == "-" and parts[2][7] == "-"
        assert len(parts[3]) == 10 and parts[3][4] == "-" and parts[3][7] == "-"
        assert int(parts[4]) <= 640
        assert parts[5] == "qfq"
    dates = [bar["date"] for bar in bars]
    assert dates == sorted(set(dates))
    assert dates[0] == "2026-01-01"
    assert dates[-1] == "2026-01-05"
