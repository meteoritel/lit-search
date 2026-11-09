# 专题检索的外部技术证据

查证日期：2026-11-09。本文核对 Crossref、OpenAlex、Europe PMC 的公开契约，以及可借鉴的排序实现。API 文档规定、固定版本源码行为与适配推论分别标注。专题召回率与排序质量需由项目的标注文献评测确定。

## 已确认结论

- Crossref 的相关排序参数是 `sort=score&order=desc`；cursor 分页与文本查询可以共用。官方历史 issue 记录过 cursor 改变默认相关排序的行为，显式排序具有直接的排查依据。[C1][C2][C3]
- OpenAlex 当前 `search` 涵盖标题、摘要、全文和 keywords；它与本地标题/摘要中的字面词匹配具有不同语义。当前官方说明还包含 stemming、Boolean、phrase、scoped search 和 `.exact`。[O1]
- RRF 使用列表内排名融合；ranx 的实际实现按文献 ID 求和，既不规范化 DOI，也不识别重试产生的重复列表。[R1][R2][R3]
- Europe PMC 的 `core` 响应含 `abstractText`，可承担摘要补充的数据来源；其生命科学索引和字段查询为生态文献提供可检索入口。[E1][E2][E3]

## Crossref：cursor 与相关排序

**文档规定。** Crossref 的官方 REST 文档列出 `score`/`relevance` 排序字段，以及 `asc`/`desc` 方向。旧文档写明有 query 时默认按相关性排序；该仓库已标记 deprecated，因此当前分页操作同时参考 Crossref 官网：初始 `cursor=*`，后续将响应的 `next-cursor` 原样作为 cursor 参数并进行 URL 编码；返回条目少于请求的 `rows` 时到达末页。[C1][C2]

**历史运行证据。** Crossref 官方 issue #1280 给出同一 `query.author` 的普通请求、加 `cursor=*` 的请求、加 `cursor=*&sort=score` 的请求；报告者记录普通请求和显式 score 请求符合相关排序，单独加 cursor 的请求顺序异常。该 issue 的复现范围为对应接口与查询。[C3]

**具体客户端实现。** habanero 在构造 payload 时并列传入 `query`、`sort`、`order`、`cursor`；分页循环仅替换 cursor，保留其他查询参数。它读取 `next-cursor`、检查空页并执行用户配置的取数上限。其 sort 校验接受 `score` 和 `relevance`。这为“查询和排序条件贯穿全部分页”的实现提供了可读源码，客户端本身不自动强制相关排序。[C4][C5]

**适配含义（推论）。** 显式携带 `sort=score&order=desc` 可以表达相关排序意图，并使离线请求断言可核查；文献映射之外还需要保留来源内顺序，RRF 才有可用的排名输入。分页能扩展同一查询的结果深度；查询匹配集合和分页排序分别影响候选覆盖。[C1][R1]

**项目复核。** 显式 score 的受控 cursor 请求已返回来源分数及已知核心文献；当前分页回归覆盖所有页保留查询与排序参数。专题结果见[评测说明](retrieval-quality-evaluation.md)。

## OpenAlex：查询语义与分值尺度

**当前文档规定。** 官方 Help 的搜索文档更新日期为 2026-11-03，固定版本内容已核对。与旧版 GitBook 文档相比，当前契约包含 keywords 扩展和新的 scoped search 参数。[O1]

| 方面 | 已查证语义 |
| --- | --- |
| 字段范围 | `search` 搜索 Works 的 title、abstract、fulltext、keywords；`search.title_and_abstract` 收窄至标题和摘要；`search.title_abstract_keywords` 加入关键词匹配。[O1] |
| 关键词扩展 | 查询中识别到 keyword 名称或其同义词时，可以由作品的 keyword 标签满足相应条件；这会包含文本中使用其他表述的记录。[O1] |
| Boolean | 使用大写 `AND`、`OR`、`NOT` 和括号；未写操作符的词按 AND 连接。排除词使用 NOT。[O1] |
| stemming | 默认进行 stemming 和停用词处理；`search.exact` 及对应 scoped `.exact` 形式关闭 stemming。[O1] |
| phrase | 双引号表示短语；标题/摘要中的短停用词短语有精确处理规则，广义全文搜索仍移除停用词。跨范围查询需要分别验证。[O1] |
| wildcard | 当前文档支持 `*`、`?`，要求 exact 搜索和足够的前缀字符；将通配符直接放入默认 stemmed search 会触发校验错误。[O1] |

**分值的已确认含义。** `relevance_score` 用于当前搜索的默认降序，官方说明其依据包括文本相似度、受上限约束的引用加成、keyword bonus。当前源码以 `function_score` 乘入引用加成，Works 默认使用 saturation；默认参数对应 `1 + 0.5*c/(c+100)`。标题、摘要和全文还具有不同匹配权重。源码是实现证据，其参数可以由服务端配置改变。[O1][O2][O3]

**跨查询比较的证据边界。** 上述文档给出单次搜索的相关分含义；已核对实现按各自查询构造匹配条件和加权分值，未包含跨查询校准步骤。因此，“不同查询的原始分可以直接取 max 或相加”缺少统一尺度依据，这是依据实现作出的推论。按列表内名次计算的 RRF 不需要该假设。[O2][O3][R1]

**适配含义（推论）。** 上游在全文、keywords 或词形处理后命中的文献，其返回标题/摘要可能没有同一字面词。对不同字段范围、同义词组和短语分别保留检索来源，能够解释候选如何进入结果集；是否采用这些信息参与筛选或排序属于项目评测问题。[O1]

## 融合与字段排序：可借鉴实现

### RRF

**原始研究。** Cormack、Clarke、Büttcher 的 SIGIR 2009 论文定义：

\[
\operatorname{RRF}(d)=\sum_{L:d\in L}\frac{1}{k+\operatorname{rank}_L(d)}
\]

排名从 1 开始。论文试验采用 `k=60`，说明该常数用于减弱异常高名次的影响；论文评测对象是其 TREC/LETOR 实验集合。[R1]

**ranx 的实际实现。** `_rrf_score` 按已排序文献 ID 顺序枚举，计算 `1/(k+i+1)`，默认 `k=60`；随后 `comb_sum` 按文献 ID 加总并排序。`Run` 接受 `query_id -> doc_id -> score` 的字典结构，构造时排序，同一 query 内同一 doc ID 只有一个字典槽位。[R2][R3][R4]

由源码可直接得出以下边界：

- **同列表重复文献：** 字典结构承载一个 doc ID 对应一个值；把原始列表转为该结构之前，仍要确定重复记录的保留规则。[R4]
- **跨列表同一文献：** 相同 doc ID 的贡献相加；裸 DOI 与 DOI URL 若作为不同字符串传入，会成为不同文献。DOI Foundation 对 DOI 名称等价比较规定的是 ASCII case folding。[R3][D1]
- **重复列表：** `rrf` 遍历调用方提交的全部 runs；重复提交同一 run 仍重复贡献。实现没有请求签名、重试识别或 provider 分组。[R2][R3]
- **来源权重：** 此实现对每条输入列表同等计票。某 provider 或查询族输入更多列表时，会有更多潜在贡献；该性质来自求和公式。是否按来源分组、如何赋权，需要专题对照评测。[R1][R2]

**适配含义（推论）。** 该算法的核心可以用标准库 `dict`、`set` 和排序独立表达。借鉴对象是“稳定身份、列表内名次、融合求和”的边界；HTTP 分页、DOI 合并和摘要补充仍是独立职责。`k=60` 是有原始研究和开源实现依据的基线值，专题上的参数选择尚未由本研究测定。[R1][R2][R4]

### BM25 与字段权重

**rank_bm25 的具体实现。** `BM25Okapi` 保存文献长度、平均长度、词频和文献频率，再对查询词累计具有 TF 饱和与长度归一化的分值。其 IDF 使用 `log(N-df+0.5)-log(df+0.5)`，负 IDF 被替换为 `epsilon * average_idf`；这个替代值随语料变化。输入分词由调用方或 tokenizer 提供，包本身没有为文献标题/摘要自动确定统一语言处理规则。[B1]

**Whoosh 的具体实现。** `BM25F` 支持字段特定的 `B`，从父 searcher 读取字段 IDF 和平均字段长度；其 `bm25` 核心公式显式使用字段长度与平均字段长度。`MultifieldPlugin` 将未指定字段的词展开到指定字段，并施加各字段的 boost。[B2][B3]

**BM25F 的公式差异。** Microsoft Research 发布的论文明确先计算 `f = Σ字段 w字段 * tf字段 / β字段`，再对合并后的 `f` 应用饱和函数 `f/(k+f)`，其中 `β字段` 为字段长度归一化因子。[B4] Whoosh 中字段 scorer、字段长度归一化和查询 boost 则是可以分别核查的机制。[B2][B3] 因此，分别计算 `BM25(title)` 与 `BM25(abstract)` 再加权相加，应称为字段加权 BM25；它与上述 BM25F 的饱和顺序不同。

**适配含义（推论）。** 标题、摘要、关键词可以作为独立字段保存匹配与长度统计。若统计语料只是本次检索得到的候选集合，`N`、`df` 与平均长度便来自候选集合；与上游完整索引的统计基础不同。算法对候选排序的作用、缺失摘要的影响，以及字段权重与 fusion 的组合均需实测。[B1][B2]

### 开源项目与许可

这些项目具有公开算法实现、明确 API 与可检查测试。表中“影响”是针对本项目 Python 标准库、单文件约束的适配分析。

| 项目 | 已核对许可证 | 可借鉴点 | 对本项目约束的影响 |
| --- | --- | --- | --- |
| habanero | [MIT](https://github.com/sckott/habanero/blob/b8f4bb67e4e01256a84a28d5ca826d73cfe59044/LICENSE.md) | payload 与 cursor 状态分开，分页保留排序参数。[C4] | 当前包声明第三方 HTTP 与进度条依赖；可参考状态流，再使用现有 opener 实现。[C6] |
| ranx | [MIT](https://github.com/AmenRa/ranx/blob/7363db0c35e92e90d6fa6fe73907b760678f765e/LICENSE) | RRF 名次转换、doc ID 求和、排序数据结构。[R2][R3][R4] | 包依赖 Numba、NumPy 等；算法核心不要求这些加速设施。[R5] |
| rank_bm25 | [Apache-2.0](https://github.com/dorianbrown/rank_bm25/blob/47aa3ddf8dc1ebeb7ef4e65f2b4536af44594099/LICENSE) | BM25 的统计量、TF 饱和、IDF 变体可单独审阅。[B1] | 源码使用 NumPy；用标准库表达公式会成为本项目自己的实现，需要明确分词和 IDF 约定。[B1] |
| Whoosh | [BSD-2-Clause](https://github.com/mchaput/whoosh/blob/d9a3fa2a4905e7326c9623c89e6395713c189161/LICENSE.txt) | 字段长度归一化与字段 boost 的职责分开。[B2][B3] | 可参考字段排序机制；完整索引、parser 和 matcher 体系超出单文件检索器所需的算法边界。[B2][B3] |

直接复用这些实现中的代码时，其许可证对应的版权和许可声明随复用内容保留。

## Europe PMC：生命科学检索与摘要补充

**文档规定。** Europe PMC REST 默认按相关性排序，支持 XML/JSON；`lite` 为默认结果类型，`core` 返回完整元数据。官方 Reference Guide 中 `abstractText` 属于 core，`doi`、`title`、`id`、`source` 可用于关联记录；`id + source` 共同构成来源内身份。[E1][E2]

**字段与 Boolean。** Reference Guide 明列 `TITLE`、`ABSTRACT`、`DOI`、`PUB_YEAR`、`FIRST_PDATE` 等字段，日期范围使用 `[起始 TO 结束]`。`FIRST_PDATE` 表示电子或印刷中的首次发表日期，只有年份等不完整日期时会由算法补全。Europe PMC 团队论文展示了 `TITLE_ABS` 与 OR、短语、通配符的组合查询，并说明支持 Boolean 和字段搜索。[E2][E3]

**分页。** 官方 Guide 规定第一页 `cursorMark=*`，后续使用 `nextCursorMark`；`pageSize` 控制分页大小。官方 REST 页面将结果语义与 Reference Guide 关联。cursor 是接口分页状态，不能用它替代文献身份去重。[E1][E2]

**生命科学与生态适配。** 官方列出 PubMed、Agricola 等数据来源，提供生物数据库交叉链接及 organism 等文本挖掘内容。团队论文将其范围表述为生命科学文献，并介绍其来源和 preprint 接入。因此它与原生生物/生态主题存在范围交集；专题金标准的覆盖程度是独立的实测问题。[E1][E3]

**接口核验。** 本研究向生产 REST API 发出以下公开请求：

```text
query=(TITLE_ABS:protist OR TITLE_ABS:protists) AND FIRST_PDATE:[2021-01-01 TO 2026-12-31]
format=json
resultType=core
cursorMark=*
pageSize=1
```

响应 `version=6.9`，回显相同 query 和 core/cursor 参数，返回海洋 protist plankton 生态论文记录，含 DOI、`abstractText`、`nextCursorMark`。这项观察确认字段组合、core 摘要和 cursor 首次请求在该实例上可用。[E4]

**适配含义（推论）。** DOI 定位后取得 core 元数据与直接主题检索是两个可比较用途。前者向已知候选补摘要，后者扩展候选集合；其对召回的影响分别来自筛选可见文本与新增记录。已查资料支持这两个入口的技术可行性，本文未测定专题增益。[E1][E2][E3]

## 专题评测的进一步问题

| 问题 | 外部证据已确定的部分 | 专题上尚未测定的部分 |
| --- | --- | --- |
| Crossref 相关取数 | 排序参数、cursor 规则、历史 cursor 排序差异；项目已核对显式 score 的专题请求。[C1][C2][C3] | 更多查询与深度下的覆盖、限速和成本。 |
| OpenAlex 查询组 | Boolean、字段范围、词形处理和关键词扩展。[O1] | 各查询组新增相关文献、噪声与截断深度的关系。 |
| RRF | 名次求和、重复列表重复贡献、doc ID 合并。[R2][R3][R4] | 来源/查询族权重、列表深度、`k` 与专题排序的关系。 |
| 字段 BM25 | 字段长度、IDF、TF 饱和与 boost 的具体实现。[B1][B2][B3] | 候选集合统计稳定性、缺失摘要、权重及其与 RRF 的组合。 |
| Europe PMC | 字段检索、core 摘要、cursor 首次请求。[E1][E2][E4] | 金标准覆盖、摘要补充成功率、补充前后筛选结果与请求预算。 |

## 来源

[C1]: https://github.com/CrossRef/rest-api-doc/blob/00b1cc2c90e62d49c82f5381710590a6d9ca53ff/README.md#sorting "Crossref 官方旧文档：Sorting；同页 Result controls 和 deprecated 声明"
[C2]: https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/ "Crossref 官方当前指南：Rows and cursors，页面更新于 2025-10-24"
[C3]: https://gitlab.com/crossref/issues/-/work_items/1280 "Crossref 官方历史 issue：cursor 导致默认 relevance 排序变化"
[C4]: https://github.com/sckott/habanero/blob/b8f4bb67e4e01256a84a28d5ca826d73cfe59044/habanero/request_class.py#L81-L176 "habanero：构造 payload 与 cursor 循环"
[C5]: https://github.com/sckott/habanero/blob/b8f4bb67e4e01256a84a28d5ca826d73cfe59044/habanero/sort.py#L1-L31 "habanero：排序参数校验"
[C6]: https://github.com/sckott/habanero/blob/b8f4bb67e4e01256a84a28d5ca826d73cfe59044/pyproject.toml#L18-L22 "habanero：运行时依赖"
[O1]: https://github.com/ourresearch/openalex-help/blob/0f61077049cb5d5ff16d70691f1873542e98eb30/content/api/searching.md "OpenAlex 官方当前搜索说明；在线版 https://help.openalex.org/api/searching/"
[O2]: https://github.com/ourresearch/openalex-elastic-api/blob/2c893a7af7f17098b2a0639d1abdd86f40850c4d/core/search.py#L1393-L1430 "OpenAlex：citation score；同文件 L1676-L1700 为 Works 默认配置与 keywords 入口"
[O3]: https://github.com/ourresearch/openalex-elastic-api/blob/2c893a7af7f17098b2a0639d1abdd86f40850c4d/core/keyword_search.py#L248-L268 "OpenAlex：keyword bonus 与字段权重"
[R1]: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf "作者发布的 RRF 原始论文，DOI 10.1145/1571941.1572114"
[R2]: https://github.com/AmenRa/ranx/blob/7363db0c35e92e90d6fa6fe73907b760678f765e/ranx/fusion/rrf.py#L18-L71 "ranx：RRF 名次转换与默认 k"
[R3]: https://github.com/AmenRa/ranx/blob/7363db0c35e92e90d6fa6fe73907b760678f765e/ranx/fusion/comb_sum.py#L17-L73 "ranx：按 doc ID 融合全部列表"
[R4]: https://github.com/AmenRa/ranx/blob/7363db0c35e92e90d6fa6fe73907b760678f765e/ranx/data_structures/run.py#L24-L95 "ranx：Run 字典结构、排序与同 doc ID 覆盖"
[R5]: https://github.com/AmenRa/ranx/blob/7363db0c35e92e90d6fa6fe73907b760678f765e/requirements.txt "ranx：运行时依赖"
[D1]: https://www.doi.org/doi-handbook/DOI_Handbook_Final.pdf "DOI Foundation：Case Insensitivity of the DOI Name"
[B1]: https://github.com/dorianbrown/rank_bm25/blob/47aa3ddf8dc1ebeb7ef4e65f2b4536af44594099/rank_bm25.py#L15-L121 "rank_bm25：语料统计、分词入口、BM25Okapi 与 IDF 变体"
[B2]: https://github.com/mchaput/whoosh/blob/d9a3fa2a4905e7326c9623c89e6395713c189161/src/whoosh/scoring.py#L264-L332 "Whoosh：BM25F、字段 B、字段统计与核心公式"
[B3]: https://github.com/mchaput/whoosh/blob/d9a3fa2a4905e7326c9623c89e6395713c189161/src/whoosh/qparser/plugins.py#L1141-L1186 "Whoosh：MultifieldPlugin 的字段 boost"
[B4]: https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/sp0237-svore.pdf "Svore 与 Burges，A Machine Learning Approach for Improved BM25 Retrieval，Section 4：先合并字段词频再饱和的 BM25F 公式"
[E1]: https://dev.europepmc.org/RestfulWebService "Europe PMC 官方 REST 文档镜像；主站 https://europepmc.org/RestfulWebService 本次访问为 403"
[E2]: https://dev.europepmc.org/docs/EBI_Europe_PMC_Web_Service_Reference.pdf "Europe PMC 官方 Reference Guide 6.9.0：searchPublications、字段索引、结果类型"
[E3]: https://pmc.ncbi.nlm.nih.gov/articles/PMC11426508/ "Europe PMC 团队论文：检索范围、TITLE_ABS/Boolean 查询示例、数据接入；DOI 10.1371/journal.pone.0303005"
[E4]: https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%28TITLE_ABS%3Aprotist%20OR%20TITLE_ABS%3Aprotists%29%20AND%20FIRST_PDATE%3A%5B2021-01-01%20TO%202026-12-31%5D&format=json&resultType=core&cursorMark=%2A&pageSize=1 "Europe PMC 生产 API 的受控公开请求；返回顺序和记录会随索引更新"
