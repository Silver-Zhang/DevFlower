# 原生会话互通与接管

## 1. 目标与范围

本轮用户授权直接实施 DevFlower 与 Codex 桌面版、Claude 本地 CLI 的同一原生会话、实时可见、切换接管与防双写；保留现有会话/认证/中转配置，不复制记录冒充同步，不合并 main。新 Task13、N1–N6 合同，未续旧 Task12/04/10/11 的预算。源码从干净的 7092922 新建独立 worktree/分支 flow/native-session-interop；原 Development40893、main 和 Stable 保留。

## 2. 做法与证据

实现本机私有 Unix socket 连接既有受监督 PTY，一个原生进程和 HOME/cwd/UUID；默认只读观察，明确 --takeover 或工作台按钮转移键盘。事务记录代次、CAS、原输入序号和未知回执防重放；旧端拒绝，不启动第二 CLI/不复制原生日志。0600 能力文件、0700 目录、同 UID/进程握手、4 观察连接上限、关闭只清理本次临时能力；工作流 HOME 锁与外层只读保护不改。原生终端页可查看/收起连接命令并接管回工作台。

真实普通目录对话复现 KeyError('common_dir')，发生于模型调用前。最初单测夹具因未准备证书提前失败，保留；修正夹具后旧候选 2 ERROR，新代码 2/2 通过。修复复用现有工作流 source_lock，真实已有写锁时不调用 Provider。首浏览器新测试两例受先前回收夹具影响；移到清理前后，一例按键早于接管回执，补等待明确接管确认后最终109/0；没有删测试或改Skip。

最终同候选 5ee475f7c4976c563bca68077bbc22e756c9a5ae64a55eaaba325f67873d0009 / tree 8e24fc88e57064c307800f07081c3986c9c127e1，245 文件：932 Python、6隔离、233 Chromium/HTTP（109+119+5）、59 JS，全部 exit0/FAIL0/ERROR0/SKIP0。Baseline、6语法、构建2文件字节一致、245文件隐私检查通过。命令/日志哈希/真实退出回执在本地测试记录；机器摘要见 evidence-summary.json。

同一最终候选实际 Codex（gpt-6.1-sol/xhigh）和 Claude（claude-opus-5-5[1m]/high）共用原 UUID、PTY 和监督进程；双端实际可见回答、接管与旧端输入拒绝。监督 PID 与原生子 PID 不混称；子进程PID没有单独采集。Codex UUID 01a17c14-5be2-7493-bd10-9b0f57eeb931，Claude UUID a891c8f2-87a2-4cca-af2a-5b72511d4166。物理后端/生效effort unknown。最初信任菜单误选退出、历史读取暂不可用、Claude诊断nonce不一致与权限菜单误操作原失败均保留；明确复测成功，不覆盖旧记录。所有原始日志、截图、HOME和认证只在本地 archives 与用户指定测试目录。

## 3. 决策记录

授权为本轮用户明确实施/真实测试/开发交付，不合并main。变更卡：只复用受管终端，不开放任意命令、导入全局会话写权或扩目录。观察与接管隔离，断开不停止/重放；CLI结束后能力失效。风险是本机原生客户端缺少共享后端；未绕过。回滚可回到原Development40893，旧Stable不替换。最多5源码轮，基础设施恢复最多2；实际1源码轮+1明确同UUID恢复后停止，未无限重试。

固定旧 Stable 外部监督真实启动 Claude 源码审核，先合同/源码/差异/原始证据，再实施说明。请求 opus5.5 1M/high，HOME为本地 review-r1-final/home，cwd为固定只读候选，UUID33983e27-0cdf-4c4b-9f96-69dde484a096。R1 240秒超时exit-15，明确同UUID恢复120秒仍超时exit-15；有26次可见工具读取，无完整结构化最终裁决，也没有足够独立动态反例结论。原始verdict=null，不称Claude ACCEPT或Claude BLOCKED。没有新的可追溯完成证据，按同候选同阻塞停止；未用完的预算不是继续请求的理由。凭据内容哈希、源码均未变。

## 4. 结论与边界

整体 **BLOCKED**，只交付已验证的本地 CLI 共享部分。当前 Codex 桌面 app-server 为独占stdio，默认control socket不存在，桌面 read_thread 对上述真实UUID返回未找到；未改桌面启动配置、重启桌面、改全局DB或复制历史。默认全局HOME的会话列表不会凭空出现隔离HOME记录。工作流执行中的热接管仍未实现，必须先让当前回合释放原HOME锁。

固定旧Gate真实exit2/BLOCKED，原因为缺失审核报告在schema处拒绝（$: wrong type）；后续门禁未执行，验证规格保留。严格旧Gate的原生实施完成回执也缺，未伪造。源码检查、自验、原生CLI业务验证、独立终审、Gate、用户验收和Stable批准分开。未取得硬ACCEPT，不能宣称全任务或完整产品K完成。

源码提交 3fbf2bac7f0e71a68276b0dc21cba38e1ef1f05f，正常push并远端读回，云端Studio contracts及Usable regression的三项检查均SUCCESS；Draft PR21 https://github.com/Silver-Zhang/DevFlower/pull/21 ，依赖PR20→PR19。main/Stable不变；project/devflower另行提交记录。新入口 `python3 /home/workspace/DevFlower/.local/open-native-session-interop.py` 已真实检查，Development41663；旧入口/40893保持。操作：原生终端→在本机终端连接→默认观察或追加 --takeover；Ctrl-]断开显示；工作台“接管到工作台”取回输入。用户本人体验验收未完成。

## 5. 过程记录

| 时间 | 事件与证据 |
|---|---|
| 2026-10-27 | 核对remote/HEAD/tree/干净worktree，独立分支建档；官方协议和本机能力核验 |
| 2026-10-27 | 共享PTY/CAS实现；真实非Git对话失败定位和修复；保留初始夹具错误与浏览器失败 |
| 2026-10-27 | 最终932/6/233/59全过；Codex与Claude真实同会话接管通过；245文件隐私通过 |
| 2026-10-27 | Claude R1及同UUID恢复均超时/null；固定旧Gate exit2；Draft交付，main/Stable保持 |
