# DevFlower 接续入口

1. 先读STATUS和当前任务五节README。源码develop在development/workbench-design-system，main在DevFlower，本目录为独立project/devflower记录。
2. 最新用户决定：先发布供Dot测试，不做无休止纠错；后续根据Issue逐个定向修复，不自动恢复全部审核/晋升目标。
3. 已发布v2026.10.29-preview.1（Prerelease，非可信Stable），HEAD154a153edde379586fc1b5aa03c6b898b9cf9f1b/tree4151c85f739aa3855d51a6fdfdc2dce04566691c/CF64728274cad007acb5d6d647cc0c094d0e06044a68205e612bea50df9f6ab7cc/333文件。
4. 当前两文件Claude partial-catalog漏报由真实Codex实施；Root外层30+13+6及baseline通过。前一source15完整1533Python/6host/73JS/266HTTP+16smoke仅证明source15；真实AI图7调用到两轮上限FAILED保留。
5. 旧source11 R1 A ACCEPT/B BLOCKED、source12 R2 A/B BLOCKED，2/5轮；当前R3未启动/无硬ACCEPT/Gate/K正式通过。旧报告、PARTIAL、超时/失败、原生会话和认证不改。
6. PR24保持Draft；main e0a0f92与正式Stable accepted-cfea19cac598不变，develop尚未与main统一。不把预览发布说成Stable晋升或整版验收。
7. 只用Codex多角色，不调用Claude模型；产品Claude支持与中转保留。原生实施会话01a18582-e292-76b1-afd8-20c5a58aff38，Sol/xhigh，物理模型/effective effort unknown。
8. 合成预览使用项目测试fixture，空HOME/合成会话、只监听localhost、Bearer令牌仅本机；真实执行/终端关闭。数据在指定DevFlower测试/test/ui-preview16。Dot负责新手/视觉/交互测试并用Issue反馈。
9. 禁止force/reset/clean、覆盖旧Stable/用户修改、删除原生会话或整分支project回流。原始大日志/截图本机archives，公开分支只精简记录，后续main正式集成/晋升要如实满足或重新明确任务边界。
