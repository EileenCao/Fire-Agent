---
name: sentiment-research
description: Use when the user wants to collect, structure, compare, or backtest news and blogger sentiment for A-share stocks, ETFs, industries, or the market, or wants evidence from a saved sentiment snapshot.
---

# 新闻与博主情绪研究

## 适用范围

把财经新闻、公开博主内容和用户投递的链接/文本/截图，整理成可追溯的结构化事件、观点和版本化情绪因子。确定性聚合由 FireAgent MCP/Python 服务完成；AI 只负责从受限上下文中抽取字段、解释结果和提出候选实验。

真实 A 股行情、财经新闻和市场验证数据必须使用已安装的 `a-stock-data` Skill。缺少该 Skill 时停止真实数据流程，不静默换成网页搜索或其他 Provider。

## 对话工作流

1. 先确认范围：标的/ETF、申万一级行业或全市场；来源及博主白名单；起止日期；15:00 Asia/Shanghai 信息截止；是否结合策略。
2. 配置来源：调用 `sentiment_source_upsert`。Provider 必须由用户明确选择；默认真实新闻 Provider 是 `a-stock-data`。
3. 采集公开财经新闻：调用 `collect_sentiment_documents`。雪球、养基宝、小红书、支付宝等受限来源不模拟登录、不绕过验证码或反爬，要求用户用 `ingest_sentiment_document` 投递链接、文本或截图的摘要/哈希。
4. 抽取前调用 `get_sentiment_extraction_context`。只根据返回的摘要、URL、哈希和元数据生成结构化抽取，再调用 `save_sentiment_extraction`；必须原样携带 `context_hash`、`extraction_model` 和 `prompt_version`。
5. 构建快照：调用 `build_sentiment_snapshot`，固定 `cutoff="15:00"`，生成 1、5、20 个交易日序列、来源分层、行业归因、证据 ID 和回测资格。
6. 解释结果：读取 `get_sentiment_snapshot`、`get_sentiment_evidence`，把事实、缺失、来源失败和 AI 观察分栏。必要时由 Agent 将分析写入对应分析接口；不要修改快照 JSON。

## 因子和时间语义

配置版本固定为 `sentiment-baseline-v1`。内容贡献由方向 `-1/0/1`、置信度、相关度和按 1/5/20 日期限的指数衰减相乘；确定性服务再聚合为 `[-100,100]`，并在至少 20 个有效历史快照后计算滚动分位。

客观等权博主共识、表现加权博主共识、观点分歧、热度、新闻事件、行业情绪和市场行为确认必须分开显示。个人长期记忆只能影响 `personalized_sentiment`，不得污染客观序列。没有合格内容、没有基准或样本不足时返回 `missing`/“不可用”，不当成 0。

每个交易日使用当日 15:00 前已公开且已采集的内容；当日快照用于下一交易日开盘执行。不得把 15:00 后发布或采集的内容回填到当日信号。正式情绪回测要求目标区间覆盖率至少 50% 且至少 20 个有效快照，否则只能探索性运行，不能激活策略。

## 策略联动边界

情绪指标可以进入条件规则，但保存策略前必须取得用户确认：因子、scope、1/5/20 horizon、raw/percentile 表示、阈值、缺失处理、仓位、成本、基准和无风险利率。策略指标使用如下契约：

```json
{
  "id": "news_sentiment_5d",
  "type": "sentiment",
  "factor": "news_event_sentiment",
  "scope": "instrument",
  "horizon": 5,
  "representation": "percentile",
  "cutoff": "15:00",
  "profile": "sentiment-baseline-v1"
}
```

从博主内容识别出的交易方法只能通过 `prepare_strategy_candidate_from_opinion` 生成候选草案。先逐字段列出原文证据、明确字段、缺失字段和推测项；与用户讨论完整 diff 并获得最终批准后，才调用 `strategy-workbench` 保存新策略版本。不得自动修改策略、运行回测、生成买卖指令或下单；本 Skill 不自动下单。

## 证据与隐私约束

- 每条记录保留 `data_as_of`/发布时间、采集时间、来源、URL、Provider、Skill 版本、模型/提示词版本、状态和 `evidence_id`。
- SQLite 和工作区只保存摘要、URL、内容哈希和结构化抽取，不保存完整原文、平台凭据或登录会话。
- URL、内容哈希和事件指纹去重；转载不能重复放大因子。快照会使用同范围历史记录自动生成最近 252 个有效值的滚动分位；不足 20 个时显示不可用。
- 原文失效后不能用新模型凭空重新抽取，报告必须说明该限制。
- AI 引用未知证据 ID 或使用过期上下文哈希时，保存必须失败。
- 需要结合用户偏好时先调用 `get_memory_context`，只在输出中保存合法的 `memory_refs`，不得把偏好写入客观情绪因子。

常用对话：

- “配置东方财富新闻和这两个雪球博主，然后分析 512890 最近 5 日情绪。”
- “我投递一篇小红书文本，请抽取事件、观点和证据，不要保存原文。”
- “查看 512890 的 1/5/20 日情绪因子和来源分解。”
- “把这条博主策略整理成候选回测策略，先列出缺失规则，不要保存。”
- “查看这次情绪快照的证据。”
