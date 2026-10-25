# 用户实测后的工作台交互整改

## 1. 目标与范围

落实用户8项截图反馈：配置不移动画布、Agent/模型选择、删除草稿、自动连接检查、成本与单Agent路径、实时用户对话、可读数据交接及一键原生终端。合同UX01–UX10见acceptance.txt。独立flow/workbench-user-experience工作树，main基线e0a0f92；现用正式Stable、认证和用户会话保护。不自动合并main或晋升Stable。

## 2. 做法与证据

最新源码HEAD cecbed3878702dcc0e8aa0ce7d25fe99cc0dcb1c，tree d96c9e0240ff97713f2613559a6c9d8d8c06baf2，完整候选dae3b9a7f9fbddfcc6f5fdd6dba332b17c82ffafa9b60c6cac06c533991581d8，194文件。源码普通推送并远端读回，[Draft PR #19](https://github.com/Silver-Zhang/DevFlower/pull/19)。完整宿主650 Python、6隔离、185 Chromium/HTTP、56 JavaScript全部exit0，FAIL/ERROR/SKIP=0；Baseline、6语法、逐字节前端重建与隐私检查通过。当前两个云端workflow/三个检查均成功；之前40188282010的Chromium失败原件保留，未用重跑掩盖：仅改两个测试文件，等待真实刷新完成后保留原可见性断言，等待拦截回调结束后导航。产品运行器/UI字节未变，但完整源码身份已变，因此真实模型及完整自验均按新CF重做，旧回执不改标。命令、实际退出、SHA和源码清单见checks.json/source-manifest.json。

当前完整DevFlower工作副本真实Codex→Claude顺序、精确上游/实际请求SHA、暂停/刷新/继续无重发通过。两Provider新聊天/刷新保留回答、一击同UUID原生终端、双写拒绝、关闭视图保留进程、重新接入不重复启动、明确关闭保留历史通过。当前真实Codex TUI执行只读工具读取README首行成功，同UUID用户问题/回答进入默认时间线并刷新保留。合成与真实模型分列，均为Agent代操作，不是用户本人验收。

旧候选真实缺陷和探索失败均保留：PDEATHSIG随HTTP创建线程结束误杀CLI（exit-15）改为服务进程身份监视并仅停止自有组；Escape被页面截走、重复嵌套隔离无法实际读取、原生自由问答默认时间线遗漏均复现/修复/重测。另有worker启动失败且无原生步骤后显式取消、测试脚本选错同名会话监视UUID、两项UI等待/Markdown断言错误；确认真实完成后仅按实际UUID读回，不盲目重发问题。原始日志/截图/JSONL/HOME只留本地archives。

独立Claude R1原始NEEDS_CORRECTION，按旧草稿归属、撤销重验、目标activation与报告费用四项意见修复；原五finding包括MA-05在后续全部CLOSED。aa5a旧候选R2/R3原始ACCEPT_WITH_LIMITATIONS，十合同PASS、八finding最终CLOSED，各自旧固定Gate实际exit2，仅因非硬ACCEPT。R2的57项针对Python中56通过，1项因嵌套namespace在断言前受限，未改Skip；56 JS及独立归属/历史反例通过。按审核者命令由正常宿主observer对同源补跑3项防写负例，全exit0；R3独立确认补证缺口关闭，执行者明确是Codex桌面宿主而非Claude。报告均按原字节保留。

新dae3候选R4原始最终ACCEPT_WITH_LIMITATIONS，10合同全部PASS，8finding全部CLOSED。固定旧Stable Gate（SHA12f0b6aa…）真正执行BLOCKED，实际exit2。正式技术验收仍因非硬ACCEPT而阻塞，不能把合同PASS推导为Gate通过。旧报告不作为新候选批准；新Controller未自批。首轮外部采集器曾因字段错误而未调用Gate，原记录保留；修正后确实执行了旧Gate并exit2，未将采集器失败冒充Gate结果。

## 3. 决策记录

用户8点作为实施范围；沿用Codex gpt-6.1-sol/xhigh、Claude claude-opus-5-5[1m]/high。实际物理后端/生效effort无证明时unknown。目录发现和登记不授予执行权；把必要的精确配置诊断并入真实确认，保留300秒、失败/撤销显式重试、CAS和未知不重放。默认可读对话，原始A–F/身份留高级；仅Provider公开摘要，不编造隐藏推理。

最多5轮独立源码审核、2次基础设施恢复；实际4轮、1次R1同UUID超时恢复。R3补证是新源码轮而非偷加基础设施重试；R4是CI测试修正后的新候选独立审核，不复用旧有限接受。源码送审Draft依据现有允许隐私检查后发布未最终批准候选的授权，不覆盖BLOCKED Gate、不等于业务自动交付或Stable晋升许可。

## 4. 结论与边界

实现/自验/真实操作已完成，独立原始最终ACCEPT_WITH_LIMITATIONS，固定Gate BLOCKED。正式技术验收仍因非硬ACCEPT而阻塞，不能把合同PASS推导为Gate通过。PR保留Draft，不合main、不发布、不替换Stable；现用Stable仍accepted-cfea19cac598/main e0a0f92。用户界面体验需要本人实际试用，不能用Agent代操作替代。开发档案继续按project/devflower同步；每次Studio运行自动提交project分支仍未实现。

新体验启动：`python3 /home/workspace/DevFlower/.local/open-ux-development.py`。入口已实际验证当前CF dae3b9a7f9fb、Development、HTTP和服务进程，页面http://127.0.0.1:33741；私有登录能力只由本地启动器传给浏览器，不公开。这里是独立完整产品测试副本，现用Stable入口`python3 /home/workspace/DevFlower/.local/open-devflower.py`保持原版。

打开文件夹→直接开始对话或选择单Agent/双Agent示例→配置Agent/模型/模式/提示词→确认一次范围并开始。点节点看任务/回答/当前上游，点连线看实际传递，高级看完整请求/回执；原生终端直接接入同一会话。删除只删当前工作区草稿，历史运行/原生会话保留；无可靠归属旧草稿不跨工作区删除。多Agent会增加真实调用，不保证省时省Token；显示费用仅为Provider报告。Provider无逐字流式内容时显示真实进程/等待输出，不用动画冒充思考。

## 5. 过程记录

| 日期 | 事件 |
|---|---|
| 2026-10-25 | 核验远端/本地、建立独立任务/合同/分支，保护Stable与用户新会话。 |
| 2026-10-25 | 完成8项整改，真实终端故障复现和修复；旧失败保留。 |
| 2026-10-25 | R1 NEEDS_CORRECTION/Gate exit2，Native Codex定向修复并冻结新候选。 |
| 2026-10-25 | aa5a完整回归/真实模型通过；R2/R3有限接受与旧Gate拒绝保留。 |
| 2026-10-25 | Draft #19首次CI发现两测试竞态，最小等待/清理修正，冻结dae3并重验。 |
| 2026-10-25 | 当前完整650/185与双Provider新鲜验收、云端CI通过；R4原始ACCEPT_WITH_LIMITATIONS，旧Gate BLOCKED；源码/档案分别同步。 |
