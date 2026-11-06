# DevFlower V2：顺序 Agent 工作流

这是隔离的 Development 入口，首期为 Linux 与本用户已安装、已登录的 Codex CLI。V2 草稿唯一权威存储是私有 state 目录的 `v2.sqlite`，不读取旧工作台数据库或原生用户历史。空白、不连通和未配置的草稿可以保存；保存不会调用模型。

## 使用

```sh
python3 -B tools/devflower_v2.py --state-dir /absolute/new/private/v2-state --allow-root /absolute/disposable/workspaces
```

需要 Python 3.11+、现代 Chromium，以及真实执行所需的 Linux 原生隔离支持。状态目录在源码和工作空间之外，由服务以 0700 创建。重启使用同一目录。可重复 `--allow-root`；`--no-browser` 打印含私人访问能力的本机 URL，不应分享。服务仅监听 `127.0.0.1`。可用 `--codex-cli /absolute/installed/launcher` 明确选择现有 CLI，不复制认证或更改账户配置。缺少 CLI 或账户配置不符时仍能编辑与保存。

打开允许范围内的文件夹，新建工作流并添加 Agent。在检查器选择 Codex，点击“刷新模型目录”，再选择真实目录中的模型和强度，填写初始化、每轮及输出要求。目录来源、时间、过期/失败状态独立显示；登录状态和目录不代表推理成功。保存的不可用选项会保留并在启动时拒绝，不静默替换。连线只支持一条无环顺序链，可拖动端口或选择下游。撤销/重做、保存/重开、未保存切换提示和面板关闭保留编辑延续 M1。

明确点击“启动”或“保存并启动”才授权本次模型调用。每个 Agent 使用独立的新原生会话；首期为 Ask 只读提示词模式，禁用文件/命令工具、Skills、子 Agent、浏览器和 MCP。CLI 可能保留询问用户工具，阻断时如实失败/待核验，不自动授权。启动时仍核验原生账户、CLI 映像、能力和权限；不会因发现成功跳过这些检查。

运行视图显示冻结版本和真实节点状态，不修改草稿。节点检查器先展示回答、实际 DevFlower 可见请求及发送/终结证据；技术身份与 Provider 回执可展开。可见请求包含适配器格式要求，并非服务端全部上下文。请求与实际模型/强度分开；原生未报告的实际字段标为 unknown，进程 exit0 不能替代关联终结回执。点击边查看下游实际持久化输入及哈希，包含执行定义的截断/包装；不把上游全文或私有回执冒充下游输入。运行历史只来自 V2 状态，重开结果只读，不恢复或重发原生会话。点击“编辑草稿”回到原编辑内容，后续编辑不会改变已有运行。

保存响应丢失时保持当前编辑与 dirty，通过成功的 scoped 列表核对准确提交快照后再决定重试；服务端明确错误保留具体信息。启动响应丢失时，浏览器按环境/工作空间持久保留原 intent；刷新、切换草稿后“核验本次启动”仍使用原 payload/key，不发送后来的新编辑。服务器同键同 payload 只返回原运行，同键不同 payload 拒绝。运行读取丢失只查询原运行；关闭页面不是取消。缺失/矛盾终结证据或死 worker 投影为待核验，不自动重放。取消与重启恢复按下节证据投影处理；实施自验、C 独立验收与最终真实候选取证分开记录，当前不声称 Stable。

## 身份与窄复用

草稿结构/大小/类型/归属/CAS 独立验证，保存不补 start/result。启动将允许的链编译为独立不可变 definition（含草稿身份/版本、执行图、Provider 绑定与源码清单），事务提交 definition、run 和唯一 `(workspace,intent_key)` 后才 spawn worker。worker 由真实 pipe 闸等待持久发布自身进程身份再执行；EOF/超时不产生业务调用。运行、activation、独立 session binding/native UUID、request、handoff、PROCESS operation、input_sent 和终结 receipt 分别关联，未知不推定。

复用 `workbench_graph` 执行验证/请求/交接、`Scheduler` 调度与 activation、`Journal` 请求/进程/实际发送证据、`LocalCodex` 新会话及原生能力/权限闸。V2 不实例化旧 WorkbenchRuntime、SessionRuntime、ProjectFiles 或原生历史接口。共同适配器仅补终结事件观察，Scheduler 仅保留已发送但未确认状态。

## 开发验证

复用现有工作台 lockfile/dependencies；没有第二套依赖版本。生成物沿用其许可证，bundle 保留法律注释。

```sh
node tools/v2/ui/build.mjs
python3 -B -m unittest discover -s tests -p 'test_v2*.py' -v
python3 -B tests/v2_browser.py --artifacts /absolute/new/editor-evidence --chromium /absolute/chromium --playwright-module /absolute/playwright
python3 -B tests/v2_execution_browser.py --artifacts /absolute/new/synthetic-evidence --chromium /absolute/chromium --playwright-module /absolute/playwright
```

`DEVFLOWER_UI_DEPS` 可指向现有相同 lockfile 的依赖根。原编辑 Chromium 与新 HTTP/合成执行故障用例不调用真实模型；它们不能替代真实调用或 C 独立验收。真实调用的固定候选、指令、原生终结/请求/交接和调用计数保存在外部交付证据中。所有失败、日志与截图保留；trace 确定性脱敏 Bearer/初始 fragment，保留原文件 hash 与脱敏说明。V2 CI 仅触发 integration 分支或手动运行，严格使用合成执行，不读取真实账户或调用模型；旧 V1 断言与门禁未删除。

草稿最多 64 个 Agent、128 条连线、400000 UTF-8 字节；单提示区最多 20000 字符。执行仅支持完整无环顺序 Agent 链和只读权限，提示词不扩展外部工具授权。


## 取消与恢复

运行视图的“取消本次运行”先持久记录取消请求，原 worker 通过既有控制检查停止后续调度并停止自有 CLI 进程。界面区分“取消已请求”“本机调度已取消”“待核验”和已核验的 Provider 终结。请求已发送但缺少一致终结证据时保持结果未知；本机进程停止不证明 Provider 未执行，也不恢复或重发。已完成回执仍可阅读，下游未执行不会补造发送或结果。

服务进程退出与关闭浏览器不同于取消。使用原 state/allow-root 重启服务，不产生新 worker 或意图；完整 host/boot/PID/start/PGID/SID 匹配的 worker 可以继续。服务不接管活 worker 的锁。原 worker 已死时，仅在 PROCESS 与 activation/operation 归属及全部进程身份匹配后，取消操作才会请求停止残留自有进程组；身份不明则保留未知，不发信号。列表、详情与节点从同一 SQLite 快照和相同证据规则投影，不会只凭 PID 或进程退出认定 Provider 结局。

服务每次重启生成新访问凭证，须从新启动入口重新打开。使用明确 `--port` 可维持原浏览器 origin；新凭证即使在当前标签形成 fragment 导航也会重新加载认证。端口改变时，原 origin 的浏览器选中状态不迁移；从“最近工作空间”选择原目录，再在运行历史显式重开原 run。未保存编辑仍服从浏览器关闭/刷新提示，不能将其当作已持久草稿。

重开只读核验冻结 definition、原请求/发送/进程/终结关联，以及有界、无符号链接的 completion 文件与 hash。若已完成文件写入但 activation/run 提交前 worker 崩溃，只有 completion 文件、原 request/process/terminal 与已存 hash 全部一致才在只读投影中恢复为已完成。若 activation hash 尚未提交，还必须存在独立的 completion-proof.json；两个文件均经临时文件 fsync、原子替换和至 state 的目录 fsync 后才发布 activation。缺 proof 的中间窗口保持未知，不补写 proof。该证据检查同一受信 state 内的损坏/一致性，不宣称抵抗拥有 state 写权限并同时改两文件的攻击者。保留原始日志，不运行调度器。文件缺失、损坏、关联矛盾、额外未知进程或异常恢复提示则继续待核验；不以其他节点的完成掩盖未知。没有“继续执行”或自动重放功能，需要新任务时必须由用户另行明确启动。

新增测试：`test_v2_recovery.py`（身份/回执负例）、`test_v2_process_recovery.py`（真实本地服务/worker/子进程、合成 Provider）、`test_v2_handoff_privacy.py`（非功能合成秘密的已知格式过滤，保留非秘密 marker 正对照）与 `tests/v2_recovery_browser.py`（真实 Chromium/进程故障，零真实模型）。这些不能替代最终候选的真实 UI 单/双节点调用。V2 CI 的原生 namespace 探针在单独 Ubuntu host job 完整运行，浏览器 job 使用固定容器；两 job 都必须通过，历史失败保留。
