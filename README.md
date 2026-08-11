# FireAgent：A 股研究与策略回测系统

> 重要：本项目的真实 A 股数据依赖 `a-stock-data` Skill。未安装或版本不满足时，项目不能运行真实数据功能；项目不会静默切换到其他数据源。

FireAgent 以 Agent 对话为入口，用六个 Skill 帮助用户研究标的、澄清策略、运行回测、分析情绪因子和进行日维度观察；确定性计算、数据证据、结果保存由本地 MCP 服务和回测引擎完成。

项目根目录的 `AGENTS.md` 负责把对话任务路由到项目内的六个 Skill。Skill 文档修改后会在下一次 Agent 任务中按项目路径读取，不需要复制到 Codex 用户 Skill 目录。

项目当前定位是本地研究工具：不自动下单，不连接券商交易接口；不需要 Ollama；飞书仅作为可选的单向午间日报渠道，不支持飞书内双向 Agent 对话。

## 普通用户：Windows 快速开始

### 0. 让 Codex、WorkBuddy、Claude Code 协助安装

项目地址：<https://github.com/EileenCao/Fire-Agent.git>

只要 Agent 具备工作区读写权限和终端执行权限，用户可以直接把项目地址以及下面的安装提示词发给 Codex、WorkBuddy、Claude Code 或其他兼容 Agent：

```text
请安装并配置 FireAgent。

项目地址：https://github.com/EileenCao/Fire-Agent.git
目标系统：Windows 10/11，优先使用 PowerShell。

请先阅读项目 README.md 和 AGENTS.md，再执行安装。请完成：
1. 将项目克隆或打开到我指定的目录；
2. 检查 Python 和 pip，创建或使用项目虚拟环境；
3. 按 README 安装 a-stock-data Skill 及其依赖；
4. 先询问我一个不在 FireAgent 仓库内的独立工作文件夹绝对路径，不要猜测路径；
5. 在项目根目录运行 python -m mcp_server.cli init --workspace <我确认的路径>；
6. 再运行 python -m mcp_server.cli sync 和 python -m mcp_server.cli doctor；
7. 配置并启动项目 MCP，但不要运行回测、发送通知或自动下单；
8. 最后只检查 FireAgent MCP 是否已连接，并列出可用工具，不运行回测。

请报告每一步的实际结果。遇到需要管理员权限、网络访问、安装包或写入文件的操作，先向我说明并请求确认。不要把密钥写入 Git、SQLite 或日志。
```

不同 Agent 的操作入口可能不同，但验收标准相同：项目文件存在、`a-stock-data` Skill 可识别、`doctor` 通过、`sync` 成功，并且 MCP 工具列表可以被读取。Codex 通常可以直接使用 `sync` 生成的项目配置；如果 Claude Code、WorkBuddy 或其他 Agent 不读取 `.codex/config.toml`，请在它们各自的 MCP 设置中添加一个 stdio 服务，工作目录为项目根目录，启动命令为 `python -m mcp_server.server`。Agent 不能替用户绕过操作系统权限或安全审批；如果无法直接执行安装，应输出可复制的命令和失败原因。

### 1. 准备 Python

建议使用 Python 3.9 或更高版本，并在 PowerShell 中确认当前进程能找到它：

```powershell
python --version
python -m pip --version
```

如果 `python` 不在 PATH 中，将下面命令中的 `python` 替换为本机 Python 的绝对路径，例如 `D:\software\Anaconda\python.exe`。

### 2. 安装必需的 a-stock-data Skill

公开安装方式是把仓库中的 `SKILL.md` 放入 Skill 目录，再安装 Skill 使用的 Python 依赖。下面命令适用于 Codex 的 Windows 用户目录：

```powershell
$skillRoot = Join-Path $env:USERPROFILE ".codex\skills\a-stock-data"
New-Item -ItemType Directory -Force -Path $skillRoot | Out-Null
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/SKILL.md" `
  -OutFile (Join-Path $skillRoot "SKILL.md")
python -m pip install "mootdx>=0.10" requests pandas stockstats
```

如果要启用历史数据的 Parquet 缓存，再安装一个 Parquet 引擎：

```powershell
python -m pip install pyarrow
```

报告图表使用 Matplotlib；图表不是回测计算的前置条件，但建议安装：

```powershell
python -m pip install matplotlib
```

核心依赖说明：

- `mootdx>=0.10`：通达信行情、日线、财务快照和 F10；FireAgent 会通过数据适配器连接通达信 TCP 服务。
- `requests`：调用腾讯、东财、同花顺、百度、新浪和巨潮等 HTTP 数据接口。
- `pandas`：行情和财务数据处理。
- `stockstats`：RSI、MACD、布林带等技术指标计算。
- `pyarrow`：只在启用 Parquet 历史数据缓存时需要。

`a-stock-data` 是 Skill 文件，不是需要通过 pip 安装的 Python 包；不要执行 `pip install a-stock-data`。安装依赖后可以验证当前 Python 环境：

```powershell
python -c "import mootdx, requests, pandas, stockstats; print('a-stock-data Python dependencies: OK')"
```

只有使用 iwencai 语义搜索时才需要额外的 API Key；行情、K 线、估值、东财、同花顺、百度、新浪和巨潮接口通常不需要 Key：

```powershell
$env:IWENCAI_API_KEY = "your_key_here"
$env:IWENCAI_BASE_URL = "https://openapi.iwencai.com"
```

取数时需要能访问 HTTP 数据接口；使用 `mootdx` 获取行情和历史 K 线时还需要网络允许通达信 TCP `7709` 端口。海外或受限网络可能导致通达信连接超时，`doctor` 通过不代表外部行情请求一定成功。

真实数据功能不需要 `akshare`、Ollama、浏览器或券商客户端。飞书通知默认关闭，启用时使用独立工作区 `config/.env` 和私人群自定义机器人 Webhook。

来源与更新说明见 [`a-stock-data` GitHub 仓库](https://github.com/simonlin1212/a-stock-data)。项目启动时会检查 `SKILL.md` 的名称和最低版本（当前最低要求为 `3.6.0`）。也可以把 Skill 安装到 Claude Code 或 Agents 目录，或者显式指定路径：

```powershell
$env:A_STOCK_DATA_SKILL_PATH = "C:\path\to\a-stock-data\SKILL.md"
```

不设置覆盖变量时，项目依次检查当前用户的 `.codex\skills`、`.agents\skills` 和 `.claude\skills`。

### 3. 初始化独立用户工作区

首次使用时，Agent 必须先向用户询问工作区路径，例如：

```text
请提供一个独立的 FireAgent 工作文件夹绝对路径。不要选择 FireAgent 代码仓库目录。
例如：D:\FireAgentWorkspace
```

用户确认后，在项目根目录执行：

```powershell
python -m mcp_server.cli init --workspace "D:\FireAgentWorkspace"
```

上面的路径只是示例，实际路径必须使用用户确认的值。初始化会在工作区创建：

```text
<用户工作区>/
├─ config/.env               # 可选：飞书 Webhook 和签名密钥，不进 Git
├─ data/raw/                 # 原始历史数据快照
├─ data/parquet/             # 可复用的 Parquet 日线缓存
├─ strategies/               # 默认策略文件 strategy.json
├─ artifacts/latest/         # 探索性回测产物
├─ artifacts/formal/         # 正式回测产物
├─ reports/
├─ logs/
└─ stock_research.sqlite3
```

仓库只保存被 Git 忽略的 `.fireagent\workspace.json` 指针；真实数据库、策略、行情、缓存、报告和日志都在独立工作区，不执行 Git 同步，也不要把该工作区初始化为 Git 仓库。若要更换工作区，必须明确使用 `--overwrite`。

### 4. 健康检查

在项目根目录 `FireAgent` 中运行：

```powershell
python -m mcp_server.cli doctor
```

成功时会显示 Python、SQLite、数据库位置和 `a_stock_data_skill` 的路径及版本。Skill 缺失、frontmatter 错误或版本过低时，命令返回非零状态，并给出安装或修复提示。

### 本地 Codex 同步与脚本

代码修改后不需要重新安装 Skill，也不需要反复修改全局 Codex MCP 配置。运行下面的命令会：

- 检查 `a-stock-data` 版本和路径；
- 检查六个项目 Skill；
- 生成项目级 `.codex/config.toml`；
- 固定当前 Python、项目目录、独立工作区、数据库和交易日历路径；
- 不覆盖独立工作区中的 Feishu 配置；是否启用由工作区 `config/.env` 控制。

```powershell
python -m mcp_server.cli sync
```

生成的 `.codex/config.toml`、`scripts/sync.ps1` 和 `scripts/sync.sh` 都是本机文件，已加入 `.gitignore`，不会提交到 Git。仓库只保留不含个人路径的示例：

```powershell
Copy-Item .\scripts\sync.ps1.example .\scripts\sync.ps1
.\scripts\sync.ps1
```

`.sh` 适用于 Git Bash 或 WSL；Windows PowerShell 优先使用 `.ps1`：

```bash
cp scripts/sync.sh.example scripts/sync.sh
chmod +x scripts/sync.sh
./scripts/sync.sh
```

`sync` 更新的是项目本地运行配置，不会修改 `C:\Users\<用户名>\.codex\config.toml`。MCP 进程已经在运行时，运行同步后需要重启 MCP 或新开一个 Codex 任务，才能加载最新代码。

### 5. 准备策略与数据

策略文件是 JSON，至少要说明：策略 ID、版本、标的集合、日线频率、入场规则、出场规则、仓位方案。正式回测还要给出带版本的成本模板。建议把策略保存为工作区的 `strategies/strategy.json`，这样运行命令不需要写路径。

需要持仓中继续投入或分批退出时，在 `position_sizing.while_holding` 中配置 `signal_add`、`periodic`，在 `exit.sell` 中配置 `all`、`percent` 或 `quantity`。`periodic.execution` 只能使用 `scheduled_open` 或 `next_open`；`strategy_configured` 不是可执行值。多标的策略还可以把 `position_sizing.capital_scope` 设置为 `per_symbol` 或 `portfolio`，并用 `action_priority` 指定同一执行日的 `SELL`、`PERIODIC_BUY`、`SIGNAL_BUY` 顺序。

例如，下面的配置表示：持仓后每月 1 日按 1000 元新增资金定投，非交易日顺延到下一交易日开盘，普通出场卖出一半持仓：

```json
{
  "position_sizing": {
    "capital_scope": "per_symbol",
    "type": "all_in",
    "lot_size": 100,
    "while_holding": {
      "periodic": {
        "enabled": true,
        "frequency": "monthly",
        "day": 1,
        "type": "fixed_cash",
        "amount": 1000,
        "funding": "external_contribution",
        "non_trading_day": "next_trading_day",
        "execution": "scheduled_open"
      }
    }
  },
  "exit": {
    "rules": [],
    "sell": {"type": "percent", "value": 0.5}
  },
  "action_priority": ["SELL", "PERIODIC_BUY", "SIGNAL_BUY"]
}
```

正常回测会根据 `validation.start_date` 和 `validation.end_date` 自动通过 `a-stock-data` 准备历史日线；未指定时默认使用最近四年至今天。数据会保存到工作区的 `data/raw/` 和 `data/parquet/`，结果写入 `artifacts/latest/`（正式模式写入 `artifacts/formal/`）。每次运行会自动创建独立目录 `YYYYMMDD-HHmmss_<strategy_id>_v<version>_run-<id>/`，不会覆盖旧运行。返回结果会标注来源、来源 URL、Skill 版本、复权口径、数据时间、缺失标的和错误。

腾讯历史接口单次默认最多返回最近约 640 条记录，因此 Provider 会把较长日期窗口按日期段拆分请求；每个请求的日期参数使用 `YYYY-MM-DD`，单次 `limit` 不超过 640。所有分段结果会合并、按日期去重并排序，再交给回测引擎，避免长期回测窗口被单次请求截断。

`--data` 仍保留，但只用于离线重放、测试夹具或用户明确提供的 JSON；它不是正常运行的必填项。显式 JSON 按标的代码分组，每根 K 线至少包含 `date`、`open`、`high`、`low`、`close`，也可提供 `adj_*`、`raw_* + adj_factor`、停牌和涨跌停字段。

先验证策略：

```powershell
python -m mcp_server.cli validate-strategy --file .\path\to\your-strategy.json
```

策略还必须显式包含以下两项；`benchmark: null` 是明确选择不使用基准，不能省略。每次回测前都要由用户确认这两项：

```json
{
  "benchmark": null,
  "risk_free_rate_annual": 0.02
}
```

运行本地探索性回测并生成可审阅产物：

```powershell
python -m mcp_server.cli run-backtest `
  --confirm-benchmark `
  --confirm-risk-free-rate
```

如果策略不在工作区默认位置，可以显式指定策略；仍然不需要 `--data`：

```powershell
python -m mcp_server.cli run-backtest `
  --strategy .\path\to\your-strategy.json `
  --confirm-benchmark `
  --confirm-risk-free-rate
```

每次运行的实际目录由命令输出中的 `artifacts.artifact_dir` 给出，包含：

- `report.md`：摘要正文、详细附录、图表和 AI 分析状态；
- `result.json`：不可由 AI 修改的确定性事实、指标、验证切分、来源和警告；
- `trades.csv`：成交与明确标记的未成交订单，包含 FIFO 批次和执行证据；
- `analysis.json`：版本化 AI 分析，初始状态为 `pending`；
- `charts/`：净值/回撤、各情景月度收益和交易盈亏 PNG。Matplotlib 不可用时仍生成文字报告，并在警告中说明原因。

报告基础指标包括最终权益、净利润、时间加权收益、年化收益、波动率、下行波动率、最大回撤及恢复日期、Sharpe/Sortino/Calmar、胜率、利润因子、盈亏比、期望收益、成交率、费用拆分、换手率、平均/最高仓位、在场时间和样本内外验证。样本不足或字段缺失显示“不可用”，不使用猜测值。

探索性回测不会替用户确认成本或仓位。需要运行正式模式时，必须显式确认两者：

```powershell
python -m mcp_server.cli run-backtest `
  --strategy .\path\to\your-strategy.json `
  --run-mode formal `
  --confirm-cost-profile `
  --confirm-position-sizing `
  --confirm-benchmark `
  --confirm-risk-free-rate
```

正式模式默认把产物写到工作区 `artifacts/formal/`。只有离线重放时才需要额外添加 `--data .\path\to\your-daily-data.json`。

旧结果或已有 run 可使用以下命令非破坏性重渲染；它只更新该 run 的独立目录，不删除旧的根目录文件：

```powershell
python -m mcp_server.cli render-backtest-report `
  --run-id 2 `
  --confirm-benchmark `
  --confirm-risk-free-rate `
  --risk-free-rate-annual 0.02
```

### 6. 使用 Agent 生成标的研究卡

研究卡默认读取 `a-stock-data`，先生成确定性事实和字段级证据，再由 Agent 解读。它支持股票和 ETF；缺失、过期、来源冲突和接口失败会保留在结果中，不会用零值静默填充。研究刷新会生成新的快照，不覆盖历史快照。

可以直接在已接入 FireAgent MCP 的对话中发送：

```text
分析 512890
结合我的策略分析 512890
启用多角色分析
查看这次研究的证据
```

对应的确定性 MCP 工具包括：`research_instrument`、`get_market_data`、`get_fundamentals`、`get_valuation`、`score_instrument`、`get_research_context`、`save_research_analysis`、`get_research_snapshot`、`list_research_snapshots` 和 `get_research_evidence`。

也可以在项目根目录用 CLI 做一次本地验证；不写 `--data` 时由 `a-stock-data` 自动准备行情和历史日线：

```powershell
python -m mcp_server.cli research 512890
python -m mcp_server.cli research-list --code 512890
```

需要显式带入观察清单或长期记忆时，可加 `--include-watchlist`、`--include-memory` 和可选的 `--memory-query`；它们只作为上下文，不会改变研究事实。

研究产物位于用户独立工作区的 `artifacts/research/<时间>_<市场代码>_research_run-<id>/`，包含 `report.md`、不可由 AI 修改的 `snapshot.json`、`evidence.json`、`analysis.json` 和可选的 `charts/price_technical.png`。其它 Provider 只能通过明确的 `provider_id` 选择，不能静默替换 `a-stock-data`。

### 7. 使用 Agent 研究新闻与博主情绪

情绪研究默认使用已安装的 `a-stock-data` 获取财经新闻；雪球、养基宝、小红书和支付宝等受限来源需要用户投递链接、文本或截图，系统不会模拟登录、绕过验证码或保存平台凭据。先让 Agent 配置来源，再抽取和聚合：

```text
配置东方财富新闻和我的雪球白名单作者。
分析 512890 最近的新闻和博主情绪，先只做确定性证据包。
查看 512890 的 1、5、20 日情绪因子和来源分解。
把这条博主策略整理成候选回测策略，列出明确规则、缺失规则和证据，不要保存。
查看这次情绪快照的证据。
```

Agent 应按以下 MCP 顺序工作：`sentiment_source_upsert` → `collect_sentiment_documents` 或 `ingest_sentiment_document` → `get_sentiment_extraction_context` → `save_sentiment_extraction` → `build_sentiment_snapshot` → `get_sentiment_snapshot`/`get_sentiment_evidence`。来源配置中的 `provider_id` 必须显式填写 `a-stock-data` 或 `public-web`；`public-web` 只读取用户明确配置的公开 RSS/Atom URL。情绪抽取由当前对话中的 Agent 完成，不需要 Ollama 或后台模型 API。

每条内容只保存摘要、URL、内容哈希、事件指纹和结构化抽取；不保存完整原文。重复 URL/哈希/事件指纹不会重复计分。每个交易日以 Asia/Shanghai 15:00 为信息截止时间，生成 1/5/20 日序列；没有内容、基准、样本或来源失败时显示 `missing`/“不可用”，不会填零。目标区间覆盖率低于 50% 或有效快照少于 20 个时，只允许探索性情绪回测，不能激活策略。

情绪产物位于工作区 `artifacts/sentiment/<时间>_<范围>_<profile>_run-<id>/`，包含 `report.md`、不可由 AI 修改的 `snapshot.json`、`evidence.json`、`author_performance.json`、`factor_series.parquet` 和可选 PNG 图表。将情绪用于策略时，策略必须明确 `factor`、`scope`、`horizon`、`representation`、`cutoff`、缺失处理、仓位、成本、基准和无风险利率；博主策略只能先生成候选，用户批准完整 diff 后才能保存和回测。

### 8. 启动 MCP stdio 服务

在支持 MCP stdio 的 Agent 中，把启动命令配置为：

```powershell
python -m mcp_server.server
```

#### 验证 MCP 是否已接入

同步完成并重启 MCP 或新开一个 Codex 任务后，在对话中输入：

```text
请检查 FireAgent MCP 是否已连接，只列出可用工具，不运行回测。
```

成功时，Agent 应通过 MCP 的 `tools/list` 只返回工具列表，不调用工具执行回测，也不访问行情数据。当前项目预期包含：

```text
validate_strategy
save_strategy_version
activate_strategy
prepare_backtest_data
run_backtest
get_backtest_result
get_backtest_report_context
save_backtest_analysis
prepare_strategy_revision
compare_backtests
observe_active_strategy
get_signal_evidence
research_instrument
get_market_data
get_fundamentals
get_valuation
score_instrument
get_research_context
save_research_analysis
get_research_snapshot
list_research_snapshots
get_research_evidence
sentiment_source_upsert
sentiment_source_list
sentiment_source_deactivate
collect_sentiment_documents
ingest_sentiment_document
get_sentiment_extraction_context
save_sentiment_extraction
backfill_sentiment_data
build_sentiment_snapshot
get_sentiment_snapshot
list_sentiment_snapshots
get_sentiment_evidence
evaluate_sentiment_authors
prepare_strategy_candidate_from_opinion
watchlist_add
watchlist_remove
watchlist_list
preview_daily_watchlist_report
configure_daily_report
send_test_notification
get_notification_status
prepare_memory
save_memory
list_memories
search_memories
get_memory_context
archive_memory
forget_memory
export_memories
preview_memory_import
import_memories
```

如果没有工具列表或提示 MCP 未连接，先在项目根目录运行 `python -m mcp_server.cli sync`，然后重启 MCP 或新开 Codex 任务再验证。

服务启动时会先检查 `a-stock-data`。可用工具包括策略校验与版本管理、数据准备、回测、结果比较、日观察、信号证据、观察清单和情绪来源/抽取/快照管理。通知工具保留接口但默认关闭。

省略 `data` 调用 `prepare_backtest_data` 或 `run_backtest` 时，Provider 会自动把原始快照写入工作区 `data/raw/`，把可复用日线写入 `data/parquet/`；SQLite 只保存运行元数据和证据，不承载历史 K 线本体。需要自定义缓存位置时，MCP 仍可传 `cache_dir`。

开发者可以在项目根目录运行完整测试；禁用当前环境中与项目无关的 pytest 自动插件：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q
```

## 用户如何与 Agent 协作

推荐的对话闭环是：

1. 如果用户表达“记住我的偏好/习惯”，Agent 先使用 `user-memory` 提议候选记忆，展示范围、来源、冲突和审批哈希，用户确认后才保存；
2. 先说明标的范围、频率、入场、出场、仓位、成本、止盈止损和验证区间；
3. Agent 使用 `strategy-workbench` 把自然语言整理为可审阅的策略版本；
4. 用户确认后保存并激活版本，再准备数据和运行回测；
5. `backtest-analysis` 分析收益、回撤、交易、样本内外差异、缺口和证据，并提出可选实验；
6. Agent 读取 `get_backtest_report_context`，每条 AI 判断和实验都引用返回的 `evidence_ids`，用户偏好单独使用 `memory_refs`；
7. 如果要调整策略，先用 `prepare_strategy_revision` 展示完整逐字段 diff，逐项与用户讨论，用户明确批准完整 diff 后才保存新版本；
8. 用户确认实验和新的 benchmark、无风险利率、成本、仓位后才运行新回测；
9. `daily-strategy-observer` 在日维度输出规则信号和证据，AI 观察单独成栏，不修改规则结果。

标的研究的推荐顺序是：先说“分析 512890”，确认标的和数据截止时间；需要策略联动时再说“结合我的策略分析 512890”；只有明确说“启用多角色分析”才启用多个分析角色；最后说“查看这次研究的证据”，Agent 应返回合法的 `evidence_id`、来源和数据时间。研究分析不能改写确定性快照，也不能自动下单。

系统只提供研究建议和证据，不自动下单，也不把“回测结果好”自动标成策略通过。

## 回测口径

当前引擎采用以下交易语义：

- 只做多；普通规则信号默认在日线收盘产生、下一交易日开盘执行；周期定投可配置为计划日开盘或下一交易日开盘；
- 默认股票和 ETF 每次按 100 股交易单位，可在策略仓位方案中覆盖；
- 持仓按买入批次保存，按 FIFO 处理成本；T+1：买入当日不可卖出；
- 支持持仓中再次触发入场信号加仓，以及 weekly/monthly/dates 周期定投；定投资金可来自新增资金或已有现金；
- 普通出场规则支持全仓、百分比或固定数量卖出；固定数量超出可卖持仓时按可卖最大数量成交并告警；
- `capital_scope=per_symbol` 使用标的独立资金，`capital_scope=portfolio` 使用共享组合现金池；
- 同一执行日按 `action_priority` 处理 `SELL`、`PERIODIC_BUY`、`SIGNAL_BUY`；停牌、涨停买入、跌停卖出和明确 `unfillable` 数据会产生 `UNFILLED` 记录；
- 跳空使用实际开盘价；止盈和止损同日同时触发时输出 `stop_first` 与 `take_first` 两个情景；
- 可使用复权字段，复权口径和分红、公司行动分别保存；
- 成本模板保存名称、版本、佣金、印花税、过户费、滑点和最低佣金；
- 外部定投资金以现金流记录；存在外部现金流时，结果增加总投入资金、净利润和时间加权收益率，不把初始资金收益率作为唯一口径；
- 缺失日线允许继续运行，但会在 JSON、Markdown 和 MCP 结果中明确标记；
- 默认样本切分为 70/30，并记录 3 年训练、1 年测试滚动验证配置；
- Python 策略插件必须先审阅批准，在独立进程中运行，使用依赖白名单、超时和网络隔离。

这些约束是回测引擎的确定性语义；AI 只解释已经生成的结果和证据。

## 开发者：系统结构

```text
FireAgent/
├─ AGENTS.md                         # 项目级 Agent 路由与安全约束
├─ README.md
├─ skills/
│  ├─ strategy-workbench/SKILL.md
│  ├─ backtest-analysis/SKILL.md
│  ├─ daily-strategy-observer/SKILL.md
│  ├─ stock-research/SKILL.md
│  ├─ user-memory/SKILL.md
│  └─ sentiment-research/SKILL.md
├─ scripts/
│  ├─ sync.ps1.example               # PowerShell 示例；实际脚本本地忽略
│  └─ sync.sh.example                # Git Bash/WSL 示例；实际脚本本地忽略
└─ mcp_server/
   ├─ cli.py                         # doctor、sync、策略校验、回测产物
   ├─ sync.py                        # 项目级 Codex MCP 配置生成
   ├─ server.py                      # MCP stdio 与工具编排
   ├─ dependencies.py                # a-stock-data 前置检查
   ├─ runtime.py                     # 本地运行时组装
   ├─ workspace.py                   # 独立用户工作区解析与初始化
   ├─ domain/
   │  ├─ models.py                   # 可序列化领域对象
   │  └─ strategy.py                  # StrategySpec 与版本契约
   ├─ adapters/
   │  ├─ a_stock_data.py             # a-stock-data 行情适配层
   │  ├─ instrument_research.py     # 标的研究 Provider
   │  └─ feishu.py                   # 飞书 Webhook 适配器，默认关闭
   ├─ services/
   │  ├─ backtesting.py               # 确定性回测引擎
   │  ├─ historical_data.py           # a-stock-data 历史日线 Provider 与缓存
   │  ├─ observer.py                  # 日规则观察
   │  ├─ research.py                  # 研究事实、技术指标和 baseline-v1 评分
   │  ├─ research_artifacts.py        # 研究卡 JSON、Markdown 和图表
   │  ├─ sentiment.py                 # 情绪抽取与确定性因子聚合
   │  ├─ sentiment_backtest.py        # 情绪快照按日期接入回测日线
   │  ├─ sentiment_artifacts.py       # 情绪快照 JSON、Parquet、Markdown 和图表
   │  ├─ provider_registry.py         # 显式 Provider 选择
   │  ├─ plugin_runner.py              # 审批后的 Python 插件隔离运行
   │  ├─ reporting.py                 # 午间观察报告
   │  └─ artifacts.py                 # JSON、Markdown、CSV 产物
   └─ storage.py                      # SQLite 元数据与运行记录
```

### 对象与数据协议

`StrategySpec` 是 Agent、Skill、MCP 和引擎之间的稳定边界。它包含 `strategy_id`、`version`、`universe`、`frequency`、`entry`、`exit`、`position_sizing`、`cost_profile`、`validation`、`data_policy` 和可选插件配置。保存后版本内容哈希进入 SQLite，激活必须使用明确的策略 ID 和版本。

数据适配器必须保留：

- `source_name`、`source_url`、`source_version`；
- 数据时间、数据区间和频率；
- `a-stock-data` Skill 名称及版本；
- 复权口径、公司行动和缺失状态；
- 不可成交、停牌和来源失败原因。

行情接入遵循 `a-stock-data` 的数据源规则：优先使用通达信和腾讯，东财请求串行限流并复用会话，主源失败时使用 Skill 定义的备用源，标的代码先统一归一化。腾讯历史日线请求使用 `YYYY-MM-DD` 日期段，并按不超过 640 条的上限分段、合并和按日期去重。新增来源应放在适配器中，不应把来源细节散落到 Skill 或报告模板。

### SQLite 记录

本地数据库默认在用户确认的独立工作区 `stock_research.sqlite3`，包括观察清单、通知配置、报告运行、投递尝试、策略版本、回测运行、信号证据、研究快照/证据和版本化长期记忆。长期记忆支持 FTS5 搜索、三级范围、用户确认后替代、归档、永久删除及 JSON 导入导出。研究快照中的确定性事实与 AI 分析分离保存；刷新只创建新快照。真实数据缓存、记忆导出和报告产物也在该工作区，不应提交 Git；Webhook 地址和签名密钥只从环境变量或未提交 `.env` 读取，不能写入数据库、日志或报告。

### 扩展方式

- 新研究流程：增加或修改 Skill 文档，确定性计算仍放在 MCP/服务层；
- 新 MCP 工具：在 `tool_definitions()` 声明输入契约，在 `McpApplication` 实现处理器，并添加结构化结果测试；
- 新数据源：实现适配器，统一来源、时间和缺失字段，保留 a-stock-data 的限流与降级策略；
- 新策略指标：先增加确定性规则和回归测试，不能让 AI 直接改写历史结果；
- 新 Python 插件：必须经过用户审阅批准，使用 `PythonStrategyPluginRunner` 的白名单、超时和无网络进程；
- 新手机渠道：实现渠道适配器，不改变日报和研究逻辑。飞书单向定时推送已实现；飞书双向 Agent、企业微信和云端任务属于后续阶段。

## 故障排查

### `doctor` 报告找不到 Skill

确认 `SKILL.md` 存在、frontmatter 中 `name: a-stock-data`，并检查版本是否至少为 `3.6.0`。也可以显式设置：

```powershell
$env:A_STOCK_DATA_SKILL_PATH = "C:\path\to\a-stock-data\SKILL.md"
python -m mcp_server.cli doctor
```

### `sync` 报告找不到项目 Skill

确认 `skills/strategy-workbench/SKILL.md`、`skills/backtest-analysis/SKILL.md`、`skills/daily-strategy-observer/SKILL.md`、`skills/stock-research/SKILL.md`、`skills/user-memory/SKILL.md` 和 `skills/sentiment-research/SKILL.md` 都存在，并且 frontmatter 的 `name` 与目录名一致。

### 长期记忆无法保存或搜索

先调用 `prepare_memory`，确认候选内容没有在确认前被改写，并将返回的 `approval_hash` 原样传给 `save_memory`。如果出现冲突，必须把返回的旧 `memory_id` 放入 `supersedes_ids`。中文全文搜索无结果时仍可使用 `memory-list`；`doctor` 会报告 FTS5 是否可用。

### `.sh` 无法运行

PowerShell 不能直接执行 `.sh`。请使用 Git Bash 或 WSL，或者改用：

```powershell
.\scripts\sync.ps1
```

### 回测报告出现缺口或未成交

这是显式数据质量结果，不应静默忽略。查看 `result.json` 的 `warnings`、情景警告和 `trades.csv` 中的 `UNFILLED` 行，确认停牌、涨跌停、缺失字段或成本模板是否符合预期。

### 报告没有图表

先运行 `python -m pip install matplotlib`。即使 Matplotlib、字体或单张图生成失败，回测和 `report.md` 仍应完成；在 `result.json.artifact_warnings` 和报告的图表警告中查看具体原因。

### AI 分析写回失败

重新调用 `get_backtest_report_context`，不要复用旧的 `context_hash`。检查每条 `summary`、`strengths`、`risks`、`data_limitations` 和 `experiments` 是否为带 `evidence_refs` 的对象数组，且每个引用都来自当前上下文的 `evidence_ids`。

### 修改策略没有保存

这是审批保护：先调用 `prepare_strategy_revision`，与用户逐项确认完整 diff，再将同一份 `change_set`、`approved_diff_hash`、`parent_version` 和 `user_confirmed=true` 传给 `save_strategy_version`。旧版本不会被覆盖。

### 长期记忆

用户可以直接对 Agent 说“记住我的最大组合回撤是 15%”或“记录我不追涨的交易原则”。Agent 应先调用 `prepare_memory` 展示规范化内容、范围、来源、冲突和审批哈希；用户确认完整内容后，才调用 `save_memory`。

长期记忆支持全局、策略和股票/ETF 三级范围。它只用于个性化风险提示和策略讨论，不会自动修改策略、回测结果、规则信号或下单。风险偏好确认 180 天后会提示复核，交易心得和习惯默认长期有效。

查看和迁移记忆：

```powershell
python -m mcp_server.cli memory-list
python -m mcp_server.cli memory-search "回撤"
python -m mcp_server.cli memory-export --output .\memory-export.json
python -m mcp_server.cli memory-import --file .\memory-export.json
```

`memory-import` 默认只预览。确认预览返回的 `import_hash` 后，再运行 `memory-import --file <文件> --confirm-hash <哈希>`。导出文件包含个人偏好和来源摘要，应保存在独立工作区，不要提交 Git。

### 如何启用通知

飞书默认关闭，正常本地回测不需要任何 Webhook。启用时，把配置放在独立工作区的 `config/.env`，不要写入仓库、SQLite 或日志：

```dotenv
FIREAGENT_ENABLE_FEISHU=1
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/替换为你的地址
FEISHU_WEBHOOK_SECRET=替换为你的签名密钥
FEISHU_MAX_PAYLOAD_BYTES=18432
```

先查看状态（不发送消息），再发送明确标记的测试消息：

```powershell
python -m mcp_server.cli notification-status
python -m mcp_server.cli notification-test --message "FireAgent 通知测试"
```

正式定时推送还需要可靠的 A 股交易日历。程序优先使用 `exchange-calendars` 的 `XSHG` 日历；也可以在独立工作区建立 `data/trading_holidays.json`，由用户维护节假日配置。两者都不存在时，`daily-report --send` 会返回 `blocked_calendar_unavailable` 并阻止发送，不会把普通工作日当成交易日：

```powershell
python -m pip install exchange-calendars
```

交易日午间日报由 Windows 任务计划程序每天 12:00 唤醒，程序设置为项目 Python，参数为 `-m mcp_server.cli daily-report --send`，工作目录为 FireAgent 项目根目录。运行器自行检查 A 股交易日历，并在配置的 12:03–12:05 窗口发送；非交易日只记录跳过，不推送。手工试跑或预览可以使用：

```powershell
python -m mcp_server.cli preview --report-date YYYY-MM-DD
python -m mcp_server.cli daily-report --send
```

自定义机器人请求体不能超过 20 KB，适配器默认按 18 KiB 安全阈值拆分并重试；Webhook 签名和错误信息会脱敏。当前方案是单向定时推送，不支持在飞书里与 Agent 双向对话；双向对话需要后续接入飞书应用机器人和消息事件桥接。完整的任务计划配置和故障排查请直接参阅本文对应章节。
