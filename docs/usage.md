# CLI、配置与检索工作流

## 配置发现与优先级

同一个配置键按以下顺序取首个非空值：

1. 进程环境变量。
2. `LITSEARCH_CONFIG` 指定的 TOML。
3. 用户级 `config.toml`。
4. 当前目录的 `litsearch.toml`。
5. 当前目录的 `.env`，仅作为扁平键回退。

`LITSEARCH_CONFIG` 是额外的高优先级配置层。显式路径不存在时退出码为 3。用户级配置位置见 [README](../README.md)。同名预设整体取高优先级版本，不同名预设叠加。

支持的环境变量：

| 环境变量 | 用途 |
|---|---|
| OPENALEX_API_KEY | OpenAlex 凭据 |
| CROSSREF_API_KEY | 兼容配置位；公共检索请求不发送该值 |
| CROSSREF_MAILTO / LITSEARCH_MAILTO | 联系邮箱，前者优先 |
| LITSEARCH_OUTPUT_DIR | 默认输出目录 |
| LITSEARCH_CONFIG | 显式 TOML 路径，只从进程环境读取 |

`.env` 提供 OPENALEX_API_KEY、CROSSREF_API_KEY、CROSSREF_MAILTO、LITSEARCH_OUTPUT_DIR 的回退值。配置文件使用 UTF-8。

```toml
output_dir = "output"
mailto = "you@example.com"
default_sources = ["openalex"]

[api_keys]
openalex = "your-key"

[presets.marine]
queries = ["marine protist grazing", "nanoflagellate bacterivory"]
year = "2022-2026"
limit = 100
max_results = 30
sort = "citations"
fetch_sort = "relevance"
require = ["marine,coastal,seawater"]
exclude = ["retraction,corrigendum"]
enrich_abstracts = 0
merge_policy = "doi"
strict_filters = false
```

数组字段使用 TOML 数组；布尔字段使用 `true` / `false`；数量字段使用整数。未知字段、错误类型和非法排序会以配置错误退出。预设别名支持 `type` → `work_type`、`source` → `sources`、`oa_filter` → `raw_filter`；同一预设中别名与正式名互斥。

命令行显式值覆盖预设，可重复选项整体替换预设数组。调用示例：

```bash
litsearch --preset marine --explain
litsearch --preset marine --require "marine,ocean" --stdout
```

配置文件里的相对 `output_dir` 锚定该配置文件所在目录；环境变量、`.env` 与命令行相对路径锚定调用目录。Python 调用 `load_config(cwd)` 时，以传入目录为项目配置与环境路径基准。

## 参数

| 参数 | 行为 |
|---|---|
| query... | 自由文本查询，可给多个；也可从预设或 concept 提供 |
| --preset NAME | 选择命名预设 |
| --source openalex,crossref,europepmc | 来源列表，默认 openalex |
| --year YEAR / START-END | 年份范围，起点不晚于终点 |
| --limit N | 每个来源/查询的候选上限，默认 25 |
| --max-results N | 合并、主题过滤、排序后的最终上限 |
| --min-citations N | 最低被引数，0 表示不限 |
| --author VALUE | 作者姓名；OpenAlex 还支持 A 开头的作者 ID |
| --institution VALUE | 机构名称、OpenAlex 机构 ID 或 ROR 链接 |
| --journal-issn VALUE | 期刊 ISSN |
| --type VALUE | 来源支持的文献类型，article/preprint 在 Crossref 中有对应映射 |
| --language VALUE | 语言代码 |
| --oa-only / --no-oa-only | 开放获取筛选 |
| --oa-filter VALUE | OpenAlex 原始 filter，同键覆盖结构化条件并生成诊断 |
| --require A,B | 组内 OR、组间 AND，可重复 |
| --exclude X,Y | 命中任一排除词即剔除，可重复 |
| --concept A,B | 同义词概念组，可重复；用于来源查询与软排序 |
| --enrich-abstracts N | 缺失摘要的新 DOI 查询预算，默认 0，使用 Europe PMC |
| --sort citations/year/relevance/rrf/bm25/hybrid | 最终展示排序，默认 citations |
| --fetch-sort relevance/citations/year | 远端候选排序，默认 relevance |
| --merge-policy title/doi | 默认 title；doi 使用保守合并策略 |
| --strict-filters / --no-strict-filters | 严格模式在联网前拒绝忽略或覆盖条件 |
| --explain | JSON 解释生效计划与来源能力，不联网、不写文件 |
| --title VALUE | Markdown 标题与默认文件名片段 |
| --output PATH | 主输出，后缀为 .md/.csv/.json |
| --doi-list PATH | DOI 列表路径 |
| --overwrite | 允许覆盖已有文件 |
| --stdout | 结果写 stdout，默认 JSON |
| --stdout-format json/md/csv/doi | 指定 stdout 格式，需同时指定 --stdout |
| --version / --help | 查看版本或完整帮助 |

## 候选、过滤与排序

`--limit` 控制每个来源/查询交给后续流程的候选窗口，`--max-results` 控制最终产出。远端候选在跨源合并后按需补摘要，执行标题/摘要主题过滤，再排序和截断。

OpenAlex、Crossref、Europe PMC 使用 cursor 分页。Crossref 与 Europe PMC 需要按被引数或语言做本地筛选时，扫描预算为 limit 的三倍，保留至多 limit 条。JSON 的 fetched_count、mapped_count、returned_count 与 stop_reason 说明每个查询的执行情况。Crossref relevance 抓取在所有页明确使用 score 降序。

`--fetch-sort` 决定哪些候选进入窗口；`--sort` 只改变窗口内结果的展示顺序。希望按被引数获取候选并排序时同时指定：

```bash
litsearch "marine bacteria" --fetch-sort citations --sort citations --stdout
```

多词术语可用查询字符串中的英文双引号表达短语。推荐先用较小窗口观察远端命中与诊断，再增加候选并核对筛选阶段统计，最后保存为预设。候选窗口内的结果数量趋稳时，应结合查询相关性和摘要核对决定是否继续扩展。

匹配前统一大小写和标点，主题过滤只检查标题与摘要：

| 写法 | 语义 |
|---|---|
| protist | 词首前缀，命中 protists |
| =sea | 整词精确匹配 |
| *bacteri* | 任意位置子串，命中 cyanobacteria |
| 海洋 | 非 ASCII 词使用子串匹配 |
| title:protist | 只匹配标题 |
| abstract:monthly | 只匹配摘要 |

`title:` / `abstract:` 适用于 require 与 exclude，可以与整词和子串写法组合。空 require 组在联网前报参数错误。每个 require 组的统计基于同一份排除词过滤后的集合，便于定位筛选瓶颈。

`--enrich-abstracts N` 在硬过滤前向 Europe PMC 按 DOI 补空摘要；N 控制新 DOI 查询次数，重试遵循 HTTP 策略。查不到或预算用尽会产生诊断，网络失败则整个检索失败。缓存与统计详见 [Python 契约](python.md#摘要补齐)。

## 概念查询与主题排序

概念组表达主题的不同要素，组内放近义词。手工 queries 与生成查询合并去重；concepts 用于软证据，require 用于硬过滤。只有 concepts 时也可以检索：

```bash
litsearch --concept "South China Sea,Daya Bay" --concept "protist,ciliate" --concept "seasonal,monthly" --source openalex,europepmc --sort hybrid --stdout
```

OpenAlex 将组内词编译为带引号的 OR 条件，组间用 AND；Europe PMC 在词前加 `TITLE_ABS:`。概念组至少为三组时，额外生成只含前两组的宽查询，因此将基本地理与研究对象放在前面。Crossref 按同义词位置轮换生成普通文本查询，并增加相同的宽查询，使用其自身的查询语义。`--explain` 的 compiled_queries 显示实际查询与来源筛选条件。

中文主题可使用可审阅的英文术语预设。将[南海时间序列示例](../examples/marine-timeseries.toml)中的预设表复制到项目 `litsearch.toml`，或将该文件作为 `LITSEARCH_CONFIG`：

```powershell
$env:LITSEARCH_CONFIG = (Resolve-Path examples/marine-timeseries.toml).Path
litsearch --preset marine_timeseries --explain
litsearch --preset marine_timeseries --stdout
```

该示例使用宽查询保留候选，再由 hybrid 排序；需要确定的筛选门槛时显式添加 require，并核对摘要缺失与过滤统计。

| 排序 | 计算与用途 |
|---|---|
| citations / year | 按被引数或年份排序 |
| relevance | 按来源原始相关分排序；缺分记录按被引数排在后段 |
| rrf | 每个独立 source/query 列表贡献 `1/(60+rank)`，同列表同文献保留最佳名次 |
| bm25 | 标题与摘要分别计算 BM25 后相加，标题权重 2、摘要权重 1；同义词组取最高贡献，再乘概念覆盖比例 |
| hybrid | 对 RRF 分与字段 BM25 分分别转为名次，共分共享名次，再以 `1/(60+rank)` 融合并乘概念覆盖比例 |

字段 BM25 使用 `k1=1.2`、`b=0.75`，IDF 为 `log(1+(N-df+0.5)/(df+0.5))`，统计来自过滤后的当前候选池。排序词组优先使用 concepts，其次 require，否则从 queries 提取非停用词。此实现为字段加权 BM25。概念覆盖比例为命中组占全部组的比例，摘要缺失会在证据中标明。

可选排序对标题明确标为补充材料、评议回复或推荐记录的条目设置 ancillary 标记，并将得分乘 0.5；条目保留在结果中。分数相同时按被引数和规范化身份确定次序。JSON 的 retrieval_hits、ranking_score 和 ranking_evidence 便于核对来源名次、字段命中与降权原因。默认排序维持 citations；专题对照见[评测说明](research/retrieval-quality-evaluation.md)。

## 去重与来源差异

相同 DOI 或同一来源身份会合并，字段优先选择被引数较高的记录，平手时 OpenAlex 优先，并从另一条记录补齐空值。跨源被引数统计口径不同。

默认 title 策略继续合并足够长的归一化同名标题；doi 策略保留不同 DOI 的同名条目，并将其 DOI 写入 related_dois 供人工核对。两种策略都保留来源身份、DOI 别名与合并依据。通用短标题不参与标题关联或合并。

Crossref 的机构、OA、OpenAlex 作者 ID 和 OpenAlex 原始 filter 条件不受支持；被引数和语言在本地处理。所有被忽略条件进入结构化诊断。严格模式会在发送任何请求前拒绝被忽略或覆盖的条件。

Europe PMC 无需 API key，年份、OA、作者姓名和 ISSN 在远端执行；被引数和语言在本地处理。机构、类型筛选、OpenAlex 作者 ID/原始 filter 及 relevance 之外的 fetch_sort 不受支持，普通模式诊断后忽略，严格模式联网前拒绝。

各来源的覆盖范围和元数据完整性各异。远端全文或关键词命中可能在本地标题/摘要中找不到对应词，且语言字段可能缺失；这些条件会影响后置过滤的保留结果。

## 输出与错误

默认生成 `<output_dir>/litsearch_<片段>_<日期>.md` 与同名 `.doi.txt`。片段优先取 title，否则取首个查询，保留中文。DOI 列表每行一个裸 DOI，无 DOI 条目在诊断中说明，可交给 Zotero 的按标识符添加功能。

主结果与 DOI 目标必须是不同文件；已有目标需显式 `--overwrite`。内容先在目标目录预写，再逐文件替换。跨文件提交失败时，退出码为 5，stdout 列出已提交路径，stderr 给出错误；调用方应结合退出码处理这批产物。

默认提交会原子地拒绝并发创建的同名目标。POSIX 上的默认提交使用同目录硬链接；文件系统不支持该操作时返回输出错误，有意替换目标时可使用 `--overwrite`。

| 退出码 | 含义 |
|---|---|
| 0 | 成功，允许合法空结果 |
| 2 | 命令行或检索意图错误 |
| 3 | 配置错误或所需凭据缺失 |
| 4 | 网络、响应结构或远端错误 |
| 5 | 文件输出失败 |

任一来源请求失败时整个检索失败。stdout JSON 包含诊断；文件模式的 stdout 为成功路径，日志与诊断走 stderr。
