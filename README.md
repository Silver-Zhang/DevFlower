# DevFlower

DevFlower 是一个可视化工作台。你在画布上安排 Agent、命令和它们之间的成果交接，DevFlower 将工作流编译成独立的 Python 脚本。在工作台运行和下载到终端运行使用同一份脚本。

## 在 Linux 上开始

需要 Linux 和 Python 3.11+。安装工作台无需 Node.js 或模型账户。运行 Agent 节点时，需要相应的原生 CLI 和可用的账户，DevFlower 沿用该 CLI 的配置和权限。

```bash
git clone --branch develop https://github.com/Silver-Zhang/DevFlower.git
cd DevFlower
./launcher/linux/install-workbench.sh --allow-root /absolute/path/to/project
```

把最后一个参数换成项目文件夹，然后从应用菜单打开 **DevFlower**，或运行 `~/.local/bin/devflower-workbench start`。已有安装可停止工作台后运行 `./launcher/linux/install-workbench.sh upgrade`。工作台只监听本机，可在 [Linux 桌面入口](docs/workbench/LINUX_DESKTOP.md) 查看配置、升级和排查方法。

1. 点击“打开文件夹”并选择项目。
2. 新建工作流，配置并连接 Agent 或命令节点。
3. 保存并检查工作流。可以预览、下载 Python，或从画布启动。
4. 在运行监控里查看实际请求、退出结果、交接内容和日志。关闭页面后仍可重新打开运行记录。

工作台也提供 Agent 管理、原生会话与终端、文件浏览、模板、AI 助手和 Git 集成。各入口的可用性取决于本机配置。参见 [助手使用指南](docs/workbench/ASSISTANT.md)、[原生 CLI 与会话](docs/workbench/CLI_NATIVE.md) 和[功能兼容清单](docs/workbench/FEATURE_PARITY.md)。

## 记录与数据

- 草稿、模板和运行记录保存在工作台私有状态目录。新安装默认使用 `${XDG_STATE_HOME:-~/.local/state}/devflower-workbench`，既有安装升级后沿用之前的位置。
- 对已保存的工作流使用“保存项目副本”，可将可导入的档案写入项目的 `.devflower/workflows/`。项目移动或复制时，可在新工作区打开这份档案。
- 原生 Agent 会话和认证由各 CLI 管理。运行日志、凭据和私有对话应留在本机。

早期 Studio 的桌面与终端入口见[兼容工作台说明](docs/WORKBENCH.md)。独立研发记录命令见 [工作流与归档](WORKFLOW.md)，受管部署见 [Stable Studio](docs/USABLE.md)。

## 源码与验证

| 路径 | 职责 |
|---|---|
| `tools/workbench/` | 工作区、画布、Agent、会话、运行记录、助手与集成，`ui/` 包含前端 |
| `tools/devflower_export.py`、`tools/workflow_script_runtime.py` | 确定性编译器和导出脚本使用的执行代码 |
| `tools/studio/`、`tools/orchestration/` | Studio 兼容入口、共享工具与旧受管执行 |
| `launcher/`、`tests/` | 本机安装入口与自动化验证 |

前端使用单一构建命令。构建依赖可以放在源码目录之外：

```bash
ui_deps=$(mktemp -d)
cp tools/studio/workbench_ui/package{,-lock}.json "$ui_deps/"
npm ci --prefix "$ui_deps" --ignore-scripts --no-audit --no-fund
DEVFLOWER_UI_DEPS="$ui_deps" node tools/build_ui.mjs workbench
```

构建 Studio 前端时使用同一命令的 `studio` 目标。Python 回归可用 `PYTHONPATH=tools python3 -B -m unittest discover -s tests -v`。Chromium 与原生 CLI 测试夹具的完整运行方式见[工作台 CI](.github/workflows/workbench.yml)。

日常开发在 `develop`。正式源码、开发分支和项目记录的关系见[分支与历史保留](docs/BRANCHING.md)，执行和兼容边界见 [Script-First](docs/workbench/SCRIPT_FIRST.md)。
