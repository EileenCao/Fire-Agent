---
name: stock-research
description: Use when a user asks to analyze one A-share stock or ETF, compare its evidence with a strategy, inspect valuation or technical data, or explain a saved research snapshot.
---

# 单标的研究卡

## 适用范围

这个 Skill 用于把用户的自然语言问题整理成可复核的单标的研究请求。真实 A 股数据必须来自已安装的 `a-stock-data` Skill；缺少它时应明确停止真实数据研究，不得静默换源。

## 对话契约

先澄清以下信息：标的代码、市场和类型（股票/ETF）、数据截止时间、是否刷新、是否结合指定策略，以及是否启用多角色分析。代码不完整或类型冲突时先询问，不猜测。

调用顺序：

1. `research_instrument` 生成确定性快照。
2. 需要细节时调用 `get_research_context` 和 `get_research_evidence`。
3. 结合用户明确授权的策略、观察清单和 `get_memory_context`；长期记忆只作为偏好和风险约束，放入 `memory_refs`，不改变事实。
4. AI 解读只引用快照中的 `evidence_id`，再通过 `save_research_analysis` 写入分析；不得修改 `snapshot.json`、历史回测或策略版本。

## 输出结构

结果按“结果概述、优势、风险、数据限制、条件式观察、证据引用”组织。确定性评分使用 `baseline-v1`，说明覆盖率、缺失项和来源时间；评分只表示继续研究或观察优先级，不输出自动买入/卖出结论。

多角色分析只有在用户明确说“启用多角色分析”后使用。基本面、技术面、消息面、风险和策略观察角色只能读取同一事实快照，不能自行补充行情或互相改写数据。

结合策略时只读取规则信号、指标值、数据截止时间和 `get_signal_evidence` 证据。若用户希望调整策略，先逐项讨论、生成完整 diff，得到用户确认后才能保存新策略；研究 Skill 不自动修改策略、不自动回测、不自动下单。

## 常见检查

- 每个重要字段都展示 `data_as_of`、`collected_at`、`source_name`、`source_url`、`provider_id`、`skill_version`、`status` 和 `evidence_id`。
- 缺失、过期、接口失败和来源冲突必须保留原因，不用零值填充。
- `provider_id` 默认是 `a-stock-data`；其他 Provider 必须由用户显式选择。
- 刷新会产生新快照，不覆盖历史研究。

示例对话：

> 分析 512890；再结合我的策略分析；启用多角色分析；最后查看这次研究的证据。
