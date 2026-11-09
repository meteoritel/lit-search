# 资料档案

本目录保存阶段研究、固定候选、人工或 Agent 判读与历史迁移说明。当前使用规则见 [CLI 文档](../usage.md)，集成规则见 [Python 契约](../python.md)，验收入口见[开发文档](../development.md)。

## 专题评测

- [检索质量对照](2026-11-09-retrieval/retrieval-quality-evaluation.md)：固定候选下的摘要、过滤、排序、来源与耗时对照。
- [外部技术依据](2026-11-09-retrieval/retrieval-design-sources.md)：来源协议与排序算法的一手资料。
- [候选与判读](2026-11-09-retrieval/fixtures)：南海时间序列、海洋 LNA/HNA 和钙钛矿主题的冻结数据。
- 摘要补齐回归仍使用 [原 gold](../../tests/fixtures/retrieval_gold.json)和[元数据回放](../../tests/fixtures/retrieval_replay.json)。

阶段评测与采集脚本已停用。档案标签保留各自的取样和判读口径，结果解释以对应报告为准。

## 迁移与计划

- [迁移说明档案](2026-11-10-compatibility.md)。
- 已完成的实施计划统一见[计划索引](../plans/README.md)。

## 本地原始证据

本地证据保存在受 Git 忽略的 `.local/archive/`，按测试日期和主题分目录：

- `2026-11-09-blackbox/`：黑盒报告、矩阵、运行输出、配置及隔离环境。
- `2026-11-09-retrieval/`：专题测试报告、来源缓存与运行记录。
- `2026-11-10-optimization/`：候选采集与性能测量结果。

这些目录仅在保有原始资料的工作区中可用。原始报告里的命令与路径采用采集时布局；个人配置和日志保留在本地忽略边界内。资料与计划档案均不进入分发包。
