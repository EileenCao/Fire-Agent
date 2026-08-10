---
name: backtest-analysis
description: Use when a saved backtest needs metric interpretation, risk review, evidence tracing, or user-approved experiment suggestions
---

# Backtest Analysis

## Overview

分析已保存的回测结果，不重新猜测策略意图，也不修改历史结果。数据来源、时间点和缺口必须来自结果快照；真实 A 股数据依赖已安装的 `a-stock-data` Skill。

## Workflow

1. 使用 `get_backtest_result`、`compare_backtests` 和 `get_signal_evidence` 读取结果。
2. 先报告收益、回撤、波动、交易数、胜率、换手和基准差异。
3. 再报告样本内/样本外、滚动验证、参数敏感性和数据缺口。
4. 将规则证据、交易明细和 AI 观察严格分栏。
5. 同日止盈/止损存在双情景时，展示区间和不确定性。
6. 实验建议只作为候选方案；用户确认后才能调用回测工具。

## Interpretation Rules

- 不自动给策略贴“通过/不通过”标签。
- 不用胜率单独判断策略质量。
- 不隐藏缺失数据、无法成交、滑点或成本。
- 不把 AI 观察写入规则交易结果。
- 证据必须包含规则、指标值、数据日期、来源和交易记录。

## Required Output

分析开始时读取与策略和标的匹配的 `get_memory_context`。用户偏好只能使用 `memory_refs`，回测事实仍必须使用 `evidence_refs`；不得把记忆写入 `result.json` 或用记忆替代指标证据。

## Deterministic context and write-back contract

回测完成后，先调用 `get_backtest_report_context`。只使用该上下文中的指标、验证摘要、警告分类、代表交易和 `evidence_ids`；不要把完整历史数据或未经工具返回的数字写入分析。

分析必须按以下字段返回：`summary`、`strengths`、`risks`、`data_limitations`、`experiments`。每一条都必须是带 `evidence_refs` 的对象，候选实验最多三个。确认上下文哈希未过期后，调用 `save_backtest_analysis`；工具会验证证据引用并更新同一 run 的 `analysis.json` 与 `report.md`。

AI 分析只解释确定性结果，不修改 `result.json`，不自动修改策略，不自动运行下一次回测，也不自动下单。若用户想调整策略，转交 `strategy-workbench` 生成逐字段 diff，并等待用户对完整 diff 的最终确认。

按“结果摘要 → 风险与缺口 → 证据 → 可选实验 → 用户确认点”输出。所有建议都是研究建议，不自动下单。
