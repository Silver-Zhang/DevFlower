# 对外 Stable 发布与分支整理

## 1. 目标与范围

后续统一 develop 开发，main 仅接收经过完整验收的 Stable 发布；整合后两者同一 HEAD/tree。保留 project/*、旧版本、原生会话、认证、所有失败与 worktree 目录。不 force/reset/clean，不续旧审核预算。

## 2. 做法与证据

当前已推送冻结12：22b4bb9698eef7019c957c5d0fc2a7f00b0c7101，tree 51efe627339d9794bc618c03a9317b4b411cbd7c，CF 817c8f5c2faf358a62d75c1c30a90885c8353f7cd1d968f9dd6197677e89e4d3，333文件。PR24 develop→main 是唯一开放 Draft。完整原始 GitHub commit 读回包含本次 HEAD/tree，摘要与原件均留本地。

同源22条命令全部exit0：1525 Python、6宿主隔离、73 JavaScript、266 Chromium/HTTP、16旧版smoke，FAIL/ERROR/SKIP均0；前端重建JS/CSS/许可证字节一致，6项云端CI全部SUCCESS。命令、计数及必要证据哈希见verification.json，合成/真实HTTP与真实模型分别记录。

实际Codex六类流程六项义务21调用全部满足判据：顺序与暂停恢复、双只读并发汇合、路由、提前成功、五轮上限、复杂流程；五轮上限的FAILED为预期负例。受控父子3个真实原生会话及12项身份/进程/预算检查通过。Terra12来自本机真实目录并完成一次high调用；物理后端及实际生效effort仍unknown，不从模型名称推导。

DeepSeek保存连接未变，assistant12-selected保留原DSML失败并明确选择同会话有界恢复证据。AI canvas12-r2真实7调用失败：两路汇合超过默认16000交接上限发生明确截断，后续有界循环正确未伪装成功。AI助手真实修复为紧凑输出与提示词；其提案工具不支持max_chars，不能声称已经改大连线限制，也没有手工改图冒充AI。首次修复运行预留2调用、实际仅1原生调用，另一分支被保护目录检查拒绝；确切原因unknown，原因猜测已纠正。确认所有旧运行终止、两次只读扫描通过后，仅一次有界fresh运行完成3调用：8实际激活、4设计未激活节点派生显示Skipped，刷新不增调用；原失败、恢复预算和拒绝记录均保留。

两个原生UUID浏览器均实际ready/exit0；native-terminals12-actual-r3证明同PTY观察、双向拒第二写者、关闭视图保留进程、正常退出0、同UUID恢复与自有停止。前两次终端harness因旧依赖pin/未登记当前工作区在0 CLI时拒绝，原件保留。仅W12明确QA Enter进入原生信任设置，认证/模型/中转未替换；旧operator仍拒陈旧配置，新generation按实际依赖重建。Desktop API可读同一canonical UUID记录，不等于桌面已接管live PTY或允许同时写入。当前启动入口实际Development端口39731，启动0模型调用，私有URL不入库。

## 3. 决策记录

冻结11历史仍为708c5834e96d2161345f8b97c10d2571e9e0e0df/tree57a66e65ea9da33446999c0d48428719e2b21e70/CF1753a8a6989821a0edb3a4448b711a8e29488499113b7708bc37a1e9cc763236。正式R1原始A=ACCEPT、B=BLOCKED；B-D01-TERRA-EVIDENCE与B-REMOTE-COMMIT-PROVENANCE两项补证在12准备，须新正式审核判断，不能改写R1。另UI-SKIPPED-LOOP-01/P2经12最小展示修复与独立定向复核scope CLOSED：派生跳过不造activation、轮次、会话、请求或回执；不是整版D06/D07、K、R2、Gate批准。

AS-PROTOCOL-01在11独立9持久化+32边界scope CLOSED；MF-01及终局paused/残留active问题原范围关闭保留，不套用为整版批准。source11最终native ACK IMPLEMENTED/exit0保持原身份。ACK12首次实际PARTIAL/exit0（310.013s）：71输入遗漏assistant12-selected/proof.json，原件未改未移动。新correction1输入78文件、14引用/9唯一文件原SHA闭合，同原UUID只读补件实际IMPLEMENTED/exit0（140.255s），两次源码CF均前后相同且无源码改动；最多2次、实际2次已结束，原PARTIAL保持。Gate12外部接口发现只提取定义而未执行verifier main，已最小修为实际只读调用，并与原聚合逐项一致；11隔离反例、后补新ACK路径18绑定+11回归及独立12路由检查通过。原工具副本、失败及部分输入保留；这些是外部工具预检，不是实际最终Gate，未据此签批准。固定旧监督器曾生成4缓存，已隔离并核验83原件恢复原CF；原拒绝与恢复日志不删、不重签旧哈希。

远端仅main、develop、project/devflower、project/codeweave；本地main、develop、project/devflower。旧PR19–23已追加superseded说明并关闭，祖先保留于develop，不伪称逐个merged。历史bundle SHA676a288dd316d10c39c7b24bb395660100856cc062cf7013bb2a96852513bdc1（6065140字节）已验证，旧worktree同提交detach且目录保留。本次按用户授权只用Codex多角色审核，不调用Claude模型；已有Claude支持/原生历史/中转配置保留。

## 4. 结论与边界

main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d/tree6c9e39e16bbbe7de95b894e0a58632b1a0f63039；正式Stable accepted-cfea19cac598未替换，未发布新tag。冻结12完整自验、当前真实流程、上述补证及ACK12 correction1均已取得。正式R2两个独立原生Codex已实际启动，各有不同真实进程的内核观察，当前RUNNING，原生会话及最终裁决待CLI回执；不能把运行中当ACCEPT。新送审包索引2332证据文件，加READ-FIRST/DEVELOPER共2334项，index SHA abf88ab16325b2e704d7d3db15e0007696682c1593e9d749184571ed1f9d1278，源码仍333文件/同CF12。完整产品/K独立最终判断、同候选双硬ACCEPT与固定旧Gate均待完成；保持Draft，不merge，不把范围ACCEPT、聚合complete或绿色CI当正式Stable批准。

本发布新任务最多5轮完整双独立源码审核；R1及旧Task16/UI预算、原始负面保持。只有当前候选必需合同/测试/审核/Gate通过后，才普通merge、main读回与集成回归、develop快进同提交并正常push，最后正式Stable/tag。用户本人审美验收与本地代理操作证据分开。

## 5. 过程记录

原始日志、截图、原生会话和全部负面仅本机archives/devflower-public-stable-release-20261029。测试仅/home/workspace/claude_workspace/DevFlower测试/public-stable-release-20261029。冻结11/R1与冻结12身份、历史失败、每次显式恢复及执行者分别保留；入库只留本五节README与必要机器摘要。本次为研发档案更新草稿，尚未commit/push；后续各交付阶段只追加真实回执，不以本文替代执行。
