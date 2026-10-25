# 画布助手与 Copilot 接入

画布旁的「AI 配置」是软件内置的聊天式设计助手，和工作流里的执行 Agent 分开。它使用 Copilot 的 headless RPC 引擎，不启动 Codex 或 Claude Code CLI 来设计工作流；不包含离线模型权重。模型推理由当前已登录的 Copilot 服务提供，受账号配额和策略限制。

打开工作文件夹，在草稿画布点击「AI 配置」，选择助手模型，确认允许发送对话及可见画布，描述目标并发送。助手可以直接更新草稿；继续聊天可调整节点、连线和提示词，画布撤销仍然可用。取消「聊天后直接更新」可先阅读建议再应用。生成期间手动编辑了画布时，迟到建议不会覆盖当前编辑。关闭面板不停止生成；重新打开读取同一对话。生成失败、断网或服务重启不自动重发有副作用的请求。

仅发送可见对话、必要的节点/连线配置和已配置 Agent 选项，不读取工作区文件。助手启用的原生工具列表为空，禁止配置发现、文件 hooks、Skills 和主机 Git 操作，拒绝反向工具及权限请求。没有自动执行、提交、推送或批准能力。完整产品仍受既有独立审核和 Gate 控制。

## 真实模型目录

`GET /api/copilot/models` 每次向当前原生 Copilot 账号查询 `auth.getStatus` 和 `models.list`，不推理，不拿静态帮助或旧缓存冒充真实目录。失败明确返回原因。模型目录不授予执行权限。Auto 的实际选择以本轮原生 usage 回报为准；未回报的模型或 effort 写 `unknown`。

2026-10-26 本机 CLI 1.0.85、协议3、已登录账号实测目录仅返回 `auto`。两次工具禁用的短请求实际 usage 回报 `gpt-5.6-luna`；不能由此推断能固定选择该模型，也不能保证后续 Auto 路由相同。帮助列出的其他模型不等于当前账号可用。

此阶段接入模型目录及内置助手后端。Copilot 作为具有文件读写、原生终端恢复及交付能力的工作流节点尚未通过隔离/生命周期验收，节点列表继续显示不可用，避免把目录发现误当成完整 Provider 适配。

## 外部 Agent 接口

接口复用工作台本机访问凭据及来源保护。凭据不得写进任务或聊天文本。

| 接口 | 用途 |
|---|---|
| `GET /api/config-assistant/schema?workspace=…` | 获取版本化计划格式 |
| `POST /api/config-assistant/proposals` | 提交 `{workspace, graph, plan}`，验证提案 |
| `POST /api/config-assistant/apply` | 对当前草稿做 CAS 检查后返回可应用图，不执行 |
| `POST /api/canvas-assistant/messages` | 对话式设计，绑定 workspace、conversation、request_id、expected 版本、graph、model、allow_context |
| `GET /api/canvas-assistant/conversation?workspace=…&id=…` | 查询实际消息及本轮状态，不重放 |
| `POST /api/canvas-assistant/cancel` | 取消本次助手生成 |

当前计划支持顺序和只读并行汇合的 start/agent/merge/result，最多12节点/24连线；高级条件/循环仍在画布配置，不伪造已支持。内置助手可以返回紧凑计划，由本地补齐所选 Profile 的明确默认模型/强度及空提示词，再走原严格验证器；不能借此引入未配置模型或权限。新消息只保留至多6组最近可见对话加当前画布，上限20轮/对话、180秒/请求；没有客户端自动重试。实际 Provider 内部重试未知。请求身份重复不再调用模型，复用身份篡改内容被拒绝。

已有外部 HTTP/文件导入接口可用，因此本阶段不增加一个能扩大执行权限的 MCP 服务。后续 MCP 可以作为这些同权限接口的薄封装，不能直接获得文件/授权/交付能力。后端边界独立于 Codex/Claude，未来可以接其他明确配置的模型 API；当前没有宣称支持任意 endpoint 或内置离线模型。
