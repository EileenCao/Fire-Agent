# FireAgent project instructions

Before handling a research, strategy, backtest, or daily-observation request in this project:

1. Read the matching project Skill:
   - `skills/strategy-workbench/SKILL.md` for clarifying and versioning a strategy.
   - `skills/backtest-analysis/SKILL.md` for interpreting results and proposing experiments.
   - `skills/daily-strategy-observer/SKILL.md` for daily rule signals and evidence.
   - `skills/stock-research/SKILL.md` for single-stock/ETF research cards and evidence-linked analysis.
   - `skills/user-memory/SKILL.md` for remembering, reviewing, correcting, or forgetting user preferences.
   - `skills/sentiment-research/SKILL.md` for structuring news/blogger content, building dated sentiment factors, and linking them to research or backtests.
2. Require the installed `a-stock-data` Skill for real A-share data. It is a Skill with data-source rules and Python helpers, not a standalone MCP server.
3. Use FireAgent MCP tools for deterministic validation, data preparation, backtesting, persistence, and evidence retrieval.
4. Keep rule signals and AI observations separate. Do not run an unconfirmed formal experiment or place orders.
5. Mark missing data, source time, source name, and Skill version instead of silently filling gaps.
6. Before every backtest, confirm the explicit benchmark choice and annual risk-free rate. After a run, use the bounded report context and evidence IDs for AI analysis.
7. Never modify a strategy from an AI suggestion directly. Prepare and discuss a complete diff, require explicit final user approval, then save a new immutable version.
8. Treat long-term memory as user context only. Prepare a memory candidate, obtain explicit confirmation, and never use memory to silently change a strategy, backtest fact, rule signal, or order.
9. For sentiment research, preserve the 15:00 cutoff, keep missing data explicit, and require approval before turning an opinion into a strategy version.

Run `python -m mcp_server.cli sync` after local code or Skill changes. It validates the project files and regenerates the ignored project-scoped Codex MCP configuration; it does not install or copy Skills globally.
