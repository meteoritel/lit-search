# Python Interface 与 JSON 契约

## 完整检索

公共入口为 `SearchClient.search(QueryPlan) -> SearchResult`。调用方显式传入 Config；客户端负责校验、抓取、合并、主题过滤、排序、截断和报告构建。检索过程通过返回值和领域异常交付，不打印日志或写结果文件。

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

`queries`、`sources`、`exclude` 是字符串数组；`require` 是 OR 词组数组，外层按 AND 组合。数量必须为整数，oa_only/strict_filters 为布尔值。直接 Python 调用和 CLI 共用检索意图校验。

需要配置文件发现时显式调用 `load_config(Path(...))`。需要离线检查时调用 `client.explain(plan)`。返回计划为独立副本，可用于报告和后续核对。

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
| filter_stats | 标题/摘要主题过滤阶段统计 |
| truncated | 最终结果上限剔除的数量 |
| count / results | 最终记录数量及内容 |
| diagnostics | 结构化诊断 |

逐查询执行信息保留 `label` / `total_count`，并提供独立机器字段：

- source、query：来源与原始查询意图。
- remote_total：来源报告的原始命中量。
- fetched_count：网络响应实际包含的候选量。
- mapped_count：成功映射到 Record 的候选量。
- returned_count：来源本地过滤与候选限制后交给合并流程的数量。
- stop_reason：limit、scan_budget 或 exhausted，分别表示候选上限、本地过滤扫描预算、远端分页结束。
- filter_execution：请求条件在该来源的执行方式，包括 remote、local、unsupported、overridden。

`merge_stats.total_in` 是进入合并的候选量，与远端收到的 fetched_count 分别统计。主题过滤与 max_results 发生在这些逐查询统计之后。

诊断包含 code、message、source、query、field；不适用的定位值为 null。code 当前包括 unsupported_filter、filter_override、provider_warning、fetch_window_limited、results_truncated、missing_doi。程序按 code 分支，message 用于展示。

## 记录与溯源

缺失标量为 None/JSON null，缺失集合为空集合；citation_count 为 0、is_open_access 为 False 都是有效值。

原有 doi、title、year、authors、venue、abstract、citation_count、source、sources、source_id 等字段继续保留。新增来源与语义字段：

| 字段 | 契约 |
|---|---|
| source_ids | 来源到身份列表的映射，跨源合并后保留全部身份 |
| doi_aliases | 保留的 DOI 身份，包括标题合并时的不同 DOI |
| raw_work_types | 来源到原始类型列表的映射 |
| work_type | 统一类型；Crossref journal-article/posted-content 映射为 article/preprint，其余来源值保留 |
| landing_page_url | 文献访问页面地址 |
| pdf_url | 来源明确标识的 PDF 地址；获取权限由来源决定 |
| oa_url | OpenAlex 提供的开放阅读地址，可能是页面或 PDF |
| merge_reasons | doi、source_id、title 等实际合并依据 |
| related_dois | 保守策略下同标题候选的其他 DOI，供人工判断关联 |

`open_access_pdf` 是保留的兼容字段；新调用方使用 pdf_url、oa_url 和 is_open_access 分别处理文件地址、开放阅读入口与访问状态。

CSV 为记录导出，集合字段采用分号文本，映射字段使用 JSON 字符串。CSV/DOI 格式侧重条目交付，需要完整执行诊断时使用 JSON 或 Python 返回值。

## 异常

UsageError、ConfigError、NetworkError、OutputError 均继承 LitsearchError。OutputError.written 保存已经提交的文件路径；SearchClient.search 只执行检索，不产生输出文件错误。

库调用保持异常供调用方处理；CLI 将异常映射为[退出码](usage.md#输出与错误)。任一来源网络失败时整体抛出 NetworkError。

## 外部来源契约

来源映射与分页参照以下官方文档，核对日期为 2026-11-09：

- [Crossref REST 文档](https://github.com/CrossRef/rest-api-doc)：cursor / next-cursor、每页 rows 与分页结束规则。
- [OpenAlex Work](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md)：open_access、best_oa_location。
- [OpenAlex Location](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/location-object.md)：landing_page_url 与 pdf_url。
