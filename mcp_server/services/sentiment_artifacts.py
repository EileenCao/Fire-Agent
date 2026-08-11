"""Reviewable artifacts for one deterministic sentiment snapshot."""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


SENTIMENT_TZ = ZoneInfo("Asia/Shanghai")


def write_sentiment_artifacts(
    output_dir: Path,
    snapshot: Dict[str, Any],
    run_id: int,
    created_at: Optional[str] = None,
    author_performance: Optional[List[Dict[str, Any]]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write one isolated, replaceable sentiment run directory.

    Deterministic files are written from ``snapshot`` and are never changed by
    AI analysis.  Chart and parquet failures are warnings, not run failures.
    """

    timestamp = _timestamp(created_at or snapshot.get("generated_at"))
    scope = snapshot.get("scope") or {}
    scope_name = _slug(scope.get("key") or scope.get("type") or "market")
    profile = _slug(snapshot.get("profile") or "sentiment-baseline-v1")
    artifact_dir = Path(output_dir) / "sentiment" / (
        "{}_{}_{}_run-{}".format(timestamp, scope_name, profile, run_id)
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = artifact_dir / "charts"

    snapshot_path = artifact_dir / "snapshot.json"
    evidence_path = artifact_dir / "evidence.json"
    author_path = artifact_dir / "author_performance.json"
    analysis_path = artifact_dir / "analysis.json"
    report_path = artifact_dir / "report.md"
    series_path = artifact_dir / "factor_series.parquet"

    _atomic_write(snapshot_path, snapshot)
    _atomic_write(evidence_path, snapshot.get("evidence", []))
    _atomic_write(author_path, author_performance or snapshot.get("author_performance", []))
    _atomic_write(analysis_path, analysis or {"status": "pending", "version": 1})

    warnings: List[str] = []
    if not _write_factor_series(series_path, snapshot):
        warnings.append("Parquet 写入不可用，因子明细未生成")
    chart_result = _render_charts(snapshot, charts_dir)
    warnings.extend(chart_result.get("warnings", []))

    report = _render_report(snapshot, analysis or {"status": "pending"}, chart_result, warnings)
    _atomic_write(report_path, report)
    return {
        "artifact_dir": str(artifact_dir),
        "report": str(report_path),
        "snapshot": str(snapshot_path),
        "evidence": str(evidence_path),
        "author_performance": str(author_path),
        "analysis": str(analysis_path),
        "factor_series": str(series_path) if series_path.exists() else None,
        "charts": chart_result.get("paths", {}),
        "warnings": warnings,
    }


def _render_report(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
    chart_result: Dict[str, Any],
    warnings: List[str],
) -> str:
    scope = snapshot.get("scope") or {}
    factors = snapshot.get("factors") or {}
    gate = snapshot.get("backtest_gate") or snapshot.get("backtest_eligibility") or {}
    coverage = snapshot.get("coverage") or gate
    lines = [
        "# 情绪因子研究报告",
        "",
        "> 本报告展示结构化内容证据与确定性聚合结果，不构成买卖指令。",
        "",
        "## 研究口径",
        "",
        "- 范围：{} / {}".format(scope.get("type", "未标注"), scope.get("key", "未标注")),
        "- 情绪因子版本：{}".format(snapshot.get("profile", "未标注")),
        "- 信息截止：{} {}".format(snapshot.get("snapshot_date", "未标注"), snapshot.get("cutoff", "15:00")),
        "- 生成时间：{}".format(snapshot.get("generated_at", "未标注")),
        "- 正式回测资格：{}".format(
            "允许" if gate.get("eligible") or gate.get("status") == "formal" else "仅探索性/不可用"
        ),
        "",
        "## 因子摘要",
        "",
    ]
    for horizon, horizon_factors in factors.items():
        lines.append("### {}".format(horizon))
        lines.append("")
        if not horizon_factors:
            lines.append("- 缺失：没有满足截止时间和范围要求的内容")
            lines.append("")
            continue
        for name, value in horizon_factors.items():
            if isinstance(value, dict):
                status = value.get("status", "available")
                display = value.get("percentile") if value.get("percentile") is not None else value.get("value")
                lines.append("- {}：{}（{}，样本 {}）".format(name, display if display is not None else "不可用", status, value.get("sample_count", 0)))
            else:
                lines.append("- {}：{}".format(name, value))
        lines.append("")

    lines.extend(["## 覆盖率与限制", ""])
    lines.append("- 覆盖率：{}".format(json.dumps(coverage, ensure_ascii=False, sort_keys=True)))
    for warning in snapshot.get("warnings", []) or []:
        lines.append("- {}".format(warning))
    for warning in warnings:
        lines.append("- {}".format(warning))
    lines.extend(
        [
            "",
            "## AI 解读",
            "",
            "AI 分析状态：{}".format(analysis.get("status", "pending")),
            "",
            "## 图表",
            "",
        ]
    )
    for name, path in chart_result.get("paths", {}).items():
        lines.append("![{}]({})".format(name, "charts/{}".format(Path(path).name)))
    if not chart_result.get("paths"):
        lines.append("- 图表不可用，详见限制说明。")
    lines.extend(
        [
            "",
            "## 产物索引",
            "",
            "- `snapshot.json`：不可由 AI 修改的确定性快照。",
            "- `evidence.json`：内容元数据、结构化证据和来源。",
            "- `author_performance.json`：只使用已结束评价窗口的作者统计。",
            "- `factor_series.parquet`：因子序列（如运行环境支持 Parquet）。",
            "- 不保存完整原文；原文失效后不能使用新模型重新抽取。",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_factor_series(path: Path, snapshot: Dict[str, Any]) -> bool:
    rows = []
    for horizon, factors in (snapshot.get("factors") or {}).items():
        for factor, value in (factors or {}).items():
            if not isinstance(value, dict):
                value = {"value": value}
            rows.append(
                {
                    "snapshot_date": snapshot.get("snapshot_date"),
                    "scope": (snapshot.get("scope") or {}).get("key"),
                    "horizon": horizon,
                    "factor": factor,
                    "value": value.get("value"),
                    "percentile": value.get("percentile"),
                    "status": value.get("status"),
                    "sample_count": value.get("sample_count"),
                }
            )
    try:
        import pandas as pd

        frame = pd.DataFrame(rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        return True
    except Exception:
        return False


def _render_charts(snapshot: Dict[str, Any], charts_dir: Path) -> Dict[str, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return {"paths": {}, "warnings": ["图表依赖不可用：{}".format(exc)]}

    paths: Dict[str, str] = {}
    warnings: List[str] = []
    charts_dir.mkdir(parents=True, exist_ok=True)
    try:
        paths["sentiment_timeline"] = str(_render_factor_chart(snapshot, charts_dir, plt))
    except Exception as exc:
        warnings.append("情绪时间序列图生成失败：{}".format(exc))
    try:
        paths["source_comparison"] = str(_render_source_chart(snapshot, charts_dir, plt))
    except Exception as exc:
        warnings.append("来源比较图生成失败：{}".format(exc))
    try:
        paths["industry_attribution"] = str(_render_industry_chart(snapshot, charts_dir, plt))
    except Exception as exc:
        warnings.append("行业归因图生成失败：{}".format(exc))
    return {"paths": paths, "warnings": warnings}


def _render_factor_chart(snapshot, charts_dir, plt):
    figure, axis = plt.subplots(figsize=(10, 4.5))
    factors = (snapshot.get("factors") or {}).get("5d") or {}
    labels = list(factors)
    values = [_number(factors[name].get("value")) for name in labels]
    axis.bar(labels, [value or 0.0 for value in values], color="#2468a2")
    axis.axhline(0, color="#333333", linewidth=0.8)
    axis.set_title("Sentiment Factors (5D)")
    axis.tick_params(axis="x", rotation=45)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = charts_dir / "sentiment_timeline.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _render_source_chart(snapshot, charts_dir, plt):
    source_counts = snapshot.get("source_counts") or {}
    labels = list(source_counts) or ["no_data"]
    values = [source_counts.get(label, 0) for label in labels]
    figure, axis = plt.subplots(figsize=(8, 4))
    axis.bar(labels, values, color="#6a994e")
    axis.set_title("Source Comparison")
    axis.tick_params(axis="x", rotation=35)
    figure.tight_layout()
    path = charts_dir / "source_comparison.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _render_industry_chart(snapshot, charts_dir, plt):
    industry = snapshot.get("industry_attribution") or {}
    labels = list(industry) or ["no_data"]
    values = [_number(industry.get(label)) or 0.0 for label in labels]
    figure, axis = plt.subplots(figsize=(8, 4))
    axis.bar(labels, values, color="#bc4749")
    axis.axhline(0, color="#333333", linewidth=0.8)
    axis.set_title("Industry Attribution")
    axis.tick_params(axis="x", rotation=35)
    figure.tight_layout()
    path = charts_dir / "industry_attribution.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _timestamp(value: Optional[str]) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SENTIMENT_TZ)
            return parsed.astimezone(SENTIMENT_TZ).strftime("%Y%m%d-%H%M%S")
        except ValueError:
            pass
    return datetime.now(SENTIMENT_TZ).strftime("%Y%m%d-%H%M%S")


def _slug(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip(".-")
    return result or "scope"


def _number(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
