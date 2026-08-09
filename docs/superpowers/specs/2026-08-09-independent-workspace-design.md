# FireAgent 独立工作区与自动数据准备设计

## 目标

用户第一次使用 FireAgent 时，由 Agent 询问一个独立的 Windows 工作目录。用户提供并确认路径后，FireAgent 自动创建数据、策略、缓存、报告和运行记录目录；这些用户数据不进入代码仓库，也不参与 Git 同步。之后回测默认自动通过已安装的 `a-stock-data` Skill 准备历史日线数据，不再要求用户填写 `--data`。

## 边界

- 代码仓库仍为 `D:\Life_lover\FIRE计划\FireAgent`，只保存代码、Skill、模板和文档。
- 工作区路径必须由用户提供，不能使用硬编码的默认数据目录。
- 工作区不能等于代码仓库，也不能位于代码仓库内部。
- 项目只在仓库内保存 `.fireagent\workspace.json` 路径指针，该文件加入 `.gitignore`。
- 不在工作区执行 `git init`、`git add`、`git commit` 或远程同步。
- `FIREAGENT_WORKSPACE` 环境变量可以临时覆盖本地路径指针，但不会自动写回配置。

## 工作区结构

初始化后创建：

```text
<workspace>/
├─ data/raw/                 # 原始下载快照和请求元数据
├─ data/parquet/             # 可复用的历史日线缓存
├─ data/trading_holidays.json
├─ strategies/               # 用户确认的策略 JSON
├─ artifacts/latest/          # 探索性回测产物
├─ artifacts/formal/         # 正式回测产物
├─ reports/                  # 报告和证据导出
├─ logs/                     # 本地运行日志，不写密钥
└─ stock_research.sqlite3    # 元数据、策略版本和运行记录
```

## 初始化流程

Agent 发现项目没有有效工作区时，必须先询问：

```text
请提供一个独立的 FireAgent 工作文件夹绝对路径。不要选择 FireAgent 代码仓库目录。
例如：D:\FireAgentWorkspace
```

用户提供路径后，Agent 调用：

```powershell
python -m mcp_server.cli init --workspace "D:\FireAgentWorkspace"
```

初始化命令验证绝对路径、仓库边界和写入权限；目录不存在时创建目录，目录已存在时只补齐 FireAgent 子目录。已有不同工作区指针时必须明确覆盖，不静默切换。

## 运行时路径解析

路径优先级为：

1. 当前进程的 `FIREAGENT_WORKSPACE`；
2. 仓库内被忽略的 `.fireagent\workspace.json`；
3. 没有工作区时，面向用户的命令返回初始化提示；测试可以显式注入临时工作区。

`runtime.py`、CLI、MCP stdio 服务和 `sync` 使用同一解析器。`sync` 将工作区路径写入被忽略的项目级 `.codex\config.toml`，并固定 `FIREAGENT_DB_PATH`、交易日历和工作区缓存路径。

## 自动历史数据

策略的验证区间优先读取 `validation.start_date`、`validation.end_date`；未指定结束日期时使用当前日期，未指定开始日期时使用最近四年。用户仍可在对话中指定其他区间。系统通过 `a-stock-data` 的通达信/腾讯优先规则获取日线，统一代码和字段，记录来源、来源 URL、Skill 版本、数据时间和缺失状态，并将可复用快照写入工作区 Parquet。

`run-backtest` 和 MCP `run_backtest` 在未提供 `data` 时自动执行数据准备；保留显式 `data` 输入用于测试、离线重放和高级用户。网络失败、数据缺失或依赖不完整时必须返回明确错误或缺口，不得静默使用其他来源。

## 验收标准

- Agent 能先询问并保存用户提供的独立工作区路径。
- 初始化会创建全部目录，代码仓库不产生用户数据。
- 工作区路径指针和 MCP 配置均被 Git 忽略。
- `doctor` 显示工作区状态和数据 Skill 状态。
- 不传 `--data` 的回测能调用注入的历史数据 Provider 并写入工作区缓存。
- 显式 `--data` 的旧用法继续有效。
- 缓存和结果保存来源、时间、Skill 版本和缺口信息。
- 测试不访问真实网络，使用注入的假 Provider；生产启动仍检查 `a-stock-data`。
