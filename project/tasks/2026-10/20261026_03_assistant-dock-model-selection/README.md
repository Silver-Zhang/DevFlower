# 侧栏助手、会话管理与直接模型选择

任务 `20261026_03_assistant-dock-model-selection`。模式、状态、版本以 task.json 为准。承接用户三点界面反馈，不延续或改写旧 Task04/05/06 审核预算与原始意见。

## 1. 目标与范围

AI助手成为右侧工具/示例模板并列页签，保留上下文；Sim式紧凑对话、独立设置子页、会话管理、浅深主题。去除Agent模型选择后的额外配置确认。查明并修复本地卡顿。基于d3ccd925，保护原生会话/认证/Stable，不重写Controller、不合并main。

## 2. 做法与证据

17文件增量：常驻助手页签/Markdown消息、草稿保留、历史搜索/切换/改名CAS、归档和可恢复回收站；拒绝对活动/未知请求归档或删除，未知请求提供停止入口。新增本地直接模型选择API，沿用原账号/Profile/effort/目录验证，不授予执行权。目录发现和实际原生执行准入保持分离，旧接口兼容。已选模型仍展示所有原生强度。

状态API此前每次重复读取502405979字节依赖，约1.1秒；现在内核inode/ctime/mtime/size/mode/nlink约束哈希复用、每次重新打开和枚举目录，约0.08秒。等长改写恢复mtime、替换inode、软硬链接、成员/权限变化与读取中突变反例覆盖，未按TTL跳过安全检查。助手目录缓存60秒，可刷新；切会话不重查目录。

最终218文件CF `0d181909923ae5c8b87afc844083bf9b810fd51717b711c84334e079001ff54c`，tree `e457f0c1723bed3817a83b88a58f7e369e9aec23`，HEAD `98ce61a5bb1fe87aa98e7b6c6de2d4178945bc5f`，正常推送PR20。同候选命令：PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v（815）；python3 tests/host_isolation.py（6）；tests/workbench_browser.py（91）及七个其他Chromium/HTTP套件（110）；node --test三套前端（56）；check --baseline、6项语法、前端重建字节一致、真实凭据与运行文件隐私检查通过。全部exit0、FAIL/ERROR/SKIP=0；21新增Python测试属于815，6新增画布浏览器用例属于201。

真实API/Chromium单列：同候选2次助手调用实际生成画布，0次工作流Agent启动；查看命名历史、归档恢复、切页签/刷新保留输入、读旧会话不重套旧图通过。原观察器错误要求回复包含流程名而超时；两次中间观察器恢复格式问题保留，第三次按原2个turn、原实际草稿恢复观察完成，新增模型调用0，不伪称原观察全部通过。最终证据 acceptance.json指向原proof及continuation3，各原始文件保留。实际Codex目录的gpt-6-astra直接选中/low，推理0次、execution_allowed=false；不把目录选择当执行成功。本轮未重跑旧Task06 Copilot业务流程，不复用旧候选为本轮验收。代理操作不代表用户本人接受。

新Claude Code源码R1 UUID `fb2d921b-0e5a-427a-946c-1ba992184f1f`，请求claude-opus-5-5[1m]/high、实际后端/effort unknown，独立只读副本及固定旧监督器；180.113秒timeout/exit-15、stdout0；同HOME/cwd/明确UUID恢复60.134秒timeout/exit-15、stdout0。两次无有效裁决后停止；原始verdict=null，不称为Claude已判BLOCKED。详见review-r1.json。日志/截图/完整会话仅本地 `/home/workspace/DevFlower/archives/devflower-assistant-dock-20261026`。

## 3. 决策记录

授权为2026-10-26本次三点UI反馈及既有开发提交/推送/Draft送审许可。B变更卡：只改可用性、本地选择和重复校验性能；依赖身份缓存涉及安全，使用无TTL元数据失效与对照测试，原工作区权限、账号认证、会话租约、真实连接与交付门禁不放宽。模型选择只保存本地偏好，不伪造执行准入。删除仅回收助手对话，不删除原生历史。保留旧测试，更新仅反映新文案/控件，增加更强的多强度断言。 同HEAD两项GitHub CI均成功：[Studio contracts](https://github.com/Silver-Zhang/DevFlower/actions/runs/40275533921)、[Usable regression](https://github.com/Silver-Zhang/DevFlower/actions/runs/40275534026)。回滚用正常revert，保留所有数据，不reset/clean/force。

## 4. 结论与边界

当前功能、自验、真实API与本机操作已完成；正式验收BLOCKED：独立Claude两次有界调用无最终裁决。固定最终Gate未调用（前置条件缺失），没有虚构Gate退出码。旧SC08进程监督P1仍OPEN，PR20/PR19保持Draft，main/Stable未改。模型服务回复时间不属于本地0.08秒指标，用户本人视觉验收待完成。

本机仍用 `python3 /home/workspace/DevFlower/.local/open-devflower-agent.py`，已验证指向新Development；旧入口脚本已备份，旧版本和原生会话保留。使用说明随源码docs/user/assistant-and-copilot.md；记录仅project/devflower，原始大数据本地。

## 5. 过程记录

| 日期 | 真实结果 |
|---|---|
| 2026-10-26 | 核验d3ccd925干净开发分支、旧Stage06记录与现有服务，建立新任务 |
| 2026-10-26 | 复现重复配置与每状态读约500MB/1.1秒，改侧栏/会话/直接选择/受约束哈希复用 |
| 2026-10-26 | 保留导入名错误、旧文案断言、历史加载时序、测试数据强度及模型读取未完成断言等失败；没有删除测试或改Skip |
| 2026-10-26 | 同候选815/6/201/56全通过，2次真实API及同turn补充观察通过，元数据目录选择未推理 |
| 2026-10-26 | Claude180秒与同UUID恢复60秒均无有效裁决，按停止条件保留Draft |
