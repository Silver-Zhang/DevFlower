# DevFlower V2 Linux 桌面入口

需要支持 pidfd 的 Linux、Python 3.11 或更新版本，以及当前用户可读取的完整 DevFlower 源码副本。安装和打开工作台不需要模型调用。Agent、验证器和原生终端在各自启动时核验 CLI 和隔离能力。

## 安装与打开

在要使用的 DevFlower V2 源码目录中运行，换成实际项目文件夹。可重复 `--allow-root` 登记多个根目录。请选择具体项目，避免将整个 HOME 或包含产品状态的目录登记为工作区。

```sh
./launcher/linux/install-v2.sh --allow-root /absolute/path/to/project
```

在应用菜单打开 **DevFlower V2**，或运行 `~/.local/bin/devflower-v2 start`。重复打开会使用同一服务。首次安装选择并保存一个仅监听 `127.0.0.1` 的端口。端口被其他程序占用时，V2 会报告失败并保留原地址。首次安装也可通过 `--port` 指定端口。

```sh
~/.local/bin/devflower-v2 status
~/.local/bin/devflower-v2 stop
~/.local/bin/devflower-v2 start --no-browser
```

`--no-browser` 打印当前私有地址，供手动打开。请勿分享带访问凭证的完整地址。停止会正常关闭 V2 服务及其受管终端。已有工作流运行或待核验请求保留各自的生命周期，可重启后查看。

## 可选原生会话与交付

默认原生历史和受管会话均关闭。修改前停止 V2，然后显式启用需要的能力。

```sh
~/.local/bin/devflower-v2 stop
~/.local/bin/devflower-v2 configure --native-history
~/.local/bin/devflower-v2 configure --managed-codex --codex-terminal
~/.local/bin/devflower-v2 start
```

`--native-history` 启用默认原生来源的读取。可分别启用 `--managed-codex`、`--managed-claude`。Codex 原生终端另需 `--codex-terminal`。使用当前登录账户的原生 CLI 配置与启动环境。安装器记录 V2 选项，原生认证和设置继续由相应 CLI 管理。若桌面环境找不到 Codex，可通过 `configure --codex-cli /absolute/path/to/codex` 指定。受管会话的启动检查失败时，可在 `desktop.log` 查看原因，并通过 `configure` 停用对应能力后重新打开。

取消设置使用对应的 `--no-...` 选项。停用历史时需同时取消已启用的受管会话和终端。修改工作区根目录时，`configure --allow-root` 接收完整的新列表。

`configure` 还支持 `--native-agent-profile`、`--native-agent-home`、`--git-delivery-target`。参数格式和核验规则与直接启动一致。每种选项可重复以替换同类完整列表。使用 `--clear-native-agent-profiles`、`--clear-native-agent-homes` 或 `--clear-git-delivery-targets` 清空相应登记。

## 更新与排查

停止 V2 后，从新源码副本运行 `./launcher/linux/install-v2.sh upgrade`，再打开 V2。更新会切换桌面和命令入口，沿用原来的端口、配置、草稿和运行记录。保持当前源码副本可读。

- 配置保存在 `${XDG_CONFIG_HOME:-~/.config}/devflower-v2`，草稿与运行状态保存在 `${XDG_STATE_HOME:-~/.local/state}/devflower-v2/workbench`。
- 启动日志位于状态目录的 `desktop.log`。V2 使用自己的桌面入口和状态目录，可与原有 DevFlower 安装并存。
- 可删除 `~/.local/bin/devflower-v2` 与 `${XDG_DATA_HOME:-~/.local/share}/applications/DevFlower-V2.desktop` 移除入口。先停止服务。保留配置和状态即可稍后重新安装。
- 已有的 [直接启动入口](CLI_NATIVE.md) 仍可使用，自行提供状态路径和允许根目录。
