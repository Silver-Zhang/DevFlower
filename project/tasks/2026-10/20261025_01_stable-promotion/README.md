# 正式 Stable 晋升、真实补证与独立审核

## 1. 目标与范围

执行 2026-10-25 用户授权：解决剩余 BLOCKED、完成真实运行和独立审核、统一最新 main、正式晋升 Stable；保留旧安装、原生会话和历史裁决。新任务 `20261025_01_stable-promotion`，run `33874f46163e4f95b59a709ef4104478`，合同 SP01–SP10 原文见 [acceptance.txt](acceptance.txt)。旧任务的预算和 BLOCKED 不改写。

## 2. 做法与证据

基线 main `bedee3229e0c4420c9112a5b62eac9a0e81d6458`。原生 Codex 在独立工作树先用失败测试复现可信入口缺失，再实施最小修复。正式 v3 入口仅接受 v2 完整外部来源链，绑定原始合同、原生实施/审核回执、观察到的 Provider/职责/可执行文件/模型/强度/会话、固定 Gate、限定 install/start 的用户批准及实际 Git 提交对象和完整 tree。拒绝伪造批准、错配及启动后证据替换，旧 v1 兼容保留，Controller 不审批自己。

最终源码候选 `cfea19cac59824ca0727e0c21ff240a5b62e1e0c228da7e033c8ae34df139fbe`，183 文件；tree `6c9e39e16bbbe7de95b894e0a58632b1a0f63039`。实现提交 `20fbc62c412d6ded7517cd581bd77b10a0c10bde`，PR [#18](https://github.com/Silver-Zhang/DevFlower/pull/18) 普通 Merge Commit 后 main `e0a0f92fed7f94bfef3a01b1298f8465c008023d`，完整 tree/候选与被审对象相同。三项 GitHub CI 成功；干净 main 已 ff-only 同步。临时开发分支已安全删除，目录保留并 detached 在最新 main。

完整同候选回归：589 Python、6 宿主隔离、169 普通 Chromium/HTTP、20 JavaScript；全部 exit 0，FAIL/ERROR/SKIP=0；Baseline、6 项语法和独立前端逐字节重建通过。合并后另跑 42 项 Stable/来源链/原生调用专项、6 宿主和 Baseline，通过。完整命令、计数、退出码、文件哈希与本地日志路径见 [validation.json](validation.json)，源码清单见 [candidate-manifest.json](candidate-manifest.json)。

真实当前候选：顺序 2 调用、双路并发汇合 3 调用、循环提前成功 2 调用、五轮上限 10 调用，另两 Provider 新建/恢复 4 回合；共 21 个完成原生回合。上限用例按合同终止 FAILED，是通过的负例。逐次核对请求哈希、交接来源/轮次、初始化一次、原生 UUID/HOME/cwd；实际 PTY 同身份独占、第二写入者拒绝、关闭保留历史、停止后本任务进程死亡。正式安装后再次在实际 Studio 完成顺序 2 调用及并发汇合 3 调用；顺序暂停/刷新/继续保留同 run。证据均为 Agent 代操作，不冒充用户本人体验验收。

独立 Claude 原始裁决：[R1 NEEDS_CORRECTION](review-r1.json)、[R2 NEEDS_CORRECTION](review-r2.json)、[R3 ACCEPT](review-r3.json)。R1 发现批准缺真实来源链，R2 发现重绑定后的 Provider/模型/职责错配；R3 独立反例和清单验证后 SP01–SP10 全 PASS，来源 P1、I16-A、MA-05 全 CLOSED。R1/R2 首次分别 1200/1800 秒超时，原进程死亡后各用同 UUID/HOME/cwd 恢复一次；共 3/5 源码轮、2/2 基础设施恢复。旧固定 Stable Gate 实际 exit 2、2、0，最终 ACCEPT；原始负面结果不删除。

实际请求 Codex `gpt-6.1-sol/xhigh`、Claude `claude-opus-5-5[1m]/high`，Claude CLI 回报模型一致；中转站物理后端和实际生效 effort 为 unknown。各轮 PID、退出码、HOME、配置根、cwd、UUID、原生记录位置在 validation.json。原生记录不入库。恢复须选对应记录原 HOME、cwd 和明确 UUID，使用固定版本 CLI 的 `--resume <UUID>`；不得使用 `--last` 或混用项目/角色。恢复交互会话仍须先排除现有写入者，并沿用原隔离/权限。

## 3. 决策记录

本次授权原话：“继续解决剩余的 BLOCKED，补齐真实运行验收和独立审核，完成正式 Stable 晋升，统一使用最新 main，保留旧版本和原生会话。”在已有明确的正常合并和分支整理授权内执行。最终硬 ACCEPT、固定旧 Gate、实际 PR 读回及 main 集成检查后，外部监督才签发限定安装/启动的来源记录；待审 Controller 没有自签批准。

正式包 `/home/workspace/DevFlower/stable/accepted-cfea19cac598` 只读，来源就是最新 main。原 Stable `usable-v1-5816ba4776b3`、旧日常包 `main-7b0c0b3bcc95`、旧脚本/指针备份、SQLite 备份、所有会话保留；运行数据和 native-homes 沿用原路径，不搬迁。切换前核实旧进程身份且空闲，切换后检查 HTTP、Stable 批准和实际原生流程。最终检查旧安装、原有两份 JSONL 和全局认证字节未变。

## 4. 结论与边界

**本任务已完成技术验收、PR 合并、正式 Stable 晋升和实际启动。** 新任务结论不覆盖旧任务 BLOCKED。用户本人视觉和交互体验尚待亲自确认。

唯一推荐启动：`python3 /home/workspace/DevFlower/.local/open-devflower.py`；可用 `--check` 查看当前批准与版本。入口打开带本机凭据的页面，私有地址不要公开。

操作：打开文件夹并确认路径 → 选择模板或空白工作流 → 添加 Agent，配置 Provider、模型、effort、模式和初始化/每轮/交接三个提示词 → 刷新模型并对未准入配置进行执行验证 → 连接节点并配置字段传递 → 填任务并确认本次范围/模型/权限 → 开始运行。运行中点连线看实际交接，点下游看本轮实际请求和原生回执；左侧选择同身份会话看历史或打开独占原生终端。暂停停止后续调度，停止取消本任务执行；关闭面板/断开视图不等于停止或删除历史。

研发时间线仍在 `project/devflower` 的任务 README、task.json、INDEX；本机 Runtime SQLite 和原生 HOME 保存实际运行细节。**通用 Studio 运行自动归档到 project 分支仍未实现**，当前为整理后同步。生产 `external_steps` 为空，自动业务 GitHub 交付未启用；本次真实开发 PR 由获准固定外部监督交付。源码版本、测试、自验、独立审核、Gate、用户批准、部署及用户体验分别陈述。

原始大日志、截图、完整会话和认证仅留本地 `/home/workspace/DevFlower/archives/devflower-stable-promotion-20261025` 及既有 native-homes；入库只有合同、结论、清单和必要机器摘要。旧 Stable 不删，历史 worktree cwd 不变。

## 5. 过程记录

| 日期 | 事件 |
|---|---|
| 2026-10-25 | 核验远端/main/worktree/旧服务，建立新任务、独立分支和合同；真实 Codex 复现后实施。 |
| 2026-10-25 | R1 NEEDS_CORRECTION；旧 Gate exit 2，修复批准来源链。 |
| 2026-10-25 | R2 NEEDS_CORRECTION；旧 Gate exit 2，修复原生调用语义错配。 |
| 2026-10-25 | R3 完整回归、真实顺序/汇合/循环/原生生命周期通过；Claude 硬 ACCEPT。 |
| 2026-10-25 | 旧固定 Gate exit 0；PR18 三项 CI 成功后普通合并，main 同步并集成回归。 |
| 2026-10-25 | 外部批准真实来源链，正式 Stable 启动；在实际安装再次通过真实顺序和并发汇合；旧版本/会话/认证保留。 |
