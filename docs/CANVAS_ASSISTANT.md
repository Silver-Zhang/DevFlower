# 画布助手与 Copilot 接入

画布旁的「AI 配置」是软件内置的聊天式设计助手，和工作流里的执行 Agent 分开。它是 DevFlower 自己实现的 Agent，拥有专用系统指令、对话历史和受限画布工具，直接调用独立配置的模型 API。不启动 Copilot、Codex 或 Claude Code CLI；不包含离线模型权重。详见 [使用说明](user/assistant-and-copilot.md)。

打开工作文件夹，在草稿画布点击「AI 配置」，选择助手模型，确认允许发送对话及可见画布，描述目标并发送。助手可以直接更新草稿；继续聊天可调整节点、连线和提示词，画布撤销仍然可用。取消「聊天后直接更新」可先阅读建议再应用。生成期间手动编辑了画布时，迟到建议不会覆盖当前编辑。关闭面板不停止生成；重新打开读取同一对话。生成失败、断网或服务重启不自动重发有副作用的请求。

仅发送可见对话、必要的节点/连线配置、已配置 Agent 选项，以及本轮明确选择的图片，不读取其他工作区文件。助手仅提供 propose_workflow 画布工具，没有文件、命令、配置发现、Skills、主机 Git 或权限批准工具。没有自动执行、提交、推送或批准能力。完整产品仍受既有独立审核和 Gate 控制。

## 真实模型目录

节点能力刷新接口通过已登记的 Copilot 原生执行 Profile 查询 `auth.getStatus` 和 `models.list`，不推理，不拿静态帮助或旧缓存冒充真实目录。失败明确返回原因。模型目录不授予执行权限。Auto 的实际选择以本轮原生 usage 回报为准；未回报的模型或 effort 写 `unknown`。

2026-10-26 本机 CLI 1.0.85、协议3、已登录账号实测目录仅返回 `auto`。两次工具禁用的短请求实际 usage 回报 `gpt-5.6-luna`；不能由此推断能固定选择该模型，也不能保证后续 Auto 路由相同。帮助列出的其他模型不等于当前账号可用。

Copilot 已作为独立工作流 Provider 接入；在隔离的管理 HOME 内支持上下文只读 Ask/Plan、明确 UUID 创建与恢复及可见历史。原生工具、文件写入、交互终端、归档/删除和分叉尚未开放。操作者登记和真实目录发现均不自动授予执行权。

## 外部 Agent 接口

接口复用工作台本机访问凭据及来源保护。凭据不得写进任务或聊天文本。

| 接口 | 用途 |
|---|---|
| `GET /api/config-assistant/schema?workspace=…` | 获取版本化计划格式 |
| `POST /api/config-assistant/proposals` | 提交 `{workspace, graph, plan}`，验证提案 |
| `POST /api/config-assistant/apply` | 对当前草稿做 CAS 检查后返回可应用图，不执行 |
| `POST /api/canvas-assistant/images` | 上传当前工作区 PNG/JPEG（名称及 base64），返回图片身份；不接受文件路径/URL |
| `POST /api/canvas-assistant/messages` | 对话式设计，绑定 workspace、conversation、request_id、expected 版本、graph、model、allow_context，可选本轮 images 身份数组 |
| `GET /api/canvas-assistant/conversation?workspace=…&id=…` | 查询实际消息及本轮状态，不重放 |
| `POST /api/canvas-assistant/cancel` | 取消本次助手生成 |

当前计划支持 start/agent/merge/condition/loop/output/result，最多12节点/24连线；有界循环最多5轮、最多2层。每层有唯一最终结果，多个分支的输出汇入该结果；output 输出数据，不自动把运行标为失败。图片模型生成的草稿仍走相同结构和权限校验。内置助手可以返回紧凑计划，由本地补齐所选 Profile 的明确默认模型/强度及空提示词，再走原严格验证器；不能借此引入未配置模型或权限。新消息只保留至多6组最近可见对话加当前画布，上限20轮/对话、180秒/请求；没有客户端自动重试。实际 Provider 内部重试未知。请求身份重复不再调用模型，复用身份篡改内容被拒绝。

已有外部 HTTP/文件导入接口可用，因此本阶段不增加一个能扩大执行权限的 MCP 服务。后续 MCP 可以作为这些同权限接口的薄封装，不能直接获得文件/授权/交付能力。后端边界独立于 Codex/Claude，未来可以接其他明确配置的模型 API；助手支持独立配置的 Anthropic/OpenAI 兼容 API，凭据仅在本机私有配置保存；不宣称内置离线模型。
