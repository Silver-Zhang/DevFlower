# 对外 Stable 发布与分支整理

## 1. 目标与范围

执行本轮发布、tag、正常合并和分支治理授权。今后统一 develop 开发，main 用于 Stable；发布后两者同步到同一提交。project/*独立保存过程记录。旧版本、全部worktree目录、原生会话、认证和失败保留，不force/reset/clean，不续旧Task16预算。

## 2. 做法与证据

当前 develop/origin 已推送357327f380e8b2bda291df3f28d0312ae1dbd585，tree9408bee5e606f187045d7af0682882960c30890f，完整CF6db329466370457ac9bd4338b0337439be8ffd42b2005af6895da905fb175284/333文件。统一Draft PR24完整保留PR19–23祖先代码；远端HEAD/tree已独立读回一致。

同源完整22命令全0：1481 Python、6宿主隔离、65 JavaScript、265 Chromium/HTTP、16旧版smoke；FAIL/ERROR/SKIP均0，baseline/语法/外部前端重建字节一致，云端Studio contracts与Usable regression均SUCCESS。分类计数含各类测试，不把重复子集加成总覆盖数。

原生Codex实现会话01a18582-e292-76b1-afd8-20c5a58aff38沿用原HOME与CODEX_HOME，请求Sol/xhigh，物理后端/生效effort unknown。公共启动初稿有一次模型容量失败及一次900秒超时，原件保留。独立预审发现全局DeepSeek连接隔离问题及合法Runtime锁/子任务产物被误拒；Root修持久化，真实原生作者复现并修P2两文件、exit0/207.515秒/IMPLEMENTED。新启动40项真实HTTP/helper测试通过，独立设计的4项反例由Root外层复验4/4通过，非独立终局批准。

完整旧候选01/02各有2项Python失败；已确定fork继承前置Controller的non-dumpable保护导致/proc身份核验失败。改测试为真实CLI对应的exec/spawn，不恢复Controller权限、不放宽产品判据；新完整1481通过。旧02/03有异步焦点断言失败；等待同一精确焦点条件后新04全部通过，新设计浏览器回归加入CI。所有旧失败均保留。

公共入口 tools/devflower_start.py已实际启动，预构建页面与原生配置可用，原DeepSeek模型/强度恢复且设置字节不变；未伪造模型执行或Stable批准。原生CLI实施UUID也已由Codex桌面实际读到，最终JSON与CLI原件完全相同；这证明同一记录可见，不冒称桌面键盘实时接管。

真实模型验收进行中：DeepSeek前三场景完成；图片复杂图首轮FAILED被安全拒绝，同一会话显式修正后有效8节点+循环body4节点草稿完成，原失败不改。当前Codex顺序/暂停恢复、双并行汇合、条件、提前成功已通过，五轮上限和复杂图、managed、最终全产品双native审核与固定Gate仍待完成。

## 3. 决策记录

远端仅保留main、develop、project/devflower、project/codeweave；本地保留main、develop、project/devflower。旧源码分支HEAD均已在develop中保全，另完整history-before.bundle SHA676a288dd316d10c39c7b24bb395660100856cc062cf7013bb2a96852513bdc1（6065140字节）校验成功；旧worktree先在同提交detach，未删目录。PR19–23追加归集说明后关闭为superseded，并非伪称每个都已合并。

## 4. 结论与边界

仅整版当前真实验收/双独立native硬ACCEPT/固定旧Gate均通过才将PR24 Ready并正常merge，读回main及集成回归，develop普通快进main。新Stable/tag尚未发布；main仍e0a0f92/tree6c9e39e1，原Stable accepted-cfea19cac598未改。UI范围旧ACCEPT不替代整版，用户本人审美验收与自动化/代理操作分列。

## 5. 过程记录

原始大日志、截图、会话仅本机archives/devflower-public-stable-release-20261029，测试仅/home/workspace/claude_workspace/DevFlower测试/public-stable-release-20261029。本任务verification.json为必要摘要，原生原始意见、超时、容量失败、fixture脚本首次错误和所有历史负面均保留，不降低合同取得发布。
