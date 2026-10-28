# 引导式工作台与正式 Stable 收尾

## 1. 目标与范围

Task15，合同 D01–D12。交付可引导使用的画布、直接模型任务、内置诊断助手和原生会话；保留既有会话、认证、Stable及原始失败。仅满足同源必需测试、真实独立硬 ACCEPT、固定旧 Gate 和产品 K 后合并/晋升。用户本人审美验收独立，Agent不代签。

## 2. 做法与证据

最新源码 `d8c402f58ad845854ec4622206f44e645db42243`，tree `f440aef51f0e3513a83bdd335ddbaa2473426a97`，289文件 CF `367e33fc48b387fdc7ee86afd925bf148c308fffb997803451dda5739692a5c3`，已隐私检查、冻结及正常push到Draft [PR22](https://github.com/Silver-Zhang/DevFlower/pull/22)。源码 tree 与冻结清单一致，提交不是验收。

阶段22：1129 Python、6宿主、61JS、Baseline、6项语法、独立构建字节一致均exit0；245项Chromium/HTTP和额外16项旧Studio smoke亦全部exit0，FAIL/ERROR/SKIP=0，见progress.json。终端定向89项通过。真实Chromium+原生Claude在本候选进入ready，原生 `/exit` 退出0，同HOME/cwd/UUID历史封存后新prepare成功；没有重放消息。浅/深色稳定页面已实际查看，旧截图的白色按钮为切换动画瞬间，未作为持续样式缺陷改代码。

阶段21 CF73f0… 的真实顺序暂停恢复2calls、并发汇合3、路由1、循环提前2均完成；五轮10calls实际拒绝如期FAILED。原QA误取 `output.accepted` 导致exit1保留；严格读取 `output.result.accepted` 的独立复核exit0。另一个文本合同复杂图3calls完成，旧模糊任务7calls后正确拒绝保留。助手问答、公开源码诊断、修复1→0及图片识别四项真实调用通过。它们不冒充阶段22整版产品K。

原生Codex阶段18/20部分失败及R1独立反例都保留。最终阶段21原生Codex UUID `01a18066-d096-7fa2-a4d2-45c1a0114fd0` 实际IMPLEMENTED，明确自验由外部root runner执行；终端阶段22补丁由root Codex实施，不假称CLI作者。中转站物理模型/生效强度未报告即unknown。

## 3. 决策记录

普通对话/工作流使用用户选定模型直接执行，不写确认意见、不额外nonce付费检查；开始点击仍固定工作区、图、任务、权限和预算，失败不自动重发。旧受控运行按其原合同保持。助手只读公开源码/帮助，修复为可预览、CAS校验、可撤销的草稿提案，不读取凭据、不任意shell、自授权限或自动批准交付。

审核预算源码3/5、基础设施恢复2/2。R1两次监督超时；R2外部driver143、native exit未知；R3真实Claude Opus5.5 1M/high在源码检查/反例后HTTP502，1411.756秒exit1，三轮均无最终裁决，不改写为ACCEPT。没有重试旧请求。原K run `5807154ccb6046459b6f654123340256` 保持BLOCKED，父固定监督真实开发/修复/非零测试/PR闭环是否足够，仍须独立判断。

## 4. 结论与边界

**尚未正式验收，PR22保持Draft，没有合并main或晋升Stable。** main仍 `e0a0f92fed7f94bfef3a01b1298f8465c008023d` / tree `6c9e39e16bbbe7de95b894e0a58632b1a0f63039`。旧Stable安装和批准不变；实际启动因旧pin固定的Codex auth文件已轮换而拒绝，没有偷换pin或覆盖认证。

本机新Development：`python3 /home/workspace/DevFlower/.local/open-guided-development.py`，实际版本d8c402f/端口38279；`--check`已验证。打开文件夹→选择示例或创建节点→配置Provider/目录模型/模式/提示词→填写任务→“检查工作流”→“开始运行”。警告可定位问题并发送AI；助手只问答不会改画布，设计提案需预览应用。下一任务复用设计、新建run，旧记录在“整轮对话”按工作流/任务/节点查看；项目 `.devflower` 保存阅读记录，原生历史仍在原HOME。

任意外部裸Claude CLI不受工作台租约控制；受支持PTY路径才可承诺单写/接管。Desktop同原生Codex UUID已被实际读取，但桌面实时刷新/强制接管未完成验收。普通原生模式的Skills/子agent未实测支持时明确不可用。极端无效图256条检查截断及助手API任意选定模型转发是独立R3反例观察，尚无正式裁决，不冒充已关闭必需finding。

所有flow分支尚未合入main且被worktree使用，无符合“已整合且闲置”的安全删除对象。project分支、18个worktree、历史证据和原生聊天未删。完整剩余项见progress.json，不能把剩余都简化成CI。

## 5. 过程记录

- 2026-10-28：原生多阶段实施、真实产品K尝试、R1失败及两次恢复，全部原件保留。
- 2026-10-28：阶段18至21按实际反例修复历史Profile、超时继承、原生能力探测、助手冻结问答和历史长标题；阶段20/21各两项CI success。R2/R3仍无裁决。
- 2026-10-28：阶段22仅修实际Claude plan-footer readiness及澄清过时诊断说明；真实终端/正常退出/同UUID再准备通过，完整1129/6/61/245回归通过，源码远端HEAD/tree一致，云端两项CI success（40633427959/40633427963）。原始日志/截图/native记录仅在本机archives；公开摘要同步project/devflower。
