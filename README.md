# FireAgent：A 股研究与策略回测系统

> 重要：本项目的真实 A 股数据依赖 `a-stock-data` Skill。未安装或版本不满足时，项目不能运行真实数据功能；项目不会静默切换到其他数据源。

FireAgent 以 Agent 对话为入口，用三个 Skill 帮助用户澄清策略、运行回测和进行日维度观察；确定性计算、数据证据、结果保存由本地 MCP 服务和回测引擎完成。

项目当前定位是本地研究工具：不自动下单，不连接券商交易接口；不需要 Ollama；首期不需要飞书或其他手机推送。

## 普通用户：Windows 快速开始

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
python -m pip install mootdx requests pandas stockstats
```

如果要启用历史数据的 Parquet 缓存，再安装一个 Parquet 引擎：

```powershell
python -m pip install pyarrow
```

来源与更新说明见 [`a-stock-data` GitHub 仓库](https://github.com/simonlin1212/a-stock-data)。项目启动时会检查 `SKILL.md` 的名称和最低版本（当前最低要求为 `3.6.0`）。也可以把 Skill 安装到 Claude Code 或 Agents 目录，或者显式指定路径：

```powershell
$env:A_STOCK_DATA_SKILL_PATH = "C:\path\to\a-stock-data\SKILL.md"
```

不设置覆盖变量时，项目依次检查当前用户的 `.codex\skills`、`.agents\skills` 和 `.claude\skills`。

### 3. 健康检查

在项目根目录 `FireAgent` 中运行：

```powershell
python -m mcp_server.cli doctor
```

成功时会显示 Python、SQLite、数据库位置和 `a_stock_data_skill` 的路径及版本。Skill 缺失、frontmatter 错误或版本过低时，命令返回非零状态，并给出安装或修复提示。

### 4. 准备策略与数据

策略文件是 JSON，至少要说明：策略 ID、版本、标的集合、日线频率、入场规则、出场规则、仓位方案。正式回测还要给出带版本的成本模板。日线数据是按标的代码分组的 JSON 对象，每根 K 线至少包含 `date`、`open`、`high`、`low`、`close`；可额外提供 `adj_*` 或 `raw_*` 加 `adj_factor`、停牌和涨跌停字段。

先验证策略：

```powershell
python -m mcp_server.cli validate-strategy --file .\path\to\your-strategy.json
```

运行本地探索性回测并生成可审阅产物：

```powershell
python -m mcp_server.cli run-backtest `
  --strategy .\path\to\your-strategy.json `
  --data .\path\to\your-daily-data.json `
  --output-dir .\data\artifacts\latest
```

输出目录包含：

- `result.json`：结构化结果、指标、验证切分、来源和警告；
- `report.md`：适合阅读和分享的回测报告；
- `trades.csv`：成交与明确标记的未成交订单。

探索性回测不会替用户确认成本或仓位。需要运行正式模式时，必须显式确认两者：

```powershell
python -m mcp_server.cli run-backtest `
  --strategy .\path\to\your-strategy.json `
  --data .\path\to\your-daily-data.json `
  --output-dir .\data\artifacts\formal `
  --run-mode formal `
  --confirm-cost-profile `
  --confirm-position-sizing
```

### 5. 启动 MCP stdio 服务

在支持 MCP stdio 的 Agent 中，把启动命令配置为：

```powershell
python -m mcp_server.server
```

服务启动时会先检查 `a-stock-data`。可用工具包括策略校验与版本管理、数据准备、回测、结果比较、日观察、信号证据和观察清单管理。通知工具保留接口但默认关闭。

`prepare_backtest_data` 可以接收 `cache_dir`，将已经准备好的日线快照写入 Parquet；SQLite 只保存运行元数据和证据，不承载历史 K 线本体。

开发者可以在项目根目录运行完整测试；禁用当前环境中与项目无关的 pytest 自动插件：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q
```

## 用户如何与 Agent 协作

推荐的对话闭环是：

1. 先说明标的范围、频率、入场、出场、仓位、成本、止盈止损和验证区间；
2. Agent 使用 `strategy-workbench` 把自然语言整理为可审阅的策略版本；
3. 用户确认后保存并激活版本，再准备数据和运行回测；
4. `backtest-analysis` 分析收益、回撤、交易、样本内外差异、缺口和证据，并提出可选实验；
5. 用户确认实验后才运行新回测；
6. `daily-strategy-observer` 在日维度输出规则信号和证据，AI 观察单独成栏，不修改规则结果。

系统只提供研究建议和证据，不自动下单，也不把“回测结果好”自动标成策略通过。

## 回测口径

当前引擎固定采用以下语义：

- 只做多，日线收盘产生信号，下一交易日开盘执行；
- 默认股票和 ETF 每次按 100 股交易单位，可在策略仓位方案中覆盖；
- T+1：买入当日不可卖出；停牌、涨停买入、跌停卖出和明确 `unfillable` 数据会产生 `UNFILLED` 记录；
- 跳空使用实际开盘价；止盈和止损同日同时触发时输出 `stop_first` 与 `take_first` 两个情景；
- 可使用复权字段，复权口径和分红、公司行动分别保存；
- 成本模板保存名称、版本、佣金、印花税、过户费、滑点和最低佣金；
- 缺失日线允许继续运行，但会在 JSON、Markdown 和 MCP 结果中明确标记；
- 默认样本切分为 70/30，并记录 3 年训练、1 年测试滚动验证配置；
- Python 策略插件必须先审阅批准，在独立进程中运行，使用依赖白名单、超时和网络隔离。

这些约束是回测引擎的确定性语义；AI 只解释已经生成的结果和证据。

## 开发者：系统结构

```text
FireAgent/
├─ README.md
├─ docs/stock-research-system-design.md
├─ skills/
│  ├─ strategy-workbench/SKILL.md
│  ├─ backtest-analysis/SKILL.md
│  └─ daily-strategy-observer/SKILL.md
└─ mcp_server/
   ├─ cli.py                         # doctor、策略校验、回测产物
   ├─ server.py                      # MCP stdio 与工具编排
   ├─ dependencies.py                # a-stock-data 前置检查
   ├─ runtime.py                     # 本地运行时组装
   ├─ domain/
   │  ├─ models.py                   # 可序列化领域对象
   │  └─ strategy.py                  # StrategySpec 与版本契约
   ├─ adapters/
   │  ├─ a_stock_data.py             # a-stock-data 行情适配层
   │  └─ feishu.py                   # 后续渠道适配器，默认关闭
   ├─ services/
   │  ├─ backtesting.py               # 确定性回测引擎
   │  ├─ observer.py                  # 日规则观察
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

行情接入遵循 `a-stock-data` 的数据源规则：优先使用通达信和腾讯，东财请求串行限流并复用会话，主源失败时使用 Skill 定义的备用源，标的代码先统一归一化。新增来源应放在适配器中，不应把来源细节散落到 Skill 或报告模板。

### SQLite 记录

本地数据库默认在 `data\stock_research.sqlite3`，包括观察清单、通知配置、报告运行、投递尝试、策略版本、回测运行和信号证据。真实数据缓存和报告产物不应提交 Git；Webhook 地址和签名密钥只从环境变量或未提交 `.env` 读取，不能写入数据库、日志或报告。

### 扩展方式

- 新研究流程：增加或修改 Skill 文档，确定性计算仍放在 MCP/服务层；
- 新 MCP 工具：在 `tool_definitions()` 声明输入契约，在 `McpApplication` 实现处理器，并添加结构化结果测试；
- 新数据源：实现适配器，统一来源、时间和缺失字段，保留 a-stock-data 的限流与降级策略；
- 新策略指标：先增加确定性规则和回归测试，不能让 AI 直接改写历史结果；
- 新 Python 插件：必须经过用户审阅批准，使用 `PythonStrategyPluginRunner` 的白名单、超时和无网络进程；
- 新手机渠道：实现渠道适配器，不改变日报和研究逻辑。飞书、企业微信和 Windows 任务计划属于后续阶段。

## 故障排查

### `doctor` 报告找不到 Skill

确认 `SKILL.md` 存在、frontmatter 中 `name: a-stock-data`，并检查版本是否至少为 `3.6.0`。也可以显式设置：

```powershell
$env:A_STOCK_DATA_SKILL_PATH = "C:\path\to\a-stock-data\SKILL.md"
python -m mcp_server.cli doctor
```

### 回测报告出现缺口或未成交

这是显式数据质量结果，不应静默忽略。查看 `result.json` 的 `warnings`、情景警告和 `trades.csv` 中的 `UNFILLED` 行，确认停牌、涨跌停、缺失字段或成本模板是否符合预期。

### 如何启用通知

首期不启用飞书，正常本地回测不需要任何 Webhook。飞书 Webhook、签名安全、限流拆分和 Windows 交易日调度将在本地回测验收后单独讨论和启用。

## 设计文档与路线

完整的边界、数据模型、MCP 接口、策略回测语义、证据链和后续通知设计见 [`docs/stock-research-system-design.md`](docs/stock-research-system-design.md)。当前路线是：本地依赖检查与回测闭环 → 日维度观察与分析建议 → 数据缓存和更完整的复权/公司行动 → 通知渠道与定时器 → 云端迁移。
