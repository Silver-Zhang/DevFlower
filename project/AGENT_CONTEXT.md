# DevFlower 接续入口

当前任务tasks/2026-10/20261028_02_codex-multi-review。用户改用Codex多Agent审核/自有QA，保留产品Claude支持、中转和原生历史。

S=development/guided-stable，flow/workbench-guided-stable，30b0a6d08c11dc0962adfa0c9af0bfd1dbdd84a5，tree d0767ed371ebd072a2df04423440f6385eb1d54b，CF a3b6a8ebe9fee7e6c299ed34a421dd88d1da5ac9cbcafcfe88c6692787f7dcc9，298files。H=archives/devflower-guided-stable-20261028，candidate-stage37。PR22 Draft，新CI待核验；旧36三CI SUCCESS不能套用；main e0a0f92，未合并。

N=H/codex-multi-review-20261028，合同cedcf84fa4453b71b7e68287b04cc64ba2c9eaf063de5d8cc3202019e97ccf9e。完整审核0/2，每轮双独立原生Codex；infra2/2不复位。旧Task15/R4/所有预算/负面保持。

DeepSeek连接本机私有持久保存，现有deepseek-flash/medium与中转/密钥保留。Development .local/open-guided-development.py仍source33，不冒充Stable。36真实问答/公开源码诊断通过；两次未声明字段提案严格拒绝，原对话明确修正后两次均完成apply，负面保留。

36全量1177Python/6host/64JS/251九套ChromiumHTTP/16旧smoke及Baseline/构建字节/语法全部通过。独立辅助153旧反例+20新反例、26UI检查/24新截图通过，B/codex-policy-counterexamples/stage36-final-precheck；不代替完整审核。Sim/Impeccable视觉字节与35相同，36仍实际重跑。

36真实CodeX顺序2/汇合3/路由1/提前退出2通过；五轮场景第8调用配置变化安全拒绝，强断言失败/exit1、复杂图未执行。H/flows-stage36-codex/strict-acceptance-failure.json说明旧JSON提前写passed的边界。下一候选需重新验真实图，不用旧版本套。

36真实browser同UUID ready后逐字/exit正常0；Desktop API读取同一原生UUID/回答哈希一致，不是记录复制，也不等于桌面GUI实时接管。共享PTY第一UUID全通过，第二claim3s超时。独立4.2s SQLite锁复现客户端3s先超时、服务端晚commit后安全释放。原生实施者UUID01a1810f-48a5-7d81-8d09-f515ad972126已修deadline，原PARTIAL因7项AF_UNIX受限保留；Root只另修标准临时目录并实际执行42项均通过。37已冻结，当前只读全回归/真实六图/独立辅助并行；不再有源码writer。流程完成前不另起CLI/TUI或终局ACK干扰配置。新反例/旧失败全保留。

ST=stable/usable-v1-5816ba4776b3，CF5816ba4776b35d625e163af805d656315a7c638ba4e60e576763b121d86b3821，Gate SHA12f0b6aaf8d257e1ce0b87346e2f328089ab28fc96c52691719b7f28787630ab。固定旧组件自举，不是当前Controller自批。B/native_codex_final_review_current.py准备双完整只读原生审核，证据READY后才启动。

剩余：统一deadline修复后冻结新候选/全量测试/真实六图与完整同UUID终端/K、双硬ACCEPT、旧Gate、条件式merge/main集成与正式Stable。B/proposed-stable-launcher.py已独立测试，保护原始配置的模型偏好漂移恢复与真实进程未确认停止；没有部署/换pointer。旧Stable、会话、认证和旧源码均保留，未清分支，project/*必须保留。
