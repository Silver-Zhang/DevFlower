# 最小 Stable：画布编译为原生 CLI 工作流脚本

## 当前决策

2026-11-12 用户确认：DevFlower 调度“部门”之间的任务，不接管原生 Agent
内部如何读文件、选择工具、使用 MCP/Skills 或派生子 Agent。暂停功能堆叠和
UI 美化，保留已有历史，不将本次代码标记为 Stable。

目标路径：画布 → 固定代码生成器 → 可读 Python → 原生 CLI → 结果交接。
编译不调用 AI。最终界面内外应执行同一份脚本，不能长期维护两套调度语义。

## 本次源码审查（基线 45fc2757108f1d42ca716b5e46255a125102bb73）

| 实际位置 | 结论与处理 |
|---|---|
| `tools/v2/documents.py`、`ui/canvasModel.mjs` | 保留画布及节点/连线定义，不更换 React Flow。 |
| `tools/v2/draft_transfer.py` | 当前导出是 JSON 草稿；绑定 Agent 仍是 ID/revision，不能单独执行。 |
| `tools/v2/agent_records.py` | 重用已保存配置，导出时固化选定版本的角色和 Prompt，不导出数据库句柄。 |
| `tools/v2/runtime.py`、`store.py` | 当前运行依赖 V2 服务、SQLite 和 `studio` 基础设施；不可简单复制为独立脚本。 |
| `tools/v2/ui/index.jsx` | 工作台状态与运行入口集中；本轮不同时进行 UI 拆分。 |
| `tools/v2/cli_providers.py` | 既有 Adapter 为受管权限模式设计，不能直接当成“完全继承原生配置”的驱动。旧路径保留。 |

## 首个可测试增量，不是 Stable

新增 `tools/devflower_export.py`：从现有数据库的只读事务取得已保存画布和匹配
版本的 Agent；也可读取内联 Agent 的原始 version-1 JSON。固定生成器导出一个
按工作流名称命名的 `.py`，其中包含配置、明确的顺序调用代码和标准库驱动。

`tools/workflow_script_runtime.py` 是生成时嵌入的固定模板，不是运行时依赖。
生成物可移到仓库之外，用 Python 3.11+ 及已配置的 CLI 运行；不需要 DevFlower
后台、数据库、浏览器、模型 SDK 或其他 Python 包。本轮按 Linux/POSIX 验证。

目前支持：Start → Agent → … → Output，Codex/Claude、新会话、文本交接、仅顺序边。
保存的交接长度上限被超过时明确停止，不截断后假装完整交付。

暂不支持导出：分支、汇合、循环、审批、独立 Tool、结构化字段/产物合同、已有
原生会话句柄、V2 命名 Profile 与选定 Skill 哈希、原生 Plan 转 Agent。
遇到这些配置会拒绝导出，不丢弃节点，不把复杂图悄悄改成顺序链。

## 权限与原生能力

必须明确选择 `--native-config` 才能导出。这是**新的执行合同**，不是旧 V2
隔离执行的等价副本：旧节点的 permission、Ask 工具限制和 subagents 筛选不随之
生效；执行权限由本机 CLI 配置及非交互模式的实际规则决定。Plan 不作这种转换。

驱动继承环境、HOME 与原生配置，不注入禁用工具/MCP/Skills/子 Agent 的参数，
也不自动加入跳过审批、关闭原生沙箱等参数。不复刻 CLI 的工具引擎。
继承配置不保证模型一定调用某项 Skill/MCP，也不保证交互 TUI 的全部能力在批处理
模式中可用。需要人工交互而无法继续时停止，不能自动替用户授权。

一次脚本调用是一次新运行；不复用 GUI 的租约或原生会话身份。日志使用新建私有
目录，保留完整 Prompt、stdout、stderr、最终回答及事件。日志可能包含源码或秘密，
仅供本机检查，不能直接公开上传。凭据不写入生成脚本，环境变量不写入事件日志。
失败、中断、结果未知不自动重试；不宣称已实现跨脚本并发锁或崩溃后续跑。

## 使用

在源码仓库中，对已保存的简单画布执行（ID 来自现有工作区/草稿记录）：

```sh
python3 tools/devflower_export.py \
  --database /absolute/private/state/v2.sqlite \
  --workspace-id WORKSPACE_ID --draft-id DRAFT_ID \
  --output-dir /absolute/export-directory --native-config
```

导出目录需预先存在，同名脚本不覆盖。文件名为工作流名称加短哈希，例如
`代码审核_XXXXXXXX.py`；哈希解决名称净化后的冲突，不是软件版本号。

```sh
python3 /absolute/export-directory/代码审核_XXXXXXXX.py --workspace /path/to/project --dry-run
python3 /absolute/export-directory/代码审核_XXXXXXXX.py --workspace /path/to/project --task '完成本次开发并审核结果'
```

`--dry-run` 只显示计划命令，不启动 CLI、不创建日志。可用 `--task-file` 输入任务，
`--run-dir` 指定一个尚不存在的日志目录。外部项目路径通过 `--workspace` 指定。
脚本可人工审查/修改，但本轮不支持反向还原画布。

## 验证与下一关口

```sh
python3 -B -m unittest discover -s tests -p 'test_native_workflow_export.py' -v
```

云端执行 20 项测试通过：包括真实本地子进程/模拟 CLI、脱离仓库运行、确定性生成、
真实数据库只读快照、配置版本、上下游数据、失效拒绝、超时、无重发和防覆盖。
**真实 Codex/Claude 模型调用未执行**：本次云端环境未安装两种 CLI、未配置账户。
旧 Runtime、GUI 启动按钮和旧权限实现未被切换或删除。

下一关口只验证该导出路径的两种真实 CLI；通过后再接画布导出入口和同一脚本运行。
之后逐项讨论 Condition/有界审核回路，不恢复大型平台功能扩展。

原生命令参考（使用时仍须核验本机 `--help`）：
- https://developers.openai.com/codex/noninteractive
- https://code.claude.com/docs/en/cli-reference
