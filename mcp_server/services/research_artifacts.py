"""Reviewable artifacts for one instrument research snapshot."""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo


RESEARCH_TZ = ZoneInfo("Asia/Shanghai")


def write_research_artifacts(
    output_dir: Path,
    snapshot: Dict[str, Any],
    snapshot_id: int,
    created_at: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    instrument = snapshot.get("instrument", {})
    timestamp = _timestamp(created_at)
    code = _slug("{}{}".format(instrument.get("market", ""), instrument.get("code", "instrument")))
    artifact_dir = Path(output_dir) / "research" / "{}_{}_research_run-{}".format(
        timestamp, code, snapshot_id
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = artifact_dir / "charts"
    snapshot_path = artifact_dir / "snapshot.json"
    evidence_path = artifact_dir / "evidence.json"
    analysis_path = artifact_dir / "analysis.json"
    report_path = artifact_dir / "report.md"

    _atomic_write(snapshot_path, snapshot)
    _atomic_write(evidence_path, snapshot.get("evidence", []))
    analysis_payload = dict(analysis or {"status": "pending", "version": 1})
    _atomic_write(analysis_path, analysis_payload)
    chart_result = _render_price_chart(snapshot, charts_dir)
    _atomic_write(report_path, _render_report(snapshot, analysis_payload, chart_result))
    return {
        "artifact_dir": str(artifact_dir),
        "report": str(report_path),
        "snapshot": str(snapshot_path),
        "evidence": str(evidence_path),
        "analysis": str(analysis_path),
        "charts": str(charts_dir),
    }


def render_research_report(
    artifact_dir: Path,
    snapshot: Dict[str, Any],
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Re-render one existing run without changing its deterministic snapshot."""

    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    charts_dir = directory / "charts"
    analysis_payload = dict(analysis or {"status": "pending", "version": 1})
    analysis_path = directory / "analysis.json"
    report_path = directory / "report.md"
    _atomic_write(analysis_path, analysis_payload)
    chart_result = _render_price_chart(snapshot, charts_dir)
    _atomic_write(report_path, _render_report(snapshot, analysis_payload, chart_result))
    return {
        "artifact_dir": str(directory),
        "report": str(report_path),
        "analysis": str(analysis_path),
        "charts": str(charts_dir),
    }


def _render_report(snapshot, analysis, chart_result):
    instrument = snapshot.get("instrument", {})
    scores = snapshot.get("scores", {})
    lines = [
        "# {}（{}{}）标的研究卡".format(
            instrument.get("name", "未命名标的"),
            instrument.get("market", ""),
            instrument.get("code", ""),
        ),
        "",
        "> 本报告只展示确定性数据、评分和条件式观察，不构成自动交易指令。",
        "",
        "## 研究口径",
        "",
        "- 数据截止：{}".format(snapshot.get("data_as_of") or "未标注"),
        "- Provider：{}；a-stock-data Skill：{}".format(
            snapshot.get("provenance", {}).get("provider_id", "未标注"),
            snapshot.get("provenance", {}).get("skill_version", "未标注"),
        ),
        "- 评分版本：{}；评分状态：{}；覆盖率：{}".format(
            scores.get("profile", "未标注"),
            scores.get("status", "不可用"),
            scores.get("coverage", "不可用"),
        ),
        "",
        "## 核心行情与技术指标",
        "",
        "```json",
        json.dumps(snapshot.get("technical", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## 分项评分",
        "",
        "```json",
        json.dumps(scores, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 数据限制与警告",
        "",
    ]
    warnings = snapshot.get("warnings", []) or ["无"]
    lines.extend("- {}".format(value) for value in warnings)
    lines.extend(["", "## 图表", ""])
    if chart_result.get("price_technical"):
        lines.append("![价格与技术指标](charts/price_technical.png)")
    else:
        lines.append("- 图表不可用：{}".format(chart_result.get("warning", "未生成")))
    lines.extend(["", "## AI 分析", ""])
    if analysis.get("status") == "pending":
        lines.append("AI 分析待生成；确定性研究事实已保存。")
    else:
        lines.append("```json")
        lines.append(json.dumps(analysis, ensure_ascii=False, indent=2))
        lines.append("```")
    lines.extend(["", "## 产物", "", "- `snapshot.json`：不可由 AI 修改的研究事实。", "- `evidence.json`：字段级证据及来源。", "- `analysis.json`：版本化 AI 分析。"])
    return "\n".join(lines) + "\n"


def _render_price_chart(snapshot, charts_dir):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return {"warning": "绘图库不可用：{}".format(exc)}
    bars = ((snapshot.get("sections") or {}).get("bars") or {}).get("data") or []
    rows = [row for row in bars if row.get("date") and row.get("close") is not None]
    if not rows:
        return {"warning": "没有可用日线"}
    try:
        charts_dir.mkdir(parents=True, exist_ok=True)
        figure, axis = plt.subplots(figsize=(10, 4.5))
        dates = [str(row["date"]) for row in rows]
        closes = [float(row["close"]) for row in rows]
        axis.plot(dates, closes, label="close", color="#2468a2")
        axis.set_title("Price and Technical Snapshot")
        axis.set_ylabel("Price")
        axis.grid(alpha=0.25)
        axis.tick_params(axis="x", rotation=35)
        axis.legend()
        figure.tight_layout()
        path = charts_dir / "price_technical.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        return {"price_technical": str(path)}
    except Exception as exc:
        return {"warning": "图表生成失败：{}".format(exc)}


def _atomic_write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _timestamp(value: Optional[str]) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(RESEARCH_TZ).strftime("%Y%m%d-%H%M%S")
        except ValueError:
            pass
    return datetime.now(RESEARCH_TZ).strftime("%Y%m%d-%H%M%S")


def _slug(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip(".-")
    return result or "instrument"
