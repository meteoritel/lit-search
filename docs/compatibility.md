# 兼容性与迁移

当前源码版本为 0.5.0，结果 schema_version 为 1。CLI 命令、单文件运行和 `import litsearch` 路径保持可用。新增 JSON 字段采用追加方式，现有 version、queries.label、queries.total_count、filters、results 等字段继续提供。

## 升级调用方

| 当前契约 | 调用方应采取的动作 |
|---|---|
| 已有输出文件需明确允许覆盖 | 有意替换结果时加 --overwrite；自动化调用检查退出码后消费路径 |
| 输出失败使用退出码 5 | 更新脚本的退出码处理，保留 stdout 已提交路径以便处理部分产物 |
| TOML/预设严格校验类型和未知键 | queries/require/exclude/default_sources 使用数组，布尔值使用原生 true/false，修正拼写错误 |
| 显式 LITSEARCH_CONFIG 路径缺失会报配置错误 | 移除无效环境变量或提供有效文件 |
| TOML 相对输出目录以配置文件所在目录为基准 | 希望固定写入位置时使用绝对路径；项目路径放到项目 TOML |
| Crossref work_type 统一为 article/preprint | 依赖原始名称时读取 raw_work_types |
| PDF、阅读页面和 OA 状态独立表达 | 新调用方使用 pdf_url、landing_page_url、oa_url 和 is_open_access |
| 来源身份参与去重，Unicode 标题得到保留 | 结果集可能与旧版本不同；使用 source_ids 和 merge_reasons 核对来源与合并依据 |
| Crossref relevance 分页显式使用 score 降序并保存原始分 | 远端候选与原 relevance 最终排序可能变化；跨来源融合可选 rrf/hybrid |
| 新增 concepts、补摘要和 rrf/bm25/hybrid | 按需开启；默认来源、citations 排序与零补齐预算保持 |
| 新增 enrichment、abstract_source、retrieval_hits、ranking_score/evidence | JSON 消费容忍追加字段；CSV 消费按列名读取，retrieval_hits 列解析 JSON |
| require 空组联网前报错 | 移除空白值、空逗号组，使用有效术语 |

`open_access_pdf` 继续提供兼容值。默认 title 合并策略保持；需要保留不同 DOI 的同名文献时显式使用 `--merge-policy doi` 或 QueryPlan.merge_policy。保守模式仍合并相同来源身份。

## Python 调用入口

完整业务调用使用 `SearchClient.search(plan)`；`run` 继续用于 CLI 流程，`search_all` 保留候选抓取兼容入口。公共导出以 `litsearch.__all__` 为准。

结果消费应容忍新增字段，并按 schema_version 识别格式。结构化诊断使用 code 做程序分支；查询身份读取 source/query，label 用于展示。
