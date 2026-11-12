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

本批基线为 `9b42992113fe9020aa542e497296dee42a87bf03`，
[基线 CI 两项通过](https://github.com/Silver-Zhang/DevFlower/actions/runs/43240359288)。
仅修改 `v2/parity-integration`，不改 main/develop/Stable，不删除用户数据、会话、旧执行器或旧测试。

## 2. 核心模块与依赖边界

| 职责 | 实际实现 | 边界 |
|---|---|---|
| 画布与结构校验 | React Flow、Canvas 组件、`documents.py`、`canvas_structure.py` | 保存不调用模型 |
| 配置保存 | `DraftStore`、`AgentRecords` | 保留 ID、版本和工作区校验 |
| 脚本编译 | `tools/devflower_export.py` | GUI 下载与终端使用同一编译器 |
| 顺序执行、交接、日志、状态与取消 | 内嵌 `tools/workflow_script_runtime.py` | 只依赖标准库和用户的原生 CLI |
| 本地验收准备与独立检查 | `tools/devflower_script_acceptance.py` | 不代替产品执行器，不自动调用模型 |
| 旧 GUI 运行与历史 | `V2Runtime`、worker/Scheduler、Run Inspector | 兼容保留，尚未切换为脚本执行 |

核心链：画布及 Agent 版本 → 固定编译器 → 独立 `.py` → 原生 CLI → 最终回答与私有日志。
未来 GUI 只需启动该产物并读取其状态；不得再翻译一遍图或另写调度器。

V2 仍复用 Studio 的存储、UI 组件、theme 和依赖锁；旧运行器仍被 GUI、恢复和历史查询使用。
AI 助手、Git 平台、自动建队/修复/交付不再扩展；需解除实际入口与钩子后才退出核心版本。
新 `execution: native-cli` 配置继续拒绝进入旧受管执行器，旧授权检查不因本批而放宽。
本批没有改 GUI JSX/CSS 或 bundle，也没有增加后台服务或运行数据库。

## 3. 本批实现与可靠性语义

编译器版本为 **3**。文件名为 `名称_脚本内容SHA256前8位.py`：同名但内容不同的工作流不再
因仅对名称取哈希而使用同一路径。同一输入与编译器产生相同字节；已有同名文件仍拒绝覆盖，
短哈希只用于命名，运行证据使用完整 SHA-256。旧导出产物不会被改写。

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

日志保留 Prompt、stdout、stderr、answer、result 和 events；不把完整原生聊天作为默认交接。
结构化产物合同、命名 Profile 映射、原生会话续用及非顺序图仍明确拒绝导出，不静默丢弃配置。

## 4. 验证与已知限制

核心回归：

```sh
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_native_workflow_export.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_workflow_script_*.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_v2_native_agent_contract.py' -v
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_v2_script_export.py' -v
```

新增回归覆盖同名产物区分、无 CLI 状态读取、取消幂等、子进程停止、死 owner、损坏日志/结果、
符号链接/FIFO、取消与已有证据损坏同时发生、两份工作区独立执行，以及审核声称通过但实际
算术测试失败的反例。新套件加入原有 V2 CI，不删除旧回归。

本地验收工具只准备两个新 Git 测试目录和一份导出脚本，不改已有项目或账户配置；两个真实
执行命令由用户显式运行。验证器检查实际生成的加法模块、上游最终回答的精确交接、交付令牌
和审核文件；独立算术测试不接受 Agent 自报 PASS 代替。验证器会执行产出的 Python 模块，
这是用户显式发起的本地测试，不是额外沙箱；不自动重跑失败的 CLI。

本轮云端环境未安装 `codex` / `claude`。模拟 CLI 与真实本地子进程测试不是模型验收，不能
据此启用 GUI 的原生运行入口。真实双 CLI、GUI 运行接线和用户本人验收仍未完成。
上一批的真实 Codex 初始化失败属于历史证据，不冒充本轮调用结果。

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

下一步只做：真实 Codex→Claude 成果交接验收 → GUI 启动同一产物并接入本产物的状态/取消
→ 确认原执行链依赖已经迁移 → 分批删除重复执行器和非核心入口。之后再逐项扩展条件与循环。
