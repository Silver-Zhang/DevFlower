# Claude恢复、进程监督收尾与最终复核

## 1. 目标与范围

用户2026-10-27明确恢复剩余工作，先用Opus5.5 1M尝试原生Claude。独立建立Task11、5源码轮/2基础设施恢复预算，引用旧Task04/Task10但不续写其预算/结论。修复遗留SC08并补同候选双Provider真实流程和独立源码审核；不合并main、不晋升/覆盖Stable、不删除原生会话或修改全局认证。

开始源码clean007d34bcf7ef1bb3af40bf32267614c80b8d65e3/treeec742da6d997857f56dd201900770931031ff189。结果 742c61f4bda27e40d641a05ea5a364f6c2585c12 / tree 7e1992f75109ae5e542b87f97c5ea1cf24d772ef / 完整候选 e33323b9fdb92eebdc5a14b74985f77c7d3349523792abcbc7e3a74cdf59bb24，239文件；[PR20](https://github.com/Silver-Zhang/DevFlower/pull/20)依赖PR19，保持Draft。源码已正常推送，原始证据、每版快照、图片和HOME只在 `/home/workspace/DevFlower/test/20261027-claude-recovery-finalization`。

## 2. 做法与证据

- Claude2.1.303本机认证状态有效；原生请求claude-opus-5-5[1m]/high，5.694秒exit0，UUID947fac9a-5f8a-46b2-8b63-7ce756237439，随机nonce与会话身份均核对。CLI报告模型同请求；实际物理后端、上下文容量与生效effort unknown，简单连通不代表复杂工具/审核可用。
- SC08在原候选的四处外层启动入口分别注入OSError/SQLite错误，8个实际子进程反例全部失败。当前先保留自有Popen引用，身份/回执发布失败精确停止组并wait回收；失败记录不可写不能阻止清理；终端不留无读线程的入口/FD；状态保守RECOVERY_REQUIRED而不重发。补身份缺失、非OS异常、持续存储故障和后代进程组，共12项通过。旧SC08官方状态尚未独立关闭。
- 同一最终r2：903Python、6宿主隔离、225Chromium/HTTP（106+119）、57JS最终exit0，FAIL/ERROR/SKIP0；Baseline结构、6语法、字节一致构建与隐私扫描通过。源码742c61f的两项云端CI均成功，精确命令与日志哈希见evidence-summary.json。原生模型试验与合成回归分列。
- 原主浏览器106项有1项delayed-prepare收到HTTP400；定向1项和不改断言的完整106复跑通过，原响应体未保存，确切根因未确认，负面结果已送独立审核。原903的旧单测以没有PID的MagicMock充当子进程，新增身份检查正确拒绝；只修正同步测试驱动PID夹具，原断言保留，原错误日志不删。
- 真实NativeCodex对当前候选核验，同UUID01a17ab7-b80a-7272-b643-9b2733b62283从180秒超时恢复一次后返回IMPLEMENTED；实际执行指定准入测试与独立终端SQLite故障、FD/回收/无关哨兵保护，不作为Claude或Stable批准。自身额外审计钩子误拦dir_fd和/dev/null的失败保留并只修正测试夹具。
- 当前DeepSeek助手真实识图仍得到9节点10连线。复杂Codex/Claude运行依次记录：r1的Claude240秒超时；r2实际6业务调用、两轮指南拒绝后按上限FAILED，4父会话/1真实只读子会话、Skill公开工具返回标记与true/false初始化绑定已检查；补齐原测试遗漏的操作文档后新运行仍在Claude600秒后超时，不能当成功。所有失败、原始否定意见与运行/activation/轮次不变。
- 最后Claude原生公开工具调用出现Read.offset3280、32800、3280000000000000等越界值，反复读取到超时。独立本机诊断使用真实CLI+合成localhost消息，3例证明328和328.0正常读取第328行，巨大值保持巨大且正确报越界；外部模型调用为0。没有复现本地数字放大，也不猜测中转站真实物理后端、不静默修正模型参数，更不把合成诊断冒充真实模型验收。

## 3. 决策记录

授权是本次用户明确“继续完成，先尝试Claude，Opus5.5 1M”，沿用high；未改全局模型、认证或系统配置。各测试/原生HOME固定在工作空间test。SC08仅做已证实的四入口小修复及共享回收助手，没有重设计Controller。旧Task04 R5 NEEDS_CORRECTION和Task10两次HTTP502/null保持原样；新任务唯一源码审核R1对应当前候选，不能借候选编号r1/r2或业务运行轮次冒充新审核次数。

2次基础设施恢复分别用于明确失败的Claude240秒连接后的新受控试验、NativeCodex同UUID一次恢复。中间正常完成但指南被拒绝属于真实有界业务失败；补文档新任务并非篡改旧运行或服务失败无限重试。新源码审核失败后预算已尽，停止外部请求，未隐式开启下一轮或换模型求ACCEPT。

## 4. 结论与边界

实现与最终自动化自验通过，正式验收仍BLOCKED。固定旧Stable外部监督真实启动Claude源码审核，UUID7a25b60f-e153-43b3-8ea8-ccbd3d0a1972，PID979300，55.870秒exit1，原始错误：`Prompt is too long · automatic compaction failed: summarization produced empty response`。原始verdict=null，没有有效ACCEPT，也不是Claude给出的BLOCKED裁决。完整源码、合同、原始失败和固定副本交给实际CLI；通过源代码数据包减少Read往返，但仍触发原生上下文/压缩失败，不能假称已完成源码复核。

固定旧Gate真实读回exit2/BLOCKED，原始review为空时在schema检查拒绝，后续验证未执行；必需验证规格保留，未传假ACCEPT或空合同。WX05当前最终正向复杂运行仍失败，WX06/SUP01缺有效独立裁决；SC08修复待独立关闭，用户本人体验验收未完成。没有源码Ready、main合并、合同K或Stable批准声明。下一步需要可靠的原生工具参数与上下文/压缩链路，再在新明确授权的受控预算中复核，不能再消耗本任务已用尽的恢复预算。

唯一推荐入口：`python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`，已实际核对进程/HTTP/源码/配置，当前Development http://127.0.0.1:46579，候选e33323b9fdb9。原入口备份、旧Stable/工作树/运行服务/会话/认证保留。原生审核HOME/cwd/config/session与记录位置见机器摘要，未使用--last或复制全局HOME。

## 5. 过程记录

|时间|事件、失败与纠正|
|---|---|
|2026-10-27|核对仓库remote/工作树/旧记录，创建独立Task11；Opus5.5 1M/high连通成功。|
|同日|SC08旧候选8反例失败；当前12通过。早期测试替身误截Git命令而非工作进程的错误保留，修正夹具后重新对照旧/新候选。|
|同日|首候选903中旧PID夹具1错误，纯测试修正后903通过；全部原断言保留。|
|同日|浏览器初次106/1失败保留，定向1/0和完整106/0；最终903/6/225/57及两项云端CI通过。|
|同日|真实双Provider运行的240秒超时、两轮指南拒绝/上限失败、补证600秒超时全保留；无假完成。|
|同日|NativeCodex同UUID恢复后IMPLEMENTED；Claude源码审核原生压缩失败/null，固定Gate拒绝exit2，预算后停止。|
|同日|零外部推理的3项真实CLI数字传输诊断通过，无法归因本地序列化；源码推Draft、记录独立同步，不合并/晋升。|
