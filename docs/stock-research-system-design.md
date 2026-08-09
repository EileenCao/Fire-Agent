# 股票研究系统设计

## 1. 目标与边界

### 目标

构建一个以 Agent 为入口、以 MCP 为工具层、以 `a-stock-data` 为 A 股数据接入层的个人股票研究系统。系统优先服务 A 股股票和 ETF，输出可追溯的研究事实、估值、评分、风险和候选筛选结果。

### 首个可交付版本

1. 输入股票或 ETF 代码，生成单标的研究结果。
2. 支持行情、财务、估值、分红、行业、公告/研报等可用数据的统一展示。
3. 输出透明的基准评分和内置风格评分模板。
4. 保存研究快照、数据来源、采集时间和人工笔记到本地 SQLite。
5. 提供基础筛选工具，支持估值、质量、股息、成长和基础技术指标；筛选只产生候选，不直接生成买卖指令。

### 明确不做

- 不接入券商交易、自动下单或自动调仓。
- 不把 Ollama 作为硬依赖。
- 不让大模型替代数据校验、估值计算或评分规则。
- 不在首版实现复杂的自定义规则编辑器、多人账号和云端部署。
- 不默认覆盖港股、美股等非 A 股市场。

## 2. 核心设计原则

- **证据优先**：每个指标都带来源、采集时间、统计口径和数据状态。
- **确定性优先**：估值、评分、筛选由可测试的 Python 规则完成；模型只解释已获取的证据。
- **部分可用**：某个数据源失败时返回缺失字段和错误原因，不用旧数据静默冒充实时数据。
- **可替换接入**：数据源、评分模板和模型运行时均通过接口隔离。
- **先小后大**：先完成 Agent 内可用的研究闭环，再扩展网页界面、复杂筛选和持仓管理。

## 3. 系统边界与架构

```text
用户
  ↓ 自然语言请求
Agent（Codex / Cursor / 其他支持 MCP 的 Agent）
  ↓ 读取 Skill，调用 MCP tools
stock-research Skill
  ↓ 研究流程、口径、输出格式
Stock Research MCP Server
  ├─ 数据适配层：a-stock-data
  ├─ 研究服务：行情 / 财务 / 估值 / 风险 / 评分 / 筛选
  ├─ 持久化：SQLite + 缓存 + 研究快照
  └─ 可选模型适配器：Ollama 或其他 OpenAI-compatible endpoint
```

### Skill 层

Skill 只描述 Agent 如何完成研究，不保存业务状态。它负责：

- 判断用户是在查单只标的、做批量筛选还是复盘研究；
- 按数据类型调用对应 MCP 工具；
- 要求输出数据日期、来源和口径；
- 先给事实，再给规则评分，再给条件式观点；
- 当数据不足或来源冲突时明确标注，不自行补全。

### MCP 层

MCP Server 是真正的业务执行层，负责调用数据接口、计算指标、读写 SQLite 和返回结构化结果。首版建议提供以下工具：

- `research_instrument(code, refresh=false)`：生成股票/ETF单标的研究包。
- `get_market_data(code, period, refresh=false)`：行情与基础交易数据。
- `get_fundamentals(code, refresh=false)`：财务、盈利质量、分红和行业数据。
- `get_valuation(code, refresh=false)`：PE、PB、股息率、历史分位及 ETF/指数估值。
- `score_instrument(code, profile="baseline")`：基准或内置风格评分。
- `screen_instruments(filters, profile="baseline")`：按过滤条件返回候选列表。
- `save_research_snapshot(code, note, score_profile)`：保存研究快照和人工笔记。
- `list_research_snapshots(code)`：读取历史快照，用于复盘比较。

工具返回统一结构：`data`、`as_of`、`source`、`methodology`、`warnings` 和 `errors`。任何无法确认日期或口径的字段都不能伪装成实时值。

## 4. 数据与持久化

### 独立用户工作区与 Git 边界

代码仓库与用户数据必须分离。首次使用时，Agent 先询问用户提供一个不在 FireAgent 仓库内的绝对路径，例如 `D:\FireAgentWorkspace`；用户确认后运行：

```powershell
python -m mcp_server.cli init --workspace "D:\FireAgentWorkspace"
```

项目只在仓库内保存被 Git 忽略的 `.fireagent\workspace.json` 指针。运行时路径解析优先使用环境变量 `FIREAGENT_WORKSPACE`，其次使用该指针；没有工作区时，面向用户的回测、MCP 和同步命令必须明确阻止并给出初始化命令。禁止在工作区执行 `git init`、`git add`、`git commit` 或远程同步。

工作区目录固定为：

```text
<workspace>/
├─ data/raw/                 # 原始数据快照
├─ data/parquet/             # Parquet 日线缓存
├─ data/trading_holidays.json
├─ strategies/               # 默认 strategy.json
├─ artifacts/latest/         # 探索性回测
├─ artifacts/formal/         # 正式回测
├─ reports/
├─ logs/
└─ stock_research.sqlite3
```

SQLite、策略文件、原始数据、Parquet、报告和日志均默认写入工作区；仓库内 `data/` 只允许保留测试夹具或占位文件，并通过 `.gitignore` 阻止本地运行数据进入 Git。

### 数据源

优先使用 `a-stock-data` 已提供的接口和降级路径：

- 行情与 ETF：腾讯/通达信等可用源；
- 财务与 F10：通达信、公开财报接口；
- 研报、公告和新闻：对应公开源；
- 指数/ETF：标的指数估值、成分、跟踪误差、净值与场内价格。

MCP 适配器不得把某一个源的字段格式泄露给上层。所有源先转换为统一的领域模型，再交给研究服务。

### SQLite 数据

首版只使用本地 SQLite，数据库路径通过配置指定，默认放在用户确认的独立工作区根目录并加入 Git 隔离。建议保存：

- `instruments`：代码、名称、市场、类型（股票/ETF）、标的指数。
- `market_snapshots`：价格、成交、换手、交易日期和来源。
- `fundamental_snapshots`：财务指标、报告期、财报口径和来源。
- `valuation_snapshots`：PE/PB/股息率/分位、计算方法和日期。
- `research_snapshots`：研究结果、评分版本、风险项、人工笔记和创建时间。
- `watchlist`：观察列表和用户标签。

每条标准化数据至少包含 `source_name`、`source_url`（如有）、`collected_at`、`as_of`、`period`、`methodology` 和 `status`。

### 缓存与刷新

- 打开研究时优先读取未过期缓存。
- 用户可手动强制刷新。
- 后续可增加定时刷新任务，但任务失败必须记录状态和错误。
- 不同数据设置不同有效期：行情短、财报长、估值按交易日更新。

## 5. 研究与评分

### 单标的研究顺序

1. 标的识别：确认代码、名称、市场和类型，避免股票代码与指数代码混淆。
2. 数据完整性：显示各数据的报告期、采集时间和缺失项。
3. 基本面：盈利、现金流、ROE、负债、分红和行业暴露。
4. 估值：绝对倍数、历史分位、股息率，以及股票/ETF适用的估值口径。
5. 风险：行业集中、盈利波动、财务风险、流动性、回撤和数据缺失。
6. 评分：先计算透明分数，再生成解释。
7. 结论：输出优势、疑点、触发条件和待跟踪事项；不默认输出直接买卖指令。

### 评分层级

首版分两层：

- **基准评分**：估值、质量、成长、股息、趋势/风险五个维度；每个维度显示原始指标、标准化分数、权重和扣分原因。
- **内置风格模板**：价值、成长、红利、趋势。模板只调整权重和适用指标，不复制一套新的计算逻辑。

自定义公式和阈值编辑器放到后续版本，避免在数据口径尚未稳定时制造不可解释的分数。

ETF 与股票分开计算：股票优先看公司盈利和现金流；ETF优先看跟踪指数、成分集中度、估值、股息、跟踪误差、费用和场内折溢价。

### 筛选雏形

筛选器支持组合条件和结果排序，首版包含：

- 估值：PE、PB、股息率、估值分位；
- 质量：ROE、盈利稳定性、现金流和负债；
- 股息：连续分红、股息率、分红变化；
- 成长：营收/利润增速及其变化；
- 技术：均线趋势、动量、成交量和回撤。

筛选结果必须显示命中条件和缺失条件，不把“通过筛选”解释为“应该买入”。

## 6. 可选 AI 层

AI 是 provider，不是系统核心。接口至少支持：

```text
generate_research_summary(evidence_bundle, profile) -> cited_summary
answer_research_question(question, evidence_bundle) -> cited_answer
```

默认不要求安装 Ollama。需要本地模型时，使用 Ollama 的 OpenAI-compatible endpoint；也可以切换到其他兼容服务。模型输出必须：

- 只能使用传入的证据包；
- 对关键事实引用指标来源或快照编号；
- 对不确定内容使用“可能/需要确认”等表述；
- 可以给出条件式观点，但不能绕过规则直接下单或伪造数据。

## 7. 失败处理与安全边界

- 数据源超时、限流、字段变化：返回可读错误，记录源和请求时间，并尝试已定义的降级源。
- 多源数值冲突：保留各源值，标记冲突，优先展示带明确口径和日期的值。
- 缺少关键指标：评分降级为“不可完整评分”，不以零值替代。
- API Key 和本地配置只从环境变量或未跟踪的 `.env` 读取，禁止写入 Skill、代码和提交记录。
- 本地数据库和缓存不提交 Git；仓库只保存 schema、示例配置和必要的测试夹具。

## 8. 目录规划

```text
FireAgent/
├─ docs/
│  └─ stock-research-system-design.md
├─ skills/
│  └─ stock-research/
│     └─ SKILL.md
├─ mcp_server/
│  ├─ server.py
│  ├─ domain/
│  ├─ adapters/
│  ├─ services/
│  ├─ storage/
│  └─ tests/
├─ config/
│  └─ example.env
├─ data/
│  └─ .gitkeep
├─ README.md
└─ .gitignore
```

上面的仓库目录不承载用户运行数据。用户工作区由 `init --workspace` 创建并独立保存数据库、策略、原始数据、Parquet、报告和日志；`.fireagent\workspace.json` 只是被忽略的本地指针。

## 9. 实施阶段与验收标准

### 阶段一：研究内核

> 本节保留早期研究内核草案。当前实现以第 11–19 节的策略、回测、日观察和 MCP 契约为准；早期的 `research_*` 工具名不是当前实现承诺的接口。

- MCP Server 可通过 stdio 启动。
- 研究与回测结果能返回结构化证据包，即使部分实时字段缺失也能说明原因。
- 股票与 ETF 能区分估值方法。
- 结果包含数据日期、来源、口径、警告和错误。
- SQLite 能保存并读取研究快照与人工笔记。

### 阶段二：评分与筛选

- 基准评分和四个内置模板输出可解释分项。
- 筛选支持估值、质量、股息、成长和基础技术条件。
- 筛选结果能显示命中条件、缺失条件和数据新鲜度。

### 阶段三：Agent 体验

- Skill 能指导 Agent 正确调用工具并按“事实→评分→风险→条件观点”输出。
- 可选 AI 只基于证据包生成带引用的摘要。
- 失败源不会导致无提示的错误结论。

### 测试要求

- 单元测试：代码标准化、股票/ETF识别、估值计算、分数计算、缺失数据处理。
- 集成测试：MCP 工具参数校验、SQLite 快照读写、数据源失败降级。
- 回归夹具：使用脱敏的股票和 ETF 样本，固定 `as_of` 日期，避免测试依赖实时行情。
- 手工验收：用自然语言请求研究用户指定的股票或 ETF，确认输出包含来源、日期、估值口径、风险和不确定项。

## 10. 当前假设

- 项目根目录为 `D:\Life_lover\FIRE计划\FireAgent`。
- 现有 FireAgent 代码可以按本设计重构；与当前契约不一致的旧接口不作为实现约束，不应覆盖用户的 IDE 配置和无关文件。
- 第一版面向单用户本地使用，MCP 使用 stdio，SQLite 本地保存。
- A 股数据接入遵循当前 `a-stock-data` skill 的可用接口和降级策略；具体端点在实现时以该 skill 的当前版本为准。
- 后续如需要独立网页，再在不改变领域服务接口的前提下增加 Web 层。
## 11. 策略工作台与回测系统

本项目的核心闭环从“研究单个标的”扩展为“通过 Agent 对话完善策略，再由确定性引擎回测”。Agent 负责澄清需求、识别未决参数、解释结果和提出实验建议；MCP 与 Python 服务负责校验、数据准备、交易模拟、持久化和证据生成。

策略使用不可变版本契约 `StrategySpec`，至少包含：

- 策略 ID、版本、名称、标的集合和日线频率；
- 入场规则、出场规则、止盈止损和动作优先级；
- 明确的仓位方案、初始资金和成本模板；
- 数据来源、复权口径、验证切分和可选 Python 插件声明。

策略工作流是“澄清 → 校验 → 用户确认 → 保存版本 → 用户激活 → 准备数据 → 探索性回测 → 分析 → 用户确认实验 → 正式回测”。没有仓位方案的策略不能进行正式回测；正式回测还必须再次确认成本模板和仓位方案。

## 12. 三个项目 Skill

### `strategy-workbench`

用于把自然语言策略整理成可审阅、可版本化的 `StrategySpec`。它必须主动询问标的、频率、信号时点、仓位、成本、滑点、停牌/涨跌停处理、验证窗口和缺失数据口径。复杂逻辑只有在用户审阅批准后才能使用 Python 插件。

### `backtest-analysis`

用于读取已保存的回测、比较多个版本、解释收益和风险、追踪信号证据，并提出用户可选择的实验。它不能修改历史结果，不能只看胜率，也不能给策略自动贴“通过/不通过”标签。

### `daily-strategy-observer`

用于读取激活版本和最新日线，输出下一交易日的规则信号。规则信号与 AI 观察严格分栏：规则信号必须有指标值、规则、数据时间、来源、Skill 版本和缺失状态；AI 观察只能解释这些证据，不能改写信号或自动下单。

三个 Skill 都依赖已安装的 `a-stock-data` Skill。Skill 只负责对话与编排，确定性计算不能放在提示词中。

## 13. MCP 工具契约

当前 MCP stdio 服务提供以下工具：

| 类别 | 工具 |
| --- | --- |
| 策略 | `validate_strategy`、`save_strategy_version`、`activate_strategy` |
| 数据与回测 | `prepare_backtest_data`、`run_backtest`、`get_backtest_result`、`compare_backtests` |
| 日观察与证据 | `observe_active_strategy`、`get_signal_evidence` |
| 观察清单 | `watchlist_add`、`watchlist_remove`、`watchlist_list` |
| 日报与通知接口 | `preview_daily_watchlist_report`、`configure_daily_report`、`send_test_notification`、`get_notification_status` |

通知工具保留接口是为了保持后续渠道适配器边界，但第一阶段默认不注册飞书渠道、不读取 Webhook、不发送网络通知。

`run_backtest` 有 `exploratory` 和 `formal` 两种模式。探索性运行用于验证想法；正式运行必须带版本成本模板，并由调用方显式确认成本模板和仓位方案。

## 14. `a-stock-data` 必需依赖与数据层

真实 A 股数据的唯一允许入口是已安装且版本满足要求的 `a-stock-data` Skill。启动检查支持 `A_STOCK_DATA_SKILL_PATH` 覆盖路径，默认检查 Codex、Agents 和 Claude Code 的用户 Skill 目录，并验证 frontmatter 中的名称和版本。`doctor`、生产 MCP 服务和真实数据回测启动时都执行检查；失败时返回安装依赖、路径和版本修复提示。

项目保留腾讯行情适配器作为 MCP 边界适配层，并通过历史 Provider 使用 Skill 认可的腾讯前复权日线线路；它不能替代 Skill 的来源路由和备用源规则。后续接入财务、研报、公告、ETF 估值等数据时，必须遵守 Skill 的通达信/腾讯优先、东财串行限流与会话复用、备用源降级和代码归一化规则。

每个指标或数据快照都应记录：`source_name`、`source_url`、`source_version`、`skill_name`、`skill_version`、实际数据时间、采集时间、口径、状态和错误原因。真实回测的结果中必须保留 Skill 版本和数据来源；单元测试可以注入假数据 Provider，但不能用假数据绕过生产启动检查。

省略 `data` 调用 `prepare_backtest_data` 或 `run_backtest` 时，历史 Provider 根据策略验证窗口自动取数，原始响应写入工作区 `data/raw/`，标准化日线写入工作区 `data/parquet/`；Parquet 引擎由本地环境提供。SQLite 只保存缓存元数据、策略版本、运行记录和证据，不把完整历史行情塞进关系表。`cache_dir` 仍可用于显式指定缓存位置。

### 本地 Agent 集成与同步边界

`a-stock-data` 是 Agent Skill：它提供数据源规则和可执行的 Python 取数代码（包括历史 K 线调用），但本身不是一个运行中的 MCP Server，也不会出现在 FireAgent 的 `tools/list` 中。FireAgent 的历史 Provider 目前通过 Skill 认可的腾讯前复权日线线路自动取数，并保留来源、URL、请求区间、数据区间、复权口径和 Skill 版本；后续可在该 Provider 内增加通达信及其他已定义备用源，不改变 MCP 和回测引擎接口。

项目提供 `python -m mcp_server.cli sync` 作为本地同步命令。它要求先完成 `init --workspace <用户确认路径>`，然后检查必需的 `a-stock-data` Skill、三个项目 Skill，并生成被 Git 忽略的 `.codex/config.toml`；配置中固定项目根目录、独立工作区、SQLite 和交易日历路径。它不会修改用户全局 Codex 配置，也不会重新安装 Skill。代码或 Skill 文档修改后再次运行该命令即可更新项目级配置和检查结果。若 MCP 进程已经启动，仍需重启 MCP 或新开任务以加载新的 Python 代码。

Windows 主入口是本地忽略的 `scripts/sync.ps1`，Git Bash/WSL 可使用本地忽略的 `scripts/sync.sh`；仓库只保留不含本机路径的 `.example` 模板。同步脚本不能在 MCP stdio 进程启动前向 stdout 写诊断内容，MCP stdout 必须只承载 JSON-RPC。

## 15. 回测交易语义

第一阶段只支持 A 股股票和 ETF、日线、仅做多。日线收盘产生信号，下一交易日开盘执行；跳空直接使用实际开盘价。默认交易单位为 100 股，仓位方案可指定交易单位、固定现金或资金比例。

引擎明确处理：

- T+1：买入当日不可卖出；
- 停牌、涨停买入、跌停卖出和数据标记的无法成交：生成 `UNFILLED` 交易和警告；
- 复权：优先使用 `adj_*` 或 `raw_* + adj_factor`，并在结果中标注价格口径；
- 公司行动：分红等现金流单独记录，不把它悄悄混入价格收益；
- 成本：理论/现实成本模板有名称、版本、佣金、印花税、过户费、滑点和最低佣金；
- 止盈止损：同一日同时触发时生成 `stop_first`、`take_first` 两个情景；开盘跳空触发时按实际开盘价处理；
- 缺失：缺失字段的日线可跳过，但必须在结果、报告和交易产物中显式标记。

默认验证记录 70/30 样本内外切分，并记录 3 年训练、1 年测试的滚动验证配置；用户可以在 `validation` 中覆盖这些参数。验证结果不改变主回测结果，也不会被 AI 重写。

Python 策略插件需要在策略中明确声明并标记为已审阅批准。运行器使用独立进程、超时、依赖白名单、静态导入检查和无网络环境；插件只能返回标准化动作，不能写入 SQLite、访问网络或直接下单。

## 16. 证据链与结果产物

回测运行保存到 SQLite 的 `backtest_runs`，每条买卖或未成交记录在 `signal_evidence` 中保存信号日期、交易日期、标的、方向、规则原因、价格、数量、来源版本和策略版本。`get_signal_evidence` 是分析 Skill 分享证据的入口。

本地 CLI 同时生成：

- JSON：完整指标、情景、验证、来源、成本、公司行动和警告；
- Markdown：面向用户阅读的摘要、来源和警告；
- CSV：成交和未成交记录，便于人工复核。

AI 输出必须把“规则信号/回测事实”和“AI 观察/可选建议”分栏。每条日维度买卖建议至少包含规则、指标值、数据截止时间、来源、Skill 版本、交易日期和证据 ID；数据不足时只能输出“无法判断”或部分结果。

## 17. 午间观察与通知设计（后续阶段）

午间日报保留原设计：A 股交易日 12:00 唤醒，使用上午收盘 11:30 数据，在 12:03–12:05 的窗口发送；只包含观察清单中的股票和 ETF，不包含全市场、组合、周报和异常提醒。每项指标必须标注实际时间、来源、来源 URL、Skill 版本和缺失状态。

飞书私人群自定义机器人、Webhook 签名、20 KB 拆分、重试、幂等键和 `delivery_attempts` 表已经在接口和数据模型中预留，但本地回测阶段不启用。未来启用时，Webhook 与签名密钥只允许来自环境变量或未提交 `.env`，不能写入 SQLite、日志或 Git。Windows 任务计划和云端定时器都只负责唤醒一次性日报运行器，不应复制研究逻辑。

## 18. 安全、可复现与边界

- 不自动下单、不连接券商、不自动调仓；
- 不要求 Ollama，AI 可由宿主 Agent 提供；
- 不把 API Key、Webhook 或签名密钥写入结果和日志；
- 固定策略版本、数据快照、Skill 版本、成本模板和验证窗口后，回测应可重复；
- 所有外部来源失败、限流、缺失和无法成交都必须可见；
- 回测建议不是投资承诺，AI 不能把回测表现直接转为未来收益判断。

## 19. 实施路线与验收

当前本地里程碑包括：Skill 前置检查、SQLite 元数据、日线回测、交易约束、策略版本、证据链、三个 Skill、MCP stdio、CLI 产物、健康检查和 Feishu 默认关闭。验收重点是：周末/非交易日不产生正式日报、股票和 ETF 均可识别、无未来数据泄漏、T+1/涨跌停/停牌/交易单位/跳空/成本/复权/公司行动和止盈止损情景有测试、缺失不静默、插件被隔离、重复运行可复现。

后续顺序为：更完整的历史数据和公司行动适配 → 更丰富的回测分析与实验管理 → 日观察长期运行 → 飞书或企业微信渠道 → Windows 任务计划 → 可迁移的云端调度。任何新增能力都不应破坏独立工作区和 Skill + MCP + SQLite 的边界。
