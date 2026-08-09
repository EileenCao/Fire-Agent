"""A-share trading-day gate with an optional XSHG calendar backend."""

import json
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Set, Union


class TradingCalendar:
    def __init__(
        self,
        holidays: Optional[Iterable[date]] = None,
        holiday_file: Optional[Union[str, Path]] = None,
    ):
        self.holidays: Set[date] = set(holidays or ())
        if holiday_file:
            self.holidays.update(_load_holidays(Path(holiday_file)))
        self._xshg = _load_xshg_calendar()

    @property
    def source(self) -> str:
        if self._xshg is not None:
            return "exchange_calendars:XSHG"
        return "weekday_plus_configured_holidays"

    def is_trading_day(self, value: date) -> bool:
        if value.weekday() >= 5 or value in self.holidays:
            return False
        if self._xshg is None:
            return True
        try:
            import pandas as pd

            return bool(self._xshg.is_session(pd.Timestamp(value)))
        except Exception:
            # A broken optional calendar must not prevent a local dry run.
            return True


def _load_xshg_calendar():
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XSHG")
    except Exception:
        return None


def _load_holidays(path: Path) -> Set[date]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("holidays", payload) if isinstance(payload, dict) else payload
    return {date.fromisoformat(str(value)) for value in values}
