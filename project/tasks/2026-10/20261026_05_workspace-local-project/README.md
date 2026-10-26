# 按工作区保存工作流与对话

任务 `20261026_05_workspace-local-project`，接续 Task08；这是新的需求与审核身份，旧任务的失败、预算和裁决均未修改。

## 1. 目标与范围

用户明确要求：打开一个工作区文件夹后，可查看该工作区的对话，工作流也保存于这个文件夹，组织清楚。新增托管 `.devflower`，保留原有数据。实施起点源码工作树干净，flow/copilot-canvas-assistant / a362fb49ee8f9ebb572ffbf247e2d74a1169fc64 / tree38c6850e1537af0f4334a27d3aaa984c7d8ea78f。main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d；不合并、不晋升Stable、不修改CodeWeave/RMC或删除原生会话。

## 2. 做法与证据

`.devflower/library.sqlite` 是工作流、模板和不可变版本的项目内存储，支持CAS；旧托管设计只复制，旧缓存不能覆盖新版本，删除墓碑防复活。对话/整轮群聊/助手使用脱敏的JSON阅读副本，不导入执行状态、HOME、令牌、许可、原生写入者或审核批准。工作流默认仍是空白，已保存设计加载到左侧供选择；切换项目隔离记录。历史整理后台执行，有错误状态、取消与幂等，开目录不等待全量聊天。

实际源码项目与包含Stable/认证/运行数据的总目录分开：总目录可保存/查看项目记录，不能直接执行或扫描私有子树。普通目录可设计画布、保存和看历史；执行根与当次授权仍独立核验。Agent与原生终端外层执行器真实遮蔽`.devflower`；仅可变项目源身份显式排除已识别的保留区，完整程序候选哈希保持全文件语义。源码快照须显式排除项目记录，严格交接工具拒绝夹带它。文件/目录链接、特殊文件、目录接管、跟踪源码藏入保留区均被拒绝。

最终源码 c8d9c1a11775013f84027c3e818a48d1c7d3674f，tree 83ada1d141a6dcdcc3f95b1e682cda34bd3e51c4，231文件CF 7307d873eb7ef10f347f3847cd5f719754e40a8b708b51d7f61e8a2747a6ef43。同候选命令/摘要：

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`：865，exit0，FAIL/ERROR/SKIP0。
- `python3 tests/host_isolation.py`：6，exit0；`python3 tools/devflower.py check --baseline`：exit0，仅结构。
- 普通Chromium/HTTP：100工作台+9项目记录+110其他=219，最终完整命令exit0、FAIL/SKIP0；57JS、6语法检查、前端重建字节一致，exit0。
- 同候选首次100项浏览器中有1项原生历史显示观察超时，根因未确认；不改断言，保留原始失败，再完整复跑100/0。早期同步迁移慢与自动恢复最后草稿造成的旧合同失败均已修复，旧日志不改为通过。
- 真实Codex只读业务run `9bf0d7c2351ff9a4d7ed1063557761eb`：COMPLETED，1业务调用、另1连接检查；模型gpt-6.1-sol/xhigh，原生UUID `01a177f5-a62f-71e2-89c8-65804628caba`。真实独立助手DeepSeek调用1次，2条消息。Provider未证明的实际物理后端/生效effort均unknown。
- 独立空宿主重新打开同一项目：工作流、真实对话和助手可读；没有复制原生HOME，无模型重发，无准入或运行权威导入。此场景是本轮持久化验收，不冒充整版合同K或用户本人接受。
- 隐私扫描231文件：已知凭据0命中，运行数据0入库，冻结后源码未变化。

上一版当前预览的本机数据库通过只读SQLite备份提取，不直接修改源库；原生HOME未复制。已在 `/home/workspace/DevFlower/.devflower` 整理3份工作流、2份模板、6份助手记录（2活动/4原回收站）、2轮任务和6份原生对话阅读副本。保留原始工作区/候选与真实运行时间，不把旧记录标成新候选通过；HTTP读回数量与只读属性通过，容器目录实际执行仍被拒绝，迁移调用模型0次。其他旧服务/档案未全局合并，原件保留。

原始证据只在 `/home/workspace/DevFlower/archives/devflower-project-local-20261026`，机器摘要见本目录evidence-summary.json，审核见review-r1.json。用户指南随源码位于docs/user/assistant-and-copilot.md。

## 3. 决策记录

本轮用户明确批准实现按工作区存储；沿用已批准的开发分支/Draft源码与project/devflower记录交付范围，未扩大到main或Stable。风险是项目数据被当权限/源码、迁移覆盖旧版本、慢历史阻塞UI。采用项目内设计主存储与外部执行权威分离；复制项目只能得到设计和阅读记录。未知/过期状态不据本地阅读副本恢复，实际执行仍检查完整候选、模型准入、租约和真实进程。回退可使用保留的旧Development入口备份；旧Stable、服务、HOME、认证和档案不删除。

## 4. 结论与边界

实现与自验通过；独立审核未完成，正式技术验收BLOCKED。固定旧Stable外部监督真实启动Claude Code2.1.303，claude-opus-5-5[1m]/high，session `a8df7386-27ba-4131-8b86-3292863eec61`；原生实际有Read7/Bash6/Grep1检查，报告模型字段claude-opus-5-5，但180秒/同UUID60秒恢复均timeout、exit-15、没有最终结构化裁决（verdict=null），不能写成Claude的BLOCKED或ACCEPT。本任务1源码轮+1基础设施恢复后停止，原生记录/UUID/HOME/cwd完整保留。旧SC08 P1仍OPEN，最终Gate未调用，不批准Controller自身或Stable。

本机已验证入口 `python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`，Development CF7307d873eb7e，http://127.0.0.1:40477。建议在目录选择器选择DevFlower-Project-Local-ready项目位置；更换目录只打开其自己的记录，不自动合并其他项目。较长原生会话沿用200条显示/读取预算，完整原件仍在原生工具中；复制SQLite需一致性备份或关闭工作台，不能只拷主文件漏掉WAL。旧托管记录后台整理失败会保留原件并显示错误；本地阅读副本不是防篡改审计原件。源快照/固定外部交付配置须明确排除`.devflower`，不能沿用未核验的旧任务许可。

用户本人体验待确认。源码已正常推送并读回c8d9c1a；[Studio contracts](https://github.com/Silver-Zhang/DevFlower/actions/runs/40317433890)与[Usable regression](https://github.com/Silver-Zhang/DevFlower/actions/runs/40317433881)均成功。PR20已更新且保持Draft，依赖PR19；项目记录独立分支同步，原始日志/截图/原生会话不上传。下一步仅在新证据支持恢复时取得当前候选有效独立最终裁决；不继续扩展功能或自行晋升。

## 5. 过程记录

| 时间 | 事实 |
|---|---|
| 2026-10-26 | 检查工作树干净、读取AGENTS/WORKFLOW，创建新任务与WP01–WP06合同。 |
| 同日 | 初版专项19/0；完整旧回归暴露离线调度器/原生PTY探测兼容问题，保留5FAIL/21ERROR并修正。 |
| 同日 | 浏览器暴露同步批量历史复制阻塞目录选择；改为后台迁移。保留自动恢复草稿的4项失败，恢复默认空白并显式从列表选择。 |
| 同日 | 最终865/6/219/57通过；真实Codex/DeepSeek与空宿主历史读回通过。早期验收脚本读取过期check字段在推理前失败，保留并纠正，没有重放业务调用。 |
| 同日 | Claude独立源码轮及准确UUID恢复先后超时，原始负面/未完成记录保留，停止重复请求；保留Draft，不合并main或替换Stable。 |
