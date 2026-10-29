# DevFlower

面向人类与 Agent 协作的轻量研发工作流。`main` 保留正式版本，`develop` 保留最新待验收开发版，`project/*` 保留项目过程记录；业务源码保持独立仓库。

## 分支，而不是 main 下的多项目目录

| 分支 | 内容 | 合并方向 |
|---|---|---|
| `main` | 正式版本的通用规则、模板、工具和测试；不含项目实例 | 接收 develop 中完成发布前验证、独立审核并获准合并的候选 |
| `develop` | 最新待验收开发版；保留已有 UI 与执行器实现，不含项目实例 | 汇集通用改进，验收后经授权进入 main |
| `project/<slug>` | 通用文件 + `project/` 下单个项目的方案、进度、任务记录 | 接收 main；不整体合回 main |
| `work/<slug>/<task>` | 从对应项目分支创建的任务分支 | 回对应 project 分支 |
| 临时任务分支（仅明确需要时） | 从 develop 派生并及时归集；不作为日常开发入口 | 回 develop；不直接发布 main |

日常源码开发统一在 `develop`，`main` 仅用于 Stable 发布；每次发布整合后将 `develop` 普通快进到同一 main 提交。长期只保留 `main`、`develop` 和 `project/*`。临时分支须在完整历史已备份、代码已由 develop/main 保全且另获明确删除授权后才能清理；旧原生会话、工作目录和 worktree 保留，分支清理不等于目录清理。一个项目工作目录不要反复切换多个项目分支。并行项目用独立 clone 或经批准的 worktree，避免未跟踪文件串项目。详见 [分支与回流](docs/BRANCHING.md)。

## 入口

Agent 先读 [AGENTS.md](AGENTS.md)，存在项目实例时再读 `project/STATUS.md` 与 `project/AGENT_CONTEXT.md`；任务只按需读 §1/§3/§4。详细规则见 [WORKFLOW.md](WORKFLOW.md)。

需要在本机打开项目、配置工作流并驱动真实 Agent 时，见 [Stable Studio 使用说明](docs/USABLE.md)。原有画布和只读会话发现见 [Studio](docs/STUDIO.md)。

命名会话、React Flow 可执行画布与 v3 图协议见 [Agent 工作台](docs/WORKBENCH.md)。普通本机入口在仓库根目录运行：

```sh
python3 -B tools/devflower_start.py --check
python3 -B tools/devflower_start.py
```

需要 Python 3.11+、Linux、Git、可运行的 `/usr/bin/bwrap`（bubblewrap）、Landlock ABI >= 6，以及自己机器上已安装并由自己登录配置的 Codex / Claude Code CLI。启动器从原生用户配置读取模型/强度，不替换原配置；缺少偏好时需明确提供 `--codex-model`/`--codex-effort` 或 `--claude-model`/`--claude-effort`。`--provider codex` 或 `--provider claude` 可限制登记范围。版本/help 检查不代表认证或模型推理成功，Copilot 保持不可执行。Python 服务使用标准库，静态资源已构建，普通启动无需 Node/npm；原生 CLI 自身的运行依赖仍需安装。

启动器只在 `${XDG_STATE_HOME:-~/.local/state}/devflower/workbench`（或 `--state-dir` 指定位置）创建私有运行配置和存储，不写源码、项目文件或原生配置，不复制认证。助手已保存的独立 DeepSeek 连接继续使用本机共享设置，不随运行目录更换而丢失，也不复制进工作区。`--check` 不创建状态或启动服务。服务在当前终端运行，只监听 `127.0.0.1`，默认打开浏览器；`--no-browser` 将含令牌的私有入口打印给本人，勿公开日志。再次启动只复用经过候选、配置、真实 PID 和 HTTP 核验的已有服务；版本变化或未知状态会拒绝并保留旧目录，正常停止使用 Ctrl-C。详见 [本机公共启动](docs/WORKBENCH.md#本机公共启动)。

管理员显式绑定入口 `python3 -B tools/devflower_workbench.py` 保留，仍需同时提供 `--operator`、`--operator-sha256`、`--candidate-sha256`、`--policy`、`--policy-sha256`；真实 v2 Stable 部署另需管理员的逐机批准证据。

对外 Stable Release 和版本 tag 应绑定已获准的 main 正式提交；GitHub 版本发布与每台机器的受信部署绑定不同。工作台默认显示 Development，表示没有该机器的有效 Stable 部署绑定，不据此判断 main 是否已审。即使下载正式版本，也不能凭 tag、复制其他机器的私有批准或自行编造证书取得运行时 Stable 身份。


## 开始一个项目

需要 Python 3.11+ 和 Git，无第三方 Python 依赖。先在干净的工作流 clone 中确认远端与基线，再创建 `project/<slug>` 分支：

```bash
git switch -c project/demo main
python3 tools/devflower.py init demo
python3 tools/devflower.py new parser-smoke --mode B
python3 tools/devflower.py check
```

`init` 只写当前工作流仓库的 `project/`，不创建/切换分支，不修改源码仓库，不提交或推送。`new` 创建一份五节 README 和一份小型 `task.json`；后者是任务模式与状态的唯一机器可读来源。编辑状态后运行 `python3 tools/devflower.py index` 更新台账；不要手工修改生成表。

```bash
python3 -m unittest discover -s tests -v
python3 tools/devflower.py check --baseline
```

`--baseline` 验证当前树中没有项目实例；普通 `check` 在项目分支检查任务与台账。检查通过不等于结果正确、用户验收或允许发布。

## 迁移与许可边界

Linux 本地双 Agent 运行器的实际接口、最小任务例、授权格式及限制见
[ORCHESTRATOR](docs/ORCHESTRATOR.md)。实施设计合同与 A01–A16 仍见
[v0.2-A](docs/design/v0.2-a/README.md)；设计文档不代替实际测试或独立验收。

现有项目记录先保留到项目分支，再从通用基线的当前树移除；不重写旧提交。物理仓库重命名由管理员执行，品牌名称不等于远端已改名。公开仓库的所有项目分支都公开，分支不提供权限隔离。

既有仓库许可证保持不变。Development 工作台直接使用 React Flow 等前端依赖，版本及第三方许可证见 [工作台说明](docs/WORKBENCH.md)；其余调研与取舍见 [REFERENCES](docs/REFERENCES.md)。
