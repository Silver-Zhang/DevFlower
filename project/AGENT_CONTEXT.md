# DevFlower 接续入口

先读 STATUS，再读 tasks/2026-10/20261025_01_stable-promotion/README.md 和 validation.json。旧任务04/06及旧 BLOCKED 不改写。

源码 main 工作树 /home/workspace/DevFlower/DevFlower，当前 `e0a0f92fed7f94bfef3a01b1298f8465c008023d` /tree `6c9e39e16bbbe7de95b894e0a58632b1a0f63039` /候选 `cfea19cac59824ca0727e0c21ff240a5b62e1e0c228da7e033c8ae34df139fbe`，183 文件。PR18 已普通合并，源码分支清理；development/stable-promotion 目录保留并 detached 最新main，原生 cwd 不搬迁。records 在 project/devflower，通过普通 merge 同步main，不能回流main；project/codeweave不动。

正式入口：python3 /home/workspace/DevFlower/.local/open-devflower.py；--check 查看批准与身份。只读安装 stable/accepted-cfea19cac598，批准来源与外部Gate/原始报告位于 archives/devflower-stable-promotion-20261025/deployment。原 Stable5816、旧日常包7b0c及旧指针/脚本/DB备份均保留。storage/native-homes 沿用 archives/devflower-model-admission-20261024/stage-final-r2 原路径；禁止清理活跃 archives/原生会话/认证。

本任务3轮审核，R1/R2 NEEDS_CORRECTION，R3真实Claude硬ACCEPT；基础设施恢复已2/2，用完。固定旧Stable Gate最终exit0。被审源码与当前main完整tree/CF一致，589Python/6宿主/169浏览器/20JS全0失败错误Skip，真实模型流程与正式安装实际顺序/汇合均通过。用户名义体验验收尚未进行。

按时间项目记录可用，通用Studio结束自动归档project仍未实现；生产external_steps为空，自动业务交付未启用。本次PR来自获准外部监督。当前任务停止在已授权正式晋升，不自动扩展功能。

原始本地证据 archives/devflower-stable-promotion-20261025。实际CLI模型Codex gpt-6.1-sol/xhigh、Claude claude-opus-5-5[1m]/high；物理后端/实际effort unknown。恢复使用机器摘要所记原HOME/cwd和明确session UUID，先排除双写，不使用--last。不删除旧会话、改写负面结论或以新结论套用旧候选。

2026-10-25 后续维护：用户明确要求连原生历史也删除，任务20261025_02已清理当前工作台7个旧业务会话（4Codex/3Claude）的JSONL及专用索引，业务会话列表为空；不是全机删除。旧晋升任务保留原文，不能据旧manifest声称这些后来获准删除的7份仍存在。独立审核会话、验收档案/运行记录、全局认证及当前Codex聊天未删。用户自测步骤见任务02 README第4节，尚未用户验收。
