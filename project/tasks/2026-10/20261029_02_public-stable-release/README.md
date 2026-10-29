# 工作台预览发布与后续问题定向修复

## 1. 目标与范围

原目标为完整验收后整合main/develop、正式Stable发布。用户最新决定优先发布供Dot测试，停止无休止纠错，后续按Issue逐项修复；本轮交付明确的Prerelease，不伪称最终审核通过。保留project分支、旧Stable、会话、认证和全部失败。

## 2. 做法与证据

已发布v2026.10.29-preview.1，源码develop 154a153edde379586fc1b5aa03c6b898b9cf9f1b，tree 4151c85f739aa3855d51a6fdfdc2dce04566691c，完整SHA256 64728274cad007acb5d6d647cc0c094d0e06044a68205e612bea50df9f6ab7cc，333文件。附源码tar、manifest、SHA256SUMS，远端tag剥离提交与三个附件哈希均读回一致；不上传原生会话/私有令牌/原始大日志。

当前最小修复由同一真实Codex实施会话完成：无关Claude登记模型缺强度信息不再屏蔽已发现模型的明确冲突。修前3项真实失败、修后3项通过；原生环境完整模块26项fixture初始化错误保留，Root外层运行全部30项通过。Provider/effort13项、隔离6项和baseline通过，最终针对性检查FAIL/ERROR/SKIP0。首次Provider命令缺PYTHONPATH导入ERROR保留，正确命令13项通过。

前一source15 eeadaa7完整22命令回归1533Python/6host/73JS/266Chromium-HTTP/16smoke全0；不是source16完整回归。source15真实六流程21调用、managed3会话/12核验、助手4请求及一次源码诊断补件成功。Terra原收尾timeout失败保留，新一次任务补证通过。AI图片复杂图7调用、最多两轮仍有未关问题，真实FAILED；负面不改成通过。

## 3. 决策记录

原正式R1 source11为A ACCEPT/B BLOCKED；R2 source12 A/B均BLOCKED，D04旧身份目录误报及D08原件漏包原意保留。13/14修复、15README、16本次Claude局部目录诊断修复各有独立身份，不将旧ACCEPT套到新候选。源码审核预算仍2/5，R3未启动，固定Gate未运行，K最终未通过。

用户改为先交付测试，再按Dot可复现Issue点对点处理。因此不继续新增全量模型运行、审核轮或大范围设计；后续功能/缺陷逐项立项。源码可公开供测试不等于可信Stable批准。独立合成预览复用项目既有浏览器fixture，空HOME/假会话，禁止真实CLI、工作流与终端，令牌仅本机0600保存。

## 4. 结论与边界

[预览版](https://github.com/Silver-Zhang/DevFlower/releases/tag/v2026.10.29-preview.1)已实际发布；[PR24](https://github.com/Silver-Zhang/DevFlower/pull/24)保持Draft。main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d/tree6c9e39e16bbbe7de95b894e0a58632b1a0f63039，正式Stable accepted-cfea19cac598保持。main/develop未统一；不声称已完成原正式晋升目标。用户体验待Dot和用户实际确认，桌面历史可见不等于桌面实时PTY接管。

## 5. 过程记录

本机archives/devflower-public-stable-release-20261029保存原始证据与全部负面；测试仅指定DevFlower测试目录。记录分支仅提交精简摘要与机器记录。合成预览在test/ui-preview16下，不使用真实会话。后续Issue应包含tag/commit、环境、复现步骤、预期/实际及必要截图，禁止提交密钥/私有网址/会话全文；不继续自动扩大测试或纠错。
