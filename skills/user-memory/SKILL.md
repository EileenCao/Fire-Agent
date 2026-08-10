---
name: user-memory
description: Use when a user asks to remember, review, correct, archive, forget, export, or import a personal risk preference, trading principle, trading habit, process preference, or investment constraint.
---

# 用户长期记忆

## Overview

把用户明确确认的回撤偏好、交易心得和习惯保存为可追溯的长期记忆。记忆只提供个性化上下文，不直接改变策略、回测事实、规则信号或交易动作；真实 A 股数据仍依赖 `a-stock-data`。

## Required contract

1. 识别用户原话，归纳为一个候选对象：类型、范围、`topic_key`、原始内容、可选结构化值和来源摘要。
2. 调用 `prepare_memory`，向用户展示规范化内容、适用范围、来源、冲突记录和审批哈希。
3. 只有用户明确确认完整候选后，才调用 `save_memory`，并传入相同候选、审批哈希和 `user_confirmed=true`。
4. 新内容与同范围同主题旧记忆冲突时，逐项展示旧值、新值和预期影响；确认后传入 `supersedes_ids`。不得静默覆盖。
5. AI 推断交易习惯时必须说明依据；未经用户确认的推断不能进入 `get_memory_context` 的有效记忆。

## Memory types and scope

类型使用 `risk_preference`、`trading_principle`、`behavioral_habit`、`process_preference` 或 `constraint`。范围使用 `global`、`strategy` 或 `instrument`；标的范围必须使用规范化的 `SH512890`、`SZ000001` 等键。

风险数值必须带单位。例如最大组合回撤使用 `{"value": 0.15, "unit": "ratio"}`，不能把“能承受一点回撤”猜成数值。风险偏好确认 180 天后会进入待复核状态。

## Retrieval

在策略澄清、回测分析和日观察开始时调用 `get_memory_context`，传入策略 ID 和标的。只引用返回的有效记忆；待复核记忆只能作为待确认事项展示。若记忆实际影响回答，说明 `memory_id/version`。

记忆可以帮助提出候选方案，但每次正式回测仍需重新确认基准、无风险利率、成本和仓位。不得把 `memory_refs` 当作收益、风险或交易事实的 `evidence_refs`。

## User operations

- `list_memories`：展示当前或包含历史版本的记忆。
- `search_memories`：搜索用户心得、习惯和偏好。
- `archive_memory`：用户确认后可恢复归档。
- `forget_memory`：用户二次确认后永久删除，并提醒历史报告可能仍包含文字副本。
- `export_memories`、`preview_memory_import`、`import_memories`：导出或先预览再确认导入。

## Common mistakes

- 用户只是在讨论一个假设时，不要把它当成已确认偏好。
- 不要把一次回测的结果自动归纳为用户习惯；必须先提议并列出依据。
- 不要用全局偏好静默覆盖策略或标的范围的冲突记忆。
- 不要把完整聊天记录、密码、令牌、券商账户或身份信息写入记忆。
- 不要因为记忆存在就跳过策略 diff、正式回测确认或下单前人工确认；记忆系统不自动下单。
