---
name: strategy-workbench
description: Use when a user wants to clarify, formalize, version, or backtest an A-share or ETF trading strategy through conversation
---

# Strategy Workbench

## Overview

将自然语言策略转换为可审阅、可复现的 `StrategySpec`。对话负责澄清意图，MCP 负责校验和执行；真实数据必须来自已安装的 `a-stock-data` Skill。

## Workflow

1. 识别标的范围、频率、入场、退出、止盈止损、仓位、成本和验证区间。
2. 高影响参数必须询问；低影响参数可以给默认值，但要在确认摘要中列出。
3. 结构化规则优先，复杂逻辑才生成 Python 插件。
4. 展示完整策略版本、仓位、成本、数据口径、预热期和验证窗口。
5. 用户确认后调用 `validate_strategy`、`save_strategy_version`；只有用户明确激活后调用 `activate_strategy`。
6. 正式回测前再次确认数据快照、成本和仓位，再调用 `prepare_backtest_data` 和 `run_backtest`。

## Rule Contract

- 支持状态、交叉事件和滚动窗口。
- 策略作者明确动作优先级；不得由 Skill 暗中改写。
- 日线策略收盘产生信号，下一交易日开盘执行。
- 缺失关键数据必须显式列出；不得把缺失当作无信号。
- 正式回测采用时间点一致数据，并记录 `a-stock-data` 版本和来源。

## Python Plugin

只有用户审阅代码并批准版本后才执行。插件通过类型化只读上下文返回标准动作；独立进程运行，禁止网络，受超时和依赖白名单限制。

## Required Output

先输出“待确认策略摘要”，再输出校验结果、数据缺口和下一步。未确认时不要运行正式回测。所有流程不自动下单，只生成研究结果和建议。
