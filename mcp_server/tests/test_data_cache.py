from pathlib import Path

from mcp_server.services.data_cache import ParquetDataCache, ParquetDataCacheError


def test_parquet_cache_path_is_deterministic_and_outside_sqlite(tmp_path):
    cache = ParquetDataCache(tmp_path / "cache")

    first = cache.path_for("SH512890", "a-stock-data:3.6.0")
    second = cache.path_for("512890", "a-stock-data:3.6.0")

    assert first == second
    assert first.suffix == ".parquet"
    assert first.parent == Path(tmp_path / "cache")


def test_parquet_cache_reports_missing_engine_without_fabricating_json(tmp_path):
    cache = ParquetDataCache(tmp_path / "cache")
    try:
        cache.write("512890", [{"date": "2026-01-01", "close": 1}], {})
    except ParquetDataCacheError as exc:
        assert "Parquet" in str(exc)
