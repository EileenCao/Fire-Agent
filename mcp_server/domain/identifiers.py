"""Strict A-share ticker normalization used by every boundary."""

import re
from typing import Optional, Tuple


_PREFIXED = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_SUFFIXED = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")
_BARE = re.compile(r"^\d{6}$")


def normalize_ticker(code: str, market: Optional[str] = None) -> Tuple[str, str]:
    """Return ``(six_digit_code, market)`` without guessing malformed input."""
    if not isinstance(code, str):
        raise ValueError("代码必须是字符串")

    value = code.strip().upper()
    parsed_market = None
    digits = None
    match = _PREFIXED.fullmatch(value)
    if match:
        parsed_market, digits = match.group(1), match.group(2)
    else:
        match = _SUFFIXED.fullmatch(value)
        if match:
            digits, parsed_market = match.group(1), match.group(2)
        elif _BARE.fullmatch(value):
            digits = value
        else:
            raise ValueError("代码必须是 6 位数字，可带 SH/SZ/BJ 前后缀")

    requested_market = market.upper() if market else None
    if requested_market and requested_market not in {"SH", "SZ", "BJ"}:
        raise ValueError("market 必须是 SH、SZ 或 BJ")
    if parsed_market and requested_market and parsed_market != requested_market:
        raise ValueError("代码中的市场与 market 参数冲突")

    resolved_market = parsed_market or requested_market or _natural_market(digits)
    return digits, resolved_market


def _natural_market(digits: str) -> str:
    if digits.startswith(("92", "8", "4")):
        return "BJ"
    if digits.startswith(("5", "6", "9")):
        return "SH"
    if digits.startswith(("0", "1", "2", "3")):
        return "SZ"
    raise ValueError("无法根据代码确定 A 股市场，请显式提供 market")
