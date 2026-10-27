# 本机原生会话与跨端接续

## 1. 目标与范围

用户明确要求 DevFlower 不再额外隔离运行。新会话使用本机原 HOME、`.codex` / `.claude` 及已批准的中转配置，按真实 UUID 查看和接续；不另建运行 HOME、不复制原生历史冒充同步。保留 CLI 权限控制、会话占用、候选与交付门禁、旧会话及 Stable。不合并 main。

Task14 的独立合同 L1–L6 与最多5轮源码审核/2次基础设施恢复单列；Task13 及旧候选的负面裁决不改写。

## 2. 做法与证据

当前源码 `f67737a9016b45d5a64896445e322ba3e32c926b`，tree `ae5512038584aa130c1c99bbc2093cb0d0194065`，完整252文件 SHA-256 `3144d2e7637010ad5d864c49f44566f765a376f3a9095699e755affb556796eb`；已正常推送 PR21 Draft，依赖 PR20→PR19。全部身份及原始证据散列见 evidence-summary.json；原始日志/截图/原生记录只在本机 archives 与指定测试目录。

- 当前同一候选：965 Python、6宿主隔离兼容、233合成 Chromium/HTTP、59 JS，均 exit0 / FAIL0 / ERROR0 / SKIP0；Baseline、6语法命令、2构建资产字节一致与完整隐私检查通过。
- 当前真实 Codex / Claude 各新建一次、按原 UUID 恢复一次，共4次实际回合；均 exit0、保留上文。Codex gpt-6.1-sol/xhigh；Claude claude-opus-5-5[1m]/high；物理后端及有效 effort 无可确认字段时 unknown。
- 实际桌面 read_thread 读取当前原生 Codex UUID/turn/问题/回答，未复制或导入；桌面状态 notLoaded，不证明已打开窗口实时刷新或活动回合热接管。
- 当前 Codex 两个官方 app-server 竞争真实拒绝；Studio 第二写者也拒绝，观察仍可读。仅在原客户端实际退出、原生锁确认释放后接续成功。unsubscribe ACK本身不足以证明释放。
- 同一原生会话的外部续聊曾 raw-visible=true / default-visible=false，现保留 Provider 的 startedAt，并按真实 native turn ID去重；当前 Chromium 默认对话直接看到外部续聊，复用旧权威库和原生库，无复制。新增同秒、无时钟、重复回合反例。
- F4 原审核独立反例证明链接锁可能改写无害凭据样本；当前以固定目录描述符、NOFOLLOW、单链接/文件类型/属主检查修复，观察不创建或改写文件。原脚本的 Codex 受控重测拒绝链接且样本不变，不冒充新独立构造。
- 当前原生终端：Codex ready，同 UUID/HOME/原生子PID/PTY已验证；Claude 同 UUID/HOME/原生子PID/PTY正确且无配置根覆盖，但60秒后仅 output，未确认 ready，测试脚本 exit2。此未通过项与合成全绿分列；关闭两者仅停止本任务终端进程，历史保留。

旧 r3 的真实 Codex→Claude 顺序流程2调用、r5的300秒失败及随后310.629秒/exit0慢中转成功均保留，不能代替当前候选完整验收。

## 3. 决策记录

取消额外隔离只改变运行方式，不意味着批准交付或允许双写。Codex 使用 CLI 自身只读/工作区写权限；Claude 当前仅 Ask/Plan。本机模式的额外Skills/子Agent和自动交付仍禁用，不能套用旧外层保护证明。旧运行与旧隔离 HOME 不静默迁移；不覆盖全局认证或重启桌面。

慢中转允许单轮1800秒的受控等待，不无限重试。普通独立对话当前仍有300秒超时上限，长等待补证不等于该UI上限已修复。

## 4. 结论与边界

实现、上述自验及真实原生回合已完成；第4轮独立审核已结束，原始裁决 NEEDS_CORRECTION，当前候选无硬 ACCEPT/最终Gate通过，不称正式验收或用户本人验收。

原始 R1 BLOCKED（1137.009秒/exit0，CF7530）；R2 NEEDS_CORRECTION（1125.264秒/exit0，CFd279）。F1观察GET写入已独立 CLOSED；F2任意不受管Claude CLI原子争用仍 OPEN；F3固定Gate及根交互实施的原生IMPLEMENTED终局回执仍 BLOCKED；F4在R4实际独立反例后CLOSED；R3曾引用未生成的独立日志，已标为证据不完整，原报告不改写。原始结构化报告逐份保留。

推荐入口 `python3 /home/workspace/DevFlower/.local/open-devflower-native.py`，当前 Development34915 / CF3144d2e76370，已实际校验进程/身份/私有API与旧Stable固定树；是本次正在运行预览的入口，不承诺重启机器后自动启服务。打开工作文件夹后选择命名会话；对话与原生终端指向同一UUID。切换写入端先结束原写入客户端，确认占用释放后再接续；关闭视图本身不等于退出原生进程。

R4实际执行13项独立锁/投影检查及另3项投影检查，均无失败；首次脚本自身fixture格式错误（exit1）也保留。原始R3报告引用不存在的独立日志，不能追认为有效独立执行。R4新执行和原报告的引用笔误均保留原意，不回写历史。

F2现有真实反例：两个不受DevFlower锁管理的Claude CLI同时恢复同一新建QA UUID，开始tick相同、PID不同、均exit0并返回各自回答。可保证的受管接管与任意外部CLI不能混为一谈。F3固定旧Gate已实际运行，exit2 / BLOCKED，首先拒绝缺失的根交互实施原生终局回执；不伪造IMPLEMENTED。R4 L3 FAIL、L5/L6 BLOCKED；没有正式技术验收。

云端contracts和Studio通过；Chromium首轮在终端prepare响应缺少data时测试抛TypeError。原失败已保留；本地同候选全109项及定向1项均通过，尚不能确定云端响应异常原因。仅重跑一次云端job，依赖安装阶段约20分钟后被取消，浏览器步骤未执行，产物上传因无结果失败，F5保持OPEN，不称CI全绿。不消耗第5轮要求相同源码改判。

本机Codex配置测试中从原hash发生变化（当前默认terra/high），旧预览正确拒绝继续；未改写原配置。保留旧启动绑定，检查无活动任务后仅停止自有闲置QA预览，重建一次当前配置绑定并复用原权威库/原生库，无复制。已批准显式QA模型/权限不变，旧执行验证不复用；新配置下Codex和Claude各一次真实原UUID恢复、当前浏览器2项与入口检查通过。此Development启动绑定不等于正式Stable/Gate批准。

当前 main、旧 Stable 和旧入口均保留；不自动合并或晋升。

## 5. 过程记录

| 时间 | 事件与真实结果 |
|---|---|
| 2026-10-27 | Task13长等待恢复2/2取得 NEEDS_CORRECTION；F1项目归档0→1，修复后受控重测0→0。旧报告/预算保留。 |
| 2026-10-27 | 首次测试子进程生成字节码导致冻结拒绝；仅自有字节码移入本地证据，继承禁用字节码后重跑。Path类型、旧精确合同和过早浏览器定位错误均修正，旧失败保留。 |
| 2026-10-27 | 本机模式原生UUID互通和真实顺序流程完成；R1 BLOCKED。 |
| 2026-10-27 | 修复CLAUDE_CONFIG_DIR导致配置位置变化/首次设置再现，补凭据轮换指纹与跨工作台锁；r5全959通过，R2发现真实F4并 NEEDS_CORRECTION。 |
| 2026-10-28 | 修复原生续聊默认视图与F4锁链接；r7全965通过，当前真实4回合/原生竞争/默认UI通过；Claude交互终端ready仍未确认。已推送f67737a；后续R3 BLOCKED、R4 NEEDS_CORRECTION均保留。 |

2026-10-28：R4原始NEEDS_CORRECTION（775.05秒/exit0），真实独立13+3检查通过；外部Claude双写反例成立，固定旧Gate exit2。源码f67737a已推送，云端浏览器首次失败/一次重跑保留；停止于Draft，不继续请求相同源码ACCEPT。
