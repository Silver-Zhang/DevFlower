# DevFlower 接续入口

当前 tasks/2026-10/20261028_02_codex-multi-review；仅 Codex 多 Agent 审核，保留 Claude 产品支持、中转、原生库及全部失败。不要重新调用 Claude 做审核。

源码 S=development/guided-stable；K2=/home/workspace/claude_workspace/DevFlower测试/guided-stable/product-k-final。两者 clean HEAD4eff18541562194a0ebda917dc2d981bfc415600/tree973aea109c040389f09ff00bd318b3f548569189/CF6e0cbd037f9c5261b3244456cbcf87ad034257934af1fc68500e80e7426262fc/306files。H=archives/devflower-guided-stable-20261028；B=上述测试guided-stable；N=H/codex-multi-review-20261028。冻结 candidate-stage45；PR22 Draft 已推送，main e0a0f92未动。

合同8032befda2fe2856ecdb02910c0d0449dc31b7df44baa618c520b2e74540d837；完整双审0/2、infra2/2，不复位。旧 Task15 R1–R4/NEEDS_CORRECTION/Gate exit2 及所有失败原样保留。预检不算完整源码 ACCEPT 或 Gate。

45完整1239Python/6host/64JS/253ChromiumHTTP+16旧smoke，21命令全exit0、FAIL/ERROR/SKIP0；三CI SUCCESS。DeepSeek4真实场景COMPLETED。H/actual-stage45-supervision按六图→AI图片复杂图→两UUID同PTY→browser→host顺序执行，不因等待重启付费请求。

44六图21真实调用按合同通过；AI44 selector重名未建run，修外部QA后真实7calls/max2 FAILED，独立Agent发现省略带合同节点会丢合同。Root零调用复现；原生同一实施UUID01a1810f-48a5-7d81-8d09-f515ad972126真实修2文件，14纯测试、Root165、辅助20全exit0，旧empty合同assert保留。45补workbench_store.py公开资料后重验，不套旧通过。

awesome-design-md为上游MIT84份DESIGN.md适配skill，pin3b605329…；共享theme.css/实际UI与docs/design/WORKBENCH_DESIGN.md及来源许可均已入源码。独立UI pass5对应43，44/45的UI字节相同；18面板52截图和8原target+2延迟/刷新+7camera+8chat通过。用户本人审美未代签。旧41/42清单和UI失败、43云端WAL测试失败/撤回保留。

Development唯一入口 python3 /home/workspace/DevFlower/.local/open-guided-development.py；已实际核验source45/41761。旧Development/Stable安装、服务记录和入口备份保留。全局DeepSeek私有连接/home/silver/.config/devflower/devflower-agent-connection.json，原deepseek-flash/medium/relay/key不变；不输出密钥。

Claude统一中心读取原HOME/.claude/projects。实际3条桌面Code metadata通过cliSessionId对应同一CLI历史；不同app/session ID不可按名称合并。此为只读关联，不是桌面实时接管或外部裸CLI双写验证。Codex45实际Desktop read_thread对应原UUID、原回执hash；H/native-desktop-shared-stage45.json，不复制原始会话。首次QA canonical编码断言失败留qa-note，无模型重放。

固定监督ST=stable/usable-v1-5816ba4776b3，CF5816ba4776b35d625e163af805d656315a7c638ba4e60e576763b121d86b3821，Gate SHA12f0b6aaf8d257e1ce0b87346e2f328089ab28fc96c52691719b7f28787630ab。辅助误import产生3pyc已精确核对并隔离，原CF恢复、incident完整保留；所有Python使用-B。

B/prepare_final_packet_task16.py只整理PENDING；须真实全部证据、核验/隐私检查后外部seal，再B/native_codex_final_review_current.py两个新UUID。正式双硬ACCEPT后各跑旧Gate，再按授权普通merge/main ff-only/集成回归/正式Stable。原生标准实施45终局IMPLEMENTED/exit0/source前后相同。

B/deploy_task16_v4.py + task16_launcher_v4.py：作者44/独立46纯fixture通过、EXTERNAL_DRAFT_PRECHECK_PASS，未部署。真实发布须当前两审核/两Gate、严格v2provenance、批准/argv绑定、持久启动intent、原始baseline FD、原存储备份与原生/认证身份保存。指针和入口各自原子替换，非一对原子。旧草案v1–v3负面保留；仍不许覆盖Stable或删除会话/证据。仅完成后清已合并闲置非project分支，保留使用中的worktree。
