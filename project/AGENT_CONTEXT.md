# DevFlower 接续入口

先读STATUS和tasks/2026-10/20261027_02_claude-recovery-finalization/README.md、evidence-summary.json、readiness.json、contract.txt。旧Task04 R5 NEEDS_CORRECTION、Task10 502/null及其预算不改。

源码 /home/workspace/DevFlower/development/copilot-canvas-assistant，flow/copilot-canvas-assistant，HEAD742c61f4bda27e40d641a05ea5a364f6c2585c12，tree7e1992f75109ae5e542b87f97c5ea1cf24d772ef，CFe33323b9fdb92eebdc5a14b74985f77c7d3349523792abcbc7e3a74cdf59bb24，239文件；DraftPR20依赖PR19。main仍e0a0f92fed7f94bfef3a01b1298f8465c008023d，Stable不替换。

Task11修复SC08四外层Popen发布失败：引用先保留，失败精确停止并wait，无法写失败记录也先清理，保守未知不重发；终端FD/无reader失败处理。12实际OS子进程反例通过，旧候选8失败。903/6/225/57与同HEAD两CI最终通过。首主浏览器1次400根因未确认，原断言完整复跑106通过；保留原负面资料。

Opus5.5 1M/high nonce5.694秒成功，但复杂工具不稳定：本候选原生6业务调用两轮拒绝达到上限，后补证在Claude600秒超时，反复巨大Read.offset。本地3种数值SSE+真实CLI模拟输入正常，外部模型调用0，只排查传输不能作真实模型验收。图片DeepSeek实际9节点10边；助手模型不随Claude审核设置变化。

NativeCodex当前候选同UUID恢复后IMPLEMENTED。Claude源码审核R1 UUID7a25b60f-e153-43b3-8ea8-ccbd3d0a1972，PID979300，55.870秒exit1，Prompt too long / empty compaction，verdict=null。固定旧Gate实际exit2/BLOCKED，尚未独立关闭SC08、WX05/06。Task11已1源码轮/2基础设施恢复，不能偷续外部请求；新用户指令明确授权且满足恢复条件后才新受控身份/预算。当前不合并main或批准Stable。

入口 python3 /home/workspace/DevFlower/.local/open-devflower-agent.py 指Development /46579。工作区测试根 /home/workspace/DevFlower/test/20261027-claude-recovery-finalization；未来新增工作流/持久HOME/保留证据统一workspace/test，普通单测/tmp会自动清理。源分支只正常推flow/copilot-canvas-assistant，记录只推project/devflower；原始大日志/截图/会话/凭据不上云，不修改CodeWeave/RMC，不删除旧版本。
