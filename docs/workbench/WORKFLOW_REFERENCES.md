# 工作流样例的设计依据

以下是对七个开源项目及其官方示例的阅读记录。验证 DevFlower 时，可以用相同任务检查画布、独立脚本和运行历史，尤其要区分某次运行内的返工与用户发起的新一轮任务。

| 项目 | 代表结构和交接 | 调试、历史与工作台交互 | DevFlower 采用的取舍 |
| --- | --- | --- | --- |
| Sim | 块之间通过输出传值，运行可在特定块暂停 | 部署接口返回执行身份，可查询状态、区分等待与结束，并按块查看执行日志 | 画布保留位置，旁边打开该次执行对应的源文件。运行状态取自原始证据。[仓库](https://github.com/simstudioai/sim) · [运行 API](https://docs.sim.ai/workflows/deployment/api) |
| n8n | 节点组成工作流。一次执行对应一次输入和独立的执行记录 | 可按工作流、状态过滤历史。失败执行支持明确选择原版本或当前版本重试 | 将前轮定义与当前定义的差异放到启动确认中。继续新任务和重试旧任务在交互上分开。[仓库](https://github.com/n8n-io/n8n) · [执行历史](https://docs.n8n.io/workflows/executions/all-executions/) |
| Dify | 分类器将输入分配到类别出口，各分支可接专门的处理节点 | 在画布上编辑类别，节点错误可以停止流程、返回默认值或走独立错误分支 | 分类模型只生成已声明的分类字段。Router 按已保存的规则选出口；缺失字段单独报错。保留未命中分支及失败原因。[仓库](https://github.com/langgenius/dify) · [分类器](https://docs.dify.ai/en/use-dify/nodes/question-classifier) · [错误处理](https://docs.dify.ai/zh/use-dify/build/predefined-error-handling-logic) |
| LangGraph | Evaluator–optimizer 把审核反馈传给生成者；路由与并行是独立模式 | checkpoint 按 thread 保存状态，能读取某个时点的快照及历史 | 有界 Loop 的审核反馈属于某次 Run；显示每轮候选版本。跨任务只继承用户选择的交付。[仓库](https://github.com/langchain-ai/langgraph) · [控制流示例](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [状态持久化](https://docs.langchain.com/oss/python/langgraph/persistence) |
| Windmill | 脚本步骤组合成流程，错误处理步骤可拿到出错步骤的结果 | 编辑器逐步配置输入、预览结果，重试次数和延迟可由用户显式配置 | 普通 Tool 的返回码随结果交接；Loop 验证失败才进入有界返工。界面展示实际退出码，便于判断下一步。[仓库](https://github.com/windmill-labs/windmill) · [流程编辑器](https://www.windmill.dev/docs/flows/flow_editor) · [错误处理](https://www.windmill.dev/docs/flows/error_handling) |
| Temporal Python samples | Continue-As-New 把显式参数交给下一次执行，同一 Workflow ID 下产生新的 Run ID 与事件历史 | 以身份关联多次运行，每次可以单独核查事件。示例展示如何限制历史增长 | 为逻辑任务系列保留 Series ID，每轮保留 Run ID、前轮引用和独立证据。新任务由用户明确发起。[Python 示例](https://github.com/temporalio/samples-python) · [Continue-As-New](https://docs.temporal.io/develop/python/workflows/continue-as-new) |
| Kestra Blueprints | 蓝图可组合顺序任务与子流程；父任务可以等待子流程结果 | 从样例查看定义并复制使用。重试父任务可能重新执行整个子流程，子流程自身的恢复策略决定哪些成功步骤会再执行 | 模板打开后先检查用户配置；显示可编译范围。多控制节点、并行与汇合用作设计示例，保留明确的导出诊断。[蓝图库](https://github.com/kestra-io/blueprints) · [子流程重试与重放](https://kestra.io/docs/how-to-guides/subflow-executions) |

## 验收时观察什么

| 场景 | 可观察的结果 |
| --- | --- |
| 1. 多 Agent 顺序研发 | B 的请求含 A 的最终交付，运行日志中顺序一致。 |
| 2. 规划、开发、测试、审核 | Tool 的实际命令、退出码和交接可以和审核输入逐项比对。 |
| 3. 条件审核与纠正 | 分别提供两种有效 verdict，每次仅有命中出口产生调用。 |
| 4. 多部门分类 | 有序规则和 fallback 使用同一份结构化分类结果。错误输入停止运行。 |
| 5. 测试失败后返工 | 首轮验证失败跳过 Reviewer，下一轮 Developer 收到真实错误。 |
| 6. 有界多轮修复 | 每轮保留候选摘要；接受最终候选或达到上限后停止。 |
| 7. 调研、分析、审核、汇报 | 最终汇报可追溯上一节点交付，不依赖原生会话历史。 |
| 8. 跨任务继续推进 | 三次新增任务、三个独立 Run；第二和第三轮可以准确引用上一轮选中的成果。 |
| 9. 多级控制组合 | 可保存设计，导出时明确指出当前编译限制。 |
| 10. 汇合或并行 | 可以表达设计意图，界面区分设计参考与可运行模板。 |
