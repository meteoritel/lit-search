# Python 接口与 JSON 契约

## 完整检索

公共入口为 `SearchClient.search(QueryPlan) -> SearchResult`。调用方显式传入 Config；客户端负责校验、编译查询、抓取、合并、按需补摘要、主题过滤、排序、截断和报告构建。检索过程通过返回值和领域异常交付，不打印日志或写结果文件。

```python
from litsearch import Config, QueryPlan, SearchClient

client = SearchClient(Config(api_keys={"openalex": "your-key"}))
result = client.search(QueryPlan(
    queries=["marine protist grazing"],
    sources=["openalex"],
    year="2022-2026",
    require=[["marine", "coastal"]],
    limit=100,
    max_results=30,
    merge_policy="doi",
))

for record in result.records:
    print(record.doi, record.title)
for diagnostic in result.report.diagnostics:
    print(diagnostic.code, diagnostic.message)

payload = result.to_dict()
```

`result.count` 为当前 records 的长度，`result.version` 为源码版本；两者为只读属性，与 to_dict() 对应字段一致。构造客户端时校验 Config 结构；search 按实际 sources 检查必需凭据。Crossref、Europe PMC 可使用空 Config，OpenAlex search 需要 key；explain 可离线调用。

`queries`、`sources`、`exclude` 是字符串数组；`require` 是 OR 词组数组，外层按 AND 组合。数量必须为整数，oa_only/strict_filters 为布尔值。直接 Python 调用和 CLI 共用检索意图校验。

`concepts` 为同义词组数组，用于查询编译与可选主题排序，保留缺少部分概念证据的候选；`require` 用于硬过滤。至少提供一个非空 queries 或 concepts。只有概念组时显式传入 `queries=[]`。来源编译与字段过滤规则见 [CLI 文档](usage.md#概念查询与主题排序)。

需要配置文件发现时显式调用 `load_config(Path(...))`。需要离线检查时调用 `client.explain(plan)`，其中 `compiled_queries` 给出逐来源的 query 和包含来源筛选语法的 remote_query。返回计划为独立副本，可用于报告和后续核对。explain 校验意图并返回来源能力诊断；strict_filters 的能力拒绝在 search 联网前执行。

## 摘要补齐

`QueryPlan.enrich_abstracts` 为非负整数，默认 0。启用后，在身份合并之后、require/exclude 之前，对缺少摘要且有 DOI 的候选查询 Europe PMC core。只接受相同 DOI 的摘要，只填空值，并将 `abstract_source` 设为 europepmc；文献身份与检索命中证据保持原语义。

预算按每次 search 中尚未缓存的新 DOI 计数，按合并后的候选顺序消耗。每批最多组合 10 个 DOI，批次仅包含剩余预算内的 DOI；HTTP 请求数随批次、分页和重试变化。每批完整读取 Europe PMC 分页，按规范化 DOI 回填首个有效摘要，完成后才缓存未找到结果。

相同 SearchClient 实例缓存成功摘要和未找到结果；缓存读取不占新 DOI 预算，HTTP 重试遵循客户端策略。补齐的元数据来源不产生检索投票。响应错误、分页未完整返回与网络失败整体抛出 NetworkError，失败批次不写入缓存。

`enrichment` 统计本次调用：requested 为实际执行的新 DOI 数，一次批次请求可贡献多个 DOI，分页、HTTP 重试和缓存读取不另计；cache_hits 为读取已有缓存的候选数，本次预取不计为缓存命中，同 DOI 的后续候选读取计入；filled 为实际填入摘要的候选数；not_found 为已查询或命中负缓存后仍无摘要的候选数；budget_skipped 为预算耗尽后跳过的候选数。已有摘要或无 DOI 的记录不进入这些计数，预算为零时该阶段不执行。

## Transport 与测试

HTTP 是内部 Seam；默认使用 urllib，opener 和重试策略支持替换。下面的完整示例只处理内存响应：

```python
import io
import json
from litsearch import Config, Http, QueryPlan, SearchClient

body = json.dumps({"message": {"total-results": 1, "items": [
    {"DOI": "10.1000/example", "title": ["Example paper"], "type": "journal-article"}
]}}).encode()
http = Http(opener=lambda request, timeout: io.BytesIO(body), polite=0)
result = SearchClient(Config(), http).search(
    QueryPlan(queries=["example"], sources=["crossref"], limit=1)
)
assert result.records[0].doi == "10.1000/example"
```

生产 HTTP 在实际请求之间等待，包括分页与重试。429/指定 5xx 与传输层故障使用退避；Retry-After 可延长等待。请求超时由 Http.timeout 配置。网络错误中的请求凭据会脱敏。

## JSON 结果

`result.to_dict()` 返回可直接序列化的结构。CLI `--stdout` 和 `.json` 文件使用相同契约：

| 字段 | 含义 |
|---|---|
| schema_version | 结果格式版本，当前为 1 |
| version | 程序版本 |
| generated | 结果生成日期 |
| queries | 逐来源/查询的执行信息 |
| filters | 规范化后的请求条件 |
| merge_stats | 进入合并、按 DOI/来源身份/标题合并和保留统计 |
| enrichment | 摘要补齐的 requested、cache_hits、filled、not_found、budget_skipped |
| filter_stats | 标题/摘要主题过滤阶段统计 |
| truncated | 最终结果上限剔除的数量 |
| count / results | 最终记录数量及内容 |
| diagnostics | 结构化诊断 |

逐查询执行信息保留 `label` / `total_count`，并提供独立机器字段：

- source、query：来源与实际执行的手工或概念编译查询。
- remote_total：来源当次报告的原始命中量，采用该来源的匹配语义；后续合并、过滤与截断各自统计，索引更新可改变命中量。
- fetched_count：网络响应实际包含的候选量。
- mapped_count：成功映射到 Record 的候选量。
- returned_count：来源本地过滤与候选限制后交给合并流程的数量。
- stop_reason：limit、scan_budget 或 exhausted，分别表示候选上限、本地过滤扫描预算、远端分页结束。
- filter_execution：请求条件在该来源的执行方式，包括 remote、local、unsupported、overridden。

`merge_stats.total_in` 是进入合并的候选量，与远端收到的 fetched_count 分别统计。主题过滤与 max_results 发生在这些逐查询统计之后。

诊断包含 code、message、source、query、field；不适用的定位值为 null。code 当前包括 unsupported_filter、filter_override、provider_warning、fetch_window_limited、empty_results、results_truncated、missing_doi、abstract_not_found、enrichment_budget_limited。程序按 code 分支，message 用于展示。

fetch_window_limited 区分 limit 候选窗口与 scan_budget 扫描预算，展示扫描、映射、保留量及来源本地条件。empty_results 在最终为空时说明远端无命中、映射无有效记录、来源本地条件筛空或 require/exclude 主题过滤筛空；逐查询原因可并存。filter_stats 始终只统计合并后的主题过滤。

## 记录与溯源

缺失标量为 None/JSON null，缺失集合为空集合；citation_count 为 0、is_open_access 为 False 都是有效值。

Crossref 日期按 published、issued 优先级选取有年份的分支。空日期保留记录，显式 null 日期元素产生带 DOI 或记录位置及 field 的 provider_warning；缺失日期为 null。年或年月部分日期补 1，内部空位保留 year、publication_date 为 null。错误的日期结构仍抛 NetworkError。

Crossref 保留有 DOI 的空标题记录，并将缺失标题的映射候选按来源/查询聚合为 field=`title` 的 provider_warning；该计数发生在来源本地筛选之前。无标题且无 DOI 的记录不进入映射结果，错误标题结构仍抛 NetworkError。来源类型保留，数据质量警告不触发 strict_filters 的预检拒绝。

Crossref 和 Europe PMC 的标题、摘要，以及补齐摘要，共用 HTML/JATS 纯文本处理：实体预解码一层，再解析已知标签；段落转为空白，上下标保留文字，保留 Unicode 与科学比较符。更深层编码保留为文本，避免无界解码。OpenAlex 使用原始标题与倒排摘要重建。

记录包含 doi、title、year、authors、venue、abstract、citation_count、source、sources、source_id 等字段。来源与溯源字段如下：

| 字段 | 契约 |
|---|---|
| source_ids | 来源到身份列表的映射，跨源合并后保留全部身份 |
| doi_aliases | 保留的 DOI 身份，包括标题合并时的不同 DOI |
| raw_work_types | 来源到原始类型列表的映射 |
| work_type | Crossref journal-article/posted-content 映射为 article/preprint；Europe PMC PPR 来源映射 preprint，Review/Journal Article 映射 review/article；OpenAlex 保留来源值 |
| landing_page_url | 文献访问页面地址 |
| pdf_url | 来源明确标识的 PDF 地址；获取权限由来源决定 |
| oa_url | OpenAlex 提供的开放阅读地址，可能是页面或 PDF |
| merge_reasons | doi、source_id、title 等实际合并依据 |
| related_dois | 保守策略下同标题候选的其他 DOI，供人工判断关联 |
| abstract_source | 当前摘要来自的来源；缺失或来源未知时为 null |
| retrieval_hits | 命中列表，每项为 source、query、rank、score；rank 从 1 开始并跨页连续，score 为来源原始相关分或 null |
| ranking_score | rrf/bm25/hybrid 的最终排序分；citations/year/relevance 为 null |
| ranking_evidence | rrf/bm25/hybrid 的 matched_groups、rrf、bm25、concept_coverage、abstract_missing、record_flags；citations/year/relevance 为空映射 |

合并记录时，对相同 `(source, query)` 保留最佳 rank，独立查询分别计票。`relevance_score` 保存合并记录中的最大原始相关分，供 relevance 排序使用；跨来源与查询的融合使用名次。排序公式见 [CLI 文档](usage.md#概念查询与主题排序)。

`ranking_evidence.rrf/bm25` 为原始分项，`ranking_score` 为所选排序经覆盖比例与 ancillary 处理后的最终分数；覆盖比例只作用于 bm25/hybrid，ancillary 因子为 0.5。标题关联统一使用 NFKC/casefold 后仅保留字母数字的身份规则，见[去重与来源差异](usage.md#去重与来源差异)。

`open_access_pdf` 是兼容字段；调用方可使用 pdf_url、oa_url 和 is_open_access 分别处理文件地址、开放阅读入口与访问状态。

CSV 为记录导出，普通集合字段采用分号文本，映射字段及 retrieval_hits 使用 JSON 字符串。CSV 记录分隔符为 CRLF，CLI stdout 与文件内容一致；需要完整执行诊断时使用 JSON 或 Python 返回值。

## 异常

UsageError、ConfigError、NetworkError、OutputError 均继承 LitsearchError。OutputError.written 保存已经提交的文件路径；SearchClient.search 只执行检索，不产生输出文件错误。

库调用保持异常供调用方处理；CLI 将异常映射为[退出码](usage.md#输出与错误)。任一来源网络失败时整体抛出 NetworkError。

## 外部来源契约

来源映射与分页参照以下官方文档，核对日期为 2026-11-09：

- [Crossref REST 文档](https://github.com/CrossRef/rest-api-doc)：cursor / next-cursor、每页 rows 与分页结束规则。
- [OpenAlex Work](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md)：open_access、best_oa_location。
- [OpenAlex Location](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/location-object.md)：landing_page_url 与 pdf_url。
- [Europe PMC REST](https://dev.europepmc.org/RestfulWebService)：core 元数据、cursorMark / nextCursorMark、字段查询；来源内身份由 source 与 id 共同构成。

Crossref relevance 抓取的所有分页显式发送 `sort=score&order=desc`。Europe PMC 默认按相关性取数；年份、OA、作者姓名和 ISSN 在远端执行，被引数与语言在本地执行；机构、原始 OpenAlex filter、类型筛选和其他 fetch_sort 不受支持。严格模式在联网前拒绝这些条件。
