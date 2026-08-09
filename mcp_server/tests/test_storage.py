from datetime import time

from mcp_server.storage import SQLiteStore


def test_watchlist_round_trip_normalizes_code_and_preserves_type(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()

    item = store.add_watchlist_item(
        "SH512890", instrument_type="ETF", note="红利观察"
    )

    assert item.code == "512890"
    assert item.market == "SH"
    assert item.instrument_type == "ETF"
    assert item.note == "红利观察"
    assert store.list_watchlist() == [item]


def test_schedule_round_trip_uses_noon_send_window(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()

    schedule = store.configure_daily_report(
        enabled=True,
        timezone="Asia/Shanghai",
        wake_time=time(12, 0),
        send_start=time(12, 3),
        send_end=time(12, 5),
        trading_days_only=True,
    )

    loaded = store.get_daily_report_schedule()
    assert loaded == schedule
    assert loaded.wake_time == time(12, 0)
    assert loaded.send_start == time(12, 3)
    assert loaded.send_end == time(12, 5)
    assert loaded.trading_days_only is True


def test_delivery_attempt_records_chunk_and_format_metadata(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    report = store.create_report_run(
        idempotency_key="daily_watchlist:2026-08-10:morning_close:v1",
        report_date=__import__("datetime").date(2026, 8, 10),
        session="morning_close",
        data_as_of="2026-08-10T11:30:00+08:00",
        status="sending",
        content="日报",
    )

    store.record_delivery_attempt(
        run_id=report["id"],
        channel_id="feishu-main",
        attempt=2,
        status="sent",
        response_code=200,
        chunk_index=1,
        chunk_count=2,
        content_format="post",
    )

    latest = store.notification_status()["latest_delivery"]
    assert latest["attempt"] == 2
    assert latest["chunk_index"] == 1
    assert latest["chunk_count"] == 2
    assert latest["content_format"] == "post"
