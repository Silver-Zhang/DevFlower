# 工作区、会话与助手运行体验修复

任务 `20261027_03_workspace-session-usability`，独立于已结束的 Task11。状态为“阻塞”指最终源码审核/Gate未通过，不代表本轮功能与自验未实施。

最终更新：源码70929224355f05c0c01976e289c22c490cb67b5e，tree34faa15ffda94ca649328e1ee2e05bca62630128，CFcad0d605eedeaeea1d089aec89c4e67cb9ae5b975d280f6c75a7fa3b1b7111f5，241文件。补齐“目录成功但漏掉1M默认模型的强度选择”和“目录过期自动刷新”；首轮089375负例确实失败，最终真实max配置选择成功但未授予执行权限，未冒称max真实推理通过。

最终同候选922Python、6隔离、231Chromium/HTTP、59JS均exit0、FAIL/ERROR/SKIP0，Baseline/语法/构建/隐私及两个最新CI成功。19项针对性测试已包含在922中。真实助手+Codex→Claude任务5c294bca7bcc420e8fa40898a914335b完成。观察与服务被会话中断终止后，使用原候选/原存储恢复读回同一run，恢复时新增模型调用0；中断中的Python/浏览器原日志保留，完整重跑922/107成功。原始补证在cache-followup子目录。

新候选尚未独立审核：旧两轮超时属于089375，不能复用为新代码结论；原2次恢复预算不重置，未再调用审核。固定旧Gate对当前缺报告实际BLOCKED/exit2。保留Draft，不合并或晋升。此前各阶段原记录如下，不能把旧测试/裁决当作新候选审核。

## 1. 目标与范围

处理用户2026-10-27七点反馈：助手记录位置、整组会话删除、按工作流分区浏览、Claude强度、助手发起运行、选中正常项目后可运行，以及指定测试目录。源码基线742c61f4/tree7e1992f7、工作树开始干净；main e0a0f92f未变。PR20仍依赖PR19，不合并或晋升Stable。

首轮候选源码 f22ae7582aa6ff6cf37507e93853cbcc54c41bb7，tree7d5cc7b76ec2a32e287b739f5701bc40acd6160e，241文件CF089375e58697a5b89ff10889dd5f2f1dc8fbc48e201f615ad195b59a2d6099b6。冻结来自旧HEAD，提交后tree/完整文件身份一致；已正常push并fetch读回同一commit/tree。项目记录不进源码分支。

## 2. 做法与证据

- 助手完成对话可读副本保存在所选项目`.devflower/assistant-*.json`；设计/模板在library.sqlite；实时权限/进程/会话占用在独立本机记录区。受保护或不可写目录用host记录区，原生HOME不复制进项目。实际测试目录已产生assistant/run/session阅读副本和library.sqlite。
- saved:阅读副本与原生句柄分路；整组清理固定成员/CAS/事务回收站，混合成员及并发/忙碌反例，不改原生历史。副本恢复入口可用。切换分类立即清除旧列表，避免旧卡片错误操作。
- 历史按工作流设计身份→历次任务→Agent节点分区；同名不同设计不混合，阅读/刷新不重发。Claude当前CLI帮助支持low/medium/high/xhigh/max请求，Provider能力、CLI语法、登记及真实执行准入仍分开；实际backend/effective effort unknown。
- 选中正常项目建立本机device/inode/operator/candidate绑定范围，浏览/复制元数据不授权，目录替换失效；支持非Git普通目录。系统/认证/运行器/Stable保护不放开，Git交付仍要求真实Git。用户指定/home/workspace/claude_workspace/DevFlower测试实际execution_allowed=true，旧.devflower保留。
- 内置助手真实模型生成request_workflow_run，绑定当前画布/任务；确认预览后才允许现有运行授权。只问答不提供运行工具；不代签、不执行模板注入、不同时修改画布并运行。

最终同候选实际命令：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`921、`python3 -B tests/host_isolation.py`6、workbench_browser106+其余既有Chromium119+新交互5=230、`node --test`59；exit0，FAIL/ERROR/SKIP全部0。Baseline、6语法、构建及两份bundle字节一致、241文件隐私检查通过。同HEAD两个GitHub CI成功，链接在PR和evidence-summary.json。18项新针对性测试已包含在921中，不能重复相加。

旧基线运行17反例2FAIL/8ERROR；中间候选新增CAS反例实际NameError，最终修复并通过。首完整920的2条旧行为预期冲突已改为“显式选中但不启动/不授予模型准入”和“只增加运行预览工具”；未删测试或改Skip。初始新浏览器脚本标签/分组定位错误、CAS草稿脚本漏read参数均保留；最终同候选新浏览器5/5。

真实模型与合成测试分列：内置助手1次直接API操作、2次原生连接检查、Codex→Claude两业务回合COMPLETED。run345e4f6eeeef5c9cbb7ea6f0f2e481de；Codex UUID01a17bb8-2ed2-7100-a193-aed61e77ee6a，Claude UUIDee93e714-d5e0-464e-b5e1-866bc81c4878，实际exit0。上下游activation/run绑定、逐字回答、请求哈希与input_sent哈希实际一致。执行者为本地Codex代理操作，不是用户亲验，也不等于完整产品K。

## 3. 决策记录

本轮依据用户七点反馈直接实施，工作流实测在指定目录的自有子工作区；NativeHOME/认证/实际权限保存在独立archives，避免放在选中项目内引发保护目录重叠。正常选中目录不要求手改operator JSON；当次真实运行确认、模型准入及私有数据边界保留。最多5源码审核、2基础设施恢复，不续开Task11旧预算。

真实Claude Code审核：R1 UUIDcb546a04-4259-406a-a8fe-44fc5fadb79b，300秒timeout/exit-15；R2 UUIDe64e8b71-c4e3-4820-a479-429e5e8ef39a，180秒timeout/exit-15，均verdict=null、源码与认证未改。R1读取含生成bundle的大diff，R2改为源码差异与有界日志，仍无最终回执。R2独立设计4类反例，因非hex request_id提前退出；Codex受控runner仅把r*32改为a*32后4/4通过，原件保留，不能写成Claude ACCEPT。

额外NativeCodex实施核验180秒超时；明确UUID01a17bbb-0b07-7b43-9f6c-ca43bf8e7ada恢复90秒仍无最终回执，原日志保留。恢复预算2次已尽，不继续外部模型调用。四个审核/核验进程组实际均不再存活，未杀其他进程。固定旧Gate真实exit2/BLOCKED，因review report=None在schema拒绝；实际业务成功不能代替源码批准。

## 4. 结论与边界

已实施、自验通过、真实两节点业务流程通过、源码和研发摘要同步云端；独立源码最终裁决缺失，Gate BLOCKED，PR20保留Draft。旧Task04/10/11负面裁决、SC08及复杂产品闭环尚未独立关闭，不改写历史。用户本人体验验收仍待本人操作；main/旧Stable/原生会话/旧版本不动。

本机入口：`python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`，实际`--check`通过，Developmentcad0d605eede，http://127.0.0.1:40893（私有凭据URL不入库）。打开测试或正常项目目录，右侧AI助手提出需求；运行建议点击“查看并确认运行”，历史进入“整轮对话”后按工作流/任务/节点查看。详细操作见源码docs/WORKBENCH.md。

原始日志、截图、脚本、失败结果在本地/home/workspace/claude_workspace/DevFlower测试/.devflower-tests/20261027-workspace-session-usability；原生HOME及审核证据在/home/workspace/DevFlower/archives/devflower-workspace-usability-20261027。云端只放本README、合同、候选清单和脱敏机器摘要。

## 5. 过程记录

|时间|事件与真实结果|
|---|---|
|2026-10-27|核验仓库、远端、worktree、干净源码基线与历史；建新任务。|
|2026-10-27|17旧版反例2FAIL/8ERROR；修复并补CAS竞态、分类切换与新任务导航。|
|2026-10-27|最终921/6/230/59和两CI通过；真实助手/连接检查/两Agent交接成功。|
|2026-10-27|Claude源码两轮无最终裁决；Codex核验及明确UUID恢复超时；受控执行Claude设计反例4/4；固定Gate拒绝，预算耗尽停止外部调用。|
|2026-10-27|源码f22ae75正常提交/push/fetch读回；DraftPR20更新，项目摘要同步project/devflower，原始证据留本地。|

本机另备干净手测目录 /home/workspace/claude_workspace/DevFlower测试/工作台体验，包含未执行的双Agent草稿和收藏模板，可直接打开体验；不与原始日志目录混用。
