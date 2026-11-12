# Script-First：唯一产品基准与最小 Stable

## 1. 目标与范围

DevFlower 是面向原生 Agent 的可视化工作流编译器。用户定义部门之间的协作，固定程序将画布
编译为可独立执行的 Python；每个 Agent 自主组织内部工具、Skills、MCP 和子 Agent。
**旧 Phase 0–7 / V1 功能矩阵是历史记录，不是新版必须保留的产品范围。**

核心约束：画布自由组合；Python 是正式产物；直接使用原生 CLI；调度确定而模型行为不必确定；
GUI 与终端必须执行同一产物；先交付最小闭环，不增加大型框架或重复执行器。
原生配置默认继承，模型与强度可选覆盖；不得擅自禁用内部能力，也不得擅自放宽原生权限。

首个 Minimal Stable 只要求：创建/保存/重开画布 → 导出 Python → 真实 Codex→Claude 顺序交接
→ GUI 与外部终端运行同一脚本 → 查看实际结果、错误并安全取消。Condition、Router、Merge、
有界 Loop、确定性 Tool 和原生多轮续聊均为后续扩展，**不是首个 Stable 的发布门槛**。
开发/审核只是验收示例，不是固定产品角色。没有真实 CLI 与 GUI 闭环证据时不得发布 Stable。

本轮以集成分支 `adeb6e4998e495e990467165dfb8271cdfcf8169` 为基线。
[基线 CI 两项通过](https://github.com/Silver-Zhang/DevFlower/actions/runs/43260418244)。

## 2. 模块与依赖边界

| 类别 | 模块 | 使用关系 |
|---|---|---|
| 核心必需 | 画布、`DraftStore`、原生 `AgentRecords`、`devflower_export.py`、独立脚本 runtime | 保存画布与 Agent 版本，编译确定性 Python，脚本执行原生 CLI、交接并记录结果 |
| 核心必需 | `script_runs.py`、`ScriptRunMonitor.jsx` | 从 GUI 发布并启动编译产物，复用脚本的状态和取消命令，展示实际证据 |
| 兼容保留 | `V2Runtime`、worker、Scheduler、Studio 公共组件与存储、原生会话和历史归档 | 已有受管草稿仍使用原授权、会话绑定和交付 Gate；历史读取仍可用 |
| 非核心可隔离 | 助手、自动建队/修复、Git 平台、Git 交付与 Copilot 目录 | 助手和 Git 平台从「兼容工具」进入，后端按路由加载；显式登记的 Git 交付目标仍在启动时校验 |
| 已无依赖可删除 | 原生画布上仅允许导出的限制、提示及受管检查入口 | 原生画布检查调用固定编译器；仍有调用方的受管模块保留 |

原生 Agent 只保存 Provider、目录、角色、Prompt 和可选覆盖。受管的工具权限、Skills、原生
会话绑定及模型目录校验位于兼容分支。原生 CLI 按自己的账户和设置运行。现有 V2 启动器仍会
为兼容工作流探测受管 Codex 版本；脚本启动、监控和独立终端执行均无需该探测结果。

## 3. 本批实现与可靠性语义

编译器版本为 **3**。文件名为 `名称_脚本内容SHA256前8位.py`：同名但内容不同的工作流不再
因仅对名称取哈希而使用同一路径。同一输入与编译器产生相同字节；已有同名文件仍拒绝覆盖，
短哈希只用于命名，运行证据使用完整 SHA-256。旧导出产物不会被改写。

GUI 预览、导出和启动编译同一份已保存画布及精确的 Agent 版本。启动前在既有启动意图表内
预留运行 ID，保存冻结的脚本和任务文件。同一意图重连时读取原记录。脚本拥有独立进程会话，
工作台退出或刷新不会取消运行。运行记录与受管 worker 分开存储，避免把脚本证据解释成原生
会话或受管回执。脚本路径和配置版本可在运行检查器核对。

生成脚本同时提供执行、`--dry-run`、`--status` 和 `--cancel`。后两者只操作显式指定的现有
私有运行目录，不需要 CLI、项目目录、DevFlower 服务或 SQLite，也不会重新发送任务。
`workflow.json` 与 RUN_STARTED 的完整脚本哈希绑定产物；节点回答和最终结果均记录内容哈希。
状态查询核对完整终态及结果文件，不能仅凭 `result.txt` 存在判成功。元数据读取有长度限制，
不跟随证据文件符号链接、不读取 FIFO。哈希用于一致性检查，不是同用户防篡改或业务正确性证明。

| 状态 | 含义 |
|---|---|
| RUNNING | 观察到脚本持有该运行目录的锁，尚无有效终态 |
| CANCEL_REQUESTED | 已写入幂等取消请求；不等于已取消 |
| CANCELLED | 脚本处理取消并写出停止记录；不承诺回滚已产生的副作用 |
| STOPPED | 脚本已停止，具体原因保留在日志；不自动重试 |
| COMPLETED | 顺序调用和结果证据完整；不等于任务质量已获独立审核通过 |
| RECOVERY_REQUIRED | 缺少终态、日志/结果损坏或其他证据不足；不自动恢复/重发 |

活跃脚本持有运行目录锁，取消通过目录中的 `cancel.request` 协作完成，最终仍由原脚本清理
自己启动的 CLI 进程组。不根据历史 PID 向任意进程发信号。脚本被强杀后，即使 CLI 子进程或
远端请求可能仍在运行，也不伪称已取消或未执行。查询与重复取消不创建新的运行。
不同运行目录隔离日志，但不保证两个 Agent 同时修改同一项目无冲突；本轮验收使用串行执行。
实现按 Linux 本地文件系统验证，未扩展跨平台或分布式进程协调。

日志保留 Prompt、stdout、stderr、answer、result 和 events，含实际进程退出码。状态和取消
可加 `--details` 读取原始事件。启动前检查失败也有停止记录。Run Monitor 显示实际请求、
内含的上下游交接、最终回答和失败原因，完整证据留在本机。原生工作流可通过任务文字引用项目
路径，文件由 Agent 按其原生权限读取；运行启动接口会明确拒绝旧式内联文件附件。
结构化产物合同、命名 Profile 映射、原生会话续用及非顺序图仍明确拒绝导出，不静默丢弃配置。

## 4. 验证与已知限制

核心回归：

```sh
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_native_workflow_export.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_workflow_script_*.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_v2_native_agent_contract.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_v2_script_export.py' -v
```

脚本与 HTTP 回归覆盖确定性字节、并发重复启动、草稿修改后的冻结身份、服务关闭后继续运行、
重新连接、失败不发下游、协作取消及过期或损坏证据。Chromium 回归覆盖预览与下载一致性，
以及受管画布、历史、助手和 Git 平台的兼容入口。用模拟 CLI 验证异常行为。

2026-11-13 本机使用 Codex CLI 0.163.0 和 Claude Code 2.1.319，先用同一份独立脚本
在两个新项目完成真实顺序交接，再用 Chromium 启动已保存的双 Agent 画布。GUI 预览、导出、
实际执行和外部终端执行的脚本具有同一 SHA-256
`ca82d371e504f4a854b2e8f421377f7e3118a199d193ed8b44eaade01e0da407`。
两条路径均验证了实际文件、精确回答交接和独立算术结果。真实调用用于正常闭环，取消与故障
使用模拟 CLI。原始记录留在本机。

## 5. 本地交接与下一关口

先确认原生 `codex`、`claude` 在本机可用，且用户自己的配置允许完成相应任务。非交互执行
未必具有交互会话的同样权限行为；DevFlower 不添加跳过审批或禁用工具的参数。
参考：[Codex 非交互说明](https://learn.chatgpt.com/docs/non-interactive-mode)、
[Claude Code 配置](https://code.claude.com/docs/en/settings)。不要向仓库上传认证文件或私有日志。

在更新后的仓库中准备验收包：

```sh
python3 tools/devflower_script_acceptance.py --prepare /tmp/devflower-local-acceptance
```

目标目录必须不存在。该命令仅准备并打印 dry-run、两次独立执行和最终验证命令，**不会调用模型**。
核对后逐条执行打印的命令；两次执行使用同一脚本、不同的新测试目录。可在关闭 DevFlower 后
执行第二次。任何一步失败都先查看日志，不将重复命令当成安全恢复。成功验证仍显示
`gui_acceptance: NOT_RUN`，不自动授予 Stable 状态。

对实际运行进行独立查询或取消：

```sh
python3 /path/to/code_review_HASH.py --status --run-dir /path/to/run-a
python3 /path/to/code_review_HASH.py --cancel --run-dir /path/to/run-a
```

普通画布导出继续使用 GUI 按钮或 `tools/devflower_export.py --database ... --workspace-id ...
--draft-id ... --output-dir ... --native-config`，可携带任务文本或 `--task-file`。
每次执行创建新的私有运行目录；已有目录拒绝启动，不提供自动 resume。

已有受管工作流仍依赖兼容执行器。后续仅在对应入口和调用方完成迁移后清理其实现。
Minimal Stable 仍需用户验收和单独发布授权。
