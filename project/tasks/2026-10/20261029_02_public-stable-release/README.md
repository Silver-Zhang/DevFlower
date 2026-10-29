# 对外 Stable 发布与分支整理

## 1. 目标与范围

统一 develop 开发、main 用于经验证的 Stable 发布；发布整合后两者达到同一 HEAD/tree。只保留 main、develop、project/*。全部旧版本、worktree、原生会话、认证和失败保留，不 force/reset/clean，不续旧 Task16 审核预算。

## 2. 做法与证据

当前已推送 develop：75ff2b8895f8a9bb7b2b085cdc8d2b6c0bde2dc9，tree b01349b9c5583581c09be97bb11c865ebfc57d9e，完整候选 CF78724ca8d7939bcc1daa5985576018bcef00ef00d90798dc20ea1eb880977afb，333文件。Draft PR24完整保留旧PR19–23的祖先代码。

该冻结07全量22命令实际全部exit0：1505 Python、6宿主隔离、65 JavaScript、265 Chromium/HTTP、16旧版smoke，FAIL/ERROR/SKIP均0，外部重建JS/CSS/许可证字节一致。六个真实Codex流程21调用通过，包括按合同拒绝的五轮上限；受控父+两子实际3个不同原生会话、共享3次预算及12项身份判据通过。新外层监督单独保存真实进程退出0/无信号，未把此前05外层工具会话143改写成0。真实内置DeepSeek问答、源码诊断、配置修复完成；07图片提案首轮含不允许字段而安全FAILED，同CID显式纠正后有效复杂图完成，原失败保留。

原生实施使用同一明确UUID 01a18582-e292-76b1-afd8-20c5a58aff38、原HOME和中转，请求Sol/xhigh，物理后端与生效effort unknown。真实完成的最小修复：公共入口合法锁/产物目录复用；桌面文件打开偏好误拦；配置变化后已授权PTY无法输入/关闭；SQLite正常回收sidecar时错误隐去项目记录。分别保存各次原生IMPLEMENTED/退出0、源前后hash和原始报告；Root另外修持久化、测试启动语义及恢复文案，署名不混淆。

定向独立复测07：原35+17+24共76项全0，额外11项nlink有限重查全0单列，不加成主回归；真实2000次SQLite并发无错误/无记录差异。旧04、05及云端原始失败、所有夹具错误保留。这些范围ACCEPT不等于整版审核。

真实子Agent又发现收尾疑点，独立反例确认MF-01/P1：子调用已发生但完成发布异常后，子记录RECOVERY_REQUIRED、活动表清空，close仍closed；完整父适配器/调度器反例还出现父与run COMPLETED。原始NEEDS_CORRECTION保留，目前正在由原生实施者最小修复；07不具备放行条件。

## 3. 决策记录

远端仅保留main、develop、project/devflower、project/codeweave；本地保留main、develop、project/devflower。旧worktree同提交detach后保留目录，代码在develop及完整bundle中保全；history-before.bundle SHA676a288dd316d10c39c7b24bb395660100856cc062cf7013bb2a96852513bdc1，6065140字节，已验证。旧PR19–23追加说明后关闭为superseded，不伪称逐个merged。只通过完整发布PR24整合。

首次原生Codex目录信任由本地QA在授权空测试目录内真实操作，不写成用户本人批准；CLI只新增该测试目录trust，认证/中转其它语义不变。实质配置变化仍拒新启动，已有PTY控制修复不等于降低新执行权限。原生桌面读到同一CLI记录不等于桌面接管PTY；状态与能力限制分别记录。

## 4. 结论与边界

main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d/tree6c9e39e16bbbe7de95b894e0a58632b1a0f63039，旧Stable accepted-cfea19cac598未替换。当前MF-01必需缺陷OPEN，完整同源产品/K、两个真正独立原生Codex整版硬ACCEPT、固定旧Gate尚未完成；不以绿色CI或定向ACCEPT代替，不merge、不tag、不伪称已发布。

本新任务最多5轮完整源码复审，旧Task16/UI历史与预算不重置；定向反例设计/补丁范围检查不冒充整版裁决。修复产生新候选后重新测试和独立复核；全部必需项通过才普通merge、读回main、集成回归、develop快进同提交，最后独立Stable批准与正常tag/release。

## 5. 过程记录

原始日志、截图、原生会话及全部失败仅留本机archives/devflower-public-stable-release-20261029；测试仅/home/workspace/claude_workspace/DevFlower测试/public-stable-release-20261029。每个原始候选、审核、运行及Gate身份分别保存，不覆盖负面。当前verification.json仅必要摘要；后续以新源/真实证据更新当前入口，旧版本由Git历史和本地原件追溯。
