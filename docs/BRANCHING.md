# 分支、同步与历史保留

## 生命周期与人工 Gate

长期只保留 `main`、`develop` 与 `project/<slug>`（包括 `project/devflower`、`project/codeweave`）：main 是正式版本，develop 保留最新待验收开发版及已有 UI/执行器实现，project/* 保留单个项目的过程记录。日常源码统一在 develop 开发；只有明确需要隔离且获准时才从 develop 创建临时任务分支，归集后清理，不作为长期入口。项目分支不整体合入 main，也不整体导入 develop。发布完成后，将 develop 普通快进到合并后的 main 并推送，使两者具有同一 HEAD/tree；开发期间 develop 可领先 main，不能反向覆盖未完成的开发。

通用改进先进入 develop，正式发布再审查 develop 到 main 的完整候选。发布 PR 的远端 HEAD/tree 必须对应当前通过适用的完整产品合同、独立审核、固定外部 Gate、旧阻断项关闭及明确发布授权的候选。先审查相对 main 的全部差异，确认仅通用框架、无 project 实例/原始日志/凭据/未审代码，报告 READY_TO_MERGE；该状态不是合并授权。人明确批准后才按批准方式实际合并，读回 main 实际 HEAD，在正式 main 重跑基础回归与 check --baseline，并记录历史继承关系。对外 Stable Release 与版本 tag 只绑定经上述流程确认的 main 提交；GitHub 发布不自动授予任何本机部署 Stable 身份，见 [工作台部署说明](WORKBENCH.md)。

清理临时本地或远端分支前，须逐项确认完整历史已有可恢复并经核对的备份，代码已由 develop/main 保全（含已有 UI 与执行器），并另获针对该分支的明确删除授权；仅有 squash 后的代码或一句“已合并”不能替代完整历史备份。不删除 main、develop、project/*，不 force-push。保留旧原生会话、日志、工作目录和 worktree；若分支仍被工作目录使用，先报告关联并保留，不能顺带删除目录。主线合并、源码推送、发布/tag 与分支清理是不同权限；已有任务许可不是未来所有任务的永久许可。源码自验、独立审核、Gate、实际合并与用户体验验收应分别记录，不以分支名称替代。

## 文件边界

main 与 develop 保留通用根规则、`docs/`、`templates/`、`tools/`、`tests/`，均不含项目实例。项目分支仅增加 `project/`：PROJECT.md、project.json、STATUS.md、AGENT_CONTEXT.md、INDEX.md、plans/、tasks/。项目专属规则不要改写根 AGENTS/WORKFLOW；确需试验共同规则时使用独立提交便于提炼。

## 通用升级 → 项目

在干净的项目工作目录先检查当前分支、差异和远端。经授权获取 main 的新版本，在 `project/<slug>` 上 merge 该版本，审查规则冲突并跑测试、`check`。更新 `project/project.json` 中采用的 framework_base 为实际 main SHA。普通 merge 不重写项目历史。

## 项目经验 → 通用主线

在 develop 重写为通用补丁或选择一个只含通用路径的提交；预检文件列表，排除 `project/`、旧 `projects/` 及私有输入。跑 `check --baseline` 与测试，提 PR 到 develop，待发布候选通过上述 Gate 后再经授权进入 main。项目整个分支不能回并 main/develop；也不要 cherry-pick 混有项目记录的提交。

## 本次迁移

现有 `projects/<name>/` 内容应先保存在项目分支，再迁至 `project/`；计划与历史报告原文保留，修正仅通过单独迁移说明，不能回写历史使它看起来用了新流程。main 的旧历史继续保留，因此历史中曾公开的数据不会因删除当前文件而变私有。

早期迁移分支 `refactor/devflower-baseline` 与 `project/codeweave` 的记录保留其历史语义，不把它们描述为本次发布已完成的合并。本次治理要求最新开发实现留在 develop，正式版本留在 main，过程记录留在 project/*；不要创建 project 分支整体到 main/develop 的合并请求。

## 并行目录与权限

一个物理目录只服务一个活动项目。多个项目可以独立 clone 同一 repo 的不同分支，或经授权使用 git worktree。切换 Git 分支不会自动清除不跟踪的日志；不要在相同目录来回切项目。不要自动删除旧 worktree/日志。

仓库公开则所有分支、历史均公开；项目分支不是权限隔离。如需私有记录，应另设私有仓库或把整个工作流仓库改为 Private，不能只“隐藏分支”。更名不会改变此边界。远端改名后先核对仓库数字 ID，再更新 remote URL；不要创建同名新仓库替代现有历史。
