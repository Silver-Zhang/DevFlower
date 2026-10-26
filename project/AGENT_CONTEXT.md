# DevFlower 接续入口

先读STATUS及tasks/2026-10/20261026_03_assistant-dock-model-selection/README.md、evidence-summary.json、review-r1.json。

开发源码 /home/workspace/DevFlower/development/copilot-canvas-assistant，flow/copilot-canvas-assistant，HEAD 98ce61a5bb1fe87aa98e7b6c6de2d4178945bc5f，tree e457f0c1723bed3817a83b88a58f7e369e9aec23，CF 0d181909923ae5c8b87afc844083bf9b810fd51717b711c84334e079001ff54c，218文件，Draft PR20依赖Draft PR19。

当前AI助手为右侧常驻页签，不弹窗；内置专属Agent独立API，不调用编程CLI。会话管理/恢复保留草稿；模型目录直接选择保存偏好，无额外配置确认，执行准入与权限保持。安全哈希缓存不按TTL跳过校验。当前815/6/201/56零失败；真实两次API+同turn补充观察，原失败脚本保留。

Claude Task07 UUID fb2d921b-0e5a-427a-946c-1ba992184f1f，请求opus5.5[1m]/high；实际模型/effort unknown；HOME/cwd/native路径在review-r1.json。180+60秒两次无有效裁决后已停止，不在旧任务隐式加轮，不无声换模型。

本机入口 python3 /home/workspace/DevFlower/.local/open-devflower-agent.py 指preview-finalized；完整原始证据 /home/workspace/DevFlower/archives/devflower-assistant-dock-20261026。旧入口备份previous-launcher.py，所有旧版本/原生会话/认证保留；不覆盖Stable。

旧Task04 SC08仍OPEN、R1–R5与Gate负面意见原样；Task05/06超时原记录保留，不复用旧ACCEPT。当前独立Gate前置条件不足。记录仅project/devflower，不入源码flow/main；CodeWeave/RMC不动。
