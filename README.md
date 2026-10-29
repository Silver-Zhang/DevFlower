# DevFlower

**在一个可视化工作台中组织 Agent、配置工作流，并查看真实对话、执行状态和上下游交接。**

DevFlower 同时保留最初的研发记录能力：工作区内保存工作流与历史，`project/*` 分支保存可以随 Git 追溯的任务、决策和验证摘要。业务源码仍放在各自的仓库中。

## 快速启动

目前实际验证的平台为 **Linux**。需要 Python 3.11+、Git、bubblewrap（`/usr/bin/bwrap`）、Landlock ABI ≥ 6，以及在本机安装、登录并配置好的 Codex 或 Claude Code CLI。原生 CLI 的具体功能以当前版本和实际探测结果为准。

```bash
git clone https://github.com/Silver-Zhang/DevFlower.git
cd DevFlower
python3 -B tools/devflower_start.py --check
python3 -B tools/devflower_start.py
```

Python 服务使用标准库，前端静态资源已构建，普通启动不需要 Node/npm。首次登录、目录信任和中转站设置在原生 CLI 中完成；DevFlower 沿用原配置，不复制认证或猜测模型。

只使用一个工具时，可以限制启动登记范围：

```bash
python3 -B tools/devflower_start.py --provider codex
# 或
python3 -B tools/devflower_start.py --provider claude
```

启动器会说明缺失的安装、配置或参数。可以用 `--codex-cli` / `--claude-cli` 指定工具路径，或用对应的 `--*-model` / `--*-effort` 指定明确的本次请求。模型名称应来自你当前工具、账号或中转站，不能照搬别人的配置。

服务只监听 `127.0.0.1`，默认打开浏览器；`Ctrl-C` 停止本次服务。`--check` 只检查环境，不创建运行状态、不启动服务或调用模型。使用 `--no-browser` 时，终端会显示带访问凭据的本机私有网址，请勿公开。

详细参数、重复启动与版本切换规则见 [本机公共启动](docs/WORKBENCH.md#本机公共启动)。

## 第一次使用

1. **打开工作区**：点击“打开文件夹”，选择具体项目，确认页面显示的路径和环境。
2. **建立流程**：选择示例模板，或在右侧添加 Agent、条件、汇合、循环等节点并连接。
3. **配置 Agent**：选择工具、当前模型、推理强度和工作方式，填写初始化提示词、每轮任务与输出要求。
4. **检查流程**：点击“检查工作流”，按提示修正缺失配置、断开的连线或字段合同。静态检查不调用模型，也不代表执行已经成功。
5. **提交任务**：填写本次目标，核对运行范围后开始。画布显示真实节点状态；点击连线查看交接内容，点击下游节点查看实际请求。
6. **查看与复用**：从会话或整轮对话查看结果、工具活动和历史。保存流程，或收藏为模板；下一任务可复用流程并填写新目标，旧运行记录保留。

编辑草稿不会改写已经冻结的运行。关闭会话页签或终端视图只关闭显示；结束原生进程、停止工作流和删除历史是不同操作。

## 工作台可以做什么

| 能力 | 使用方式与边界 |
|---|---|
| 可视化画布 | 拖动、缩放、多选、连线、复制、撤销与重做；浅色、深色及跟随系统主题 |
| 可执行工作流 | 顺序、条件路由、只读并发与汇合、有界循环、人工确认及结果节点 |
| 真实状态与交接 | 查看当前节点、轮次、等待原因，以及本轮来源、字段、实际输入和 Provider 回执 |
| 统一会话 | 按 Provider 或 Workflow 分组，查看命名会话、可读历史及原生终端占用状态 |
| DevFlower 助手 | 在右侧对话，解释功能、检查已批准的产品源码、诊断错误、提出工作流或修复草稿；支持流程图图片输入 |
| 保存与复用 | 保存工作流、收藏模板、复用配置提交下一任务；历史按工作区和工作流组织 |
| 研发交付 | 验证和交付节点需固定的受控外部执行器、当次授权及完整 Gate；普通本机入口不会自动获得提交、推送或发布权限 |

内置助手使用独立模型 API，不借用 Codex 或 Claude Code 的 Agent。首次在右侧“AI 助手”的模型连接中配置自己的服务、模型与密钥后，连接会在本机持续保存；已有 DeepSeek 设置不因重启或切换工作区丢失。软件不附带付费 API 密钥或本地模型权重。

目录发现、操作者登记和实际执行成功是不同证据。过期目录不能证明当前模型或强度不可用；真实调用失败会保留错误。提示词与上游输出不能扩大下游权限或批准交付。

## 对话和研发记录存在哪里

| 内容 | 保存位置 |
|---|---|
| 工作流、收藏模板与项目历史 | 普通工作区的 `.devflower/`；设计与模板使用 `library.sqlite` |
| 助手已完成对话的阅读副本 | 工作区 `.devflower/assistant-<哈希>.json` |
| 实时执行、进程、会话占用及授权状态 | 工作台本机私有状态目录；默认 `${XDG_STATE_HOME:-~/.local/state}/devflower/workbench` |
| 助手 API 连接 | `${XDG_CONFIG_HOME:-~/.config}/devflower/devflower-agent-connection.json`，不写入项目或聊天 |
| Codex / Claude Code 原生历史 | 各工具原有的 HOME 与配置根；不以复制聊天冒充会话同步 |
| 可提交到 Git 的研发档案 | 独立 `project/<项目名>` 分支内的 `project/`，一任务一份五节 README 和必要机器记录 |

受保护或无法写入的工作区使用本机记录区保存 `.devflower`，页面显示实际位置；未知的已有目录不会被覆盖。系统目录或包含认证、Stable、运行状态的父目录可以浏览，但执行任务应选择其中具体项目。

原始日志、截图、认证和原生会话留在本地。需要同步研发进度时，将精简结论、决策、源码身份和验证摘要整理到 `project/*`，不要把整个运行目录提交到公开仓库。查看历史或刷新页面不会重新调用模型。

## main、develop 与 Stable

| 分支 | 用途 |
|---|---|
| `main` | 已批准集成的正式源码，作为 Stable 发布基线，不放项目实例 |
| `develop` | 日常开发与待验收候选；完成验证、独立审核和授权后通过发布 PR 进入 main |
| `project/<slug>` | 项目的任务、过程、决策和历史；可接收 main，不整体合回源码分支 |

发布整合完成后，`develop` 普通快进到同一 `main` 提交，随后继续在 `develop` 开发。临时分支只有在代码和历史已保全、无人使用且获准清理后才删除；分支清理不删除旧工作目录、会话或证据。

[Releases](https://github.com/Silver-Zhang/DevFlower/releases) 和对应 tag 标识实际发布的版本。`develop` 的新功能、绿色 CI 或 Draft PR 不等于 Stable 已发布。

**源码版本与本机受信部署分别管理。** 普通源码启动默认显示 Development；逐机批准的 Stable 部署使用独立启动入口与完整证据绑定。下载正式 tag 也不能复制别人的私有批准、认证或自行编造证书来获得受信身份。旧 Stable 在新版本正式晋升前保持原样。

## 单独使用研发记录工具

不启动工作台也可以使用原有记录流程。在独立、干净的工作流 clone 中建立项目分支：

```bash
git switch -c project/demo main
python3 -B tools/devflower.py init demo
python3 -B tools/devflower.py new parser-smoke --mode B
python3 -B tools/devflower.py index
python3 -B tools/devflower.py check
```

`init` 不切换分支、不修改业务源码、不提交或推送；`new` 创建五节 README 与 `task.json`。任务状态以 `task.json` 为准，INDEX 由工具生成。多个项目使用独立 clone 或 worktree，避免在同一目录反复切换项目分支。

## 文档与开发

- [工作台使用说明](docs/WORKBENCH.md)：配置、会话、交接、助手、存储与原生运行边界。
- [Stable Studio](docs/USABLE.md)：可信部署、自举研发与交付要求。
- [AGENTS.md](AGENTS.md) 与 [WORKFLOW.md](WORKFLOW.md)：Agent 入口、授权、独立审核与归档。
- [分支与回流](docs/BRANCHING.md)：源码和项目记录的隔离、整合与清理。
- [运行器](docs/ORCHESTRATOR.md) 与 [v0.2-A 合同](docs/design/v0.2-a/README.md)：原有 CLI 接口及验收边界。
- [设计约定](docs/design/WORKBENCH_DESIGN.md) 与 [参考资料](docs/REFERENCES.md)：界面规范、依赖和第三方许可。

开发自验的基础命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -B tools/devflower.py check --baseline
python3 -B tests/host_isolation.py
node --check tools/studio/static/app.js
```

`--baseline` 用于不含项目实例的源码分支；项目分支使用普通 `check`。前端改动还需构建和 Chromium/HTTP 回归。测试、独立审核、用户体验验收、合并与正式发布分别记录，不能相互替代。

当前实测范围为 Linux；Codex 桌面端的原生记录可见性与受控终端的同一 PTY 接管分别验证，不宣称桌面应用已经接管外部 CLI 的实时终端。Claude Code 保留其原生 CLI 历史与本机配置；Copilot 仅在安装、认证和能力实际验证后开放，公共启动器不会把它默认登记为可执行工具。

既有许可证及第三方依赖归属保持不变，见 [工作台依赖说明](docs/WORKBENCH.md) 与源码中的许可文件。
