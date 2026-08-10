---
name: daily-strategy-observer
description: Use when an activated A-share or ETF strategy needs daily rule signals, next-session observations, or evidence-backed monitoring
---

# Daily Strategy Observer

## Overview

生成日维度策略观察，使用激活的策略版本、模拟持仓和 `a-stock-data` 数据。正式信号与 AI 观察必须分开，不自动下单。

## Workflow

1. 确认激活策略和策略版本；没有激活策略时停止并说明原因。
2. 默认观察策略标的全集，可接受观察清单过滤。
3. 使用时间点一致的最新日线数据和已保存的模拟持仓。
4. 收盘后计算正式规则信号，建议下一交易日开盘执行。
5. 午间数据只能生成“上午观察”，不能伪装成正式收盘信号。
6. 每条信号关联 `get_signal_evidence`，记录规则、指标、数据时间、来源和缺失状态。

## Output Shape

观察开始时读取 `get_memory_context`。记忆只影响 AI 观察的个性化表达，不改变规则信号；输出涉及用户偏好时附带 `memory_refs`，不得自动下单。

## 与回测报告的边界

日维度观察只读取已激活策略和当前 `a-stock-data` 数据，规则信号、数据时间、来源、缺失状态和 AI 观察分栏输出。它不会改写策略版本、`result.json` 或历史回测，也不会因为 AI 观察自动触发回测或下单。

固定输出两栏：

1. **规则信号**：买入、卖出、减仓、持有、未触发或无法判断。
2. **AI 观察**：趋势、风险、数据缺口和需要用户确认的研究问题。

重复信号应压缩，无法取得关键数据时明确标记“无法判断”，不得静默跳过或补造数据。所有流程不自动下单。
