# Codex 多 Agent 复审与条件式 Stable 收尾

## 1. 目标与范围

用户明确本任务不再调用Claude审核，改为Codex多Agent。保留旧Task15/R1–R4及BLOCKED/失败/预算。新任务guided-stable-codex-audit-20261028，最多2轮完整源码审核、每轮2个独立原生Codex会话；基础设施恢复2/2已用。必须完整产品合同、真实合同K、独立硬ACCEPT和固定旧Gate通过后，才可合并/正式Stable/清理已合并闲置分支。用户新增：内置助手默认DeepSeek，设置一次跨重启/工作区/版本保存；重申全页面视觉要求，不能以按钮和配色修改冒充完成。

## 2. 做法与证据

最新源码30b0a6d08c11dc0962adfa0c9af0bfd1dbdd84a5，tree d0767ed371ebd072a2df04423440f6385eb1d54b，298文件，候选SHA256 a3b6a8ebe9fee7e6c299ed34a421dd88d1da5ac9cbcafcfe88c6692787f7dcc9。flow/workbench-guided-stable正在正常同步；[PR22](https://github.com/Silver-Zhang/DevFlower/pull/22)仍Draft，37全量回归/真实验收与新CI待完成。此前36的3项CI SUCCESS保留，不套37。

此前真实原生Codex UUID01a1810f-48a5-7d81-8d09-f515ad972126修复reviewer provider兼容、保护目录held-FD语义和真实任务目标绑定。Root另修扫描上限/首次扫描失败关闭、空Git占位目录、AI新节点任务目标默认与嵌套夹具；署名区分，不冒充全部由原生Agent实施。已有真实顺序/汇合/Router/循环记录保留原身份；不用于新候选通过证明。source28歧义条件循环5轮拒绝和上限失败原件保留。

本次助手连接改为本机私有持久设置；默认DeepSeek独立API，不借用CLI认证；模型栏选择保存默认，连接页面保存后立即采纳新模型。旧显式DeepSeek连接已迁移，原件/密钥/中转配置未覆盖；新Development真实HTTP读回deepseek-flash/medium且不返回密钥，0新增模型调用。

历史source32的完整Python清单FAIL、source34四项浏览器FAIL、source35的目录失败提示竞态CI FAIL与独立运行时占用干扰均留原件，未删断言/改Skip。source35串行工作台132通过，真实Codex六图21调用与DeepSeek四场景通过；这些不能批准36。

source36已完成1177 Python、6宿主、64JS、Baseline/语法/构建字节一致，全部exit0、FAIL/ERROR/SKIP0；九套Chromium/HTTP共251项与独立旧smoke16项也全部通过；21条实际命令均exit0，源码前后不变。真实Codex当前顺序2调用、并发汇合3、条件1、明确文字合同提前退出2通过；五轮拒绝上限在第8调用预检发现本机配置变化后安全FAILED，强十调用断言失败/overall exit1，未跑满五轮，复杂图未启动。旧JSON里提前比较FAILED状态的passed字段不代表强断言通过，另有严格失败注记，不改旧JSON。全部是真实执行语义测试，不把小型问答图当产品K。

原生Codex实施者在K2实际修复只读metadata与保护扫描协调、恢复阶段过早ready和真实多词footer/CSI定位展示。初次guard失败具体条目仍unknown，另外真实无认证隔离HOME的CLI初始化观测到3个临时alias symlink；只作为机制证据，不反推旧失败。协调锁不覆盖所有外部客户端，原保护扫描不放宽。真实Chromium在36恢复同UUID后逐字输入/exit，COMPLETED/exit0且历史保留；共享PTY、占用/接管已实测，突发整串/exit+CR没有退出的负面保留，逐键专项第一个UUID同PTY双端接管/陈旧输入拒绝/detach保持/exit0/再次同UUID ready/停止均通过；第二个UUID接管超时，未通过。独立真实UNIX socket/SQLite反例证明3秒客户端超时后可晚commit再安全unclaimed，原生实施者正最小修统一deadline，不据此虚报全部通过。标准原始适配器最终IMPLEMENTED/exit0是原实施者只读核对的终局收据，不冒充再次修改。

source36实际DeepSeek普通问答和公开源码诊断通过；两次画布提案加入未声明备注字段被严格拒绝，未改变草稿。相同原对话分别收到一次明确修正要求后，两次均COMPLETED并实际apply，完整性错误归零；不是自动网络重试，也未放宽工具schema。所有原始失败保存。

原生实施者随后修统一接管deadline，原报告PARTIAL（工具权限拒绝7项AF_UNIX测试）保留；Root在实际主机执行新deadline与旧终端共42项全部通过，仅另修测试临时目录使其支持只读冻结候选。37已重新freeze并启动完整回归/真实六图；避免在真实流程期间另起原生TUI造成主机偏好配置变化，不更新原operator的旧pin。

## 3. 决策记录

原合同4dd4ae7d…原件保留，首次正式源码审核前追加用户DeepSeek持久化要求，新合同SHA256 cedcf84fa4453b71b7e68287b04cc64ba2c9eaf063de5d8cc3202019e97ccf9e。完整源码审核尚0/2，不偷续旧任务第六轮。独立辅助发现并修正两项：设置保存后仍沿用旧模型、worker丢失自定义设置保护路径。

旧Development的Codex配置指纹已变化，新source32启动拒绝原样保留；source33使用独立新启动配置绑定当前本机配置，不更改旧operator/认证/全局配置，不冒充Stable。旧服务、旧Stable和所有原生记录保留。

## 4. 结论与边界

尚未完成整体验收。同一最终候选的完整浏览器、真实复杂流程、原生终端与合同K、两名原生Codex完整硬ACCEPT和固定Gate仍待完成。main仍e0a0f92，Stable未更新，未合并、未清分支。用户本人视觉验收未代签。Development入口python3 /home/workspace/DevFlower/.local/open-guided-development.py已实际验证，当前source33，仅Development。

## 5. 过程记录

- 2026-10-28：用户切换Codex审核，保存原始Provider授权及新预算，保留旧负面。
- 2026-10-28：修复保护目录真实竞态/删除误拦、Git占位目录和任务目标丢失，冻结各独立候选与真实QA身份。
- 2026-10-28：实现DeepSeek默认与跨版本连接持久保存；保留并修正首次浏览器未重构建失败、完整Python清单失败、独立辅助两项问题及Development配置漂移拒绝。
- 2026-10-28：按实际Sim与Impeccable Operate修正密度/字号/焦点/小屏/专注画布/配置恢复；source35独立逐页20项和18截图，source36重新验证中。
- 2026-10-28：真实浏览器暴露恢复状态与CSI布局问题，由同原生实施者逐次修复并保留负面；冻结并推送36，不把原始突发输入失败改为PASS。
- 原始大日志、截图、API连接、会话与失败原件仅留本机archives和独立test工作区，不进入本记录分支。
