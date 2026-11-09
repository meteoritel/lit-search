# 黑盒测试后的优化技术路线与验收

状态：已完成并归档，2026-11-10。实施基线：litsearch 0.5.0，提交 1cf08ee。

本轮按日期正确性、文本与诊断、规范化与路径、质量评测及性能测量的顺序实施。生产修复、回归、示例和正式文档已同步；排序迁移与摘要批处理按证据门槛完成取舍。

## 输入与职责边界

输入为工作区中的 [.local/test-report.md](../../../.local/archive/2026-11-09-blackbox/test-report.md) 与 [.local/README.md](../../../.local/archive/2026-11-09-blackbox/README.md)。原始日志和在线测量保存在受忽略的 .local；复现日期/文本问题的离线响应、固定候选与判读已纳入测试资源。

保持运行时仅标准库、[单文件源码](../../../src/litsearch.py)、import/CLI 入口及既有退出码。Provider 处理来源结构，SearchClient 完成检索、合并、补摘要、过滤与排序，CLI 负责终端和文件交付。正式契约见 [Python Module](../../python.md)、[CLI 规则](../../usage.md)；[迁移说明](../../archive/2026-11-10-compatibility.md)保存在资料档案。

## 已实施工作

| 工作包 | 已落地行为 | 验收依据 |
|---|---|---|
| A：Crossref 日期 | published/issued 统一选取；显式空日期保留记录并给出来源、查询、字段和 DOI/记录位置警告；内部日期空位保留年，真实结构错误仍为 NetworkError | 无年份完整检索、CLI、回退、部分日期、坏结构、分页及混合来源离线回归 |
| B：文本与诊断 | HTMLParser 处理已知 HTML/JATS 标签及编码标签，保留科学比较符；empty_results 解释筛空阶段，fetch_window_limited 区分 limit 与 scan_budget | 过滤、排序、合并、JSON/CSV/Markdown 和混合来源 JSON/stderr 回归；合法空集 exit 0 |
| C：规范化与交付 | 精确重复 concepts 组统一去重；CLI explain 追加绝对默认输出目录和配置来源；明确 UTF-8 消费及 overwrite 逐文件提交语义 | 输入不变、查询/排名/explain 使用同一规范化计划；解释无文件副作用；源码及安装入口在 CP936 默认环境严格解码 UTF-8 |
| D：质量与性能 | 固定排序前来源候选；citations 基线、来源/查询消融；LNA/HNA 与独立难负例判读；网络、正常等待、重试、补摘要及冷热缓存测量 | 离线回放和受控计时回归；真实多源候选采集；三轮在线性能测量 |
| E：API 与策略决策 | SearchResult.count/version 只读动态属性；海洋 LNA/HNA TOML 预设；凭据校验时点文档 | 属性及序列化回归；预设经真实 CLI explain 解析；策略决策见下节 |

README 中 year 保持可选。Crossref 无年份问题由明确离线响应复现并验收；本轮在线多源快照使用 2015–2026 年份条件。

## 条件优化的决策

- **默认排序保留 citations。** LNA/HNA 固定候选中 hybrid 严格 P@20 为 0.55，citations 为 0.30；含难负例的钙钛矿主题中 relevance/rrf 的宽口径 P@20 与 nDCG 更高。默认迁移门槛未达到，hybrid 继续作为显式选项。
- **严格专题范围使用显式 require。** 固定海洋词与核酸分组词条件保留该候选内全部核心标签，hybrid 严格 P@20 为 0.80。require 同时影响无 concepts 时的排序词组；研究报告分别说明保留集合与配置整体效果。
- **Crossref 与查询策略保持。** 来源消融显示 Crossref 增加其他来源未覆盖的核心文献，同时带来噪声；两条查询覆盖不同核心文献。保留 query 参数、轮换语义与固定窗口。
- **摘要批处理未采用。** 在线样本仅有一个待补 DOI，冷缓存补摘要主要耗时来自远端响应，该输入没有合并请求的空间。生产保留逐 DOI、默认零预算及同客户端缓存；并发和持久缓存未引入。

完整方法、候选标签口径和性能数据见[检索质量对照](../../archive/2026-11-09-retrieval/retrieval-quality-evaluation.md)。固定快照与标签见[资料索引](../../archive/README.md)。阶段评测脚本已停用，当前验收入口见[开发文档](../../development.md)。

## 最终检查

以下检查在本地 Windows、Python 3.12.13 环境通过：

```text
uv run --locked --extra dev python -m pytest             309 passed
uv run --locked --extra dev ruff check .                 passed
uv run --locked --extra dev mypy                         passed
uv build                                               passed
uv run --locked --extra dev python scripts/check_distribution.py  passed
```

分发检查包含 wheel 隔离安装、安装后离线检索、CP936 默认环境下的 UTF-8 stdout/stderr、独立单文件运行，以及 sdist 中示例、测量工具和评测资源。原南海与原钙钛矿回放保持既有指标；新增难负例主题已单独回放。海洋 LNA/HNA 预设经 CLI explain 检查。

## 风险与限制

- 已知限制：新标签由 Agent 依据标题/摘要判读，尚无独立人员盲审；生境未知与元数据不足条目显式保留。候选内标签覆盖与原 gold 已知召回使用各自分母。
- 已知限制：在线耗时随远端响应波动；三轮经验 p95 等于观测最大值。当前单缺摘要输入未覆盖批处理收益场景。
- 已知限制：本轮执行本地 Windows/Python 3.12 检查；项目 Windows/Linux、Python 3.12/3.14 CI 矩阵仍由 CI 执行。
- 假设：多缺摘要输入可能使有界批处理有收益；需要新的真实输入、官方组合 DOI 查询契约与重复测量后再决定。

本轮无待实施工作包。后续性能实验应从多缺摘要真实输入开始；默认排序迁移需补充独立人员复核和更多含难负例的主题。返回[计划索引](../README.md)。
