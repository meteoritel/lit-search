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

`.env` 提供 OPENALEX_API_KEY、CROSSREF_API_KEY、CROSSREF_MAILTO、LITSEARCH_OUTPUT_DIR 的回退值。配置文件使用 UTF-8；`.env` 兼容文件开头的 UTF-8 BOM，TOML 按标准解析器的编码规则读取。

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

`--fetch-sort` 决定哪些候选进入窗口；`--sort` 只改变窗口内结果的展示顺序。专题查找保留默认 relevance 取数，再显式使用 hybrid；在该窗口内找高被引文献使用 citations：

```bash
litsearch "marine bacteria" --source crossref --sort hybrid --stdout
litsearch "marine bacteria" --source crossref --sort citations --stdout
```

OpenAlex/Crossref 的 `--fetch-sort citations/year` 在来源匹配范围内优先取高被引或较新的候选；主题查询仍随请求发送，但与默认相关性窗口相比，贴近专题的文献可能排在窗口之外。Europe PMC 当前仅支持 relevance 取数。

查询语义按来源区分：OpenAlex 支持英文双引号短语与大写 AND/OR/NOT，其搜索范围可包含全文和关键词；Europe PMC 支持字段与 Boolean 查询，concepts 编译到 `TITLE_ABS:`；Crossref 按自身词查询语义检索，英文引号或 AND 不作为本工具的标题/摘要硬约束。需要每组主题词都出现在本地标题或摘要时，显式使用 `--require`。

推荐先用较小窗口观察命中与诊断，再增加候选并核对筛选阶段统计，最后保存为预设。来源、查询数量、分页、本地扫描条件及补摘要预算都会增加请求成本；Europe PMC 或多个缺失摘要 DOI 的检索宜逐步扩大预算，实际耗时取决于远端响应和重试。

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

`--enrich-abstracts N` 在硬过滤前向 Europe PMC 按 DOI 补空摘要，默认 0。N 控制尚未缓存的新 DOI 数，按候选顺序分成每批最多 10 个 DOI；分页和重试不额外消耗 DOI 预算。每批完整读取分页后缓存摘要或未找到结果，同客户端可复用正负缓存。查不到或预算用尽会产生诊断，响应或网络失败则整个检索失败。`requested` 按新 DOI 计数；缓存与统计详见 [Python 契约](python.md#摘要补齐)。

## 概念查询与主题排序

概念组表达主题的不同要素，组内放近义词。手工 queries 与生成查询合并去重；concepts 用于软证据，require 用于硬过滤。

规范化先去除词项两端空白和组内重复词，再移除词项及词序均相同的重复组，保留首次出现的位置。组内同义词顺序保留；词序不同的组仍分别参与查询与排序。只有 concepts 时也可以检索：

```bash
litsearch --concept "South China Sea,Daya Bay" --concept "protist,ciliate" --concept "seasonal,monthly" --source openalex,europepmc --sort hybrid --stdout
```

OpenAlex 将组内词编译为带引号的 OR 条件，组间用 AND；Europe PMC 在词前加 `TITLE_ABS:`。概念组至少为三组时，额外生成只含前两组的宽查询，因此将基本地理与研究对象放在前面。Crossref 按同义词位置轮换生成普通文本查询，并增加相同的宽查询，使用其自身的查询语义。`--explain` 的 compiled_queries 显示实际查询与来源筛选条件。

预设由用户在 TOML 配置中定义，格式见[配置发现与优先级](#配置发现与优先级)。可先使用 `--explain` 核对实际查询，再根据检索结果调整 concepts、require 和排序方式。

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

Crossref 的机构、OA、OpenAlex 作者 ID 和 OpenAlex 原始 filter 条件不受支持；被引数和语言在本地处理。所有被忽略条件进入结构化诊断。严格模式会在发送任何请求前拒绝被忽略或覆盖的条件。

Europe PMC 无需 API key，年份、OA、作者姓名和 ISSN 在远端执行；被引数和语言在本地处理。机构、类型筛选、OpenAlex 作者 ID/原始 filter 及 relevance 之外的 fetch_sort 不受支持，普通模式诊断后忽略，严格模式联网前拒绝。

各来源的覆盖范围和元数据完整性各异。远端全文或关键词命中可能在本地标题/摘要中找不到对应词，且语言字段可能缺失；这些条件会影响后置过滤的保留结果。

## 输出与错误

默认生成 `<output_dir>/litsearch_<片段>_<日期>.md` 与同名 `.doi.txt`。片段优先取 title，否则取首个查询，保留中文，截断上限为 48 个字符；不同标题或参数可能生成同名目标。批次检索可通过不同 `--output` 路径隔离产物。DOI 列表每行一个裸 DOI，无 DOI 条目在诊断中说明，可交给 Zotero 的按标识符添加功能。

主结果与 DOI 目标必须是不同文件；已有目标需显式 `--overwrite`。内容先在目标目录预写，再逐文件替换。跨文件提交失败时，退出码为 5，stdout 列出已提交路径，stderr 给出错误；调用方应结合退出码处理这批产物。

联网前发现已有文件未允许覆盖、目标为目录或父路径为文件时，按目标用法错误返回 2。目录目标应改为文件路径，`--overwrite` 只允许替换已有文件。预写或提交阶段的权限错误、同名竞争等文件操作失败返回 5。

默认提交会原子地拒绝并发创建的同名目标。POSIX 上的默认提交使用同目录硬链接；文件系统不支持该操作时返回输出错误，有意替换目标时可使用 `--overwrite`。

`--overwrite` 对各目标分别替换；多进程写入同一目标时最后提交者胜出。主结果与 DOI 文件分别提交，可能来自不同运行。需要隔离运行产物时使用独立输出路径。

| 退出码 | 含义 |
|---|---|
| 0 | 成功，允许合法空结果 |
| 2 | 命令行、检索意图或联网前的输出目标用法错误 |
| 3 | 配置错误或所需凭据缺失 |
| 4 | 网络、响应结构或远端错误 |
| 5 | 文件输出失败 |

任一来源请求失败时整个检索失败。stdout JSON 包含诊断；文件模式的 stdout 为成功路径，日志与诊断走 stderr。

stdout、stderr 和结果文件均为 UTF-8；CSV 的记录分隔符为 CRLF，stdout 与文件输出一致，不附加空记录，字段内换行按 CSV 引号规则保留。管道、子进程和日志读取端应显式使用 UTF-8，例如 subprocess.run(..., text=True, encoding="utf-8")。合法空结果保持退出码 0，empty_results 诊断说明筛空阶段，fetch_window_limited 说明已扫描窗口；stop_reason 保留取数阶段的结束原因。
