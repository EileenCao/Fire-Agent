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

按“结果摘要 → 风险与缺口 → 证据 → 可选实验 → 用户确认点”输出。所有建议都是研究建议，不自动下单。
