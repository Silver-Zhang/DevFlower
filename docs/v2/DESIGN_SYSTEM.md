# DevFlower V2 工作台设计系统

中央画布是工作主区。左侧定位项目、流程与运行，右侧只展示当前选择或任务工具。长表单、阅读和终端有自己的滚动区。使用 V2 作用域内的语义 Token，沿用已有共享主题和图引擎。

| Token | 浅色 | 深色 |
|---|---|---|
| 应用底色 | `#EDF1F6` | `#10151F` |
| 工作表面 | `#FFFFFF` | `#171F2D` |
| 画布底色 | `#F4F7FB` | `#111926` |
| 边界 | `#D8E0EB` | `#303D50` |
| 主文字 / 次文字 | `#142238` / `#50617A` | `#E8EEF7` / `#AAB8CB` |
| 交互强调 | `#244BC6` | `#9AB0FF` |
| 成功 / 等待 / 错误 / 运行 | `#067452` / `#865008` / `#A32138` / `#244BC6` | `#73DDB2` / `#EFC584` / `#FF9BA9` / `#A7B9FF` |

中文用 Noto Sans SC 或系统回退。英文用 Inter 或系统无衬线回退。正文 14px，标签与证据摘要 12–13px，页面和面板标题 16–22px。代码、路径和终端用系统等宽字体。使用 4px 步进，常用间距 8/12/16/24px。桌面控件至少 32px，主要动作 36px。粗指针下增大点击区域。

按钮使用主动作、普通动作、工具图标和危险动作四种权重。Input、Select、Dropdown 使用一致边界和焦点。Tooltip 是补充说明，图标按钮保留可访问名称。Tabs 明确选中项。状态徽标同时呈现文字与标记。错误给出可执行的恢复入口，加载状态依据请求，运行状态依据真实事实。弹窗使用稳定焦点、Escape 及触发器焦点恢复。短窗口首先保留主动作和有效画布高度。

首屏从打开工作空间进入。空图提供创建或选择 Agent、添加节点和模板的就近动作。工作流标题与保存、检查、启动成组。迁移和任务上下文在需要时展开。画布节点用图标、名称、类型、连接端口和真实状态形成层级。历史运行突出只读和运行状态。次级回执和原始身份逐步展开。

参考来源仅用于交互和布局。 [Sim](https://docs.sim.ai/workflows) · [Linear](https://linear.app/docs/display-options) · [VS Code](https://code.visualstudio.com/docs/editing/getting-started/userinterface) · [Raycast](https://manual.raycast.com/search-bar) · [Vercel](https://github.com/vercel-labs/web-interface-guidelines)
