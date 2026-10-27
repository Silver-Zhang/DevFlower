# DevFlower 接续入口

先读STATUS与tasks/2026-10/20261027_04_native-session-interop/{README.md,contract.txt,evidence-summary.json,readiness.json}。本轮新Task13，不续Task12或更早审核预算。

源码/home/workspace/DevFlower/development/native-session-interop，flow/native-session-interop，3fbf2bac7f0e71a68276b0dc21cba38e1ef1f05f/tree8e24fc88e57064c307800f07081c3986c9c127e1/CF5ee475f7c4976c563bca68077bbc22e756c9a5ae64a55eaaba325f67873d0009，245文件。DraftPR21→PR20→PR19，不合并main，不晋升Stable。

实现本地Unix接管既有原生PTY，默认只读、明确CAS接管、旧代次拒绝、断连不重放；脚本tools/devflower_attach.py不启动第二CLI。真实非Git新建对话common_dir异常修复；复用原source_lock。完整932/6/233/59全过、245文件隐私通过；真实Codex/Claude同UUID双端回答和切换通过。监督PID与原生子PID区分。

完整目标仍BLOCKED：Codex桌面独占stdio，无可连control socket，read_thread找不到实际UUID；未改其HOME/启动/DB。工作流活动回合不能热接管，需现有HOME锁释放。Claude源码审核UUID33983e27-0cdf-4c4b-9f96-69dde484a096，固定旧Stable监督，240s+120s恢复均超时、原始verdict=null；1源码轮+1基础设施恢复后因无完成证据停止。没有独立动态反例裁决。固定旧Gate exit2，缺审核报告且原生实施完成回执未伪造。不要把此任务写成ACCEPT/完整K或用户本人验收。

新启动入口/home/workspace/DevFlower/.local/open-native-session-interop.py，Development41663；旧open-devflower-agent.py/40893保留。原始证据/HOME在archives/devflower-native-interop-20261027；测试在/home/workspace/claude_workspace/DevFlower测试/native-interop。R2和R3预览直接共用原测试storage/native，未复制会话或数据库；原服务安全退出后才切换自有测试候选。旧Stable和所有用户/历史数据不删。
