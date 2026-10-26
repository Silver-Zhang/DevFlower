# 文件夹打开、图片工作流与原生扩展

任务 `20261027_01_workspace-images-skills-agents`，源分支 `flow/copilot-canvas-assistant`，记录分支 `project/devflower`。原始输出、图片、所有候选和原生 HOME 仅在 `/home/workspace/DevFlower/test/20261027-workspace-images-skills-agents`；本文和机器摘要不包含会话全文、认证或隐藏推理。

## 1. 目标与范围

修复可读文件夹因 Development 运行数据而不能打开；助手接收流程图图片生成可编辑工作流；做实 Skill 只读挂载及原生多 Agent 开关；复杂流程在工作区 `test/` 测试。浏览、执行授权、模型准入、审核和 Stable 晋升分别判断。保护旧版本、用户修改、原生历史和凭据，不修改 CodeWeave/RMC，不合并 main。

开始时源码 clean：HEAD c8d9c1a11775013f84027c3e818a48d1c7d3674f，tree 83ada1d141a6dcdcc3f95b1e682cda34bd3e51c4；当前结果 007d34bcf7ef1bb3af40bf32267614c80b8d65e3 / tree ec742da6d997857f56dd201900770931031ff189 / 完整候选 dbe28848f475e1fdd5bd322984a156969df77740f9a68b3d8be524d7633f5847，237 文件。[Draft PR20](https://github.com/Silver-Zhang/DevFlower/pull/20) 依赖 PR19，未改写旧 Task09 或更早结论。

## 2. 做法与证据

- 目录浏览使用独立入口，规范化明确选择的符号链接。系统/受保护/不可写或未知 `.devflower` 使用独立托管记录，原件不覆盖；已存在合法项目记录继续使用。总目录能查看，不能因此让 Agent 接管其中的 Stable、认证与其他运行数据。
- 原用户页面仍是旧服务；新启动器已绑定最新候选，不能靠刷新旧页面升级。代理操作真实浏览器打开 `/home/workspace/DevFlower` 成功、读取原有项目历史；执行按钮正确提示应选具体代码项目。没有冒充用户本人验收。
- PNG/JPEG 明确选图，最多3张、每张2MB，校验实际头部/尺寸/PNG CRC、哈希、工作区和回合身份；直接 API 图片块；不接收外部 URL/路径。历史保留元信息，不自动重复发送。当前 DeepSeek Flash 实际识别9节点10连线，包括并行、汇合、两轮有界循环及条件；配置请求xhigh，实际effort/物理后端unknown。模型不支持图片或输出不合法时保留草稿并报错。
- 原生 Skill 完整内容冻结/哈希绑定、只读挂载、遮蔽未选自动发现目录；拒绝链接、特殊文件、私有配置和改变权限的头部，修改后刷新重选。模型连接诊断真实反例曾能读到业务 Skill，已改为公共占位内容，真实挂载仅在获准业务运行中发生；修复前后均用实际隔离文件读取证明，诊断回执明确为合成 nonce，无模型调用。
- Codex/Claude Ask/Plan 可选只读子 Agent，Agent 写入模式拒绝。Codex 原生 config/read 实测 ON=true、OFF=false，最多两子任务/一层；Claude固定只读助手/子模型，不声称原生未提供的数量上限。Copilot 扩展未开放。子调用可能额外消耗用量。
- r8真实复杂流程 `bd364257d1e1fbea91eb5b2fbea143c4` 完成：2路 Codex 并行→汇合→独立会话编写/复核→满足条件提前退出循环→一支输出、一支Skipped，共4根调用和1真实子会话；可见工具返回确认读取Skill。r9仅改复选框CSS和浏览器可读性断言，Python/原生执行字节一致，仍分别记录候选，不伪称r9已跑Codex/Claude整版验收。r7另有Codex+Claude 6调用、2轮false→true的旧候选证据，保留原意。

同一最终r9候选的命令：Python完整891、隔离6、Chromium/HTTP225（106+119）、JS57，均exit0、FAIL/ERROR/SKIP=0；Baseline结构检查exit0、前端构建/6语法检查/字节复建/已知凭据和运行数据扫描通过。精确命令、路径和日志哈希见 evidence-summary.json；合成测试与真实调用分列。普通单测临时文件使用会自动清理的 `/tmp`，持久工作流工作区、HOME和全部保留证据在 `test/`。不将私有临时文件清理失败或旧候选结果包装成产品通过。

## 3. 决策记录

授权：用户2026-10-27明确要求上述功能与test目录复杂测试，沿用源码/记录Draft同步授权；不新增Provider、不合并main或覆盖Stable，不复制全局认证/HOME。Native扩展权限必须强制隔离；不能靠提示词保护写入范围。主线和Stable保持原样。

新任务WX01–WX06合同独立建档，最多5源码轮/2基础设施恢复。首次真实Claude nonce超时后一次明确配置恢复用于r7流程；源码审核502后一次同UUID恢复再次502，两次基础设施恢复预算已用尽，停止服务请求。不通过换候选或新UUID继续追求ACCEPT。新发现的连接诊断隐私缺陷修正后重新冻结自验，未借旧报告放行。实际审核模型请求沿用本项目claude-opus-5-5[1m]/high，未擅自更换成助手模型。

回退仅切回保留的旧入口/工作树；各开发快照、运行数据库、原生聊天和旧服务均保留。未做reset、clean、强推或会话删除。

## 4. 结论与边界

实现与最终自动化自验已完成，独立审核与正式Gate仍BLOCKED。固定旧Stable监督实际启动Claude R1及一次同UUID恢复，PID671984/677123，exit1/1，原始API Error 502；均verdict=null，没有有效ACCEPT，也不是Claude给出的BLOCKED裁决。审核对象为r7，当前r9未经独立源码复核。HOME、cwd、UUID、退出、原始错误/哈希见evidence-summary.json；历史原件不改。

WX05当前最终候选的Codex/Claude双Provider完整真实复验缺失，WX06独立最终裁决缺失；旧SC08 P1继续OPEN，未被本轮功能测试关闭。没有最终Gate通过、合同K完成、用户体验验收或Stable晋升声明。PR保持Draft，等待服务恢复后的合规复核；不启动第三次服务恢复。

唯一推荐入口：`python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`，已核对进程、配置、源码哈希及HTTP状态，当前Development http://127.0.0.1:35837。私有登录由本地启动器处理，勿将日志首行私有网址发到聊天。点击“打开文件夹”选择普通项目；本轮测试位置 `/home/workspace/DevFlower/test/20261027-workspace-images-skills-agents/workspace-r9`；AI助手点击“图片”；节点“高级→Skills与子Agent”；复杂真实历史在r8服务及本地test证据，不冒充当前版本已执行。

源码已正常推送并读回 HEAD 007d34bcf7ef1bb3af40bf32267614c80b8d65e3，tree与最终候选一致；记录分支单独提交/推送，具体交付回执留本地，不自动合并。操作说明随源码 docs/WORKBENCH.md、docs/user/assistant-and-copilot.md 交付。

## 5. 过程记录

| 时间 | 事件、失败及纠正 |
|---|---|
| 2026-10-27 | 核对各worktree/remote，创建独立任务，不影响旧用户修改与原生会话。 |
| 同日 | 早期测试发现受保护位置开始按钮未禁用、助手归档/关闭竞争、回执漏扩展字段，均定向修复并重跑；旧失败留本地。 |
| 同日 | r6真实Claude业务复核超时保留；r7一次受控nonce恢复后真实双Provider两轮完成，图像首轮错误保留并显式纠正，不自动重放。 |
| 同日 | Claude源码R1与同UUID恢复均HTTP502，无裁决；旧候选与原始输出保留。 |
| 同日 | 实际隔离文件读取复现诊断Skill内容泄漏；修正为公共占位并保留原始作用域哈希。补旧ID刷新、quoted/YAML合并、子Agent OFF的负例。 |
| 同日 | r8全回归891/6/225/57通过，真实复杂Codex流程4调用完成。证据解析器首次仅识别字符串工具输出而漏掉列表；保留首次脚本，支持实际公开列表字段后确认Skill标记，不读取隐藏推理。 |
| 同日 | 代理浏览器发现Skill复选框过大，r9仅CSS和断言修正；最终完整回归891/6/225/57通过，DeepSeek再次真实识图成功；当前源码送Draft，正式验收仍阻塞。 |
