# Issue tracker: Local Markdown

本仓库的 issue 和 spec 使用 `.scratch/` 下的 Markdown 文件。

## 文件约定

- 每个功能使用 `.scratch/<feature-slug>/` 目录。
- 需求说明保存在 `.scratch/<feature-slug>/spec.md`。
- 实施 issue 各自保存为 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，在功能目录内从 `01` 编号。
- 实施 issue 顶部使用 `Status:` 行记录 triage 状态，取值见 [triage 标签配置](triage-labels.md)。
- 讨论与补充信息追加到 issue 文件底部的 `## Comments` 下。

## 发布与读取

技能要求“发布到 issue tracker”时，按上述约定创建对应 Markdown 文件，按需创建目录。

技能要求“读取相关 ticket”时，读取用户给出的文件路径。只有编号时，在 `.scratch/*/issues/` 中查找；出现多个匹配时，先确认具体路径。

## Wayfinder 探索任务

- 探索地图保存在 `.scratch/<effort>/map.md`，包含 `Notes`、`Decisions-so-far`、`Fog`。
- 每个子任务保存在 `.scratch/<effort>/issues/<NN>-<slug>.md`，编号从 `01` 开始，正文描述待回答的问题。
- 子任务顶部用 `Type:` 记录 `research`、`prototype`、`grilling` 或 `task`。
- 探索子任务的 `Status:` 使用 `open`、`claimed`、`resolved`。
- 依赖以 `Blocked by: NN, NN` 记录；所列任务全部为 `resolved` 时解除阻塞。
- 领取任务时，选择编号最小、状态为 `open` 且依赖已解除的任务，先保存 `Status: claimed`。
- 完成后将答案写入 `## Answer`，设置 `Status: resolved`，并在地图的 `Decisions-so-far` 中追加结论摘要与任务链接。

## 实施计划

临时实施计划的存放与归档遵循 [开发文档](../development.md#文档与计划)。
