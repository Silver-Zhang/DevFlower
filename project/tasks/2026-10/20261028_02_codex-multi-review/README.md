# Codex 多 Agent 复审与条件式 Stable 收尾

## 1. 目标与范围

用户明确本任务不再调用Claude审核，改为Codex多Agent。保留旧Task15/R1–R4及BLOCKED/失败/预算。新任务guided-stable-codex-audit-20261028，最多2轮完整源码审核、每轮2个独立原生Codex会话；基础设施恢复2/2已用。必须完整产品合同、真实合同K、独立硬ACCEPT和固定旧Gate通过后，才可合并/正式Stable/清理已合并闲置分支。用户新增：内置助手默认DeepSeek，设置一次跨重启/工作区/版本保存；重申全页面视觉要求，不能以按钮和配色修改冒充完成。

## 2. 做法与证据

最新源码ac05aecaf481b55038711b3f7a4bb82f0adc3dee，tree f2f81a9388f0e8f31ec833b56e374fd10e6bd2e9，294文件，候选SHA256 a8cc42108ef8d30a05dd08069d788eb51b5c180db8f471fe38f733a7b728f426。源码flow/workbench-guided-stable已正常推送并git远端读回；PR22仍待最终验收。

此前真实原生Codex UUID01a1810f-48a5-7d81-8d09-f515ad972126修复reviewer provider兼容、保护目录held-FD语义和真实任务目标绑定。Root另修扫描上限/首次扫描失败关闭、空Git占位目录、AI新节点任务目标默认与嵌套夹具；署名区分，不冒充全部由原生Agent实施。已有真实顺序/汇合/Router/循环记录保留原身份；不用于新候选通过证明。source28歧义条件循环5轮拒绝和上限失败原件保留。

本次助手连接改为本机私有持久设置；默认DeepSeek独立API，不借用CLI认证；模型栏选择保存默认，连接页面保存后立即采纳新模型。旧显式DeepSeek连接已迁移，原件/密钥/中转配置未覆盖；新Development真实HTTP读回deepseek-flash/medium且不返回密钥，0新增模型调用。

source32完整Python1165项，1FAIL/0ERROR/0SKIP：新增模块漏公开源码清单。source33仅补清单一项，相关20项通过；此处不把首次失败改写成完整回归通过。source32完整工作台Chromium/HTTP128项、host6项、JS64项、Baseline通过；之前助手定向52、Provider27、持久设置11项通过。source33辅助独立Codex自建16项持久化/迁移/CAS/并发/链接/权限/保护根/worker反例全通过，exit0、FAIL/ERROR/SKIP0，源码前后不变；不替代最终完整原生审核。

## 3. 决策记录

原合同4dd4ae7d…原件保留，首次正式源码审核前追加用户DeepSeek持久化要求，新合同SHA256 cedcf84fa4453b71b7e68287b04cc64ba2c9eaf063de5d8cc3202019e97ccf9e。完整源码审核尚0/2，不偷续旧任务第六轮。独立辅助发现并修正两项：设置保存后仍沿用旧模型、worker丢失自定义设置保护路径。

旧Development的Codex配置指纹已变化，新source32启动拒绝原样保留；source33使用独立新启动配置绑定当前本机配置，不更改旧operator/认证/全局配置，不冒充Stable。旧服务、旧Stable和所有原生记录保留。

## 4. 结论与边界

尚未完成整体验收。全页面视觉精修、同一最终候选的真实复杂流程/合同K、两名原生Codex完整硬ACCEPT和固定Gate仍待完成。main仍e0a0f92，Stable未更新，未合并、未清分支。用户本人视觉验收未代签。Development入口python3 /home/workspace/DevFlower/.local/open-guided-development.py已实际验证，当前source33，仅Development。

## 5. 过程记录

- 2026-10-28：用户切换Codex审核，保存原始Provider授权及新预算，保留旧负面。
- 2026-10-28：修复保护目录真实竞态/删除误拦、Git占位目录和任务目标丢失，冻结各独立候选与真实QA身份。
- 2026-10-28：实现DeepSeek默认与跨版本连接持久保存；保留并修正首次浏览器未重构建失败、完整Python清单失败、独立辅助两项问题及Development配置漂移拒绝。
- 原始大日志、截图、API连接、会话与失败原件仅留本机archives和独立test工作区，不进入本记录分支。
