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
有界 Loop 与其中的确定性验证是原生脚本扩展。原生多轮续聊仍属后续扩展。
开发/审核只是验收示例，不是固定产品角色。没有真实 CLI 与 GUI 闭环证据时不得发布 Stable。

本轮混合验收以 `61fb754365cb3580e3ef9ed18c0ff4135b058471` 为基线。
[基线 CI](https://github.com/Silver-Zhang/DevFlower/actions/runs/43351745852)。

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
助手/Git 兼容入口时才构造，并发首次访问共享同一实例。旧运行列表和详情由
`ManagedRunHistory` 读取当前工作区的冻结证据，受管启动、恢复与历史继续共享同一投影及
回执核验。Provider 初始化失败会返回既有错误响应，历史读取与原生脚本可以继续。新旧路径
复用同一份 AgentRecords；兼容路径的原权限、会话及恢复检查不变。

用户显式登记原生历史/会话操作、命名 Profile、独立 HOME 或 Git 交付目标时，仍按原方式在
启动时校验，不把无效登记悄悄推迟。单独的 `--codex-cli` 仅为兼容工厂保留可执行文件选择，
不影响原生脚本从用户 PATH 调用 CLI。

`/status` 以 `managed_runtime_loaded` 区分是否已加载。加载前 `provider` 和
`execution_available` 均为 null；旧 GUI 的 `provider_available` 仅保留显式启动入口，不能
视作 CLI 已安装、能力验证或推理通过的证据。真正启动仍须通过原有服务端检查。

### V2 功能保留与执行边界（2026-11-13）

当前的依赖精简涉及兼容模块的初始化时机与历史投影，沿用既有菜单和存储结构。会话中心
（历史、续聊、别名及终端接管）、Agent 管理、文件与模板、历史归档、助手/自动建队/修复、
GitHub/GitLab 与交付能力继续保留，沿用原来的配置条件和授权边界；未支持的能力不会因此
被宣称已支持。默认不加载与用户点击后可用是两个独立要求，两者都需验证。

原生脚本工作流支持顺序链、候选审核 Condition、有序规则 Router 和有界 Loop。旧工作流中的条件、路由、循环、汇合、审批、恢复和
结构化交接继续由其原受管执行器处理。一个已
冻结运行只属于自己的执行路径：不得自动回退、双重启动、复用另一条路径的权限或伪造
会话关联。旧执行器仅维护兼容性，新编排能力在脚本链实现，不发展两套新的调度语义。

后续删除仅限证明无调用方的重复实现，且须先有等价替代和回归证据；产品功能下线须另行
取得用户授权。用户验收和 Stable 发布授权仍独立于 CI。

### 混合集成核对（2026-11-14）

| 功能 | 实际入口和执行边界 | 本轮覆盖 |
|---|---|---|
| 工作区、文件、模板、设计器（W01–W12 及设计器条目） | `index.jsx` → `DraftStore`、`project_files.py`、`workflow_templates.py`、`canvas_structure.py`。由两条运行路径共用。旧文件附件冻结仍在 `data_handoff.py` | `v2_browser`、`v2_draft_transfer_browser`、`v2_designer_runtime_browser`，以及原生运行中的跨工作区访问拒绝 |
| Agent 与 Provider | `AgentManager.jsx` → `AgentRecords`。原生配置直达编译器，受管目录、工具权限和会话绑定由 `cli_providers.py`、`agent_execution.py` 处理 | Chromium 实建并绑定 Codex、Claude；`v2_native_agent_contract`、`v2_agent_records`、`v2_provider_catalog_browser` |
| 会话中心及终端（S01–S15） | `NativeSessions.jsx` / `NativePty.jsx` → `native_sessions.py`、`session_operations.py`、`codex_terminals.py`。保持原生身份、显式批准和共用写租约 | 会话浏览器夹具覆盖历史、分页、搜索、别名、回收和发送；`v2_codex_terminals`、`v2_native_controls`、`native_attach` 覆盖终端授权、会话定位和共享 PTY 代次 |
| 原生运行和监控 | `/check`、`/launch` 的原生入口 → `script_runs.py`、`devflower_export.py` → 独立脚本。Run Monitor 读取脚本事件和参数文件 | 同一 Chromium 工作区实走预览、下载、运行与交接。用模拟 CLI 验证丢失响应、刷新、切换、进程存活期间重启和取消。并发、失败、超时由脚本与 HTTP 回归覆盖 |
| 旧受管执行和历史 | 同一工作台的受管入口 → `V2Runtime` / worker / Scheduler。`ManagedRunHistory` → `recovery.project` → `RunInspector`，冻结回执规则与原路径共用 | 同一工作区交替执行并打开新旧历史，重启后历史读取不加载 Runtime。`v2_lazy_runtime`、`v2_execution_browser`、`v2_recovery_browser`、`v2_run_inspector_browser` 和 `v2_run_summary_browser` |
| 助手与 Git 平台 | 兼容工具 → `assistant.py`、`git_platform.py`。自动建队、修复及 Git 交付保留原受管入口，交付继续验证 Gate | `v2_assistant_browser`、`v2_git_platform_browser` 验证实际交互与合成平台数据；`v2_assistant_repairs`、`v2_workflow_portability` 覆盖修复和交付约束 |

本轮消除的是历史查询对启动探测和全工作区运行反序列化的依赖。脚本与受管运行的证据不同，
各自使用其原有协议。菜单入口共用，冻结数据及权限校验各自保留。历史绑定的旧
`runtime_sources` 仍用于显式恢复的源码校验，跨版本的旧运行继续可读。既有两条执行路径
尚无等价替代的部分包括受管审批、绑定会话、恢复与交付 Gate。

上述混合验收使用合成 Provider 与模拟 CLI。真实模型、远端 GitHub/GitLab 用户操作及
实际账号的 Codex TUI 接管沿用独立验收边界，上表的夹具结果不代表这些操作的实测结论。

## 3. 本批实现与可靠性语义

编译器版本为 **9**。文件名为 `名称_脚本内容SHA256前8位.py`：同名但内容不同的工作流不再
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
| COMPLETED | 实际执行路径的调用和结果证据完整，审核结论查看独立的候选证据 |
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
命名 Profile 映射、原生会话续用及超出下述控制节点子集的图明确拒绝导出。

### P1 原生输出合同

在已绑定原生 CLI Agent 的画布节点选择「结构化结果 v1」或「候选审核 v1」。标识保存为
`native_output: {"version":1,"kind":"result"}` 或 `kind: "review"`。字段规则沿用
`output_contract` 的路径、类型和必需标记，路径以 `result.` 开头。交付允许列表继续使用
`artifact_paths`。现有受管合同和未启用原生合同的文本节点继续使用各自原有语义，且受管
执行入口拒绝原生合同标识。

结构化结果要求最终回答只包含 JSON 对象 `{"result":{"schema_version":1,"summary":"…","data":{}},"artifacts":[]}`。
摘要非空，`data` 为对象，UTF-8 回答最多 80,000 字节。声明允许路径时至少报告一个实际文件。
仅接受精确匹配的工作区相对路径，且路径在生产节点的工作目录内。每次最多 8 个文件，单个
最多 1,000,000 字节，总计最多 8,000,000 字节。文件通过文件描述符打开和核验，拒绝符号
链接、硬链接、跨挂载路径、受保护路径和非普通文件。

审核节点直接接收前一结构化生产节点的交付。Python 按路径排序并生成
`{"schema_version":1,"files":[{"path":"…","bytes":0,"sha256":"…"}]}`。候选摘要是该清单
排序键、无多余空白的 UTF-8 JSON 字节的 SHA-256。审核输入包含清单和摘要。
审核固定返回 `schema_version`、`verdict`、`candidate_sha256`、`summary`、`issues`
和空的外层 `artifacts`。只有 `accept` 和 `needs_correction` 有效，问题等级为
`critical`、`major` 或 `minor`。每条问题必须引用候选中的文件，并有非空描述和建议。
`COMPLETED` 表示已获得有效交付和结论，具体结论见 `verdict`。P1 仍按已保存的顺序执行。

审核派发前和返回后分别重读文件。通过核验的结构化结果和前后清单作为独立证据落盘，事件
绑定其摘要。查询已完成的审核时会再次核验当前候选。文件缺失或内容已变时返回
`RECOVERY_REQUIRED`，已记录的审核结论可作为历史证据查看。此机制记录观测到的文件内容，
无法发现两次核对之间发生后又恢复的改动，也无法证明审核 Agent 阅读或理解了文件。
Reviewer 沿用原生 CLI 和权限，读取实际工作区。

### P2-A 原生 Condition

在 Start 和两条独立的 Output 路径之间可以配置一个 Condition。它必须直接接收同一运行中已
核验的「候选审核 v1」。复用画布表达式 `{"field":"result.verdict","op":"equals","value":"accept"}`
和 `true`、`false` 端口。`accept` 走 `true`，`needs_correction` 走 `false`。每条分支可以直接
连接自己的文本 Output，或先经过若干顺序 Agent。检查和导出会拒绝其他表达式、额外 Condition、
分支汇合、断开的节点或循环。每条分支沿用自己的交接限制，传递数据时第一位 Agent 接收 Reviewer 的交付。

脚本在条件判断前再次读取候选文件，只有摘要仍匹配才记录 `CONDITION_EVALUATED` 并执行对应
Python 分支。CLI 可用性和工作目录在分支 Agent 启动时检查，只有选中的分支触发这些检查。
Run Monitor 展示判定、候选摘要、选中和未选中的路径，以及实际开始的 CLI 和最终回答。

已选分支可以继续修改文件。回读时若发现用于分流的候选已变，仍保留本次真实选择和最终结果，
并标记原审核属于历史版本。没有 Condition 的 P1 审核沿用原有 `RECOVERY_REQUIRED` 投影规则。
已经导出的 v4 和 v5 脚本与运行记录继续由各自的独立脚本运行、查询和取消。

### P2-B 原生 Router

在画布绑定一个原生 Agent，启用「结构化结果 v1」，在输出合同声明参与判断的字段，例如
`result.data.category` 类型 `string`。Router 直接连接此 Agent。沿用现有规则编辑器添加
1–16 条规则，使用 `equals` 比较 `string`、`number` 或 `boolean` 值。规则名称可改，
上移和下移会保留端口 ID 和已有连线。每条规则与 `fallback` 都要连接，且各自沿顺序路径
到达独立的文本 Output。一个脚本导出只支持一个控制节点，拒绝分支汇合、循环和混合嵌套。

Python 先校验所有规则读取的字段。即使合同把字段设为可选，Router 仍要求这些字段在本次
结果中存在且类型匹配。数字与布尔类型分开。全部字段有效时按已保存顺序取首个精确匹配，
没有命中才进入 `fallback`。无效结构化输出、缺字段或类型错误会停止工作流。
`ROUTER_EVALUATED` 记录上游节点、结构化交付摘要、命中规则和选中端口。状态查询从冻结的
输入证据复算决策，Run Monitor 显示实际参与判断的字段、跳过的路径与最终结果。

### P3 原生 Loop

外层固定为 Start → Loop → Output。先保存 Start → 交付 Agent → 审核 Agent → Output，
再在 Loop 中选择已有草稿作为冻结子流程。两个 Agent 均绑定已保存的原生 CLI 配置版本。
交付节点开启「结构化结果 v1」并声明文件，审核节点开启「候选审核 v1」。退出条件为
`result.verdict equals "accept"`，最大迭代次数为 1–5，默认 3。编译时会核对嵌套的 Agent
版本，后来编辑源草稿不会改写已固定的子图。嵌套 Loop、分支和并行不支持。

每轮重新调用两个原生 CLI。返工时 Developer 继续接收原始任务，加上上一轮的审核摘要、
问题列表与候选摘要。每轮在同一工作区开发，运行目录 `iterations/<轮次>/` 保存独立的
请求、回答、审查记录和已声明候选文件的快照。候选快照只包含本轮已校验的交付文件。

只有有效的 `accept` 会返回最终结果。仍需返工且达到上限、调用失败、候选变化或取消时停止。
状态查询核对逐轮事件、候选快照和最终结果。前轮候选可在工作区被后续轮次替代，原快照与
审核仍可回读。最终候选内容变化时降为 `RECOVERY_REQUIRED` 并保留历史审核。
既有 v4–v8 导出继续由各自的独立脚本执行和查询。

### P3.1 Loop 内确定性验证

可在冻结子流程的交付与审核 Agent 之间插入一个 Tool，选择 `command`，填写 JSON 字符串数组
`argv`、工作区相对目录 `cwd`（默认 `.`）和 `timeout`（默认 300 秒）。显式启动后按保存
的参数数组直接执行，继承当前用户环境。未配置 Tool 的 Loop 仍可执行。

Tool 前后重新读取候选文件。只有两次摘要都等于本轮交付摘要，验证结果才有效。每轮保存
实际参数、目录、退出码、双方摘要和原始日志。退出码 0 会把验证结果交给 Reviewer。正常
非零退出跳过审核，并把测试失败原因、日志末尾摘要及候选摘要交给下一轮 Developer。超时、
启动失败、被信号终止、候选变化或取消会停止。运行监控可逐轮查看命令与验证日志。

### P4-A 通用 Command Tool

顺序链以及 Condition、Router 的已选分支可以运行一个或多个 Command Tool。沿用画布的
`argv`、`cwd` 和 `timeout`。命令按保存的参数数组直接执行，继承当前用户的权限和环境。
Run Monitor 显示实际 argv、工作目录、退出码、结构化结果和日志摘录，完整日志保存在运行目录。

普通 Tool 的退出码 0 交接 `success: true`，非零退出码交接 `success: false`，下游可继续分析。
启动失败、超时和取消停止该工作流。Tool 交接包含日志尾部摘要，命令本身只使用已保存的 argv。
若顺序 Tool 连接生产者和候选审核者，候选清单随交接传递，审核仍按 P1 核验实际文件。
Condition 与 Router 的判定输入仍来自各自要求的 Agent 合同，Loop 内的验证按 P3.1 执行。

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

混合验收使用原有编译器、独立脚本协议和 GUI 产物。`test_v2_lazy_runtime.py` 在阻断受管
模块导入的实际启动器中执行脚本并读取已冻结的受管记录，另验证慢探测及初始化错误期间的
历史读回、工作区隔离、同意图去重、旧运行权限与并列展示。

2026-11-18 的 P1 验收从仓库外运行独立导出的 Python。Codex CLI 0.163.0 的默认模型
`gpt-6.2-sol` 被账号以 `model_not_found` 拒绝。第二个隔离项目显式选择
`gpt-6.1-sol`，与 Claude Code 2.1.319 均以退出码 0 完成。Claude 的工具记录显示读取
了两个候选文件并执行算术检查。独立验证器复算清单和候选摘要并执行算术测试，审核结论为
`accept`。浏览器夹具另验证画布保存和重开、预览与执行脚本字节一致，以及文件变更后的
状态回读。

P2-A 在全新的 Git 工作区从 Chromium 启动真实 Codex → Claude → Success，验证
`accept → true`、三个 CLI 退出码 0，以及 Correction 没有启动。独立核对候选摘要，执行
4 个算术用例，并核实 Claude 的两次文件读取与 Python 检查。在仓库外使用与 GUI 预览、
导出和实际运行逐字节相同的脚本，以模拟 CLI 核对两条分支。旧 v4 文件另行独立复跑。

P2-B 的 Chromium 回归覆盖规则重排、端口和连线保持、预览与实际脚本一致，以及规则命中和
`fallback` 的运行监控。独立终端另用真实 Codex 完成分类和科研分支，两个 CLI 退出码均为 0。
首个命中顺序、非法输入和未发起其他分支的证据以模拟 CLI 覆盖。

P3 的真实 Codex → Claude 运行首轮获得 `accept`，两个 CLI 退出码均为 0。验收另行核算
归档候选摘要，验证两次文件读取、Python 检查和 4 个独立算术用例。第二、第三轮接受、
超限、取消及其他故障由模拟 CLI 验证。Chromium 验证冻结子图、证据回读和最终候选变更。

P3.1 在仓库外用真实 Codex → 本地验证命令 → Claude 完成首轮接受，三个进程退出码均为 0。
验收复算了双方候选摘要，并独立复跑 4 个验证用例。Chromium 与命令行夹具以模拟 Agent 和实际
本地验证命令覆盖了测试失败跳过审核、返工后接受，以及逐轮日志和退出码回读。

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

## 6. 下一轮流程控制的最小接口

继续扩展前，先用实际项目的测试命令验证退出码、超时、取消和逐轮候选证据。首版 Tool 的
执行范围保持在冻结 Loop 子流程内。
