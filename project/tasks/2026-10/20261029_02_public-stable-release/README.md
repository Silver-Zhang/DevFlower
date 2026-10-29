# 对外 Stable 发布与分支整理

## 1. 目标与范围

后续统一 develop 开发，main 仅接收经过完整验收的 Stable 发布；整合后两者同一 HEAD/tree。保留 project/*、旧版本、原生会话、认证、所有失败与 worktree 目录。不 force/reset/clean，不续旧审核预算。

## 2. 做法与证据

当前已推送冻结11：708c5834e96d2161345f8b97c10d2571e9e0e0df，tree 57a66e65ea9da33446999c0d48428719e2b21e70，CF 1753a8a6989821a0edb3a4448b711a8e29488499113b7708bc37a1e9cc763236，333文件。PR24 develop→main 是唯一开放 Draft。

同源22条命令全部exit0：1525 Python、6宿主隔离、67 JavaScript、265 Chromium/HTTP、16旧版smoke，FAIL/ERROR/SKIP均0；前端重建JS/CSS/许可证字节一致，6项云端CI全部SUCCESS，远端HEAD/tree读回一致。计数解析器的一次list/int比较失败另留原诊断；实际测试原输出不改。

实际Codex六类流程六项义务21调用：顺序与暂停恢复、双只读并发汇合、路由、提前成功、五轮上限、复杂流程。原五轮流程第3轮CLI exit0但timeout=true被拒；原生记录确实有回答，但最终结构流是否完整unknown。确认旧进程组结束后只进行一次fresh只读补证：真实10调用、五次false达到上限FAILED，满足负例判据；随后复杂3调用成功。原FAILED/complete=false保留，flows11-selected显式选择前4项与新2项，不重写原件。

父子3个真实原生会话及12项身份/进程/预算断言通过。当前两UUID原生终端均通过ready、共享同一PTY、两个方向拒旧写者、关闭视图不杀进程、/exit退出0、原UUID恢复和自有停止。原第2UUID停trust、浏览器同名选择歧义保留，改唯一定位并显式完成W11首次原生信任后另建QA绑定；不是全局自动trust或盲更新哈希。Desktop API可读同一canonical原生记录，但notLoaded不能解释为桌面已接管live PTY。

DeepSeek连接原样保存。当前助手普通问答与简单图修复成功；原源码诊断收到raw DSML、原图片提案max_tokens真实FAILED。每义务只一次显式同会话恢复后，标准工具源码诊断及图片新图成功；新图正确使用result.accepted，由普通浏览器实际运行3调用完成，刷新不增调用。原09错误字段/合同拒绝/DSML失败、10遗漏相邻片段的独立反例全部保留。原生Codex初修10，Root补齐11相邻纯控制片段；独立9持久化+32边界范围检查ACCEPT/AS-PROTOCOL-01 CLOSED，不能视为整版批准。

Root在09修复终局paused覆盖和残留节点假运行，独立范围UI-FINAL-PAUSE/ACTIVE CLOSED；MF-01在08独立关闭。外部发布工具R1/R2负面与R3限定93例、11派生122离线守卫检查保留，均不替代K/两个真实原生全源码审核/固定Gate。固定旧监督器曾被Root未加-B生成4个缓存，已仅将生成缓存完整隔离、核验83原文件不变并恢复原CF；前置拒绝0模型调用与恢复日志保留，不重新签发旧哈希。

## 3. 决策记录

远端仅main、develop、project/devflower、project/codeweave；本地main、develop、project/devflower。旧PR19–23追加superseded说明后关闭，代码祖先保留于develop；不伪称逐个merged。历史bundle SHA676a288dd316d10c39c7b24bb395660100856cc062cf7013bb2a96852513bdc1，6065140字节，已验证。原worktree同提交detach且目录保留。

本次只用Codex多角色进行编程与审核，不调用Claude模型；产品已有Claude支持/历史/中转配置保留。请求Sol/xhigh，DeepSeek助手medium；物理后端及实际生效effort unknown。原生实际开发与Root后续代码分开署名。已授权的正常合并/Stable/tag/分支整理仅在验收条件满足后执行。

## 4. 结论与边界

main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d/tree6c9e39e16bbbe7de95b894e0a58632b1a0f63039；正式Stable accepted-cfea19cac598未替换，未发布新tag。当前完整自验和实际流程义务通过，最终原生实施者只读回执已获IMPLEMENTED/exit0/同源不变；完整产品/K的正式R1双独立原生Codex审核已经实际启动，硬ACCEPT及固定旧Gate尚未取得。保持Draft，不merge，不把范围ACCEPT或绿色CI当Stable批准。

本发布新任务最多5轮完整双独立源码复审，旧Task16/UI预算与负面保持。只有必需合同/测试/审核/Gate通过后，才普通merge、main读回及集成回归、develop快进同提交并正常push，最后正式Stable/tag。用户本人审美验收与本地代理操作证据分开。

## 5. 过程记录

原始日志、截图、原生会话和全部负面仅本机archives/devflower-public-stable-release-20261029。测试仅/home/workspace/claude_workspace/DevFlower测试/public-stable-release-20261029。入库仅五节README与必要机器摘要；各候选/运行/报告保持原身份。所有对外阶段结果后续追加真实回执，不以本说明替代执行。
