# Copilot 节点与 DevFlower 专属助手分离

任务 `20261026_02_provider-assistant-separation`，B 模式；状态、版本以 task.json 为准。新用户纠正产生新身份，不延续或改写旧 Task04/05 预算与裁决。

## 1. 目标与范围

Copilot 是工作流 Agent 执行工具；DevFlower 助手是软件自己实现的 Agent，拥有独立对话、专用系统指令和闭合画布工具，通过独立模型 API 推理，不调用三个编程 Agent CLI，不宣称离线权重。基于 eeb6189 的现有 PR20 增量开发，依赖 PR19，不合并 main 或晋升 Stable。已有会话、认证和证据保留。

## 2. 做法与证据

31文件更新，新增独立 API Agent、隔离的 Copilot 执行适配器、固定 worker、私有连接 CAS、原生历史读取及27项边界测试。助手真实两次聊天生成并修改画布，执行 Agent 启动0次。真实 Copilot Studio 顺序 run `5e9ea7c0e4b55d70d7627a8f5cbfe5ef` 完成2次业务调用、1次节点间交接；刷新不重放，另一次恢复使用相同原生UUID。两个节点实际 native user.message 与 UI 实际请求逐字哈希匹配。请求 Auto，usage回报 gpt-5.6-luna；物理后端与有效 effort unknown。助手请求/回报 claude-opus-5-5、请求high，有效effort unknown。

最终完整候选 `e8126d502d486b37c6b99fa0e7ab5dba7427edc19da97d3dae5cf0e09c85e499`，tree `475ef880731fb8e48c99013cb21af6b3af9e24d5`，214文件；commit `d3ccd925ca103f9f8b89fd8f60e40f615db33d7c` 已正常推送。命令和结果：PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v：794；python3 tests/host_isolation.py：6；tests/workbench_browser.py：85；七个其余 Chromium/HTTP 套件：110；node --test 三套前端测试：56；check --baseline、6项语法、前端重建及产物逐字一致、已知真实凭据与运行文件检查均通过。全部exit0、FAIL/ERROR/SKIP=0。27新测试属于794，不重复相加。普通 Chromium 与真实模型证据分列，均为本地Agent代操作，用户本人体验尚待确认。

本地完整原始日志、失败截图、源码副本和原生会话在 `/home/workspace/DevFlower/archives/devflower-provider-assistant-20261026`，不上传。机器摘要 evidence-summary.json、合同 acceptance.txt、逐候选身份 candidate-history.json 可追溯。最初一轮完整回归在并行编辑源码期间触发正确身份拒绝，属于无效测试安排；真实失败原日志保留。R1浏览器3失败（标签与旧后端替身）；R2真实工作流发现无HOME登录查询污染冻结候选，运行器源码身份检查正确拒绝（不是固定最终Gate）；已修复专用临时HOME/state/cache，原变动副本保留。Prompt schema边界红绿测试也保留，最终记录包括真实发送的完整可见指令。

独立审核新R1真实Claude Code会话 `fd7b6dac-f5a7-4eeb-9450-e3a0185b0b56`，请求 claude-opus-5-5[1m]/high，固定旧监督器 usable-v1-5816ba4776b3，独立只读完整候选、480秒上限，隐式重试设0。原生执行428.907秒后exit1、监督器timeout=false，可见原始错误Request timed out；24次Read/1次Bash后未取得结构化最终裁决。同一HOME/cwd/UUID恢复180秒，timeout=true、exit-15、stdout0，仍无有效裁决。原始verdict=null，不伪称Claude给出BLOCKED。重复同一阻塞后停止；新Task06仅1源码轮+1基础设施恢复，预算和所有原始失败保留。固定最终Gate未调用（独立裁决前置条件缺失），监督器readiness=BLOCKED。

## 3. 决策记录

授权依据为2026-10-26用户纠正：Copilot接Agent模块，专门创建DevFlower内置助手，外部Agent提供接口/MCP。沿用既有正常开发提交/推送/Draft送审授权；不使用本任务批准main合并或Stable。B变更卡：认证broker/独立模型连接风险；保留目录发现与执行准入分离、单写入者和强制隔离。先完成真实反例与最小修复，不换模型或覆盖全局配置。原生TUI、写入、归档/删除/分叉未验证即禁用。外部接口复用HTTP计划/schema/tools与原严格校验，不声称MCP已实现。

回滚为正常revert开发提交，保留所有原生会话/证据/旧候选；不reset、clean、force-push。独立审核最多5轮/基础设施恢复2次，重复同阻塞无新证据则停止，不能逼迫ACCEPT。

## 4. 结论与边界

自验和真实功能闭环通过；正式验收仍阻塞：两次有界原生审核调用没有有效最终裁决。固定最终Gate未调用，不伪造退出码或批准。PR20保留Draft：[PR20](https://github.com/Silver-Zhang/DevFlower/pull/20)。继承Task04的SC08外层Popen日志失败孤儿P1仍OPEN，旧Task04/05负面结论不改写，本轮不关闭整版产品阻塞。main和Stable保持原版本。

本机入口 `python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`；--check已核验固定候选、进程身份、配置哈希和实际HTTP状态。当前Development页面 http://127.0.0.1:46019；凭据只在本机日志/私有存储。助手点击AI配置；Copilot在节点基本设置的执行工具中选择；详见源码 docs/user/assistant-and-copilot.md。原入口和运行版本未覆盖。

## 5. 过程记录

| 时间 | 事件、失败及纠正 |
|---|---|
| 2026-10-26 | 核对干净开发分支、main与远端；保留旧Task05误解实现和超时审核 |
| 2026-10-26 | 明确独立API Agent与Copilot工作流Provider的职责，建立PA01–PA08合同 |
| 2026-10-26 | 原生目录只返回Auto；管理HOME下创建/明确UUID恢复成功 |
| 2026-10-26 | R1/R2失败和冻结副本保留，修复标签/测试边界、登录state污染和实际请求绑定 |
| 2026-10-26 | R4同候选794/6/195/56全部通过，真实两轮助手+顺序Copilot+恢复通过 |
| 2026-10-26 | 正常提交推送d3ccd925；新Task06独立Claude R1及同UUID恢复均无有效裁决；按停止条件停止，PR20保留Draft |

两项远端CI均通过：Studio contracts 40262088343、Usable regression 40262088339。研发记录仅同步project/devflower；原始日志/截图/原生会话留本地。
