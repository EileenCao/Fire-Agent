"""Runtime wiring for the frozen 512890 morning strategy signal."""

import json
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.strategy_signal import MorningStrategySignalEvaluator


def build_morning_strategy_signal_builder(
    strategy_path: Path,
    historical_provider,
    evaluator: Optional[MorningStrategySignalEvaluator] = None,
    external_position_provider=None,
):
    """Return a report callback backed by one explicit, immutable strategy file."""

    payload = json.loads(Path(strategy_path).read_text(encoding="utf-8"))
    spec = StrategySpec.from_dict(payload)
    evaluator = evaluator or MorningStrategySignalEvaluator()

    def build(items: Iterable, snapshots: Iterable, report_date: date):
        snapshot_by_code = {item.code: item for item in snapshots}
        results = []
        for code in spec.universe:
            external_position = (
                external_position_provider(code)
                if external_position_provider is not None
                else None
            )
            snapshot = snapshot_by_code.get(code)
            if snapshot is None or snapshot.price is None:
                results.append(
                    {
                        "status": "unavailable",
                        "code": code,
                        "action": "UNDETERMINED",
                        "mode": evaluator.mode,
                        "strategy_id": spec.strategy_id,
                        "strategy_version": spec.version,
                        "error": "上午行情价格不可用",
                        "external_position": external_position,
                    }
                )
                continue
            fetched = historical_provider.fetch(
                [code], spec.validation.get("start_date", "2019-01-01"), report_date
            )
            bars = [
                bar
                for bar in (fetched.data.get(code) or [])
                if str(bar.get("date")) < report_date.isoformat()
            ]
            if not bars:
                results.append(
                    {
                        "status": "unavailable",
                        "code": code,
                        "action": "UNDETERMINED",
                        "mode": evaluator.mode,
                        "strategy_id": spec.strategy_id,
                        "strategy_version": spec.version,
                        "error": "历史日线不可用",
                        "external_position": external_position,
                    }
                )
                continue
            as_of = snapshot.as_of.isoformat() if snapshot.as_of else report_date.isoformat()
            result = evaluator.evaluate(
                spec,
                bars,
                report_date=report_date,
                morning_price=float(snapshot.price),
                data_as_of=as_of,
            )
            result["external_position"] = external_position
            results.append(result)
        return results

    return build
