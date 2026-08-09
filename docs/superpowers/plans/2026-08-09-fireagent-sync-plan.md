# FireAgent Local Sync Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-platform local `sync` command that validates the required Skill, generates an ignored project-scoped Codex MCP configuration, and provides PowerShell plus Git Bash/WSL launchers without requiring MCP or Skill reinstallation after every code edit.

**Architecture:** The Python CLI owns deterministic synchronization behavior. It validates `a-stock-data` and the three project Skills, then writes `FireAgent/.codex/config.toml` using the current Python interpreter and absolute project paths. Thin local shell wrappers call that CLI; only wrapper copies containing machine-specific choices are ignored, while portable examples remain reviewable.

**Tech Stack:** Python standard library, existing `mcp_server.cli`, pytest, PowerShell, POSIX shell, Codex project-scoped TOML configuration.

## Global Constraints

- Real-data workflows require the installed `a-stock-data` Skill and must fail clearly when it is missing.
- Feishu remains disabled by default.
- MCP STDIO stdout must remain reserved for JSON-RPC; the sync command is separate from the long-running MCP server.
- Local generated config and shell wrappers must not be committed; portable examples may be committed.
- This change does not yet make `run_backtest` fetch historical bars automatically; that remains a separate data-provider change.

---

### Task 1: Specify sync behavior with failing CLI tests

**Files:**
- Create: `mcp_server/tests/test_sync.py`

**Interfaces:**
- Consumes: `mcp_server.cli.main(["sync"])` and `A_STOCK_DATA_SKILL_PATH`.
- Produces: A JSON result containing generated config path, Skill metadata, and project Skill checks.

- [ ] **Step 1: Write the failing tests**

```python
def test_sync_generates_project_scoped_codex_config(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: a-stock-data\nversion: 3.6.0\n---\n", encoding="utf-8")
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill))
    monkeypatch.chdir(tmp_path)

    assert main(["sync"]) == 0

    result = json.loads(capsys.readouterr().out)
    config = tmp_path / ".codex" / "config.toml"
    assert config.exists()
    assert result["status"] == "ok"
    assert result["config_path"] == str(config)
    assert "[mcp_servers.fireagent]" in config.read_text(encoding="utf-8")
    assert "mcp_server.server" in config.read_text(encoding="utf-8")


def test_sync_fails_without_required_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(tmp_path / "missing" / "SKILL.md"))
    monkeypatch.chdir(tmp_path)

    assert main(["sync"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert not (tmp_path / ".codex" / "config.toml").exists()
```

- [ ] **Step 2: Run the focused tests to verify the missing command fails**

Run: `pytest -q mcp_server/tests/test_sync.py`

Expected: FAIL because the CLI has no `sync` command yet.

### Task 2: Implement the Python sync command

**Files:**
- Create: `mcp_server/sync.py`
- Modify: `mcp_server/cli.py`
- Test: `mcp_server/tests/test_sync.py`

**Interfaces:**
- Produces: `sync_project(root: Path) -> dict`.
- Produces: `.codex/config.toml` with `command`, `args`, `cwd`, `env`, and timeout fields.

- [ ] **Step 1: Add the `sync` parser branch and failing-result contract**

The CLI must dispatch `sync` before opening the SQLite store, so a missing Skill cannot create a misleading configured state.

- [ ] **Step 2: Run the focused tests and confirm the expected failure remains**

Run: `pytest -q mcp_server/tests/test_sync.py`

Expected: FAIL on the missing `sync_project` implementation or generated config.

- [ ] **Step 3: Implement the minimal sync behavior**

The implementation must:

1. Call `require_a_stock_data_skill()`.
2. Verify the three project Skill files exist and contain their declared names.
3. Create `.codex/` under the project root.
4. Generate `.codex/config.toml` using `sys.executable`, the project root, the Skill path, an absolute SQLite path, and Feishu disabled.
5. Return JSON-safe paths and version metadata without printing secrets.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest -q mcp_server/tests/test_sync.py`

Expected: PASS.

### Task 3: Add ignored local wrappers and portable examples

**Files:**
- Create: `scripts/sync.ps1.example`
- Create: `scripts/sync.sh.example`
- Create locally but ignore: `scripts/sync.ps1`
- Create locally but ignore: `scripts/sync.sh`
- Modify: `.gitignore`

**Interfaces:**
- PowerShell entry point: `scripts/sync.ps1`.
- POSIX entry point: `scripts/sync.sh`.
- Both call `python -m mcp_server.cli sync` from the repository root and forward arguments.

- [ ] **Step 1: Add ignore rules and example-file tests**

Assert that `scripts/sync.ps1`, `scripts/sync.sh`, and `.codex/config.toml` are ignored while the `.example` files remain visible to Git.

- [ ] **Step 2: Run the ignore-rule test and confirm it fails before the rules exist**

Run: `pytest -q mcp_server/tests/test_sync.py -k ignore`

Expected: FAIL because the ignore patterns are not present.

- [ ] **Step 3: Add wrappers**

The PowerShell wrapper must honor `FIREAGENT_PYTHON` and default to `python`. The shell wrapper must honor the same variable and use `set -euo pipefail`. Neither wrapper may emit output before the Python CLI starts.

- [ ] **Step 4: Run the wrapper/ignore tests**

Run: `pytest -q mcp_server/tests/test_sync.py`

Expected: PASS.

### Task 4: Document the workflow and limits

**Files:**
- Modify: `README.md`
- Modify: `docs/stock-research-system-design.md`

**Interfaces:**
- User command: `python -m mcp_server.cli sync`.
- Windows command: `./scripts/sync.ps1` after copying the example to the ignored local wrapper.
- Git Bash/WSL command: `./scripts/sync.sh` after copying the example to the ignored local wrapper.

- [ ] **Step 1: Add README instructions**

Explain that code edits do not require MCP re-registration, `sync` regenerates only project-local configuration and validates Skills, the current Codex MCP process may need restart/new task, and `.sh` requires Git Bash or WSL.

- [ ] **Step 2: Update the design document**

Record the distinction between Skill instructions and MCP tools, the local sync boundary, and the future `fetch_historical_bars` integration that will remove the current raw-`data` requirement from `run_backtest`.

- [ ] **Step 3: Run documentation contract tests**

Run: `pytest -q mcp_server/tests/test_readme_contract.py mcp_server/tests/test_sync.py`

Expected: PASS.

### Task 5: Full verification

**Files:**
- No additional files.

- [ ] **Step 1: Run all Python tests**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run compile and CLI checks**

Run: `python -m compileall -q mcp_server`

Run: `python -m mcp_server.cli sync`

Expected: compile succeeds; sync returns `status: ok`, reports the installed `a-stock-data` version, and creates only ignored `.codex/config.toml` plus local wrappers.

- [ ] **Step 3: Verify the MCP protocol remains clean**

Start `python -m mcp_server.server` with the generated project environment and send `initialize` followed by `tools/list`.

Expected: only JSON-RPC responses appear on stdout; bootstrap diagnostics do not corrupt the protocol.
