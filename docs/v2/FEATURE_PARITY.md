# DevFlower V1 → V2 需求追踪矩阵（首轮静态核对）

> 状态：初版功能清单，不是完成验收。用户需求优先；V1 源码作为可复用实现来源，V2 架构与安全不变量不退化。

- 核验日期：2026-11-10；V1 源码基线：`06432659432b82730c421cd137345a95244232d2`；V2 初始静态核对：`9bee63323ee4da33495b0b391590b0d150445f7f`。
- 依据：V1 README、docs/WORKBENCH.md、Issues #6/#9/#14/#16/#30/#64/#65/#132/#133、V1/V2 实际代码路径。
- **C** 表示有对应代码实现入口，不表示达到 V1 全功能语义，更不表示真实账号与用户验收通过；**P** 表示部分能力；**M** 表示目标 V2 未提供。
- 原始 V1 路径是追溯入口，不代表该 V1 功能本身已在真实环境充分验证；#编号为历史 Issue 需求来源。
- 当前独立、真实多 Provider、自举的整体功能验收状态：**未完成**；不能据 CI 绿灯推定本矩阵要求已通过。

## 六模块实现基线

| 模块 | 条目 | 代码可见 C | 部分 P | 尚缺 M | 代码覆盖下界¹ |
|---|---:|---:|---:|---:|---:|
| 工作空间 | 12 | 7 | 2 | 3 | 58.3% |
| 会话中心 | 15 | 12 | 3 | 0 | 80.0% |
| Agent 与 Provider | 15 | 6 | 7 | 2 | 40.0% |
| 工作流设计器 | 15 | 7 | 1 | 7 | 46.7% |
| Workflow Runtime | 15 | 7 | 0 | 8 | 46.7% |
| 运行监控与研发自动化 | 15 | 4 | 3 | 8 | 26.7% |
| **合计** | **87** | **43** | **16** | **28** | **49.4%** |

¹ 代码覆盖下界 = C / 总条目，P 不折算完成比例；这只是当前静态源码覆盖，不是最终产品完成率。对每条 C 仍需单独验证，未核验的功能验收计数为零。

## 原始需求逐项追踪

### 工作空间

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| W01 | 打开本地工作空间 | `tools/studio/workbench_ui/FolderPicker.jsx` | `tools/v2/store.py` | C | P0 | 用文件夹选择器打开可丢弃项目并刷新 |
| W02 | 最近工作空间与再次打开 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/ui/index.jsx` | C | P1 | 重启后选择先前工作空间且身份不混淆 |
| W03 | 关闭工作空间与未保存提示 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/ui/index.jsx` | C | P0 | 保存、放弃、取消各操作均不丢错误草稿 |
| W04 | 不同工作空间状态隔离 | `tools/studio/workbench_runtime.py` | `tools/v2/store.py` | C | P0 | A/B 工作空间切换，草稿、事件、会话无串用 |
| W05 | 项目文件树与目录浏览 | `tools/studio/workbench_ui/ProjectFiles.jsx` | `tools/v2/project_files.py` | C | P1 | 按目录打开、展开与返回，边界安全 |
| W06 | 文本文件只读预览 | `tools/studio/workbench_ui/ProjectFiles.jsx` | `tools/v2/ui/ProjectFiles.jsx` | C | P1 | 正常文本、超限、二进制、拒绝路径均明确 |
| W07 | 可配置的任务项目上下文与文件引用 | `tools/studio/project_files.py` | — | M | P0 | 任务引用所选文件并被真实 Agent 读取 |
| W08 | 远程工作空间与运行环境区分 | `#9` | — | M | P2 | 远程环境信息和凭据隔离正确 |
| W09 | 嵌套目录原生配置发现 | `tools/studio/agent_sessions.py` | `tools/v2/native_sessions.py` | P | P1 | 嵌套 .claude/.codex 完整覆盖与覆盖统计 |
| W10 | 工作流模板库与收藏 | `tools/studio/workbench_ui/WorkflowLibrary.jsx` | — | M | P1 | 模板新建、收藏、加载、修改不污染原版 |
| W11 | 项目内设计迁移与历史归档 | `tools/studio/project_files.py` | `tools/v2/draft_transfer.py` | P | P1 | 迁移不重写原始历史，导入导出可读回 |
| W12 | 路径权限与保护范围 | `tools/studio/workspace_access.py` | `tools/v2/workspace_select.py` | C | P0 | symlink、protected、跨 workspace 不泄露 |

### 会话中心

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| S01 | 原生 Codex 会话只读发现 | `tools/studio/session_center.py` | `tools/v2/native_sessions.py` | C | P0 | 已批准范围内发现真实 UUID/来源 |
| S02 | 原生 Claude 会话只读发现 | `tools/studio/session_center.py` | `tools/v2/native_sessions.py` | C | P0 | 不同 provider/HOME 不误合并 |
| S03 | 嵌套路径与历史覆盖度报告 | `tools/studio/agent_sessions.py` | `tools/v2/native_sessions.py` | C | P1 | 超预算 partial，而非宣称 complete |
| S04 | 按 Provider 分组与过滤 | `tools/studio/workbench_ui/SessionList.jsx` | `tools/v2/ui/NativeSessions.jsx` | C | P1 | 分组过滤不丢会话且状态明确 |
| S05 | 按 Workflow 分组与关联节点 | `tools/studio/workbench_ui/session_groups.mjs`、`tools/studio/workbench_sessions.py` | `tools/v2/native_session_links.py`、`tools/v2/ui/NativeSessions.jsx` | C | P1 | 通过运行定义、节点会话、激活、进程 cwd、原生 UUID 与记录的交叉核对后链接 Agent；其余归入未关联组 |
| S06 | 会话搜索与排序 | `tools/studio/workbench_ui/SessionList.jsx` | `tools/v2/ui/NativeSessions.jsx` | C | P1 | 列表排序搜索与分页一致 |
| S07 | 长会话分页与窗口搜索 | `tools/studio/agent_sessions.py` | `tools/v2/native_session_pages.py` | C | P1 | 定位连续可见历史而不扫描越权 |
| S08 | 用户回答、Markdown 与代码块阅读 | `tools/studio/workbench_ui/ConversationView.jsx` | `tools/v2/ui/NativeMessages.jsx` | C | P1 | Markdown、代码、工具消息清晰区分 |
| S09 | 公开工具活动与摘要 | `tools/studio/tool_activity.py` | `tools/v2/native_session_activity.py` | C | P1 | 工具活动隐藏私有输入并保留状态 |
| S10 | 新建独立原生会话 | `tools/studio/session_center.py`、`local_codex_adapter.py`、`local_claude_adapter.py` | `tools/v2/session_operations.py` | C | P0 | Codex 与 Claude 使用单独批准的原生配置创建。Claude 2.1.319 采用其用户级认证、服务地址及原生模型默认。发送意图先落盘，按已核验的实际 UUID 重开 |
| S11 | 重命名与别名 | `tools/studio/workbench_sessions.py` | `tools/v2/native_session_views.py` | P | P1 | 本地别名按工作空间与会话持久化；Provider 原生命名写入口待实现 |
| S12 | 隐藏、归档、回收站与恢复 | `tools/studio/session_batch.py` | `tools/v2/native_session_views.py` | C | P1 | 单条可逆整理；Codex 0.163.0 工作流整组入回收站按预览成员与历史版本原子提交，忙碌或来源变化整组拒绝 |
| S13 | 安全恢复已有原生会话 | `tools/studio/session_center.py` | `tools/v2/session_operations.py` | C | P0 | Codex 使用原生锁。Claude 只续聊本产品已核验创建且最新历史、配置、工作区和 UUID 一致的会话。单写协调范围为参与 DevFlower 租约的写者 |
| S14 | 一键终端与 PTY 连接/接管 | `tools/studio/native_terminals.py`、`native_attach.py`、`NativeTerminal.jsx` | `tools/v2/codex_terminals.py`、`tools/v2/ui/NativePty.jsx` | P | P1 | 受支持的 Linux/Bubblewrap 与 Codex 0.163.0 可启用同一受管 PTY，浏览器与本机端显式接管。Claude PTY 关闭，外部 Claude 历史保持只读 |
| S15 | 原生会话单写租约与跨端一致性 | `tools/studio/native_local.py` | `tools/v2/session_lease.py`、`tools/v2/session_operations.py`、`tools/v2/codex_terminals.py` | P | P0 | Codex 受管 API 与 PTY 共用 UUID 租约和原生线程锁。Claude 受管入口共用 UUID 租约，外部 CLI 不参与。未知效果不重发 |

#### 会话中心候选证据

产品候选 `9ef55fe22e5400b96bf212868cee753780cbdb99`，tree `47990fe0be6dfe8b949f25ca35c21149fe0b40a0`。A 核对工作树与构建，B 自测和 C 独立验收分别绑定该候选。

| 层级 | 已核对的范围 |
|---|---|
| CODE_PRESENT | S05 与 S10 有真实入口，S11 有本地别名，S12 有单条与工作流整组事务，S13 与 S15 有 Codex 路径，S14 显示具体拒绝原因。 |
| INTEGRATED | 现有只读发现、翻页、搜索、活动、Markdown 和单条整理保留。独立发送走 V2 令牌与状态库；工作流仍只创建原生新会话。发送与工作流在收到原生 UUID 后进入同一受管锁域。 |
| TESTED | B 自测通过 195 项单元测试及 7 套 Chromium 夹具。C 独立复跑 7 套针对性单元测试和 7 套合成 Chromium 场景，验证真实产品 HTTP 路径的 Codex 新建、同 UUID 续聊及 Claude Code 经 DeepSeek Flash 新建，并在 UI 读回两类真实历史。C 以实际 Codex CLI 和合成 Provider 验证单成员组的回收、恢复与拒绝条件。 |
| ACCEPTED | C 整体结论为 BLOCKED，暂定 UI 评分 81/100。Claude 共享 HOME 续聊与两种 Provider 的终端接管关闭，真实 Provider 的 UI 发起和多成员组原子性仍未独立验证。用户验收与完整 GitHub CI 均待进行。 |

| 追踪 | 范围 | 证据 |
|---|---|---|
| S11a | DevFlower 本地别名 | CODE_PRESENT、INTEGRATED、TESTED。单条事务按会话和工作空间保存，浏览器验证重开后保留。 |
| S11b | Provider 原生命名写入 | 待实现。 |
| S12a | 单条隐藏、归档、回收及恢复 | CODE_PRESENT、INTEGRATED、TESTED。已有 V2 浏览器夹具覆盖可逆分类与视图版本冲突。 |
| S12b | 工作流整组回收 | CODE_PRESENT、INTEGRATED、TESTED。实际 Codex CLI 搭配本机响应夹具验证已核实分组、锁占用拒绝和整组提交；回收后可按单条恢复。 |

启动时同时传入私有权限 `0600` 的 `--native-history-config` 和 `--native-session-writes`。历史配置 `{"version":1,"discover_defaults":true}` 批准浏览默认根。写入配置 `{"version":1,"codex":true,"claude":true}` 分别批准两种独立写入。Claude 写入目前核验了原生 CLI 2.1.319、默认 HOME 及其用户级设置。选择 `"claude":true` 时，CLI 按自己的优先级使用原生登录、服务地址、模型和强度。可选的 `"claude":{"model":"sonnet","effort":"low"}` 传递原生参数。显式启动环境仅传递已列出的 Claude/Anthropic 调用变量，状态库保存环境摘要。旧配置的 `anthropic_base_url` 仅在它与已设置的 `ANTHROPIC_BASE_URL` 一致时兼容。使用 CLI 用户级设置管理地址的旧配置应移除此字段。Claude 纯对话保持禁用 Hooks、MCP、Chrome 与工具，并排除项目和本地设置。 [Claude Code 环境变量](https://code.claude.com/docs/en/env-vars)

#### CLI-native 安全收尾

独立复核绑定产品候选 `760b1fd3c4979bb90c26409821394f536c8421fd`，tree `614c159354fb89892d9d6b7213a8977408813b2b`。

| 层级 | 本轮范围 |
|---|---|
| CODE_PRESENT / INTEGRATED | Claude 新建接入原生用户配置和受限启动环境。CLI 版本核验和授权启动均经封存的可执行映像，实际进程及 cwd 在发送输入前复核。V2 新建 UUID 在启动前持有现有生产租约，受管冲突拒绝在 CLI 启动之前。 |
| TESTED | 实际 Claude 2.1.319 搭配本机合成服务，核对原生设置及凭据脱敏。同 UUID 受管竞争仅首个进程访问服务。原生进程异常退出后，重新打开状态库仍需恢复核验，同意图和同目标均无第二次调用。直接 CLI 对照的同 UUID 重叠续聊均触达本机服务。 |
| ACCEPTED | C 独立复核确认启动映像替换缺陷已修复，原子替换和原文件就地改写均无法让替换程序获取认证环境。模块整体为 BLOCKED。当前主机没有可核验的独立 Claude 写入权限域。真实 Codex TUI 的隔离诊断通过，生产鉴权、响应过滤、浏览器与本机接管及完整生命周期尚未交付。两种生产 PTY 与 Claude 共享 HOME 续聊保持关闭。完整 GitHub CI 未触发。参见 [CLI-native 决策](CLI_NATIVE.md)。 |

在工作空间中打开原生会话，点击「新建会话」。Codex 先刷新并选择模型、强度；发送完成后打开已核验的原生记录。点击「继续会话」发送到同一 Codex UUID。只有来源与原目录能核实的记录才允许续聊。响应不完整时使用「核验本次发送」查询同一意图。工作流模式的「整组移入回收站」先显示全组预览，确认时再次校验成员、原生协调锁、转录版本与 DevFlower 分类。Codex 协调机制使用已核对的 [0.163.0 写锁协议](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rollout/src/writer_lock.rs)。

API 使用现有 V2 本机令牌。发送为 `POST /api/v2/session-send`，提交 `workspace`、`provider`、`handle`、`revision`、`intent_key`、`prompt`，Codex 另带 `model`、`effort`。新建的 `handle` 和 `revision` 为 `null`。`GET /api/v2/session-operation?workspace=…&intent_key=…` 只查询。批量回收用 `POST /api/v2/workflow-session-trash`，提交 `workspace`、`revision`、`group` 获取预览，确认时增加预览返回的 `expected`。

#### 受管 Claude 续聊

授权边界调整为参与 V2 租约的写者。受管资格来自 V2 已核验的新建记录及最近一次完成回执。配置、工作区、原生 UUID、文件位置及转录摘要都须匹配，执行前在同一写租约内再次核验。外部创建记录、变更记录和结果待核验记录保持只读。独立的直接 Claude CLI 可以绕过 DevFlower 租约。真实 Claude CLI 与本机合成服务的自验覆盖同 UUID 续聊、并发拒绝、同工作区外部记录拒绝和异常退出后的恢复状态。独立验收待进行。

#### 最终安全候选

功能候选为 `b2e043cb9a0f9895ec5caa4bc57ea85e4639ab9f`，tree `e95b86ecf6c368f07b9412ee1195a3e778c26cc2`。验收记录单独提交。

| 层级 | 证据与范围 |
|---|---|
| CODE_PRESENT | S13 增加 Claude 受管来源与历史核验。S14 接入 Codex 真实 TUI，复用原生输出、终端附接和键盘代次。 |
| INTEGRATED | S15 将终端与会话发送接入同一写租约。V2 产品令牌、已核验会话、独立终端批准和 OS 沙箱均为启动条件。关闭视图保留进程，显式停止处理对应进程。单次发送的完成回执在释放租约后公布。 |
| TESTED | B 的合成单元测试及实际 CLI 搭配本机合成 Provider 通过。A 在一次性工作空间以真实 Codex 服务和 Claude Code 的本机 DeepSeek Flash 设置分别完成新建、同 UUID 续聊和原意图查询，核对回复与原生历史。实际 Codex TUI 和原生工具通过批准目录读写、私有存储、外部路径、`..` 与符号链接反例。A 在本机终端运行产品生成的接管命令，并验证中止正在请求的真实 Codex 后端后保留恢复状态及同意图拒绝重发。 |
| 独立 QA | C 在固定候选复验 Claude 受管写者互斥、外部历史只读、历史变化与异常恢复。Codex 复验键盘代次、实际本机输入、同进程重连、指定停止、停止后刷新重开及隔离启动故障。终端 UI 复测 80/100，单项 16、17、15、16、16。 |
| ACCEPTED | 发布门槛以同一提交的 [V2 完整工作流](https://github.com/Silver-Zhang/DevFlower/actions/workflows/v2.yml?query=branch%3Av2%2Fparity-integration) 与固定候选独立复验记录为准。用户验收单独记录。 |

启用 Codex 终端需在私有写配置中设定 `"codex":true,"codex_terminal":true`。终端目前依赖 Codex 0.163.0 与 Linux Bubblewrap。复制「本机连接」命令可查看同一 PTY，加 `--takeover` 才申请键盘，按 `Ctrl-]` 断开本机视图。用户直接输入 `!` 命令时会收到范围拒绝，当前原生 TUI 随后结束，可重新打开原会话。C 还证实，直接从外部 CLI 续聊同一受管 Claude UUID 能绕过 V2 租约，DevFlower 未检测到这次重叠。见 [CLI-native 权限和生命周期](CLI_NATIVE.md)。

### Agent 与 Provider

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| A01 | Codex 真目录发现及认证 | `tools/studio/model_catalog.py` | `tools/v2/provider.py` | C | P0 | 模型/effort 来源真实，目录不等于推理成功 |
| A02 | Claude Code 执行能力与模型目录 | `tools/studio/local_claude_adapter.py` | `tools/v2/cli_providers.py`、`tools/v2/claude_native_catalog.py` | C | P0 | 原生初始化给出模型与推理强度，受管进程使用本机配置 |
| A03 | Copilot 模型目录与身份 | `tools/studio/copilot_adapter.py` | `tools/v2/copilot_catalog.py` | P | P1 | 目录与认证来源可证，不等于执行 |
| A04 | Copilot 可执行 Agent 适配 | `tools/studio/copilot_adapter.py` | — | M | P2 | 仅真实支持的模式可执行 |
| A05 | 同 Provider 多 Profile 选择 | `tools/studio/workbench_sessions.py` | `tools/v2/cli_providers.py` | P | P1 | 每种 CLI 使用本机默认 Profile，多 HOME 选择待接入 |
| A06 | Agent 身份、职责、背景和目标 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/agent_records.py`、`tools/v2/ui/AgentManager.jsx` | C | P0 | 角色模板与 Provider 分离，版本按工作区保存和重开 |
| A07 | Ask / Plan / Agent 模式 | `tools/studio/workbench_ui/AgentPicker.jsx` | `tools/v2/cli_providers.py`、`tools/v2/codex_agent_plan.py` | C | P0 | 两种 CLI 的三种模式有原生映射，Codex 完整 Plan 转 Agent 需单独确认 |
| A08 | 新会话与已有会话切换 | `tools/studio/workbench_ui/SharedConversationOption.jsx` | `tools/v2/agent_execution.py` | C | P0 | 核验原生来源、工作目录、UUID 及写租约，外部 Claude 保持只读 |
| A09 | 模型与推理强度配置 | `tools/studio/workbench_ui/ModelSelector.jsx` | `tools/v2/cli_providers.py`、`tools/v2/ui/AgentManager.jsx` | C | P1 | CLI 目录、用户默认、手工模型及逐模型推理强度独立显示 |
| A10 | 初始化、每轮、输出交接 Prompt | `tools/studio/workbench_graph.py` | `tools/v2/definitions.py` | C | P0 | 真实回合各部分独立可追溯 |
| A11 | 结构化输出合同和字段选择 | `tools/studio/data_contracts.py` | `tools/v2/runtime.py` | P | P1 | Codex 沿用结构化回执，Claude 交接真实可见文本，自定义 schema 和字段选择待接入 |
| A12 | 节点工具与文件写入权限 | `tools/studio/agent_extensions.py` | `tools/v2/codex_agent_scope.py`、`tools/v2/claude_agent_scope.py` | C | P0 | 原生工作区读写与受控命令，验证越界、认证环境及不可用时的拒绝 |
| A13 | Skills 发现与选择 | `tools/studio/agent_extensions.py` | `tools/v2/agent_records.py`、`tools/v2/claude_agent_scope.py` | C | P1 | Claude、Codex 原生选择及内容复核，Claude 区分工作区与用户级来源 |
| A14 | 子 Agent 与托管子任务 | `tools/studio/managed_children.py` | — | M | P2 | 深度、数量、授权和回执正确 |
| A15 | 超时、预算与能力错误 | `tools/studio/workbench_runtime.py` | `tools/v2/cli_providers.py`、`tools/v2/runtime.py` | P | P0 | 两者支持超时与调用次数约束，Claude 支持原生美元预算 |

#### Phase 3 候选证据

功能候选 `d7f3c1d8b4f7f861d8c73ac3cb3655a45cd247b5`，tree `adcb4821d530bb671644cb157c8a2c521a83191a`。

| 层级 | 证据与范围 |
|---|---|
| CODE_PRESENT / INTEGRATED | 工作区 Agent 配置、原生 Provider 目录、独立启动和工作流引用共用 V2 运行器。启动冻结配置版本，已有会话复用原生 UUID 和写租约。独立发送、工作流与 PTY 互查未确认写入。 |
| TESTED | 合成测试覆盖配置版本、Adapter 契约、进程归属原子发布和跨入口未知状态。实际 CLI 搭配本机合成服务通过 Codex 工作区读写及越界反例、精确原生 Skill、实际请求交接和部分扫描下的会话关联。 |
| 真实模型服务 | A 使用本机配置的 Codex 和 Claude Code / DeepSeek Flash 完成两节点工作流。核对冻结版本、实际输入哈希、上游标记、原生 UUID 和两条 Session Center 关联。两种 Provider 均从 Agent 入口继续同一已绑定 UUID，重复启动意图返回原运行。 |
| 独立 QA | C 在固定候选复验 Chromium 配置与直接启动、原生 CLI 工作流、会话续用、权限、Skill 选择和丢失响应后的重放。CLI 及 OS 检查使用一次性工作区和本机合成服务。 |

Phase 3 收尾候选的证据按类型记录。

| 层级 | 收尾范围 |
|---|---|
| CODE_PRESENT / INTEGRATED | Claude 工作区 Agent 与所选原生 Skills 接入既有的配置冻结、原生历史和写租约。Codex Plan 使用原生协作模式，完整计划需要显式确认才能进入 Agent。 |
| TESTED（合成与 OS） | 用例检查工作区、符号链接、进程环境、Bash 网络、启动拒绝和原生历史发布。Codex CLI 对照用例验证 Plan 与确认后的 Agent 回执、权限及幂等。 |
| 真实 CLI 与模型服务 | 独立记录实际 CLI 版本、Provider、工作区标记、Skills 调用、原生 UUID 和请求回执。目录发现与合成回执单独记账。 |
| ACCEPTED | 仅在同一集成候选取得独立 QA 和完整 CI 通过后成立。 |

原生子 Agent、逐命令人工审批和多 Profile 保留为后续需求。完整 CI 以同一集成提交的 [V2 工作流](https://github.com/Silver-Zhang/DevFlower/actions/workflows/v2.yml?query=branch%3Av2%2Fparity-integration) 为准。模式与原生参数见 [Agent Manager 执行映射](CLI_NATIVE.md#agent-manager-执行映射)。

### 工作流设计器

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| D01 | 空白草稿与未配置保存 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/documents.py` | C | P0 | 空白/不完整草稿可保存重开 |
| D02 | 节点添加与拖动 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/ui/index.jsx` | C | P0 | 拖动位置持久化，不误启动 |
| D03 | 端口、方向、箭头和交接定义 | `tools/studio/workbench_ui/HandoffEdge.jsx` | `tools/v2/ui/index.jsx`、`tools/v2/canvas_structure.py` | C | P0 | 合法连线可选择、配置和删除，非法端口、重复连线和循环有诊断 |
| D04 | 多选、框选和批量操作 | `tools/studio/workbench_ui/canvas_editing.mjs` | `tools/v2/ui/index.jsx` | C | P1 | 多选、框选、整组拖动和批量删除准确 |
| D05 | 复制、粘贴、批量克隆 | `tools/studio/workbench_ui/canvas_editing.mjs` | `tools/v2/ui/canvasEditing.mjs` | C | P1 | 新建节点及内部连线 ID，副本显式绑定 Agent，撤销恢复 |
| D06 | 撤销与重做 | `tools/studio/workbench_ui/canvas_editing.mjs` | `tools/v2/ui/index.jsx` | C | P0 | 结构与坐标恢复，不影响运行 |
| D07 | 节点对齐、布局及自由画布菜单 | `tools/studio/workbench_ui/CanvasMenus.jsx` | `tools/v2/ui/index.jsx` | C | P1 | 对齐、分布和右键添加使用同一草稿历史 |
| D08 | 缩放、平移、视图适配 | `tools/studio/workbench_ui/EditorToolbar.jsx` | `tools/v2/ui/index.jsx` | C | P1 | 缩放、平移和视图适配可操作，保存后恢复视口 |
| D09 | 节点和连线检查器 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/ui/index.jsx`、`tools/v2/ui/CanvasNodes.jsx` | C | P0 | 可关闭和重开，切换保留配置，Agent 复用原有管理器 |
| D10 | 统一节点库与 Agent 角色模板 | `tools/studio/workbench_ui/WorkflowLibrary.jsx` | `tools/v2/ui/CanvasNodes.jsx`、`tools/v2/ui/AgentManager.jsx` | C | P1 | 十类节点共用视觉与配置入口，Agent 模板继续由 Agent Manager 提供 |
| D11 | Condition、Router、Merge、Loop 可编辑 | `tools/studio/workbench_ui/RouterEditor.jsx` | `tools/v2/canvas_types.json`、`tools/v2/ui/CanvasNodes.jsx` | C | P0 | 结构化判定、稳定分支、汇合策略与有界循环可配置并保存 |
| D12 | Start、Human、Tool、Validation、Output 可编辑 | `tools/studio/workbench_ui/index.jsx` | `tools/v2/canvas_types.json`、`tools/v2/ui/CanvasNodes.jsx` | C | P1 | 初始数据、审批、工具权限、验证规则与输出配置可保存并检查 |
| D13 | 工作流模板与示例复用 | `tools/studio/workflow_library.py` | — | M | P1 | 使用模板不修改源模板 |
| D14 | 草稿 CAS、节点配置版本和 Agent 引用隔离 | `tools/studio/workbench_store.py` | `tools/v2/store.py`、`tools/v2/documents.py` | C | P0 | 节点坐标和配置独立保存，Agent 引用固定版本，失效时明确要求重绑 |
| D15 | 草稿传输与导入校验 | `tools/studio/project_files.py` | `tools/v2/draft_transfer.py`、`tools/v2/documents.py` | C | P1 | 旧格式保留，扩展字段往返一致，结构错误拒绝，未完成配置可保存 |

#### Phase 4 验收边界

草稿继续使用 V2 文档和归档版本 1。旧节点隐式为 Agent。新版节点携带类型及版本化配置，边携带端口身份和可选交接定义。开始节点无入边，输出节点无出边。汇合节点允许多个上游，其余输入端口最多接收一条边。分支按稳定端口身份连接。草稿检查可报告缺少的字段与连线，草稿保存检查拒绝损坏的结构。

顺序 Agent 链沿用现有冻结及授权流程。新增节点和非默认交接只具备设计、校验与持久化能力，运行请求在登记启动前被拒绝。Condition、Router、Merge、Loop、Human Approval 等执行能力在 Runtime 表中单独追踪。

`WORKFLOW_DESIGNER_ACCEPTED` 以同一候选的独立 Chromium 操作验收、UI 评分和完整 [V2 CI](https://github.com/Silver-Zhang/DevFlower/actions/workflows/v2.yml?query=branch%3Av2%2Fparity-integration) 为准。

### Workflow Runtime

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| R01 | 真实顺序 Agent 执行 | `tools/studio/workbench_scheduler.py` | `tools/v2/runtime.py` | C | P0 | 两节点实际执行，非模拟回执 |
| R02 | 只读并发与汇合 | `tools/studio/workbench_scheduler.py` | — | M | P0 | 并发只读，汇合只触发一次 |
| R03 | 条件判断分支 | `tools/studio/workbench_graph.py` | — | M | P0 | true/false/null/缺字段明确处理 |
| R04 | 多路 Router 与 fallback | `tools/studio/routing.py` | — | M | P1 | 不同标签正确选路，超时明确 |
| R05 | 有界 Loop 与修订闭环 | `tools/studio/workbench_scheduler.py` | — | M | P0 | 达到轮数/预算停止，不假成功 |
| R06 | Human Approval | `tools/studio/workbench_scheduler.py` | — | M | P1 | 审批与模型输出身份分离 |
| R07 | 验证与交付执行节点 | `tools/studio/workbench_graph.py` | — | M | P1 | 失败 Gate 禁止交付 |
| R08 | 冻结运行定义与源码身份 | `tools/studio/workbench_runtime.py` | `tools/v2/definitions.py` | C | P0 | 启动后草稿更改不影响该次执行 |
| R09 | 请求分节与实际可见 Prompt | `tools/studio/workbench_graph.py` | `tools/v2/runtime.py` | C | P0 | 初始/每轮/上游/输出要求有来源 |
| R10 | 自定义 handoff 数据和字段合同 | `tools/studio/workbench_graph.py` | — | M | P0 | 选择字段或产物引用，类型错误拒绝 |
| R11 | 运行事件持久化与投影 | `tools/studio/workbench_store.py` | `tools/v2/store.py` | C | P0 | run、activation、handoff 与事件一致 |
| R12 | 取消与确认状态 | `tools/studio/workbench_runtime.py` | `tools/v2/recovery.py` | C | P0 | 取消不误伤别的任务，不盲目重发 |
| R13 | 暂停、继续与安全重试 | `tools/studio/workbench_scheduler.py` | — | M | P0 | 暂停不启新节点，未知外部操作不重试 |
| R14 | worker 崩溃与 UNKNOWN 恢复 | `tools/studio/workbench_failures.py` | `tools/v2/worker_facts.py` | C | P0 | 进程身份和回执矛盾时待核验 |
| R15 | 启动幂等和授权约束 | `tools/studio/workbench_runtime.py` | `tools/v2/runtime.py` | C | P0 | 同 intent 不重复产生 run |

### 运行监控与研发自动化

| ID | 用户能力 / 功能要求 | V1 来源 | V2 对照 | 现状 | 优先级 | 最小验收证据 |
|---|---|---|---|---|---|---|
| O01 | 真实节点状态与画布显示 | `tools/studio/workbench_ui/RuntimeOverview.jsx` | `tools/v2/ui/index.jsx` | C | P0 | 状态来源运行事实，不是动画猜测 |
| O02 | 运行列表与历史详情 | `tools/studio/workbench_ui/RunJournal.jsx` | `tools/v2/run_summary.py` | C | P0 | 刷新只读、不重发 |
| O03 | 逐事件日志与完整追踪 | `tools/studio/run_journal.py` | `tools/v2/run_summary.py` | P | P1 | 事件序列、失败原因和导出一致 |
| O04 | 节点实际请求和回执检查器 | `tools/studio/workbench_ui/trace_views.jsx` | `tools/v2/ui/index.jsx` | C | P0 | 已发送请求与回执身份可对应 |
| O05 | 点击连线查看真实交接 | `tools/studio/workbench_ui/HandoffEdge.jsx` | `tools/v2/ui/index.jsx` | C | P0 | 实际边/源/目的 activation 一致 |
| O06 | AI 助手与独立对话 | `tools/studio/workbench_ui/CanvasAssistant.jsx` | — | M | P1 | 独立会话问答，不冒充运行 |
| O07 | AI 配置工作流提案和预览 | `tools/studio/configuration_assistant.py` | — | M | P1 | 提案可人工预览应用，不能代授权 |
| O08 | 运行错误诊断与问题定位 | `tools/studio/workflow_inspection.py` | — | M | P1 | 定位节点错误、给可操作修复步骤 |
| O09 | GitHub 任务与交付集成 | `tools/orchestration/github_delivery.py` | — | M | P1 | 外部 Gate 与真实 PR 权限严格区分 |
| O10 | GitLab 任务与交付集成 | `#6` | — | M | P2 | 按授权 MR 与 CI，无自签 |
| O11 | 自动测试、独立审核与交付 Gate | `tools/orchestration/gate.py` | — | M | P1 | 验证失败或限制结论不得交付 |
| O12 | 执行费用/Token 用量 | `tools/studio/usage.py` | `tools/v2/run_summary.py` | P | P2 | unknown 不补零，provider 自报需标注 |
| O13 | 成熟工作台 UI/UX 和无障碍 | `tools/studio/workbench_ui/WorkspaceLayout.jsx` | `tools/v2/ui/style.css` | P | P1 | 模块级操作与 1280/1440/窄屏评分 |
| O14 | 真实终端与运行监控联动 | `tools/studio/workbench_ui/NativeTerminal.jsx` | — | M | P1 | 真实日志和键盘接管，不启动第二写者 |
| O15 | 完整自举研发场景验证 | `#132` | — | M | P0 | 真实 Codex 开发-测试-Claude 审核-修复闭环 |

## 迁移与实施规则

1. **默认移植优先**：先定位 V1 源码和测试，评估直接移植/薄适配；只有接口结构、安全、数据权威冲突时才局部重构或重写。保留外部可观察语义，不复制旧耦合。
2. **避免为单个细节单独建分支**：一次聚焦一个完整模块；除非存在并行写入冲突才开任务分支；优先在既有 V2 集成分支累计本地提交。
3. **验收以用户场景为单位**：B 开发过程做局部测试；A 模块级审查；C 完整模块交付后执行真实 UI 走查；GitHub 完整 CI 每个可交付集成批次触发一次。
4. **两轮无实质进展即更换方案**，不能继续在同一问题上消耗上下文；高风险失败应立即阻断相关范围。
5. C 状态转为 DONE 的必要条件：指定 Git SHA、实际 UI 操作步骤、可复现输出/日志、合成与真实模型证据分开；本矩阵的 C 只是 code-present，不是 DONE。
6. 禁止改写旧 Stable、真实原生会话和凭据；保留 V2 的身份、授权、幂等、终结回执及 UNKNOWN/RECOVERY_REQUIRED。

## 首批交付排序（按用户可用性，而非微小 Issue 数）

- 模块 1：会话中心可操作闭环（发现 → 阅读 → 新建/恢复 → 安全终端），优先处理 S10/S13/S14/S15。
- 模块 2：Agent 真正执行工作（Claude 适配、模式、文件/工具权限），优先 A02/A06/A07/A12。
- 模块 3：工作流可组合控制流与 handoff（D11、R02–R05、R10），先利用 V1 已实现的 graph/scheduler 语义，再接入 V2。
- 模块 4：运行监控与研发审核（O08–O11/O15），同时逐批补齐其余功能。

## 本矩阵尚需核验的边界

- 历史 Issue 细节、用户此前口头提出的全部页面操作与 V1 行为仍需由 A/C 持续对照；新增条目不得覆盖原编号。
- 现有 GitHub Actions 仅证明合成执行、浏览器和部分 native 机制测试，不能证明完整 V1 parity。
- 独立任务阶段应把每条 P/M 变为 C，再经 C/用户验收转为 DONE。未经明确决定，不删除需求。
