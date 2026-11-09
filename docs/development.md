# 开发、测试与交付

## 环境与检查

Python 3.12+，运行时仅标准库。开发依赖由 pyproject.toml 声明，uv.lock 固定解析结果。

```bash
uv sync --locked --extra dev
uv run --locked --extra dev python -m pytest
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy
uv build
uv run --locked --extra dev python scripts/check_distribution.py
```

Ruff 检查运行错误和无效代码；mypy 检查运行模块及有类型标注的调用链。GitHub Actions 在 Windows/Linux 的 Python 3.12 和 3.14 环境执行测试、静态检查、构建及安装产物检查。

检索测试使用 FakeOpener 和内存响应，主动阻止未安排的额外请求。涉及 HTTP 的测试同时控制正常请求等待与重试等待。新增来源行为应覆盖请求映射、异常响应、分页结束与结果诊断。

## 职责与依赖方向

```text
CLI Adapter → 配置发现/参数解析 → QueryPlan
            → SearchClient.search → Provider Adapter → Http
                                  → 身份合并/按需补摘要 → Http
                                  → 主题过滤/证据排序 → SearchResult
            → 渲染/文件交付
```

源码继续保留单文件交付。SearchClient 是完整检索 Module 的 Interface；CLI 组织输入与交付副作用；Provider Adapter 隐藏来源查询、分页与字段差异；Http 内部 Seam 支持真实 urllib 与测试 opener。

配置与检索意图的校验覆盖 CLI 和 Python 两条入口。记录的 False/0 是有效值；合并时保留来源身份、逐查询全局排名与摘要来源。主题过滤在跨源合并与摘要补齐后执行。补摘要不增加检索投票；RRF 按独立来源/查询计票。新增业务规则应从完整检索 Interface 验证，并保留有独立领域价值的合并和匹配案例。

## 检索质量评测

离线样例在 tests/fixtures：retrieval_gold.json 保存原始专题金标准；retrieval_judgements.json 保存扩展候选判读；retrieval_candidates.json 保存来源命中和排序所需元数据；retrieval_abstracts.json 保存补齐对照的缓存摘要；retrieval_replay.json 用于完整检索入口的过滤回归。钙钛矿主题由 retrieval_perovskite.json 与对应 judgements 保存。公开元数据快照不含请求凭据。

```bash
# 用原 gold 统计已知召回；gold 之外单列为未判读
uv run python scripts/evaluate_retrieval.py --gold tests/fixtures/retrieval_gold.json tests/fixtures/retrieval_candidates.json

# 同池对比补摘要与排序；不联网
uv run python scripts/benchmark_retrieval.py --gold tests/fixtures/retrieval_judgements.json --metadata tests/fixtures/retrieval_abstracts.json tests/fixtures/retrieval_candidates.json

# 使用原硬过滤条件做消融
uv run python scripts/benchmark_retrieval.py --gold tests/fixtures/retrieval_judgements.json --metadata tests/fixtures/retrieval_abstracts.json --require-from tests/fixtures/retrieval_replay.json tests/fixtures/retrieval_candidates.json

# 独立主题回放
uv run python scripts/benchmark_retrieval.py --gold tests/fixtures/retrieval_perovskite_judgements.json tests/fixtures/retrieval_perovskite.json
```

标签以规范化 DOI 或 `source:source_id` 为键，tier 1/2/3 互斥；同一合并身份含冲突标签时报错。原 gold 的核心加相关为 tier 1 与 tier 2 的并集，gold 外记录为未判读。前列全部判读后才给 P@20 与 nDCG@20，样本不足时分母为实际前列长度；nDCG 的 gain 为 3/1/0。原 gold 用于已知召回，扩展判读用于排序对照，分别报告分母。

benchmark 的 allocation_experiment 用固定的逐查询候选流模拟按页取数，比较轮流分配与按新增身份比例选择来源；它是离线实验，不改变生产抓取。完整方法、结果与适用范围见[评测说明](research/retrieval-quality-evaluation.md)。

## 版本与构建

版本的唯一来源为 src/litsearch.py 中的 __version__，setuptools 从该属性生成分发元数据。wheel 交付运行模块与元数据；sdist 同时包含测试、fixtures、用户文档、TOML 示例和交付检查脚本。计划档案不进入分发包。

`check_distribution.py` 在临时隔离环境安装当前版本 wheel，检查入口、来源位置、离线检索 JSON、配置解释与独立单文件运行，并核对 sdist 的验证资源和许可证元数据。

来源协议调整时先查[官方依据](python.md#外部来源契约)，使用响应样例增加契约回归；真实在线检索使用个人配置，凭据留在环境变量或受忽略的配置文件中。

## 文档与计划

README 提供安装与最短调用路径；usage.md 描述 CLI/配置，python.md 描述 Python/结果契约，compatibility.md 描述当前迁移动作。

临时实施安排放入 docs/plans，完成验收后移入 docs/plans/archive，并更新计划索引。正式文档与代码注释描述已实现行为。
