# DevFlower V2 CLI-native 架构决策

## 决策

DevFlower 是本机 CLI Agent 的可视化管理与 Workflow 编排平台。真实会话由安装的原生 CLI 创建、续聊和落盘。DevFlower 保留用户已配置的 CLI 认证、模型服务、配置目录和原生 Skills 发现机制。用户配置的模型、强度、权限、工作模式和工作目录应映射到所选 CLI 版本实际支持的入口。环境和参数由服务端构建并在发送前复核。

| 职责 | 权威来源 |
|---|---|
| Command Builder | 已安装的 Provider CLI 映像、版本、帮助及能力探测。分别生成 Codex 与 Claude Code 的原生参数，固定此次授权的模型、模式、cwd 与身份。 |
| Process Manager | 已授权的具体本机进程、原生输入输出、PTY、归属和终结回执。未知效果进入恢复状态，同一发送意图只读回既有记录。 |
| Session Manager | 原生历史、Provider、配置根、原始 cwd 与真实 UUID。DevFlower 的别名、分组和回收分类是本地视图元数据。 |
| Workflow Runtime | 已冻结工作流、节点调度和交接。调用已验证的 Provider 入口，按实际事件记录进程和运行结果。 |

认证和服务路由交给原生 CLI。DevFlower 只传递明确受信的本机配置来源或启动环境，凭据值不得写入状态库、日志或浏览器响应。受管 Claude 在传递认证环境前校验并封存已批准的可执行映像，按描述符启动，继续信任本机系统动态加载器和库。纯对话模式和权限受限的终端可以关闭超出此次授权的 CLI 能力。产品应显示实际限制。CLI 目录、帮助文本或成功返回某个参数只用于发现能力，安全路径需要真实进程和隔离反例。

## 会话写入边界

- 会话读权限不授予写权限。写入前固定工作区身份、配置根、UUID、请求和意图。相同会话的受管 API、进程和 PTY 使用同一个已有租约域。PTY 浏览器和本机连接还须在同一监督进程上切换输入代次。
- Codex 0.163.0 的原生执行路径具有写者协调证据。V2 保留原生写锁和进程回执，并用自己的租约协调受管入口。PTY 属于独立能力，须按实际启动模式重新核验。
- Claude Code 2.1.319 的写入权只授予 DevFlower 已核验创建、且最新原生历史与完成回执一致的会话。核验在受管租约内重复执行，任何已观察到的外部变更都会撤销续聊资格。外部创建的记录只读，Claude PTY 关闭。共享 HOME 中独立启动的 CLI 不参与 V2 租约，同 UUID 的两个外部进程曾同时触达本机合成服务。
- V1 Codex 封装把原生存储挂载到应用服务器命名空间。真实 TUI 的用户 Shell 在此路径读到了私有存储测试标记。CLI 对模型工具的沙箱不能代替这条用户 Shell 的操作系统边界。任何终端实现必须验证用户 Shell 和其他执行入口看不到受保护的状态、认证和无关会话，同时按授权保留工作区读写。V1 的终端显示、监督和本机接管协议可继续复用。

## Codex PTY 权限和生命周期

V2 写配置需同时启用 `codex` 和 `codex_terminal`。用户在已核验会话上选择模型与强度，并明确批准该工作区的终端读写。服务核验 CLI 映像、会话、工作区和租约。Bubblewrap 把真实 TUI 限定在运行依赖和获准工作区，通过只接受该进程的 Unix WebSocket 转发指定 UUID 的原生请求。客户端 `!` Shell 及扩大作用域的请求被拒绝。模型工具由原生 Codex 沙箱执行，权限固定为工作区读写、必要运行依赖只读、禁止工具联网，且不继承后端认证环境。缺少受支持的隔离或有效权限回执时，终端不会开放输入。

本机 Codex 0.163.0 的原生沙箱帮助程序通过其主程序路径重新执行。服务从已批准的封存映像生成私有只读映像，通过已核验文件描述符启动并在操作前再次检查。原始安装路径的替换不会改变此次映像。拥有同一主机用户权限并能直接修改服务私有目录的进程处于本机信任边界内。

浏览器与本机连接复用同一 PTY 和键盘代次。关闭视图只断开连接，显式停止才结束对应前端和后端。会话 API 和 PTY 共用写租约，未确认的操作保留恢复状态。

## Agent Manager 执行映射

Agent 配置按工作区保存。角色模板填充身份内容，执行权限由单独的配置与启动确认决定。可从 Agent Manager 直接运行，也可把同一配置版本添加到画布。启动会冻结 Agent 版本、Provider、工作目录和会话身份。运行详情分别显示身份、提示词、上游交接与实际发送的可见请求。

| CLI 与模式 | 原生入口 | 执行边界 |
|---|---|---|
| Codex Ask | `exec --json` | 仅对话，或通过已核验权限配置读取批准目录。 |
| Codex Agent | `exec --json -c default_permissions=…` | Bubblewrap 建立批准目录的读写范围。原生命令工具的网络、额外目录和后端认证环境不可用。 |
| Codex Plan | `app-server` 的 `collaborationMode=plan` | 仅使用批准目录的只读权限，关闭命令执行，不代答原生交互请求。DevFlower 显示实际计划和原生回执。 |
| Claude Ask | `-p --permission-mode manual --permission-prompts none --tools ''` | 仅对话，使用 Claude 本机认证、模型和服务配置。 |
| Claude Plan | `-p --permission-mode plan --permission-prompts none --tools ''` | 原生 Plan 权限模式下生成计划，文件与命令工具关闭。 |
| Claude Agent | `-p --restricted --permission-mode acceptEdits` | 允许原生文件工具和受控 Bash。Linux Bubblewrap 与 Landlock 限定工作区。Bash 使用清理后的环境，无法新建网络套接字或读取进程环境。 |

Claude 启动沿用私有配置中的原生写入批准。Codex 目录取自 `model/list`，Claude 取自无用户消息的原生初始化。界面分别标出 CLI 目录、原生配置、默认值和手工模型。Claude 可传递原生 `--max-budget-usd`，两种 CLI 的运行均受 V2 调用次数和超时约束。

Codex 只在收到完整原生 Plan 后显示执行确认。确认包含计划正文、工作区和读写范围，随后用原会话 UUID 建立单独的 Agent 运行，核验 Codex 报告的 `default` 模式。重复提交同一确认返回原运行。

Claude Agent 读取已配置的原生 API 认证环境，支持在 `settings.json` 中配置的 API 密钥或令牌。工具模式需要 Bubblewrap、Landlock ABI 6 和已核验的 CLI。磁盘 OAuth 凭据或自定义证书无法安全投射时，该模式拒绝启动。会话继续使用 V2 的身份、写租约和未知状态恢复。新会话将 CLI 写出的原始文件原子发布到原生历史目录，续聊仅挂载所批准的原始记录。

Codex 使用原生 Skills 目录及精确文件引用。Claude Agent 在启动前核验所选内容和来源，再通过临时原生插件加载工作区与用户级 Skills。CLI 初始化回执必须包含所选名称。原生 Skills 文件保持原样，技能共享当前 Agent 的工具权限。原生子 Agent、逐命令人工审批及其他 Provider 执行入口仍是后续需求。

## 验证与发布门槛

固定候选后分别记录合成测试、真实 CLI、真实 Provider、操作系统隔离与独立 QA 证据。Claude 的对照测试应同时覆盖两次受管请求和外部直接续聊。Codex 测试应复现真实 TUI 输入、受保护标记不可读、工作区授权读写、跨会话拒绝、关闭视图与显式停止，以及浏览器和本机端在同一 PTY 上的输入互斥。安全路径缺少对应证据时，该能力保持关闭。模块状态以功能追踪矩阵为准。

参考资料 [Codex 0.163.0 原生写锁](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rollout/src/writer_lock.rs) · [Codex 用户 Shell 实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/user_shell.rs)
