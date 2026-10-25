# 会话操作、原生终端与 AI 配置助手

## 1. 目标与范围

落实用户六项截图反馈：连接以Agent为主、Claude初始化与回答、直接会话操作、分组折叠/整组清理、其他模型UI配置、AI自动配置及外部提案接口。新任务20261025_04，run01cb7ddbee8a27ef53cd7dae8014b89a；原Task03的R1 NEEDS_CORRECTION、R2–R4 ACCEPT_WITH_LIMITATIONS及旧Gate不改。

基线cecbed387870。最终源码HEAD `2df889c381eeb6607578f8659fe270831181c4d6`，tree `13c2440320b659bc612e42847fcf5c89e33e25fd`，203文件CF `b77ec0adccee5e6912371641b71dd7b8ec6279850dcd58e00838fd70f4970a7f`；完整清单candidate-manifest.json。清单中的HEAD是冻结前提交，最终提交tree/CF与冻结、测试、独立审核相同。合同见acceptance.txt，原SHA5cb10d9a31d8c603db4a07151dfdf39fff32fcda3893e1061cfe3d03d931c352。

只交付Draft PR19；不合并main、不升级Stable，不动CodeWeave/RMC，不删用户原生历史/认证/旧安装。

## 2. 做法与证据

- 主连接文案只显示Agent，模型/诊断计数保留在详情。普通聊天使用原生可见文本；旧结构化执行结果保留并标来源，不写回假原生消息。
- Claude只继承已完成的显示偏好，首次信任/认证/权限仍由原生CLI提示。Codex固定既有CLI并关闭启动升级菜单。两个Provider实际同UUID恢复旧回答、继续真实问答、拒绝第二写入、关闭视图保进程、重接无重复、明确停止后历史保留。
- 会话操作直接显示按钮，Provider/Workflow/节点组可折叠记忆。整组固定成员/版本，取得全部HOME锁后复核原生身份、排队/外部写入，原子移入可恢复回收站；不永久删除原生文件或运行。
- 实际目录的其他模型可在UI配置。登记不是执行许可；精确Provider/模型/effort/模式/Profile/环境的原生验证、TTL、撤销和CAS保持。正常明确操作内按需检查连接。
- AI助手使用已连接模型：描述目标→生成严格提案→预览应用草稿→可撤销，不能自动运行/交付。支持顺序/只读并行汇合；外部HTTP或文件用同一封闭schema，无任意endpoint/key/命令/授权字段。
- 发起意图/取消/发布/证书按事务排序。物理Popen后先记录已启动事实，日志失败杀死并回收自有进程组；恢复性SQLite异常进入同一受控失败处理。保留未启动取消、真实失败撤销和精确记录CAS。占用检查使用实际可执行文件/脚本、UUID/HOME与存活身份，覆盖Claude版本进程名、别名和解释器；已知不透明活进程仍拒绝。

当前同候选完整Python737、宿主隔离6、普通Chromium/HTTP193、JS56，全部退出0且FAIL/ERROR/SKIP=0；Baseline、6项语法、重建字节一致性、203文件隐私检查通过。87定向含真实SQLite引擎/实际无害进程故障；正常宿主另按原审核命令21项通过，和总回归有重叠，不相加。命令、时长、原生退出码和hash见validation.json。

真实模型单列：AI生成→应用→Codex gpt-6.1-sol/xhigh与Claude claude-opus-5-5[1m]/high顺序、实际交接/请求hash匹配；双Provider普通聊天、同UUID终端问答/生命周期及进程扫描独立于HOME锁的实测均通过。模型是请求参数，物理后端/生效effort未提供时unknown，不冒充服务证明。

R16原观察因同名标题first选中Claude而超时，原Codex请求已COMPLETED。受控观察按Provider+实际history handle读取原操作，未新建/重发业务消息，然后完成原UUID终端测试；proof明确observation_continuation，不称初次观察通过。原失败及两个producer均保留。

| 独立源码轮次 | 原始Claude裁决 | 固定旧Gate |
|---|---|---|
| R1 | NEEDS_CORRECTION | BLOCKED |
| R2 | NEEDS_CORRECTION | BLOCKED |
| R3 | NEEDS_CORRECTION | BLOCKED |
| R4 | NEEDS_CORRECTION | BLOCKED |
| R5 | NEEDS_CORRECTION | BLOCKED |

原始结构化review-r1–r5.json、gate-r1–r5.json原样保存。固定旧Gate SHA12f0b6aaf8d257e1ce0b87346e2f328089ab28fc96c52691719b7f28787630ab；被审Controller不审批自身。原生Codex实施核验另列：r15真实BLOCKED见implementation-r15-BLOCKED.json，当前同角色核验不等于独立Claude。

## 3. 决策记录

授权来自本轮六项反馈及持续实施/测试/独立审核/开发PR交付范围。实际5轮源码审核，基础设施恢复2/2；R1/R2初始监督超时和按明确同UUID/HOME/cwd恢复保留，未偷开第6轮。R3/R4真实新缺陷均定向回修，每次新CF重验，不重写旧裁决。

仅本地原始大日志、截图、原生JSONL、会话HOME和认证：`/home/workspace/DevFlower/archives/devflower-session-config-20261025`。项目分支只提交摘要、清单、必要机器记录/结构化裁决；一任务一份五节README。工作区和旧服务/安装不删除。

## 4. 结论与边界

原始最终裁决 `NEEDS_CORRECTION`；固定Gate `BLOCKED`。技术验收阻塞：SC08-POST-SPAWN-SUPERVISOR-JOURNAL-FAILURE-ORPHAN。PR19保持Draft，源码正常推送并读回，main未合并、Stable未更新；所有CI成功。项目记录交付的实际远端读回另存本地delivery.json，不以文档提交SHA冒充源码SHA。

剩余唯一必需P1：`SC08-POST-SPAWN-SUPERVISOR-JOURNAL-FAILURE-ORPHAN`。触发条件为成功创建子进程后，PID/所有权信息写入失败；独立无害子进程反例发现进程仍存活但未跟踪，配置任务的SQLite分支还可能停在QUEUED。涉及ConfigurationAssistant.start、NativeTerminals.start、WorkbenchRuntime.spawn、SessionCenter.launch四处外层启动；不同于已修复的内部orchestration.process日志边界。需要逐处确保失败时准确终止/回收自有进程组，并完成或保守标记任务。5轮/2次预算已满，本轮不继续代码修改或第6轮审核。固定Gate实际退出2，见validation.json的exit-readback。

入口：`python3 /home/workspace/DevFlower/.local/open-ux-development.py`（加`--check`核验）。这是独立Development预览/独立数据，旧工作台与原生会话仍在原位置。打开文件夹→开始对话选择Agent，或画布“AI配置”描述目标→预览应用→填写任务→明确开始。会话菜单直接选动作；分组标题可折叠，Workflow标题旁可整组移入回收站。关闭视图保进程，“关闭此终端”才结束该CLI。首次原生目录信任/权限仍需实际输入。

审核恢复：`python3 /home/workspace/DevFlower/.local/resume-session-config-review.py`（加`--check`核验）。使用原HOME/cwd和明确UUID，拒绝存活写入者；不是在预算耗尽后继续新增正式审核。原生记录位置见validation.json，不复制整个全局HOME、不用--last。

剩余限制：用户本人体验未验收；无离线模型权重或任意BaseURL/key配置页；复杂循环/交付仍在画布配置。Studio每次运行自动提交project分支尚未实现，事件DB/原生会话与Git研发记录并存。本轮未替换正式Stable accepted-cfea19cac598；原部署因Codex OAuth文件自然刷新导致旧依赖指纹不符，目前HTTP400拒绝启动，不能称Development为已晋升Stable。

## 5. 过程记录

2026-10-25立项并继承PR19已有画布/会话改进。后续保留原生Codexr2/r4/r6 BLOCKED和原Task03有限接受。

2026-10-26：R1指出取消后仍发CLI和批量拿锁后未复核外部写入；R2继续指出最后读取→发起间窗口；R3关闭发起窗口后指出异常撤销的取消快照；R4关闭旧问题后复现物理启动但日志失败的误分类。外部只读观察另复现Claude2.1.303进程名漏报（late-finding-r4.json）。

r12浏览器刷新入口闪失，固定慢请求反例前失败后通过；r13云端旧画布保存检查失败，固定响应顺序复现旧响应抢身份，最新请求归属最小修复，全部旧断言保留。r10早期异步准备首错确切原因仍未证实，失败和复查均保留，不写成环境已定位。

r15修复物理启动/清理和占用检测后，原生Codex实际发现SQLite异常类型遗漏并BLOCKED；保留91项通过及单项真实SQLite失败。r16只补sqlite3.Error处理及2项真实引擎反例，87定向/737全回归和当前真实模型重新验证。一次观察harness同名误选通过原操作续读解决，无业务重放。最后第5轮与Gate结果如上，用户本人体验独立待确认。
