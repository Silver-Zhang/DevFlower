# DevFlower V2：M1 工作流编辑与保存

## 1. 目标与范围

这是隔离的 Development V2 入口。当前提供 Linux 本机工作空间、空白工作流、Agent 配置、顺序连线、拖动、撤销重做、草稿保存与重开。空白、不连通或尚未配置模型的草稿均可保存。

M1 不启动模型，不提供原生历史、终端、Router、并行、Loop 或自动交付。启动按钮明确标示等待 M2。请求模型与强度是用户手动登记的计划配置，界面不会将其称为已发现、已认证或已成功调用。

## 2. 使用与验证

需要 Linux、Python 3.11+ 和现代 Chromium。选用可丢弃的工作空间父目录，状态目录须在产品源码和工作空间之外。普通启动不需要 Node 或安装 Python 依赖：

```sh
python3 -B tools/devflower_v2.py --state-dir /absolute/new/private/v2-state --allow-root /absolute/disposable/workspaces
```

工作空间根目录须已存在。可以重复 `--allow-root` 指定多个允许位置。状态目录由服务以 0700 创建，重启使用同一目录即可重开已保存草稿；包含其他应用状态的目录会被拒绝。`--no-browser` 会打印含私人访问能力的本机 URL，不应公开分享。服务仅监听 `127.0.0.1`。

点击右上角文件夹按钮，浏览或输入允许范围内的路径并打开。新建空白工作流后可以立即保存。添加两个 Agent，填写 Provider、模型、三个提示区；从右侧端口拖向下游左侧端口，或在检查器选择下游并点击连接。节点拖动和连接均支持撤销/重做。点击连线可以删除连接。删除 Agent 会一并移除其连线。

检查只返回配置诊断，不访问 Provider。关闭检查器或按 Escape 会保留编辑。切换工作空间/工作流或新建时，未保存内容可选择保存、放弃或取消。保存期间继续编辑不会被迟到响应覆盖，也不会误标为已保存。浏览器关闭/刷新有原生未保存提示；明确放弃后未保存内容不会持久化。保存、重开和状态读取均不创建原生会话。

开发时复用现有工作台锁定的依赖，不引入第二套版本。检查 lockfile 后可在本任务隔离工作树运行：

```sh
npm ci --ignore-scripts --no-audit --no-fund --prefix tools/studio/workbench_ui
node tools/v2/ui/build.mjs
python3 -B -m unittest discover -s tests -p test_v2_drafts.py -v
python3 -B tests/v2_browser.py --artifacts /absolute/new/evidence-dir --chromium /absolute/chromium --playwright-module /absolute/playwright
```

也可用 `DEVFLOWER_UI_DEPS` 指向已安装相同版本的工作台依赖根目录再构建。第三方许可证沿用 `tools/studio/workbench_ui/THIRD_PARTY_LICENSES.txt`，生成 bundle 保留法律注释。浏览器测试使用真实启动器/HTTP/Chromium 与专用空目录，不调用 Provider。截图、trace 和失败记录保存在源码之外；不能作为真实模型执行或独立 C 验收。

## 3. 决策记录

ADR-001/002：同仓隔离 V2 入口和新 state，首期 Linux/Codex。**V2 草稿唯一权威存储是该 state 目录的 `v2.sqlite`；不读写工作空间 `.devflower`、旧工作台数据库或其兼容缓存。**

草稿结构验证独立于原执行验证；保存不补 start/result。执行定义、运行、节点执行、交接、可见请求与 Provider 回执将各自绑定身份，不用草稿版本替代执行定义 hash。M1 已预留 SQLite `launch_intents` 唯一键 `(workspace, intent_key)`，重复请求同 payload 返回同 run ID，不同 payload 拒绝。后续执行必须先成功提交该事务，再启动任何工作进程；M1 没有启动 HTTP 接口。

只通过窄切面复用现有 `WorkbenchStore` 事务/WAL/CAS 和通用 canonical/hash；旧执行验证、真实 Provider 通道、Stable 与旧入口不改动。

## 4. 结论与边界

本入口当前只交付 M1 编辑与持久化子集。模型发现、认证成功、真实顺序执行、交接/实际请求/回执核对，以及取消/重启恢复属于后续 M2/M3；未实现前不声称全 MVP 可用或 Stable。

草稿最多 64 个 Agent、128 条连线、400000 UTF-8 字节；单个提示区最多 20000 字符。保存允许不完整拓扑，但拒绝重复身份、自连接、非法引用与不支持的权限。画布连线交互仅建立无环顺序链。当前只保存只读权限；不会将提示词当作外部工具授权。

## 5. 过程记录

2026-11-07：在隔离任务分支实现独立 V2 草稿服务和画布。实际自验及失败修复证据由交付报告绑定候选；实施者自验与 C 独立功能/UI 评审、D 裁决和用户体验验收分别记录。
