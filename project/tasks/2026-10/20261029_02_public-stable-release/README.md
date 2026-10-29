# 对外 Stable 发布与分支整理

## 1. 目标与范围

统一 develop 开发、main 用于经验证的 Stable 发布，整合后同一HEAD/tree；保留project/*。用户授权条件满足后的正常merge/Stable/tag及清理，不force/reset/clean。旧版本、工作目录、原生会话、认证和失败全部保留；不续旧审核预算。

## 2. 做法与证据

已推送冻结09：df03af7a30260ac594e522e960e4a81406c598cf，tree aa2d37e61aa374345dcebdc2fb21e1cfa2eaecf2，CF c606af1997030c3e219873850a12e609eff04a3ea731ab04e611fdd90e4b0d10，333文件。Draft PR24保留旧PR19–23的所有祖先实现。

同源22命令全部exit0：1516 Python、6宿主隔离、67 JavaScript、265 Chromium/HTTP、16旧版smoke，FAIL/ERROR/SKIP均0；重建JS/CSS/许可证字节一致；6项云端CI全SUCCESS。设计浏览器日志含取消HTTP响应的BrokenPipeError，12项场景与命令均通过，原诊断不抹去。汇总脚本几次计数字段读取错误单独保留，不改原测试退出或数量。

真实Codex六图21调用及受控父子3调用全部满足本职判据，新的外层监督也实际exit0。真实原生CLI通过工作台与另一socket客户端共享同一PTY，拒绝旧写者、关闭视图不杀进程、/exit退出0、同UUID恢复和自有进程停止通过；不是复制聊天文本。Desktop API确读到同一原生UUID及当前公开源码核查消息，但notLoaded/interrupted为App对外部CLI的视角，不据此声称桌面PTY已被同时接管。

当前原生修复链包括公共入口精确锁/产物复用、桌面显示偏好、配置变化后的既有PTY控制、SQLite sidecar和MF-01。Root之后依据真实Runtime反例最小修复终局暂停覆盖、残留节点假运行：09独立原始范围ACCEPT明确UI-FINAL-PAUSE/ACTIVE CLOSED；完整候选身份前后独立重算一致。MF-01的08独立关闭、07原始父误COMPLETED反例、所有旧超时/容量/143退出/故障夹具保留。范围通过不等于整版审核。

DeepSeek助手的普通问答、真实公开源码诊断、简单配置修复及图片图提案实际完成，但额外复杂图填错accepted路径和分支值，真实运行进入错误补证分支；Root实际停止旧run保留CANCELLED/4次调用。随后同助手CID两次显式修正：第一次输入合同不匹配被安全拒绝；第二次API200但只回DSML工具调用文本，无可用提案。当前仍须独立判断与解决该缺口，不能把“有回答”写成“复杂图可运行”。

外部发布工具限定预检R1/R2分别NEEDS_CORRECTION，原始负面不改。修正系统exe属主、只读观测join、跨run Gate、合法子派发终态、单事件JSONL及未批准配置后，固定R3的47当前协议+46旧恢复反例全部PASS；无真实发布/CLI/批准动作。该scope不替代产品/K/native审核/Gate。

## 3. 决策记录

远端仅main、develop、project/devflower、project/codeweave；本地main、develop、project/devflower。旧worktree同提交detach且目录保留，完整history-before.bundle SHA676a288dd316d10c39c7b24bb395660100856cc062cf7013bb2a96852513bdc1，6065140字节，已验证。旧PR19–23追加说明后关闭为superseded，未伪称逐个merged。

未知desktop新字段按现行保护拒旧绑定，未擅自allowlist；新QA实例只在独立核对具体bool增量后绑定当前配置。原生目录信任由本地QA在明确测试目录亲自操作，不写成用户本人确认；全局仅新增该目录trust后，为新测试另建operator generation，旧operator/run/baseline不改，不盲更新任何哈希。中转、模型、effort、认证及已保存DeepSeek连接保留。

## 4. 结论与边界

main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d/tree6c9e39e16bbbe7de95b894e0a58632b1a0f63039，正式Stable accepted-cfea19cac598未替换。当前完整自验通过但复杂AI修图仍有实际失败，全产品/K、两个独立原生Codex硬ACCEPT及固定旧Gate尚未完成；保持Draft，不merge、不tag、不称新Stable已发布。

本发布新任务最多5轮完整源码复审，旧Task16/UI历史与预算不重置。定向反例/补丁检查与外部工具预检单列，不能冒称整版裁决。全部必需项通过后才普通merge、main读回及集成回归、develop快进相同提交，最后依据正式批准发布Stable/tag。用户本人审美验收另列。

## 5. 过程记录

原始日志、截图、原生会话及全部失败仅本机archives/devflower-public-stable-release-20261029；测试仅/home/workspace/claude_workspace/DevFlower测试/public-stable-release-20261029。verification.json仅必要摘要，各候选/运行/报告的原始身份分别保存，不覆盖负面记录。
