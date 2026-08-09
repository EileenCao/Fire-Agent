# Independent Workspace and Automatic Data Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Agent ask the user for an independent FireAgent workspace, create all user-data directories there, and run backtests without requiring a manually configured `--data` file.

**Architecture:** Add a single workspace resolver that reads `FIREAGENT_WORKSPACE` or an ignored `.fireagent/workspace.json` pointer. Runtime, CLI, MCP, and sync all use the resolver. Add an injectable historical-data Provider backed by the `a-stock-data`-compatible daily-bar route; automatic runs write provenance-aware Parquet snapshots to the workspace while explicit data objects remain supported for tests and offline replay.

**Tech Stack:** Python 3.9+, `pathlib`, SQLite, Parquet via the existing cache service, `pytest`, `mootdx`/`requests` supplied by `a-stock-data`.

## Global Constraints

- The real A-share data dependency is the installed `a-stock-data` Skill, minimum version `3.6.0`.
- The workspace path is supplied by the user; there is no hardcoded user-data default.
- The workspace must not equal or be inside the FireAgent code repository.
- Workspace data, secrets, logs, SQLite and cache files must not be written to Git.
- Production data calls must preserve source, URL, data time, Skill name/version and missing status.
- Unit tests inject a fake Provider and never bypass the production Skill check for real-data startup.
- Existing explicit `--data` and MCP `data` inputs remain backward-compatible.

---

### Task 1: Workspace resolver and initialization

**Files:**
- Create: `mcp_server/workspace.py`
- Modify: `mcp_server/cli.py`
- Modify: `.gitignore`
- Test: `mcp_server/tests/test_workspace.py`
- Test: `mcp_server/tests/test_cli.py`

**Interfaces:**
- `initialize_workspace(project_root: Path, workspace_path: Path, overwrite: bool = False) -> Workspace`
- `load_workspace(project_root: Path, required: bool = True) -> Workspace`
- `Workspace.root`, `.db_path`, `.calendar_path`, `.strategy_dir`, `.raw_dir`, `.parquet_dir`, `.latest_artifacts_dir`, `.formal_artifacts_dir`, `.reports_dir`, `.logs_dir`
- CLI command: `python -m mcp_server.cli init --workspace <absolute-path> [--overwrite]`

- [x] **Step 1: Write failing tests** for path rejection, directory creation, ignored pointer, and repeated loading.
- [x] **Step 2: Run `python -m pytest mcp_server/tests/test_workspace.py mcp_server/tests/test_cli.py -q` and confirm the new tests fail because the workspace module/command is absent.
- [x] **Step 3: Implement the resolver and `init` command.** Reject repository paths and descendants, create the exact directory tree from the design, write `.fireagent/workspace.json`, and return JSON without exposing secrets.
- [x] **Step 4: Add `.fireagent/workspace.json` and workspace-data patterns to `.gitignore` and preserve existing ignored local sync files.
- [x] **Step 5: Run the targeted tests and confirm they pass.

### Task 2: Route runtime, CLI and sync through the workspace

**Files:**
- Modify: `mcp_server/runtime.py`
- Modify: `mcp_server/cli.py`
- Modify: `mcp_server/sync.py`
- Modify: `mcp_server/server.py`
- Test: `mcp_server/tests/test_runtime_workspace.py`
- Test: `mcp_server/tests/test_sync.py`

**Interfaces:**
- `build_store(project_root=None, require_workspace=False)` uses `<workspace>/stock_research.sqlite3`.
- `build_calendar(project_root=None, require_workspace=False)` uses `<workspace>/data/trading_holidays.json`.
- `sync_project` requires a valid workspace and writes `FIREAGENT_WORKSPACE` plus workspace DB/calendar paths to `.codex/config.toml`.

- [x] **Step 1: Write failing tests** proving the store and generated MCP config use the independent workspace.
- [x] **Step 2: Run the targeted tests and confirm they fail with current repository-relative paths.
- [x] **Step 3: Implement shared workspace resolution in runtime and sync; make user-facing CLI/MCP startup return an actionable initialization error when no workspace exists.
- [x] **Step 4: Update existing CLI/sync fixtures to initialize a temporary workspace explicitly, then run targeted tests.

### Task 3: Automatic historical data Provider and cache

**Files:**
- Create: `mcp_server/services/historical_data.py`
- Modify: `mcp_server/services/data_cache.py`
- Modify: `mcp_server/runtime.py`
- Modify: `mcp_server/server.py`
- Test: `mcp_server/tests/test_historical_data.py`

**Interfaces:**
- `HistoricalDataProvider.fetch(codes: list[str], start_date: str, end_date: str) -> dict[str, list[dict]]`
- `HistoricalDataResult.data`, `.provenance`, `.missing_symbols`, `.cache_paths`
- `WorkspaceHistoricalDataProvider(workspace, skill, fetcher=None)` supports injected fetchers in tests and production daily-bar fetching through the Skill-approved source route.

- [x] **Step 1: Write failing tests** for code normalization, date filtering, provenance, missing symbols, and Parquet cache writes using a fake fetcher.
- [x] **Step 2: Run the targeted tests and confirm they fail because no automatic Provider exists.
- [x] **Step 3: Implement the Provider.** Use serial source calls, normalize returned bars to `date/open/high/low/close/volume/amount`, attach `a-stock-data` provenance, and write raw metadata plus Parquet cache under the workspace.
- [x] **Step 4: Implement cache reuse for an existing matching code/range/source snapshot and explicit missing-data status for incomplete results.
- [x] **Step 5: Run the targeted tests and confirm they pass without network access.

### Task 4: Remove the normal CLI/MCP `data` requirement

**Files:**
- Modify: `mcp_server/cli.py`
- Modify: `mcp_server/server.py`
- Modify: `mcp_server/domain/strategy.py`
- Modify: `skills/strategy-workbench/SKILL.md`
- Test: `mcp_server/tests/test_backtest_cli.py`
- Test: `mcp_server/tests/test_mcp_server.py`

**Interfaces:**
- `run-backtest --strategy` remains defaulted to the workspace strategy file; `--data` becomes optional.
- `run_backtest` and `prepare_backtest_data` accept optional `data`; when absent they use the Provider and the strategy validation window.
- Strategy window keys are `validation.start_date` and `validation.end_date`.

- [x] **Step 1: Write failing tests** for a no-`--data` CLI run and an MCP run with a fake historical Provider.
- [x] **Step 2: Run the targeted tests and confirm the parser/application still requires explicit data.
- [x] **Step 3: Implement automatic data preparation and keep explicit data precedence for offline tests/replay.
- [x] **Step 4: Ensure results retain provenance, cache paths, date range and missing symbols.
- [x] **Step 5: Run all backtest/MCP tests and confirm both automatic and explicit data paths pass.

### Task 5: Documentation and user onboarding

**Files:**
- Modify: `README.md`
- Modify: `docs/stock-research-system-design.md`
- Modify: `FireAgent运行命令与方式.txt`
- Test: `mcp_server/tests/test_readme_contract.py`

- [x] **Step 1: Add a failing README contract test** for the user-question-first workspace setup, independent path, no-Git rule, `init`, and no-`--data` example.
- [x] **Step 2: Update README, design document and TXT instructions with the exact Agent prompt, expected directory tree, initialization command, MCP verification prompt, and troubleshooting.
- [x] **Step 3: Run the README contract test and full test suite.
- [x] **Step 4: Run `python -m compileall -q mcp_server`, `git diff --check`, and inspect the final diff for secrets or repository-relative user data paths.
