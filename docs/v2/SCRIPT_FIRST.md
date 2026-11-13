# Script-First：唯一产品基准与最小 Stable

## 1. 目标与范围

DevFlower 是面向原生 Agent 的可视化工作流编译器。用户定义部门之间的协作，固定程序将画布
编译为可独立执行的 Python；每个 Agent 自主组织内部工具、Skills、MCP 和子 Agent。
**Script-First 决定新开发的主方向；旧 Phase 0–7 / V1 矩阵不再驱动功能堆叠，但
FEATURE_PARITY.md 仍是已有 V2 功能的兼容回归清单，不是删除这些功能的授权。**

核心约束：画布自由组合；Python 是正式产物；直接使用原生 CLI；调度确定而模型行为不必确定；
GUI 与终端必须执行同一产物；先交付最小闭环，不增加大型框架或重复执行器。
原生配置默认继承，模型与强度可选覆盖；不得擅自禁用内部能力，也不得擅自放宽原生权限。

首个 Minimal Stable 只要求：创建/保存/重开画布 → 导出 Python → 真实 Codex→Claude 顺序交接
→ GUI 与外部终端运行同一脚本 → 查看实际结果、错误并安全取消。Condition、Router、Merge、
有界 Loop、确定性 Tool 和原生多轮续聊均为后续扩展，**不是首个 Stable 的发布门槛**。
开发/审核只是验收示例，不是固定产品角色。没有真实 CLI 与 GUI 闭环证据时不得发布 Stable。

本轮启动依赖精简以 `960d0667f1174071be6a5c9300403e1f8008dd69` 为基线。
[基线 CI 两项通过](https://github.com/Silver-Zhang/DevFlower/actions/runs/43269851278)。

## 2. 模块与依赖边界

| 类别 | 模块 | 使用关系 |
|---|---|---|
| 核心必需 | 画布、`DraftStore`、原生 `AgentRecords`、`devflower_export.py`、独立脚本 runtime | 保存画布与 Agent 版本，编译确定性 Python，脚本执行原生 CLI、交接并记录结果 |
| 核心必需 | `script_runs.py`、`ScriptRunMonitor.jsx` | 从 GUI 发布并启动编译产物，复用脚本的状态和取消命令，展示实际证据 |
| 兼容保留 | `V2Runtime`、worker、Scheduler、Studio 公共组件与存储、原生会话和历史归档 | 已有受管草稿仍使用原授权、会话绑定和交付 Gate；历史读取仍可用 |
| 扩展功能保留、按需加载 | 助手、自动建队/修复、Git 平台、Git 交付与 Copilot 目录 | 助手和 Git 平台从「兼容工具」进入，后端按路由加载；显式登记的 Git 交付目标仍在启动时校验 |
| 已替代的限制 | 原生画布上仅允许导出的限制及提示 | 原生画布已使用编译器检查与脚本执行；受管检查与执行入口仍保留 |

原生 Agent 只保存 Provider、目录、角色、Prompt 和可选覆盖。受管的工具权限、Skills、原生
会话绑定及模型目录校验位于兼容分支。原生 CLI 按自己的账户和设置运行。

默认启动器不再导入或构造 `V2Runtime`，不做受管 Provider 探测。状态查询、原生 Agent
配置、画布保存/编译，以及原生脚本启动/监控/取消均不需要兼容执行器。原生 PTY 与会话标题
写入模块仅在显式启用会话操作时导入；共享存储、图校验和只读历史工具继续复用，不为改目录复制实现。

兼容 Runtime 由服务内一个加锁的惰性工厂创建。只有实际访问受管配置、检查/执行、模型发现、
助手/Git 兼容入口，或读取当前工作区已有的受管运行记录时才构造；并发首次访问共享同一实例。
初始化中的慢探测不会占用原生 API 的锁，失败不自动重试，也不阻断原生脚本路径。新旧路径
复用同一份 AgentRecords；兼容路径的原权限、会话及恢复检查不变。

用户显式登记原生历史/会话操作、命名 Profile、独立 HOME 或 Git 交付目标时，仍按原方式在
启动时校验，不把无效登记悄悄推迟。单独的 `--codex-cli` 仅为兼容工厂保留可执行文件选择，
不影响原生脚本从用户 PATH 调用 CLI。

`/status` 以 `managed_runtime_loaded` 区分是否已加载。加载前 `provider` 和
`execution_available` 均为 null；旧 GUI 的 `provider_available` 仅保留显式启动入口，不能
视作 CLI 已安装、能力验证或推理通过的证据。真正启动仍须通过原有服务端检查。

### V2 功能保留与执行边界（2026-11-13）

本批只改变兼容模块的初始化时机，不删除 V2 功能、不改变菜单、不改存储结构。会话中心
（历史、续聊、别名及终端接管）、Agent 管理、文件与模板、历史归档、助手/自动建队/修复、
GitHub/GitLab 与交付能力继续保留，沿用原来的配置条件和授权边界；未支持的能力不会因此
被宣称已支持。默认不加载与用户点击后可用是两个独立要求，两者都需验证。

原生脚本工作流仅承诺已实现的顺序子集；已有条件、循环、汇合、审批、恢复和结构化交接
继续由其原受管执行器处理，不能把新编译器尚不支持误写成整个 V2 已移除该功能。一个已
冻结运行只属于自己的执行路径：不得自动回退、双重启动、复用另一条路径的权限或伪造
会话关联。旧执行器仅维护兼容性，新编排能力在脚本链实现，不发展两套新的调度语义。

后续删除仅限证明无调用方的重复实现，且须先有等价替代和回归证据；产品功能下线须另行
取得用户授权。用户验收和 Stable 发布授权仍独立于 CI。

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
PYTHONPATH=tools python3 -B -m unittest discover -s tests -p 'test_v2_lazy_runtime.py' -v
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

本轮不修改编译器、独立脚本驱动或 GUI 产物，未重新进行真实模型调用。新增测试在全新解释器
禁止导入受管执行模块，再通过实际启动器和 HTTP 完成模拟 CLI 顺序闭环；另外验证并发只
初始化一次、慢/失败初始化隔离、已有会话授权拒绝和显式兼容登记的启动校验。此前真实验收
属于上轮证据，不冒充本轮实测。提交前另补充延迟加载下的原受管 HTTP 启动、冻结版本、
工作区隔离和历史投影回归，以及助手/Git 入口共用 Runtime、新旧运行并列且不改写记录的检查。

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
