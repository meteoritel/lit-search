# CLI、配置与检索工作流

## 检索范围

项目面向使用英文关键词与同义词组织的 OpenAlex、Crossref、Europe PMC 文献检索。中文文献检索不在项目规划中，包括中文数据库接入、中文分词与中英自动翻译。中文主题请先整理为英文术语，再提交查询。

CLI 中文提示、UTF-8 输入输出、中文标题和路径均受支持。查询直接使用用户提供的词项；`--language` 按来源语言字段筛选，`--concept` 按用户提供的同义词编译查询，两者都不执行翻译。

例如，将“南海定鞭藻”整理为地理与类群概念，再检查来源实际收到的查询：

```shell
litsearch --concept "South China Sea" --concept "haptophyte,Haptophyta,coccolithophore" --source europepmc --explain
litsearch --concept "South China Sea" --concept "haptophyte,Haptophyta,coccolithophore" --source europepmc --limit 25 --sort hybrid --stdout
```

概念组参与来源查询和软排序；需要标题或摘要必须包含某组词时，显式增加 `--require`。来源查询语义与候选窗口见下文。

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

`.env` 提供 `OPENALEX_API_KEY`、`CROSSREF_API_KEY`、`CROSSREF_MAILTO`、`LITSEARCH_MAILTO`、`LITSEARCH_OUTPUT_DIR` 的回退值（`CROSSREF_MAILTO` 优于 `LITSEARCH_MAILTO`）。配置文件使用 UTF-8；`.env` 兼容开头的 UTF-8 BOM，TOML 按标准解析器编码读取。

```toml
output_dir = "output"
mailto = "you@example.com"
default_sources = ["openalex"]

[api_keys]
openalex = "your-key"

[presets.topic]
queries = ["time series forecasting", "forecasting models"]
year = "2022-2026"
limit = 100
max_results = 30
sort = "hybrid"
fetch_sort = "relevance"
require = ["forecast,prediction"]
exclude = ["retraction,corrigendum"]
enrich_abstracts = 0
merge_policy = "doi"
strict_filters = false
```

数组字段使用 TOML 数组；布尔字段使用 `true` / `false`；数量字段使用整数。未知字段、错误类型和非法排序会以配置错误退出。预设别名支持 `type` → `work_type`、`source` → `sources`、`oa_filter` → `raw_filter`；同一预设中别名与正式名互斥。

命令行显式值覆盖预设，可重复选项整体替换预设数组。调用示例：

```bash
litsearch --preset topic --explain
litsearch --preset topic --require "forecast,prediction" --stdout
```

配置文件里的相对 `output_dir` 锚定该配置文件所在目录；环境变量、`.env` 与命令行相对路径锚定调用目录。Python 调用 `load_config(cwd)` 时，以传入目录为项目配置与环境路径基准。

CLI `--explain` 的 output.directory 为解析后的绝对默认输出目录，output.origin 为配置来源。解释过程不创建目录，也不检查目标冲突。Python `SearchClient.explain` 只解释检索计划与来源能力。explain 校验检索意图并返回来源能力诊断；即使启用 strict_filters，也保留这些诊断供检查，严格拒绝在实际 search 联网前执行。

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
| --type VALUE | 来源支持的文献类型；Crossref 映射 `article`/`preprint` 并支持 Crossref 原生类型，其他值（如 `review`）将被忽略 |
| --language VALUE | 语言代码 |
| --oa-only / --no-oa-only | 开放获取筛选 |
| --oa-filter VALUE | OpenAlex 原始 filter；同键或同语义组覆盖结构化条件并生成 `filter_override` 诊断 |
| --require A,B | 组内 OR、组间 AND，可重复 |
| --exclude X,Y | 命中任一排除词即剔除，可重复 |
| --concept A,B | 同义词概念组，可重复；用于来源查询与软排序 |
| --enrich-abstracts N | 缺失摘要的新 DOI 查询预算，默认 0，使用 Europe PMC |
| --sort citations/year/relevance/rrf/bm25/hybrid | 最终展示排序，默认 citations |
| --fetch-sort relevance/citations/year | 远端候选排序，默认 relevance |
| --merge-policy title/doi | 默认 title；doi 使用保守合并策略 |
| --strict-filters / --no-strict-filters | 严格模式在联网前拒绝忽略或覆盖条件 |
| --explain | JSON 解释生效计划、各源查询数与筛选能力，不联网、不写文件 |
| --title VALUE | Markdown 标题与默认文件名片段 |
| --output PATH | 主输出，后缀为 .md/.csv/.json |
| --doi-list PATH | 覆盖 DOI 列表路径（不能传空串） |
| --no-doi-list | 跳过生成 DOI 列表文件 |
| --overwrite | 允许覆盖已有文件 |
| --stdout | 结果写 stdout，默认 JSON |
| --stdout-format json/md/csv/doi | 指定 stdout 格式，需同时指定 --stdout |
| --version / --help | 查看版本或完整帮助 |

使用 `--oa-filter` 时，以下 OpenAlex filter 键会覆盖对应结构化参数并产出 `filter_override` 诊断（其中 `--year` 允许单侧覆盖 `from_publication_date` 或 `to_publication_date`，而 `publication_year` 会整体覆盖年份范围）：

| 结构化参数 | 对应的 OpenAlex 原始 filter 键 |
|---|---|
| `--year` | `from_publication_date`、`to_publication_date`、`publication_year` |
| `--min-citations` | `cited_by_count` |
| `--author` | `authorships.author.id`、`raw_author_name.search` |
| `--institution` | `institutions.id`、`institutions.ror`、`raw_affiliation_strings.search` |
| `--journal-issn` | `primary_location.source.issn` |
| `--type` | `type` |
| `--oa-only` | `is_oa`、`open_access.is_oa` |
| `--language` | `language` |

## 候选、过滤与排序

`--limit` 控制每个来源/查询交给后续流程的候选窗口，`--max-results` 控制最终产出。远端候选在跨源合并后按需补摘要，执行标题/摘要主题过滤，再排序截断。

- **游标分页与扫描预算**：三源均使用游标分页。Crossref 与 Europe PMC 需要按 `--min-citations` 或 `--language` 在本地筛选时，扫描预算扩大为 `3 × limit`（保留至多 `limit` 条），并发出 `provider_warning`。`queries[]` 中的 `fetched_count`、`mapped_count`、`returned_count` 与 `stop_reason`（`limit` / `scan_budget` / `exhausted`）记录执行情况。Crossref `relevance` 抓取在所有分页明确使用 `score` 降序。
- **抓取序 vs 展示序**：`--fetch-sort` 决定进入窗口的候选，`--sort` 只改窗口内结果的展示顺序。专题检索应保留默认 `relevance` 抓取，并显式指定 `--sort hybrid`；使用 `--fetch-sort citations` 或 `year` 仅适用于不依赖主题相关性、纯按被引或时间抓取的场景（Europe PMC 仅支持 `relevance` 抓取）。
- **各来源查询语义差异**：
  - OpenAlex 支持英文双引号短语与大写 `AND`/`OR`/`NOT`，检索范围可涵盖全文与关键词。
  - Europe PMC 支持字段与布尔语法，概念组自动编译至 `TITLE_ABS:`。
  - Crossref 按自身分词与打分语义检索，引号与 `AND` 不作为标题/摘要硬约束；如需各主题词组必现于本地标题或摘要，请配合 `--require`。
- **预算控制**：建议先以小窗口配合 `--explain` 核对命中与 `sources[].query_count`，再扩大 `--limit` 与 `--enrich-abstracts`（实际耗时随查询数、分页及重试次数增长）。

匹配前统一大小写和标点，主题过滤只检查标题与摘要：

| 写法 | 语义 |
|---|---|
| protist | 词首前缀，命中 protists |
| =sea | 整词精确匹配 |
| *bacteri* | 任意位置子串，命中 cyanobacteria |
| 海洋 | 非 ASCII 词使用子串匹配 |
| title:protist | 只匹配标题 |
| abstract:monthly | 只匹配摘要 |

`title:` / `abstract:` 适用于 require 与 exclude，可以与整词和子串写法组合。CLI 去掉逗号分隔的空词项；空 require/concept 组在联网前报参数错误，空 exclude 项不产生排除效果。每个 require 组的统计基于同一份排除词过滤后的集合，便于定位筛选瓶颈。

`--enrich-abstracts N` 在硬过滤前向 Europe PMC core 按 DOI 补空摘要（默认 `0`）：

- `N` 仅统计**本次尚未缓存的新 DOI**，按候选顺序每批最多合并查询 10 个 DOI；同客户端会复用正负缓存。
- 分页、HTTP 重试与缓存读取不额外消耗预算，但任一批次请求或解析失败会导致整次检索失败。
- 由于 Europe PMC 偏重生命科学与生物医学期刊，物理海洋、地球科学等非生医期刊可能会较多返回 `abstract_not_found`。详见 [Python 契约](python.md#摘要补齐)。

## 概念查询与主题排序

概念组表达主题的不同要素，组内放近义词。手工 queries 与生成查询合并去重；concepts 用于软证据，require 用于硬过滤。

规范化先去除词项两端空白和组内重复词，再移除词项及词序均相同的重复组，保留首次出现的位置。组内同义词顺序保留；词序不同的组仍分别参与查询与排序。只有 concepts 时也可以检索：

```bash
litsearch --concept "South China Sea,Daya Bay" --concept "protist,ciliate" --concept "seasonal,monthly" --source openalex,europepmc --sort hybrid --stdout
```

- **OpenAlex**：将每组内词编译为带引号的 `OR` 条件，组间以 `AND` 连接。
- **Europe PMC**：规则同 OpenAlex，但每个词项加 `TITLE_ABS:` 前缀。
- **Crossref**：按同义词位置轮换生成纯文本查询（查询条数等于最长同义词组长度，组数 ≥ 3 时再加 1 条首词宽查询），不使用其他来源的布尔语法。
- **宽召回查询**：概念组 ≥ 3 组时，额外生成只含前两组的宽查询（OpenAlex/Europe PMC 为 1 条前两组联合查询，Crossref 为 1 条前两组首词查询），因此建议将核心地域与研究对象放在前两组。

联网前可用 `--explain` 查看 `compiled_queries`（实际查询语法）及 `sources[].query_count`（各源待发请求条数预估），再据此调整概念组或预设。

| 排序 | 计算与用途 |
|---|---|
| citations / year | 按被引数或年份排序 |
| relevance | 按来源原始相关分排序；缺分记录按被引数排在后段 |
| rrf | 每个独立 source/query 列表贡献 `1/(60+rank)`，同列表同文献保留最佳名次 |
| bm25 | 标题与摘要分别计算 BM25 后相加，标题权重 2、摘要权重 1；同义词组取最高贡献，再乘概念覆盖比例 |
| hybrid | 对 RRF 分与字段 BM25 分分别转为 dense 名次（1、1、2），共分共享名次；正分项再以 `1/(60+rank)` 融合并乘概念覆盖比例 |

字段 BM25 使用 `k1=1.2`、`b=0.75`，IDF 为 `log(1+(N-df+0.5)/(df+0.5))`。N 来自主题过滤后、最终截断前的候选池；df 按每个词项在标题或摘要中的命中并集计数，每个候选至多计一次，字段限定词只统计指定字段。标题与摘要分别计算平均长度和长度归一化，缺失字段长度记为零。排序词组优先使用 concepts，其次 require，否则从 queries 提取非停用词。此实现为字段加权 BM25。概念覆盖比例为命中组占全部组的比例，作用于 bm25/hybrid，摘要缺失会在证据中标明。

rrf/bm25/hybrid 对标题明确标为补充材料、评议回复或推荐记录的条目设置 ancillary 标记，并将最终 `ranking_score` 乘 0.5；条目保留在结果中。`ranking_evidence.rrf/bm25` 保存覆盖比例与 ancillary 处理前的原始分项；结合 concept_coverage、record_flags 和所选排序复算最终分数。分数相同时按被引数和规范化身份确定次序。默认排序为 citations。

## 去重与来源差异

相同 DOI 或同一来源身份会合并，字段优先选择被引数较高的记录，平手时 OpenAlex 优先，并从另一条记录补齐空值。跨源被引数统计口径不同。

标题身份先做 NFKC 与 casefold 规范化，再只保留字母数字，因此连字符、空白和下划线不区分身份。默认 title 策略合并足够长的归一化同名标题；doi 策略保留不同 DOI 的同名条目，并将其 DOI 写入 related_dois 供人工核对。两种策略使用同一标题身份规则，并保留来源身份、DOI 别名与合并依据。通用短标题不参与标题关联或合并。

Crossref 有 DOI 的空标题记录保留，并按来源/查询聚合产生 field 为 title 的 provider_warning；其他来源可在合并时补齐标题。来源登记的 grant、peer-review 等类型同样保留；只需要 article 时，对支持类型筛选的来源显式使用 `--type article`。

Crossref 的机构、OA、OpenAlex 作者 ID、OpenAlex 原始 filter 及非 Crossref 支持类型（如 `review`）不受支持；被引数和语言在本地处理。所有被忽略或覆盖的条件进入统一格式的结构化诊断，严格模式会在发送任何请求前拒绝。

Europe PMC 无需 API key，年份、OA、作者姓名和 ISSN 在远端执行；被引数和语言在本地处理并产出 `provider_warning`。机构、类型筛选、OpenAlex 作者 ID/原始 filter 及 `relevance` 之外的 `fetch_sort` 不受支持，普通模式诊断后忽略，严格模式联网前拒绝。

各来源的覆盖范围和元数据完整性各异。远端全文或关键词命中可能在本地标题/摘要中找不到对应词，且语言字段可能缺失；这些条件会影响后置过滤的保留结果。

## 输出与错误

默认生成 `<output_dir>/litsearch_<片段>_<日期>.md` 与同目录同名 `.doi.txt`。片段优先取 `--title`，否则取首个查询，英文转小写、空格及非词字符替换为 `-`，保留中文并按词边界截断（上限 48 字符；全标点时退化为短哈希）。不同参数若产生同名目标，可通过 `--output` 隔离路径。

显式指定 `--output path/to/name.<ext>` 且未传 `--doi-list` 时，DOI 列表默认派生为同目录的 `path/to/name.doi.txt`；如不需要生成 DOI 列表，请传入 `--no-doi-list`。DOI 列表每行一个裸 DOI（可直接交给 Zotero 按标识符添加），无 DOI 条目会在诊断中说明。

主结果与 DOI 目标必须是不同文件；已有目标需显式 `--overwrite`。内容先在目标目录预写，再逐文件替换。跨文件提交失败时退出码为 5，stdout 列出已提交路径，stderr 给出错误。

联网前发现已有文件未允许覆盖、目标为目录、父路径为文件或 `--doi-list ""` 时，按用法错误返回 2。`--overwrite` 只替换已有文件，不接受目录目标。预写或提交阶段遇到权限错误或同名竞争等 IO 失败返回 5。

默认提交原子拒绝并发创建的同名目标（POSIX 使用同目录硬链接，文件系统不支持时返回输出错误；有意替换已有文件请用 `--overwrite`）。使用 `--overwrite` 时各目标分别替换，多进程写入同一目标最后提交者胜出；如需严格隔离，请为每次运行指定独立路径。

| 退出码 | 含义 |
|---|---|
| 0 | 成功，允许合法空结果 |
| 2 | 命令行、检索意图或联网前的输出目标用法错误 |
| 3 | 配置错误、指定 `--preset` 不存在或所需凭据缺失 |
| 4 | 网络、响应结构或远端错误 |
| 5 | 文件输出失败 |

任一来源请求失败时整个检索失败。stdout JSON 包含诊断；文件模式的 stdout 为成功路径，日志与诊断走 stderr。

stdout、stderr 和结果文件均为 UTF-8；CSV 的记录分隔符为 CRLF，stdout 与文件输出一致，不附加空记录，字段内换行按 CSV 引号规则保留。管道、子进程和日志读取端应显式使用 UTF-8，例如 subprocess.run(..., text=True, encoding="utf-8")。合法空结果保持退出码 0，empty_results 诊断说明筛空阶段，fetch_window_limited 说明已扫描窗口；stop_reason 保留取数阶段的结束原因。
